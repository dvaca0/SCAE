"""Recognition pipeline: detection and OCR behind explicit protocols.

The prototype imported `ultralytics` and `easyocr` at module scope, which
meant importing the web app loaded 100 MB of weights, and the models could
not be swapped or stubbed. Here:

  * `VehicleDetector` and `PlateReader` are protocols, so a better model
    (a dedicated plate detector, a cloud LPR, a Jetson-side service) is a
    new class, not a rewrite.
  * Models load lazily on first use and are cached per process.
  * A `stub` backend lets the platform run — and be developed against — on
    a machine without the CV stack.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Optional, Protocol, runtime_checkable

import numpy as np

from app.core.config import get_settings
from app.recognition.plate import PlateReading, best_reading

logger = logging.getLogger(__name__)

# COCO classes that count as a vehicle: car, motorcycle, bus, truck.
VEHICLE_CLASSES = {2: "car", 3: "motorcycle", 5: "bus", 7: "truck"}


@dataclass
class BoundingBox:
    x1: float
    y1: float
    x2: float
    y2: float
    confidence: float
    class_id: int
    class_name: str

    def crop(self, frame: np.ndarray) -> np.ndarray:
        h, w = frame.shape[:2]
        x1 = max(0, int(self.x1)); y1 = max(0, int(self.y1))
        x2 = min(w, int(self.x2)); y2 = min(h, int(self.y2))
        if x2 <= x1 or y2 <= y1:
            return frame
        return frame[y1:y2, x1:x2]


@dataclass
class RecognitionResult:
    """Everything one frame produced. Persisted as Detection rows."""

    reading: PlateReading
    boxes: list[BoundingBox] = field(default_factory=list)
    detector_name: str = ""
    ocr_name: str = ""
    elapsed_ms: int = 0

    @property
    def vehicle_found(self) -> bool:
        return bool(self.boxes)


@runtime_checkable
class VehicleDetector(Protocol):
    name: str

    def detect(self, frame: np.ndarray) -> list[BoundingBox]: ...


@runtime_checkable
class PlateReader(Protocol):
    name: str

    def read(self, image: np.ndarray) -> list[tuple[str, float]]: ...


# --------------------------------------------------------------------------
# Real implementations
# --------------------------------------------------------------------------
class YoloVehicleDetector:
    """YOLO via ultralytics. Weights load on first detect() call."""

    def __init__(self, model_path: str):
        self.name = f"yolo:{model_path}"
        self._model_path = model_path
        self._model = None

    def _ensure_model(self):
        if self._model is None:
            from ultralytics import YOLO  # imported late on purpose

            logger.info("Cargando modelo de detección %s", self._model_path)
            self._model = YOLO(self._model_path)
        return self._model

    def detect(self, frame: np.ndarray) -> list[BoundingBox]:
        model = self._ensure_model()
        results = model.predict(source=frame, verbose=False)
        raw = results[0].boxes.data.cpu().numpy()
        boxes = []
        for x1, y1, x2, y2, conf, cls in raw:
            cls_id = int(cls)
            if cls_id not in VEHICLE_CLASSES:
                continue
            boxes.append(BoundingBox(float(x1), float(y1), float(x2), float(y2),
                                     float(conf), cls_id, VEHICLE_CLASSES[cls_id]))
        # Largest vehicle first: at a lane, the nearest car is the subject.
        boxes.sort(key=lambda b: (b.x2 - b.x1) * (b.y2 - b.y1), reverse=True)
        return boxes


class EasyOcrPlateReader:
    """EasyOCR. Returns every text box with its confidence — the caller,
    not the OCR, decides which one looks like a plate."""

    def __init__(self, languages: list[str], use_gpu: bool = False):
        self.name = "easyocr"
        self._languages = languages
        self._use_gpu = use_gpu
        self._reader = None

    def _ensure_reader(self):
        if self._reader is None:
            import easyocr  # imported late on purpose

            logger.info("Cargando OCR EasyOCR (%s)", ",".join(self._languages))
            self._reader = easyocr.Reader(self._languages, gpu=self._use_gpu)
        return self._reader

    def read(self, image: np.ndarray) -> list[tuple[str, float]]:
        reader = self._ensure_reader()
        out = []
        for _bbox, text, conf in reader.readtext(image):
            out.append((str(text), float(conf)))
        return out


# --------------------------------------------------------------------------
# Stubs — let the platform run without the CV stack
# --------------------------------------------------------------------------
class StubVehicleDetector:
    name = "stub-detector"

    def detect(self, frame: np.ndarray) -> list[BoundingBox]:
        h, w = frame.shape[:2]
        return [BoundingBox(0, 0, float(w), float(h), 0.99, 2, "car")]


class StubPlateReader:
    """Returns nothing; the caller falls back to manual plate entry."""

    name = "stub-ocr"

    def read(self, image: np.ndarray) -> list[tuple[str, float]]:
        return []


# --------------------------------------------------------------------------
# Pipeline
# --------------------------------------------------------------------------
class RecognitionPipeline:
    """detect -> crop -> OCR -> normalize.

    Each stage is separately replaceable, and the plate interpretation is
    delegated to `recognition.plate`, which knows about Mexican layouts.
    """

    def __init__(self, detector: VehicleDetector, reader: PlateReader):
        self.detector = detector
        self.reader = reader

    def process(self, frame: np.ndarray) -> RecognitionResult:
        started = time.perf_counter()
        boxes = self.detector.detect(frame)

        fragments: list[tuple[str, float]] = []
        if boxes:
            # Read the largest vehicle first; stop as soon as a valid plate
            # appears so a queue of cars does not multiply OCR cost.
            for box in boxes[:3]:
                fragments.extend(self.reader.read(box.crop(frame)))
                if best_reading(fragments).is_valid:
                    break
        else:
            # No vehicle detected: try the whole frame rather than give up.
            # A tight plate crop from a dedicated LPR camera has no "car" in it.
            fragments.extend(self.reader.read(frame))

        reading = best_reading(fragments)
        elapsed = int((time.perf_counter() - started) * 1000)
        return RecognitionResult(
            reading=reading, boxes=boxes,
            detector_name=self.detector.name, ocr_name=self.reader.name,
            elapsed_ms=elapsed,
        )


@lru_cache(maxsize=1)
def get_pipeline() -> RecognitionPipeline:
    """Process-wide pipeline, built from settings."""
    settings = get_settings()
    if settings.recognition_backend == "stub":
        return RecognitionPipeline(StubVehicleDetector(), StubPlateReader())
    if settings.recognition_backend == "onnx":
        # PARCHE LOCAL (Raspberry Pi 5 / aarch64) - ver /opt/scae/PATCHES.md
        # Detector de placas YOLOv9-t + OCR CCT, ambos ONNX Runtime. Es la via
        # viable en esta plataforma: opencv-python no tiene rueda cp313+aarch64
        # en ninguna version, y easyocr depende de ella y de torch (461 MB).
        # Se importa aqui, tarde, para no cargar onnxruntime cuando no se usa.
        from app.recognition.onnx_backend import build_onnx_pipeline

        return build_onnx_pipeline(
            settings.detector_model, settings.ocr_model,
            settings.detector_min_confidence,
        )
    return RecognitionPipeline(
        YoloVehicleDetector(settings.detector_model),
        EasyOcrPlateReader(settings.ocr_languages, settings.ocr_use_gpu),
    )


def decode_image(data: bytes) -> Optional[np.ndarray]:
    """Decode uploaded bytes into a BGR frame, or None if unreadable.

    PARCHE LOCAL (Raspberry Pi 5 / aarch64 / Python 3.13) — ver
    /opt/scae/PATCHES.md. El original usaba cv2.imdecode, pero opencv-python no
    publica rueda cp313+aarch64 en ninguna version. Pillow hace el mismo
    trabajo sin dependencias nativas que haya que compilar. Se devuelve BGR
    para conservar exactamente el contrato de cv2.imdecode.
    """
    import io

    from PIL import Image, UnidentifiedImageError

    try:
        with Image.open(io.BytesIO(data)) as image:
            image.load()
            frame = np.asarray(image.convert("RGB"))
    except (UnidentifiedImageError, OSError, ValueError):
        return None
    if frame.size == 0:
        return None
    return frame[:, :, ::-1].copy()  # RGB -> BGR
