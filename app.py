import os
import sqlite3
import torch
import cv2
import logging
from fastapi import FastAPI, File, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, StreamingResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
import shutil
import time
import warnings
import asyncio
from datetime import datetime

warnings.filterwarnings("ignore", category=FutureWarning)

logging.basicConfig(level=logging.INFO)

CURRENT_VIDEO_PATH = None
websocket_clients = []  # WebSocket-соединения
object_tracker = {}  # {id объекта: координаты bounding box}


def create_db():
    conn = sqlite3.connect('video_events.db')
    cursor = conn.cursor()
    cursor.execute('''CREATE TABLE IF NOT EXISTS video_events (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        video_name TEXT,
        timestamp INTEGER,
        people_count INTEGER
    )''')
    conn.commit()
    conn.close()


def create_directories():
    folders = ["static", "uploaded_videos", "processed_videos", "templates"]
    for folder in folders:
        if not os.path.exists(folder):
            os.makedirs(folder)
            logging.info(f"Создана директория: {folder}")


create_directories()

app = FastAPI()

app.mount("/static", StaticFiles(directory="static"), name="static")

def generate_frames(video_path: str):
    logging.info(f"Начало обработки видео: {video_path}")

    # Загружаем YOLOv5 модель
    model = torch.hub.load('ultralytics/yolov5', 'yolov5s')
    cap = cv2.VideoCapture(video_path)

    if not cap.isOpened():
        logging.error(f"Не удалось открыть видео: {video_path}")
        return None

    try:
        while cap.isOpened():
            ret, frame = cap.read()
            if not ret:
                break

            # YOLO для предсказания объектов
            results = model(frame)
            detections = results.xyxy[0].cpu().numpy()

            # отслеживание объектов
            current_objects = {}
            people_count = 0
            for *xyxy, conf, cls in detections:
                if int(cls) == 0:  # Только люди
                    people_count += 1
                    x1, y1, x2, y2 = map(int, xyxy)

                    # генерируем уникальный ID объекта на основе его координат
                    object_id = f"{x1}-{y1}-{x2}-{y2}"
                    current_objects[object_id] = (x1, y1, x2, y2)

                    # проверяем, новый ли это объект
                    if object_id not in object_tracker:
                        asyncio.run(send_object_event_to_websockets(f"Объект {object_id} появился в кадре"))

                    # отрисовка рамки и метки
                    cv2.rectangle(frame, (x1, y1), (x2, y2), (0, 255, 0), 2)
                    cv2.putText(frame, f"Person {conf:.2f}", (x1, y1 - 10), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 255, 0), 2)

            # проверяем, какие объекты исчезли
            for obj_id in list(object_tracker.keys()):
                if obj_id not in current_objects:
                    asyncio.run(send_object_event_to_websockets(f"Объект {obj_id} пропал из кадра"))
                    del object_tracker[obj_id]

            # обновляем список активных объектов
            object_tracker.clear()
            object_tracker.update(current_objects)

            # асинхронно отправляем данные через WebSocket (количество людей)
            asyncio.run(send_people_count_to_websockets(people_count))

            # кодируем кадр в JPEG и возвращаем его
            _, buffer = cv2.imencode('.jpg', frame)
            frame = buffer.tobytes()

            yield (b'--frame\r\n'
                   b'Content-Type: image/jpeg\r\n\r\n' + frame + b'\r\n')
            time.sleep(0.03)  # задержка для реального времени

    except Exception as e:
        logging.error(f"Ошибка при обработке видео: {e}")
    finally:
        cap.release()
        logging.info("Обработка видео завершена.")

# функция для отправки данных о количестве людей
async def send_people_count_to_websockets(people_count: int):
    timestamp = datetime.now().strftime("%H:%M:%S")
    for websocket in websocket_clients:
        try:
            await websocket.send_json({"people_count": people_count, "timestamp": timestamp})
        except Exception as e:
            logging.error(f"Ошибка отправки через WebSocket: {e}")

# функция для отправки событий объектов
async def send_object_event_to_websockets(event: str):
    for websocket in websocket_clients:
        try:
            await websocket.send_json({"event": event})
        except Exception as e:
            logging.error(f"Ошибка отправки события объекта через WebSocket: {e}")

# эндпоинт для WebSocket соединения
@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()
    websocket_clients.append(websocket)
    logging.info("Новое WebSocket-соединение установлено.")
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        logging.info("WebSocket-соединение закрыто.")
        websocket_clients.remove(websocket)

# эндпоинт для загрузки видео
@app.post("/upload")
async def upload_video(file: UploadFile = File(...)):
    global CURRENT_VIDEO_PATH

    logging.info(f"Загрузка видео: {file.filename}")
    video_path = f"uploaded_videos/{file.filename}"
    with open(video_path, "wb") as f:
        shutil.copyfileobj(file.file, f)

    CURRENT_VIDEO_PATH = video_path  # устанавливаем путь для потоковой передачи
    logging.info(f"Видео {file.filename} загружено.")
    return {"message": "Видео успешно загружено и готово к обработке."}

# эндпоинт для потоковой передачи видео
@app.get("/video_stream")
async def video_stream():
    if not CURRENT_VIDEO_PATH:
        return JSONResponse(content={"error": "Видео не загружено."}, status_code=400)

    return StreamingResponse(generate_frames(CURRENT_VIDEO_PATH), media_type="multipart/x-mixed-replace; boundary=frame")

# главная страница с HTML формой
@app.get("/", response_class=HTMLResponse)
async def read_root():
    with open("templates/index.html", "r", encoding="utf-8") as f:
        return f.read()

# точка входа
if __name__ == "__main__":
    create_db()  # бд
    logging.info("Все подготовительные шаги завершены. Запуск сервера...")
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
