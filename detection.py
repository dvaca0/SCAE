# detection.py
from ultralytics import YOLO
import pandas as pd

# Cargar el modelo YOLOv8 pre-entrenado
model = YOLO('yolov5l.pt')  # Asegúrate de tener el modelo en el directorio o proporciona la ruta correcta

def detectar_vehiculos(frame):
    # Realizar inferencia
    results = model.predict(source=frame)
    detections = results[0].boxes.data.cpu().numpy()

    # Crear DataFrame
    columns = ['xmin', 'ymin', 'xmax', 'ymax', 'confidence', 'class']
    detections_df = pd.DataFrame(detections, columns=columns)

    # Filtrar vehículos (car, truck, bus, motorcycle)
    vehicle_classes = [2, 3, 5, 7]  # Car, Motorcycle, Bus, Truck
    vehiculos_detectados = detections_df[detections_df['class'].isin(vehicle_classes)]
    return vehiculos_detectados

