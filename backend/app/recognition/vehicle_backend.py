"""Deteccion de vehiculos con YOLOX sobre ONNX Runtime.

PARCHE LOCAL (Raspberry Pi 5) - ver /opt/scae/PATCHES.md

Para que
--------
El detector que ya usabamos (`yolo-v9-t-*-license-plates`) busca **placas**, no
vehiculos. Sirve para decidir, pero no para dibujar en la vista en vivo lo que
el sistema esta viendo: un rectangulo sobre una placa de 127 px no le dice al
guardia "hay un coche en el carril". Esto lo complementa, no lo sustituye.

Por que YOLOX-S cuantizado a int8
---------------------------------
Medido en esta Pi 5, con 2 hilos de ONNX Runtime y entrada 640x640:

    yolox-s int8 (9 MB)                        158 ms
    yolo-v9-t-640-license-plates (fp32, 8 MB)  211 ms

El cuantizado a int8 sale **mas rapido que el detector de placas que ya
teniamos**, que era el resultado opuesto al que esperaba: en ARM64 los kernels
de int8 compensan de sobra el modelo mas grande. Eso es lo que hace viable
detectar vehiculos de verdad aqui, en vez de aproximarlo con movimiento.

Procedencia y licencia: zoo de modelos de OpenCV
(huggingface.co/opencv/object_detection_yolox), YOLOX es Apache-2.0. La
licencia esta junto al modelo en /opt/scae/models/.

Detalles del postprocesado, verificados contra la implementacion de referencia
del zoo de OpenCV (`yolox.py`) y comprobados despues sobre fotogramas reales:

* La entrada **no se normaliza**. Nada de dividir por 255 ni restar la media:
  YOLOX se entrena con valores crudos de 0 a 255. (El `yolox.py` de referencia
  declara `mean`/`std` y despues no los usa en `preprocess`; copiarlos habria
  sido un error silencioso.)
* El orden de canales es BGR, igual que entrega `decode_image`.
* La salida [1, 8400, 85] viene SIN decodificar: hay que sumar la rejilla y
  multiplicar por el stride, y `wh` va en escala logaritmica.
* La puntuacion de cada caja es `objectness * clase`, no la clase sola.
"""
from __future__ import annotations

import logging
import threading
from pathlib import Path
from typing import Optional

import numpy as np

from app.recognition.pipeline import VEHICLE_CLASSES, BoundingBox

# Las 80 clases de COCO, para poder nombrar cualquier deteccion al diagnosticar.
COCO_NAMES = {
    0: "person", 1: "bicycle", 2: "car", 3: "motorcycle", 4: "airplane",
    5: "bus", 6: "train", 7: "truck", 8: "boat",
}

logger = logging.getLogger(__name__)

MODELS_DIR = Path("/opt/scae/models")
INPUT_SIZE = 640
STRIDES = (8, 16, 32)
# Valor de relleno del letterbox que usa YOLOX.
PAD_VALUE = 114


def _build_grid() -> tuple[np.ndarray, np.ndarray]:
    """Rejilla y strides expandidos, en el mismo orden que la salida del modelo."""
    grids, expanded = [], []
    for stride in STRIDES:
        size = INPUT_SIZE // stride
        xv, yv = np.meshgrid(np.arange(size), np.arange(size))
        grid = np.stack((xv, yv), 2).reshape(1, -1, 2)
        grids.append(grid)
        expanded.append(np.full((1, grid.shape[1], 1), stride))
    return (
        np.concatenate(grids, 1).astype(np.float32),
        np.concatenate(expanded, 1).astype(np.float32),
    )


def _letterbox(frame: np.ndarray) -> tuple[np.ndarray, float]:
    """Escala manteniendo la proporcion y rellena hasta 640x640.

    Devuelve el lienzo y la razon de escala, que es lo unico que hace falta
    para devolver las cajas a coordenadas del fotograma original (el relleno
    va abajo y a la derecha, asi que no hay desplazamiento que deshacer).

    Se usa Pillow y no OpenCV porque en esta plataforma (Python 3.13 + ARM64)
    no existe ninguna rueda de opencv-python; ver PATCHES.md.
    """
    from PIL import Image

    h, w = frame.shape[:2]
    ratio = min(INPUT_SIZE / h, INPUT_SIZE / w)
    nh, nw = max(1, int(round(h * ratio))), max(1, int(round(w * ratio)))
    resized = np.asarray(
        Image.fromarray(frame).resize((nw, nh), Image.BILINEAR)
    )
    canvas = np.full((INPUT_SIZE, INPUT_SIZE, 3), PAD_VALUE, dtype=np.uint8)
    canvas[:nh, :nw] = resized
    return canvas, ratio


