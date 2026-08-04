# lpr.py
import easyocr

# Inicializar el lector OCR
reader = easyocr.Reader(['en'], gpu=False)

def reconocer_placa(image):
    # Realizar OCR en la imagen
    results = reader.readtext(image)
    for bbox, text, conf in results:
        # Simple heurística para filtrar texto de placas
        if len(text) >= 5 and len(text) <= 10:
            return text.strip().upper()
    return None
