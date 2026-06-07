# Образ датчика-эмулятора. На Ubuntu-ВМ тот же скрипт запускается напрямую:
#   sudo apt install python3-pip -y && pip install paho-mqtt && python3 sensor_emulator.py
FROM python:3.12-slim

# PYTHONUNBUFFERED - логи сразу в `docker compose logs` (не буферизуются)
# PYTHONIOENCODING - корректный вывод символов °C/% в логах
ENV PYTHONUNBUFFERED=1 \
    PYTHONIOENCODING=utf-8

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY sensor_emulator.py .

CMD ["python", "sensor_emulator.py"]