def _nms(boxes: np.ndarray, scores: np.ndarray, iou_threshold: float) -> list[int]:
    """NMS propio: el de OpenCV no esta disponible aqui y son cuatro lineas.

    `boxes` en formato x1,y1,x2,y2.
    """
    x1, y1, x2, y2 = boxes[:, 0], boxes[:, 1], boxes[:, 2], boxes[:, 3]
    areas = np.maximum(0.0, x2 - x1) * np.maximum(0.0, y2 - y1)
    order = scores.argsort()[::-1]
    keep: list[int] = []
    while order.size > 0:
        i = int(order[0])
        keep.append(i)
        if order.size == 1:
            break
        rest = order[1:]
        xx1 = np.maximum(x1[i], x1[rest])
        yy1 = np.maximum(y1[i], y1[rest])
        xx2 = np.minimum(x2[i], x2[rest])
        yy2 = np.minimum(y2[i], y2[rest])
        inter = np.maximum(0.0, xx2 - xx1) * np.maximum(0.0, yy2 - yy1)
        union = areas[i] + areas[rest] - inter
        iou = np.where(union > 0, inter / union, 0.0)
        order = rest[iou <= iou_threshold]
    return keep


class OnnxVehicleDetector:
    """Detector de vehiculos COCO (coche, moto, autobus, camion)."""

    def __init__(
        self,
        model: str = "yolox-s-int8-coco.onnx",
        conf_threshold: float = 0.35,
        iou_threshold: float = 0.5,
        max_boxes: int = 6,
        classes: Optional[dict[int, str]] = None,
    ) -> None:
        # Que clases de COCO cuentan como "vehiculo aqui".
        #
        # Por defecto las de verdad: coche, moto, autobus, camion. Es
        # configurable por una razon medida, no por capricho: sobre la maqueta
        # a escala, YOLOX etiqueta el coche de 1:18 como **train** con 0.387 de
        # confianza, no como `car`. La geometria es correcta (la caja cae justo
        # sobre el coche, centro 790,306 en un fotograma de 1280x720); lo que
        # falla es la clase, porque un modelo a escala sobre una mesa no se
        # parece a ningun coche del conjunto de entrenamiento.
        #
        # Asi que la maqueta anade `train` por configuracion y un acceso real
        # NO debe hacerlo: alli un "tren" en el carril es ruido.
        self._classes = dict(classes) if classes else dict(VEHICLE_CLASSES)
        self.name = f"yolox:{model}"
        self._model_path = MODELS_DIR / model
        self._conf = conf_threshold
        self._iou = iou_threshold
        self._max_boxes = max_boxes
        self._session = None
        self._input_name = ""
        self._lock = threading.Lock()
        self._grid, self._strides = _build_grid()

    def _ensure_session(self):
        """Carga perezosa y una sola vez. ONNX Runtime no es seguro de crear
        desde varios hilos a la vez, de ahi el lock."""
        if self._session is not None:
            return self._session
        with self._lock:
            if self._session is not None:
                return self._session
            if not self._model_path.is_file():
                raise FileNotFoundError(
                    f"No se encontro el modelo de vehiculos: {self._model_path}"
                )
            import onnxruntime as ort

            opts = ort.SessionOptions()
            # Dos hilos como maximo: esta Pi comparte CPU con la plataforma de
            # monitoreo energetico de la tesis, que tiene prioridad absoluta.
            opts.intra_op_num_threads = 2
            opts.inter_op_num_threads = 1
            opts.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
            logger.info("Cargando detector de vehiculos %s", self._model_path.name)
            self._session = ort.InferenceSession(
                str(self._model_path), opts, providers=["CPUExecutionProvider"]
            )
            self._input_name = self._session.get_inputs()[0].name
            return self._session

    def detect(self, frame: np.ndarray) -> list[BoundingBox]:
        """Devuelve los vehiculos del fotograma, el mas grande primero."""
        session = self._ensure_session()
        canvas, ratio = _letterbox(frame)
        # CHW, float32, SIN normalizar (ver cabecera del modulo).
        blob = np.transpose(canvas, (2, 0, 1))[np.newaxis].astype(np.float32)
        raw = session.run(None, {self._input_name: blob})[0]

        dets = np.array(raw[0], dtype=np.float32, copy=True)
        if dets.ndim != 2 or dets.shape[1] < 6:
            return []

        # Decodificar: centro = (xy + rejilla) * stride, tamano = exp(wh) * stride.
        strides = self._strides[0]
        dets[:, :2] = (dets[:, :2] + self._grid[0]) * strides
        dets[:, 2:4] = np.exp(np.clip(dets[:, 2:4], -20, 20)) * strides

        # La puntuacion es objectness * clase.
        cls_scores = dets[:, 4:5] * dets[:, 5:]
        best = cls_scores.argmax(axis=1)
        best_score = cls_scores[np.arange(cls_scores.shape[0]), best]

        # Quedarse solo con las clases que son un vehiculo y pasan el umbral.
        wanted = np.isin(best, list(self._classes.keys()))
        keep_mask = wanted & (best_score >= self._conf)
        if not keep_mask.any():
            return []

        cx, cy, w, h = (dets[keep_mask, i] for i in range(4))
        xyxy = np.stack(
            [cx - w / 2.0, cy - h / 2.0, cx + w / 2.0, cy + h / 2.0], axis=1
        )
        scores = best_score[keep_mask]
        classes = best[keep_mask]

        boxes: list[BoundingBox] = []
        fh, fw = frame.shape[:2]
        for idx in _nms(xyxy, scores, self._iou)[: self._max_boxes]:
            # Deshacer el letterbox y recortar al fotograma.
            x1, y1, x2, y2 = (xyxy[idx] / ratio).tolist()
            x1 = max(0.0, min(float(fw), x1))
            y1 = max(0.0, min(float(fh), y1))
            x2 = max(0.0, min(float(fw), x2))
            y2 = max(0.0, min(float(fh), y2))
            if x2 - x1 < 2 or y2 - y1 < 2:
                continue
            cls_id = int(classes[idx])
            boxes.append(BoundingBox(
                x1, y1, x2, y2, float(scores[idx]), cls_id,
                self._classes.get(cls_id, COCO_NAMES.get(cls_id, str(cls_id))),
            ))
        # El vehiculo mas grande primero: en un carril, el sujeto es el de cerca.
        boxes.sort(key=lambda b: (b.x2 - b.x1) * (b.y2 - b.y1), reverse=True)
        return boxes


