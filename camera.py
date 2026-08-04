# camera.py
import cv2

def obtener_feed_video(ruta_video):
    cap = cv2.VideoCapture(ruta_video)
    while cap.isOpened():
        ret, frame = cap.read()
        if ret:
            yield frame
        else:
            break
    cap.release()
