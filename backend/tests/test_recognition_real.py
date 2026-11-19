"""Prueba de regresion del reconocimiento REAL (modelos ONNX).

PARCHE LOCAL (Raspberry Pi 5) - ver /opt/scae/PATCHES.md

El resto de la suite corre con `SCAE_RECOGNITION_BACKEND=stub` para no cargar
modelos, lo cual es correcto: ninguna de esas pruebas es sobre vision. Pero
dejaba el reconocimiento **sin ninguna prueba**, y ahi es donde mas se ha roto
cosas en este despliegue: el orden clase/score del detector, el doble softmax del
OCR, la confianza como media en vez de como minimo.

Estas fixtures son fotos de maqueta con calcomanias de alto contraste, de la
implementacion paralela que el operador recibio (ver
/opt/scae/fixtures/PROCEDENCIA.txt). Nuestro pipeline las lee con confianza
1.000, asi que sirven exactamente para lo que hace falta: si una de estas deja de
leerse, algo se rompio en el reconocimiento.

Se saltan solas si no estan las fixtures o los modelos, para que la suite siga
corriendo en una maquina sin ellos.
"""
import os
from pathlib import Path

import pytest

FIXTURES = Path("/opt/scae/fixtures")
MODELS = Path("/opt/scae/models")

# placa esperada, ancho minimo de la caja en px
CASES = [
    ("caso_000.jpg", "FTC224A", 100),
    ("caso_014.jpg", "GMX905H", 100),
    ("caso_070.jpg", "HKL517B", 50),
]

pytestmark = pytest.mark.skipif(
    not FIXTURES.is_dir() or not (MODELS / "cct_s_v2_global.onnx").is_file(),
    reason="faltan las fixtures de /opt/scae/fixtures o los modelos ONNX",
)


@pytest.fixture(scope="module")
def pipeline():
    """Pipeline ONNX real, al margen del backend `stub` de conftest."""
    os.environ["SCAE_RECOGNITION_BACKEND"] = "onnx"
    from app.core.config import get_settings
    from app.recognition.onnx_backend import build_onnx_pipeline

    get_settings.cache_clear()
    s = get_settings()
    try:
        yield build_onnx_pipeline(
            "yolo-v9-t-640-license-plates-end2end.onnx",
            "cct_s_v2_global.onnx",
            s.detector_min_confidence,
        )
    finally:
        os.environ["SCAE_RECOGNITION_BACKEND"] = "stub"
        get_settings.cache_clear()


@pytest.mark.parametrize("name,expected,min_width", CASES)
def test_reads_a_high_contrast_plate(pipeline, name, expected, min_width):
    from app.recognition.pipeline import decode_image
    from app.recognition.plate import best_reading

    path = FIXTURES / name
    if not path.is_file():
        pytest.skip(f"falta {path}")
    frame = decode_image(path.read_bytes())
    assert frame is not None, "no se pudo decodificar la imagen"

    boxes = pipeline.detector.detect(frame)
    assert boxes, f"{name}: el detector no encontro ninguna placa"
    assert boxes[0].x2 - boxes[0].x1 >= min_width, (
        f"{name}: la caja salio de {boxes[0].x2 - boxes[0].x1:.0f} px de ancho"
    )

    fragments = []
    for box in boxes[:3]:
        fragments.extend(pipeline.reader.read(box.crop(frame)))
    reading = best_reading(fragments)
    assert reading is not None and reading.normalized == expected, (
        f"{name}: se leyo {reading.normalized if reading else None}, "
        f"se esperaba {expected}"
    )
    assert reading.is_valid, f"{name}: la lectura no paso la validacion mexicana"
    # Con una placa de alto contraste la confianza tiene que ser alta. Si esto
    # baja, probablemente se rompio el postprocesado del OCR (ya paso una vez:
    # un doble softmax hundio 0.9999 a 0.0702).
    assert reading.confidence >= 0.80, (
        f"{name}: confianza {reading.confidence:.3f}, demasiado baja para una "
        f"placa de alto contraste"
    )