class OnnxYolo11VehicleDetector:
    """Detector de vehiculos YOLO11n. Mas rapido que YOLOX, pero AGPL.

    PARCHE LOCAL (Raspberry Pi 5) - ver /opt/scae/PATCHES.md

    Medido en esta Pi, con 2 hilos de ONNX Runtime:

        yolo11n_320 (entrada 320)    82 ms
        yolox-s int8 (entrada 640)  237 ms
        yolo11n     (entrada 640)   342 ms

    Casi tres veces mas rapido que YOLOX, y de ahi que se adopte. **La contra es
    la licencia**: YOLO11 es de Ultralytics, **AGPL-3.0**, frente al Apache-2.0
    de YOLOX. La AGPL se activa con el uso en red, y SCAE es exactamente un
    servicio de red: distribuirlo o darle servicio a terceros obligaria a ofrecer
    el codigo fuente. El operador lo decidio sabiendolo; YOLOX sigue disponible
    cambiando `SCAE_LPR_VEHICLE_MODEL` y `SCAE_LPR_VEHICLE_ARCH`.

    El postprocesado NO es el de YOLOX y confundirlos da cero detecciones en
    silencio:

    * La entrada va **normalizada a 0-1** (YOLOX la toma cruda, 0-255).
    * El letterbox va **centrado**, no pegado arriba a la izquierda, asi que al
      deshacerlo hay que restar los dos desplazamientos.
    * La salida es `[1, 84, N]` y hay que **transponerla** a `(N, 84)`.
    * No hay columna de objectness: las 80 puntuaciones de clase van directas
      desde la columna 4. Multiplicar por una "objectness" inexistente -como en
      YOLOX- destrozaria las confianzas.
    """

    def __init__(
        self,
        model: str = "yolo11n-320-coco.onnx",
        conf_threshold: float = 0.30,
        iou_threshold: float = 0.5,
        max_boxes: int = 6,
        classes: Optional[dict[int, str]] = None,
    ) -> None:
        self._classes = dict(classes) if classes else dict(VEHICLE_CLASSES)
        self.name = f"yolo11:{model}"
        self._model_path = MODELS_DIR / model
        self._conf = conf_threshold
        self._iou = iou_threshold
        self._max_boxes = max_boxes
        self._session = None
        self._input_name = ""
        self._size = 320
        self._lock = threading.Lock()

    def _ensure_session(self):
        if self._session is not None:
            return self._session
        with self._lock:
            if self._session is not None:
                return self._session
            if not self._model_path.is_file():
                raise FileNotFoundError(
                    f"No se encontro el modelo de vehiculos: {self._model_path}"
                )
            import onnxruntime as ort

            opts = ort.SessionOptions()
            # Dos hilos: esta Pi comparte CPU con la plataforma de la tesis.
            opts.intra_op_num_threads = 2
            opts.inter_op_num_threads = 1
            opts.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
            logger.info("Cargando detector de vehiculos %s", self._model_path.name)
            self._session = ort.InferenceSession(
                str(self._model_path), opts, providers=["CPUExecutionProvider"]
            )
            inp = self._session.get_inputs()[0]
            self._input_name = inp.name
            # El tamano de entrada lo dice el propio modelo (320 o 640).
            dim = inp.shape[2]
            self._size = int(dim) if isinstance(dim, int) else 320
            return self._session

    def detect(self, frame: np.ndarray) -> list[BoundingBox]:
        from PIL import Image

        session = self._ensure_session()
        size = self._size
        h, w = frame.shape[:2]
        ratio = min(size / h, size / w)
        nh, nw = max(1, round(h * ratio)), max(1, round(w * ratio))
        top, left = (size - nh) // 2, (size - nw) // 2
        canvas = np.full((size, size, 3), PAD_VALUE, dtype=np.uint8)
        canvas[top:top + nh, left:left + nw] = np.asarray(
            Image.fromarray(frame).resize((nw, nh), Image.BILINEAR)
        )
        # RGB, CHW, normalizado a 0-1.
        blob = np.ascontiguousarray(
            canvas[:, :, ::-1].transpose(2, 0, 1)[None], dtype=np.float32
        ) / 255.0

        pred = np.asarray(session.run(None, {self._input_name: blob})[0][0]).T
        if pred.ndim != 2 or pred.shape[1] < 5:
            return []
        scores = pred[:, 4:]
        cls = scores.argmax(axis=1)
        conf = scores.max(axis=1)
        keep = np.isin(cls, list(self._classes.keys())) & (conf >= self._conf)
        if not keep.any():
            return []

        cx, cy, bw, bh = (pred[keep, i] for i in range(4))
        # Deshacer el letterbox CENTRADO: restar el desplazamiento y dividir.
        x1 = (cx - bw / 2 - left) / ratio
        y1 = (cy - bh / 2 - top) / ratio
        x2 = (cx + bw / 2 - left) / ratio
        y2 = (cy + bh / 2 - top) / ratio
        xyxy = np.stack([x1, y1, x2, y2], axis=1)
        conf, cls = conf[keep], cls[keep]

        boxes: list[BoundingBox] = []
        for idx in _nms(xyxy, conf, self._iou)[: self._max_boxes]:
            bx1 = max(0.0, min(float(w), float(xyxy[idx][0])))
            by1 = max(0.0, min(float(h), float(xyxy[idx][1])))
            bx2 = max(0.0, min(float(w), float(xyxy[idx][2])))
            by2 = max(0.0, min(float(h), float(xyxy[idx][3])))
            if bx2 - bx1 < 2 or by2 - by1 < 2:
                continue
            cid = int(cls[idx])
            boxes.append(BoundingBox(
                bx1, by1, bx2, by2, float(conf[idx]), cid,
                self._classes.get(cid, COCO_NAMES.get(cid, str(cid))),
            ))
        boxes.sort(key=lambda b: (b.x2 - b.x1) * (b.y2 - b.y1), reverse=True)
        return boxes


