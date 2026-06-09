#!/usr/bin/env bash
# Настройка датчика-эмулятора на Ubuntu-ВМ (iot2.1) через uv.
# uv сам приносит Python и ставит paho-mqtt (метаданные PEP 723 в sensor_emulator.py). sudo НЕ нужен.
#
# ВАЖНО: рядом с этим скриптом должен лежать sensor_emulator.py (скопируй его на ВМ).
# Запуск:
#   chmod +x 02_sensor_setup.sh
#   ./02_sensor_setup.sh
# Режим времени спросит интерактивно; чтобы не спрашивал - MODE=fast / MODE=normal
# или явные DAY_PERIOD_SEC/RATE_MULT. Адрес брокера: BROKER_IP=192.168.2.205 ./02_sensor_setup.sh

set -e

# --- параметры (идут в .env при первом создании) ---
BROKER_IP="${BROKER_IP:-192.168.2.205}"
MQTT_PORT="${MQTT_PORT:-1883}"
MQTT_USER="${MQTT_USER:-mqtt_exp}"
MQTT_PASSWORD="${MQTT_PASSWORD:-pass_mqtt}"
TOPIC_PREFIX="${TOPIC_PREFIX:-farm/ilya}"
# DAY_PERIOD_SEC и RATE_MULT задаются ниже - через режим (normal/fast) или явно.

WORKDIR="$(cd "$(dirname "$0")" && pwd)"

if [ ! -f "${WORKDIR}/sensor_emulator.py" ]; then
    echo "ОШИБКА: рядом нет sensor_emulator.py."
    echo "Скопируй его в ${WORKDIR} и запусти скрипт снова."
    exit 1
fi

# --- выбор режима времени (спрашиваем при КАЖДОЙ установке) ---
# Приоритет: явные DAY_PERIOD_SEC/RATE_MULT > MODE=fast|normal > вопрос в терминале > обычный.
if [ -z "${DAY_PERIOD_SEC:-}" ] && [ -z "${RATE_MULT:-}" ] && [ -z "${MODE:-}" ] && [ -t 0 ]; then
    echo "Какой режим времени записать в .env?"
    echo "  1) обычный - реальное время, сутки = настоящие сутки (для Home Assistant)"
    echo "  2) fast    - ускоренный, сутки за ~38 секунд (для Python-визуализатора)"
    printf "Номер [1]: "
    read -r _ans
    case "$_ans" in
        2|fast|f|F) MODE=fast ;;
        *) MODE=normal ;;
    esac
fi
case "${MODE:-normal}" in
    fast) DAY_PERIOD_SEC="${DAY_PERIOD_SEC:-37.5}";  RATE_MULT="${RATE_MULT:-4}" ;;
    *)    DAY_PERIOD_SEC="${DAY_PERIOD_SEC:-86400}"; RATE_MULT="${RATE_MULT:-0.02}" ;;
esac
echo "  режим: ${MODE:-normal} (DAY_PERIOD_SEC=${DAY_PERIOD_SEC}, RATE_MULT=${RATE_MULT})"

echo "=== 1. Установка uv (если еще нет) ==="
if ! command -v uv >/dev/null 2>&1 && [ ! -x "${HOME}/.local/bin/uv" ]; then
    wget -qO- https://astral.sh/uv/install.sh | sh
fi
export PATH="${HOME}/.local/bin:${PATH}"
uv --version

echo "=== 2. Конфиг .env (перезаписывается при каждой установке) ==="
# .env - единый источник настроек. Между установками режим можно менять и тут вручную,
# затем перезапуск ./run_sensor.sh. Но повторный запуск этого скрипта перезапишет .env.
cat > "${WORKDIR}/.env" <<EOF
# Конфиг датчика-эмулятора. Меняй значения и перезапускай ./run_sensor.sh.
MQTT_HOST=${BROKER_IP}
MQTT_PORT=${MQTT_PORT}
MQTT_USER=${MQTT_USER}
MQTT_PASSWORD=${MQTT_PASSWORD}
TOPIC_PREFIX=${TOPIC_PREFIX}
# Длина модельных суток в секундах: 86400 = реальное время (24 ч), 37.5 = сутки за ~38 секунд (fast).
DAY_PERIOD_SEC=${DAY_PERIOD_SEC}
# Частота публикаций: реальное время -> 0.02 (гладко по правилу 1800/DAY_PERIOD), fast -> 4.
RATE_MULT=${RATE_MULT}
TZ=Europe/Moscow
EOF
echo "  записан ${WORKDIR}/.env (режим ${MODE:-normal})"

echo "=== 3. Обертка запуска run_sensor.sh (uv run, читает .env) ==="
cat > "${WORKDIR}/run_sensor.sh" <<'EOF'
#!/usr/bin/env bash
# Запуск датчика через uv (paho-mqtt берется из PEP 723 в sensor_emulator.py).
# Настройки - из .env рядом. Меняешь .env, перезапускаешь - новый режим применился.
cd "$(dirname "$0")"
export PATH="${HOME}/.local/bin:${PATH}"
set -a
. ./.env
set +a
exec uv run sensor_emulator.py
EOF
chmod +x "${WORKDIR}/run_sensor.sh"

echo "=== 4. Проверка связи с брокером (адрес из .env) ==="
set -a
. "${WORKDIR}/.env"
set +a
if timeout 3 bash -c "cat < /dev/null > /dev/tcp/${MQTT_HOST}/${MQTT_PORT}" 2>/dev/null; then
    echo "  порт брокера ${MQTT_HOST}:${MQTT_PORT} доступен: OK"
else
    echo "  ВНИМАНИЕ: брокер ${MQTT_HOST}:${MQTT_PORT} недоступен."
    echo "  Проверь: сетевой мост на обеих ВМ, VPN выключен, брокер запущен, ufw открыл порт."
fi

echo
echo "==================================================="
echo " Датчик готов (через uv, без venv и sudo). Настройки в файле .env (там же)."
echo " Брокер: ${MQTT_HOST}:${MQTT_PORT}, топики ${TOPIC_PREFIX}/<датчик>"
echo " Режим времени: DAY_PERIOD_SEC=${DAY_PERIOD_SEC} (86400 = реальное, 37.5 = fast)."
echo " Запуск (показывает консоль публикаций):"
echo "   ./run_sensor.sh"
echo "==================================================="
