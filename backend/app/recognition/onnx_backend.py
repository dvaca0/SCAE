"""Backend de reconocimiento basado en ONNX Runtime.

PARCHE LOCAL (Raspberry Pi 5 / aarch64 / Python 3.13) — ver /opt/scae/PATCHES.md

¿Por qué existe este fichero?
-----------------------------
El backend original (`YoloVehicleDetector` + `EasyOcrPlateReader`) no se puede
instalar en esta plataforma: `opencv-python` y `opencv-python-headless` **no
publican rueda cp313+aarch64 en ninguna versión jamás publicada**, y `easyocr`
depende de ella y de `torch` (461 MB de descarga, ~1.5 GB instalado). Compilar
OpenCV son 2-4 h con los 4 núcleos saturados en una Pi que comparte hardware con
una plataforma de monitoreo energético en producción.

`recognition/pipeline.py` declara `VehicleDetector` y `PlateReader` como
`Protocol` precisamente para esto. Cita del autor:

    "so a better model (a dedicated plate detector, a cloud LPR, a Jetson-side
     service) is a new class, not a rewrite"

Así que esto es una clase nueva, no una reescritura. Se conservan intactos:
`plate.py` (normalización mexicana), los umbrales de confianza, el motor de
decisión, la auditoría, el registro de eventos y la carga manual de fotos.

Diferencia de diseño respecto al original
-----------------------------------------
El original detectaba **vehículos** (clases COCO) y pasaba el recorte del coche
al OCR. Este detecta **placas** directamente y pasa el recorte de la placa, que
es lo que el OCR necesita. Encaja en el mismo `Protocol` y en el mismo bucle de
`RecognitionPipeline.process()`, y da un recorte mucho más limpio al OCR.

Modelos (ambos MIT, descargados de releases oficiales)
-----------------------------------------------------
Detector: ankandrew/open-image-models · yolo-v9-t-{384,640}-license-plates-end2end
          Entrada [1,3,S,S] float32 NCHW · Salida [N,7] con NMS ya incluido
OCR     : ankandrew/fast-plate-ocr    · cct_{s,xs}_v2_global
          Entrada [N,64,128,3] uint8 NHWC RGB · Salidas plate[N,10,37] y region[N,66]
          `plate_regions` del modelo incluye explícitamente 'Mexico'.

Dependencias: onnxruntime + numpy + Pillow. **Nada de OpenCV, nada de torch.**
"""
from __future__ import annotations

import logging
import threading
from functools import lru_cache
from pathlib import Path
from typing import Optional

import numpy as np

from app.recognition.pipeline import BoundingBox

logger = logging.getLogger(__name__)

MODELS_DIR = Path("/opt/scae/models")

# Del fichero cct_s_v2_global_plate_config.yaml publicado con el modelo.
OCR_ALPHABET = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ_"
OCR_PAD_CHAR = "_"
OCR_IMG_H = 64
OCR_IMG_W = 128
OCR_MAX_SLOTS = 10

# plate_regions del modelo, en orden. La salida `region` tiene 66 clases.
OCR_REGIONS = [
    "Albania", "Andorra", "Argentina", "Armenia", "Australia", "Austria",
    "Azerbaijan", "Bahrain", "Belarus", "Belgium", "Bosnia and Herzegovina",
    "Brazil", "Bulgaria", "Cambodia", "Canada", "Croatia", "Cyprus",
    "Czech Republic", "Denmark", "Estonia", "Finland", "France", "Georgia",
    "Germany", "Gibraltar", "Greece", "Guernsey", "Hungary", "Iceland",
    "Indonesia", "Ireland", "Israel", "Italy", "Latvia", "Liechtenstein",
    "Lithuania", "Luxembourg", "Malaysia", "Malta", "Mexico", "Moldova",
    "Monaco", "Montenegro", "Netherlands", "New Zealand", "North Macedonia",
    "Norway", "Poland", "Portugal", "Qatar", "Romania", "San Marino", "Serbia",
    "Singapore", "Slovakia", "Slovenia", "Spain", "Sweden", "Switzerland",
    "Thailand", "Turkey", "United States", "Ukraine", "United Kingdom",
    "Vietnam", "Unknown",
]


def _session(path: Path):
    """Sesión de ONNX Runtime limitada a 2 hilos.

    La Pi tiene 4 núcleos y la tesis tiene prioridad absoluta: dejar que
    onnxruntime use los 4 saturaría la máquina durante cada inferencia.
    """
    import onnxruntime as ort

    opts = ort.SessionOptions()
    opts.intra_op_num_threads = 2
    opts.inter_op_num_threads = 1
    opts.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
    opts.log_severity_level = 3  # sólo errores
    return ort.InferenceSession(
        str(path), sess_options=opts, providers=["CPUExecutionProvider"]
    )