_detector: Optional[object] = None
_detector_lock = threading.Lock()


def parse_classes(spec: str | None) -> Optional[dict[int, str]]:
    """Convierte "car,truck,train" en el mapa de clases de COCO.

    Un nombre desconocido se ignora en vez de reventar: esto sale de un fichero
    de configuracion que edita una persona.
    """
    if not spec or not spec.strip():
        return None
    by_name = {v: k for k, v in COCO_NAMES.items()}
    out: dict[int, str] = {}
    for token in spec.split(","):
        name = token.strip().lower()
        if name in by_name:
            out[by_name[name]] = name
        elif name.isdigit() and int(name) in COCO_NAMES:
            out[int(name)] = COCO_NAMES[int(name)]
        elif name:
            logger.warning("Clase de vehiculo desconocida, se ignora: %r", name)
    return out or None


# Arquitectura -> clase. El postprocesado de YOLOX y el de YOLO11 NO son
# intercambiables (ver OnnxYolo11VehicleDetector), asi que el modelo se elige
# junto con la arquitectura, nunca por el nombre del fichero.
ARCHITECTURES = {
    "yolox": OnnxVehicleDetector,
    "yolo11": OnnxYolo11VehicleDetector,
}


def get_vehicle_detector(
    model: str = "yolo11n-320-coco.onnx",
    classes: Optional[dict[int, str]] = None,
    conf_threshold: float = 0.30,
    arch: str = "yolo11",
):
    global _detector
    cls = ARCHITECTURES.get(arch)
    if cls is None:
        raise ValueError(
            f"Arquitectura de detector desconocida: {arch!r}. "
            f"Opciones: {', '.join(sorted(ARCHITECTURES))}"
        )
    with _detector_lock:
        if (
            _detector is None
            or not isinstance(_detector, cls)
            or _detector._model_path.name != model
            or (classes is not None and _detector._classes != classes)
        ):
            _detector = cls(
                model, classes=classes, conf_threshold=conf_threshold
            )
        return _detector
