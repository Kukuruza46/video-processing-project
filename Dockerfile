# Базовый образ Python
FROM python:3.10-slim

# Установка системных зависимостей
RUN apt-get update && apt-get install -y \
    libgl1-mesa-glx \
    libglib2.0-0 \
    && apt-get clean

# Установка рабочей директории в контейнере
WORKDIR /app

# Копируем файл с зависимостями и устанавливаем их
COPY requirements.txt requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

# Копируем весь проект в контейнер
COPY . .

# Указываем рабочую директорию для запуска приложения
WORKDIR /app

# Открываем порт 8000 для приложения
EXPOSE 8000

# Команда для запуска приложения
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8000", "--reload"]