def _bgr_to_rgb(frame: np.ndarray) -> np.ndarray:
    """El pipeline de SCAE trabaja en BGR (contrato de cv2.imdecode); los
    modelos esperan RGB."""
    if frame.ndim == 2:
        return np.stack([frame] * 3, axis=-1)
    if frame.shape[2] == 4:
        frame = frame[:, :, :3]
    return frame[:, :, ::-1]


def _letterbox(rgb: np.ndarray, size: int) -> tuple[np.ndarray, float, int, int]:
    """Redimensiona a size x size conservando la relación de aspecto y rellenando.

    Devuelve (imagen, escala, pad_x, pad_y) para poder devolver las cajas a las
    coordenadas del fotograma original. Sin esto, las cajas saldrían deformadas
    en cualquier fotograma que no sea cuadrado — y ninguno lo es.
    """
    from PIL import Image

    h, w = rgb.shape[:2]
    scale = min(size / w, size / h)
    nw, nh = max(1, int(round(w * scale))), max(1, int(round(h * scale)))
    resized = Image.fromarray(rgb).resize((nw, nh), Image.BILINEAR)
    canvas = Image.new("RGB", (size, size), (114, 114, 114))
    pad_x, pad_y = (size - nw) // 2, (size - nh) // 2
    canvas.paste(resized, (pad_x, pad_y))
    return np.asarray(canvas), scale, pad_x, pad_y


class OnnxPlateDetector:
    """Detector de placas YOLOv9-t exportado a ONNX, con NMS en el grafo."""

    def __init__(self, model_path: str | Path, conf_threshold: float = 0.35):
        self._path = Path(model_path)
        if not self._path.is_file():
            raise FileNotFoundError(f"No existe el modelo detector: {self._path}")
        self.name = f"onnx:{self._path.stem}"
        self._conf = conf_threshold
        self._sess = None
        self._size = None
        self._lock = threading.Lock()

    def _ensure(self):
        # Carga diferida y protegida: el worker de cámara y las peticiones HTTP
        # comparten proceso, y dos cargas simultáneas duplicarían la memoria.
        if self._sess is None:
            with self._lock:
                if self._sess is None:
                    logger.info("Cargando detector de placas %s", self._path.name)
                    self._sess = _session(self._path)
                    shape = self._sess.get_inputs()[0].shape
                    self._size = int(shape[2])
        return self._sess

    def detect(self, frame: np.ndarray) -> list[BoundingBox]:
        sess = self._ensure()
        rgb = _bgr_to_rgb(frame)
        lb, scale, pad_x, pad_y = _letterbox(rgb, self._size)
        blob = np.ascontiguousarray(
            lb.astype(np.float32).transpose(2, 0, 1)[None] / 255.0
        )
        raw = sess.run(None, {sess.get_inputs()[0].name: blob})[0]
        raw = np.asarray(raw)
        if raw.size == 0:
            return []
        raw = raw.reshape(-1, raw.shape[-1])

        h, w = rgb.shape[:2]
        boxes: list[BoundingBox] = []
        for row in raw:
            # Salida end2end, VERIFICADA empíricamente contra este export:
            #   [batch_id, x1, y1, x2, y2, class, score]
            # Ojo: `class` va ANTES de `score`, al contrario de lo que hacen
            # otros exports de YOLO. Leerlas al revés hace que el filtro de
            # confianza vea la clase (0.0) y descarte TODAS las detecciones en
            # silencio. Comprobado: caja (511,445)-(770,534) frente a una placa
            # sintética en (512,446)-(768,529).
            if row.shape[0] >= 7:
                x1, y1, x2, y2, cls, score = (
                    row[1], row[2], row[3], row[4], row[5], row[6]
                )
            else:  # pragma: no cover - export sin batch_id
                x1, y1, x2, y2, cls, score = (
                    row[0], row[1], row[2], row[3], row[4], row[5]
                )
            if float(score) < self._conf:
                continue
            # Deshacer el letterbox.
            bx1 = (float(x1) - pad_x) / scale
            by1 = (float(y1) - pad_y) / scale
            bx2 = (float(x2) - pad_x) / scale
            by2 = (float(y2) - pad_y) / scale
            bx1, by1 = max(0.0, bx1), max(0.0, by1)
            bx2, by2 = min(float(w), bx2), min(float(h), by2)
            if bx2 <= bx1 or by2 <= by1:
                continue
            boxes.append(
                BoundingBox(bx1, by1, bx2, by2, float(score), int(cls), "plate")
            )
        # La placa más grande primero: en una caseta, el vehículo más cercano.
        boxes.sort(key=lambda b: (b.x2 - b.x1) * (b.y2 - b.y1), reverse=True)
        return boxes


