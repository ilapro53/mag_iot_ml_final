# Образ визуализатора (headless-режим: строит PNG с 4 графиками).
# Живое окно matplotlib запускается с ХОСТА, а не отсюда (контейнеру негде рисовать окно).
FROM python:3.12-slim
ENV PYTHONUNBUFFERED=1 \
    PYTHONIOENCODING=utf-8

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY visualize.py .

ENTRYPOINT ["python", "visualize.py"]