class OnnxPlateReader:
    """OCR de placas CCT exportado a ONNX. Entrenado con placas de México
    entre otras (ver `plate_regions` del modelo)."""

    def __init__(self, model_path: str | Path):
        self._path = Path(model_path)
        if not self._path.is_file():
            raise FileNotFoundError(f"No existe el modelo OCR: {self._path}")
        self.name = f"onnx:{self._path.stem}"
        self._sess = None
        self._lock = threading.Lock()
        self.last_region: Optional[str] = None

    def _ensure(self):
        if self._sess is None:
            with self._lock:
                if self._sess is None:
                    logger.info("Cargando OCR de placas %s", self._path.name)
                    self._sess = _session(self._path)
        return self._sess

    def read(self, image: np.ndarray) -> list[tuple[str, float]]:
        if image is None or image.size == 0:
            return []
        sess = self._ensure()
        from PIL import Image as PILImage

        rgb = _bgr_to_rgb(image)
        # El modelo NO conserva la relación de aspecto (keep_aspect_ratio: false)
        # y toma uint8 directo, sin normalizar.
        pil = PILImage.fromarray(rgb).resize(
            (OCR_IMG_W, OCR_IMG_H), PILImage.BILINEAR
        )
        blob = np.asarray(pil, dtype=np.uint8)[None]
        outputs = sess.run(None, {sess.get_inputs()[0].name: blob})

        named = {o.name: outputs[i] for i, o in enumerate(sess.get_outputs())}
        plate_out = np.asarray(named.get("plate", outputs[0]))[0]  # (10, 37)

        # CUIDADO: este export YA aplica softmax por slot — cada fila suma 1.0 y
        # los valores están en [0,1]. Verificado empíricamente: las sumas por
        # slot salen exactamente 1.0 y el carácter ganador vale ~0.9999.
        # Aplicar softmax otra vez aplastaba 0.9999 a 0.0702, por debajo del
        # umbral de 0.55 de SCAE, de modo que TODA placa correctamente leída
        # acababa en revisión manual. Se detecta la normalización en vez de
        # asumirla, para que el código siga valiendo si otro export devuelve
        # logits crudos.
        sums = plate_out.sum(axis=-1)
        already_normalised = bool(
            np.all(plate_out >= -1e-6) and np.allclose(sums, 1.0, atol=1e-3)
        )
        if already_normalised:
            probs = plate_out
        else:
            shifted = plate_out - plate_out.max(axis=-1, keepdims=True)
            probs = np.exp(shifted)
            probs /= probs.sum(axis=-1, keepdims=True)

        idx = probs.argmax(axis=-1)
        chars, confs = [], []
        for slot, j in enumerate(idx):
            ch = OCR_ALPHABET[int(j)]
            if ch == OCR_PAD_CHAR:
                continue
            chars.append(ch)
            confs.append(float(probs[slot, j]))

        if "region" in named:
            reg = np.asarray(named["region"])[0]
            r = int(reg.argmax())
            if 0 <= r < len(OCR_REGIONS):
                self.last_region = OCR_REGIONS[r]

        text = "".join(chars)
        if not text:
            return []
        # Confianza de la lectura = probabilidad del carácter MÁS DÉBIL, no la
        # media. Una placa sólo es tan fiable como su carácter menos seguro: si
        # seis caracteres van a 0.999 y uno a 0.50, la media (0.93) diría
        # "adelante" mientras la cadena puede estar mal, y en control de acceso
        # un falso "permitido" es mucho peor que una revisión manual de más.
        # Esto alimenta review_confidence_threshold de SCAE (0.55), así que es
        # deliberadamente conservador y nunca se redondea hacia arriba.
        confidence = float(min(confs)) if confs else 0.0
        return [(text, confidence)]


@lru_cache(maxsize=1)
def build_onnx_pipeline(
    detector_model: str, ocr_model: str, detector_conf: float | None = None,
):
    """Construye el pipeline ONNX reutilizando `RecognitionPipeline`."""
    from app.recognition.pipeline import RecognitionPipeline

    det = MODELS_DIR / detector_model
    ocr = MODELS_DIR / ocr_model
    conf = 0.35 if detector_conf is None else float(detector_conf)
    logger.info(
        "Backend de reconocimiento ONNX: %s (conf>=%.2f) + %s",
        det.name, conf, ocr.name,
    )
    return RecognitionPipeline(
        OnnxPlateDetector(det, conf_threshold=conf), OnnxPlateReader(ocr)
    )
