#!/usr/bin/env bash
# Настройка датчика-эмулятора на Ubuntu-ВМ (iot2.1).
# Ставит Python и paho-mqtt 2.x в venv, кладет конфиг в .env, готовит запуск sensor_emulator.py.
#
# ВАЖНО: рядом с этим скриптом должен лежать sensor_emulator.py (скопируй его на ВМ).
# Запускать БЕЗ sudo (sudo нужен только для apt внутри, чтобы venv остался твоим):
#   chmod +x 02_sensor_setup.sh
#   ./02_sensor_setup.sh
# При создании .env скрипт спросит режим времени (обычный/fast). Чтобы не спрашивал:
#   MODE=fast ./02_sensor_setup.sh        (или MODE=normal)
#   DAY_PERIOD_SEC=37.5 RATE_MULT=4 ./02_sensor_setup.sh   (явные значения)
# Адрес брокера: BROKER_IP=192.168.2.205 ./02_sensor_setup.sh

set -e

# --- параметры (идут в .env при первом создании) ---
BROKER_IP="${BROKER_IP:-192.168.2.205}"
MQTT_PORT="${MQTT_PORT:-1883}"
MQTT_USER="${MQTT_USER:-mqtt_exp}"
MQTT_PASSWORD="${MQTT_PASSWORD:-pass_mqtt}"
TOPIC_PREFIX="${TOPIC_PREFIX:-farm/ilya}"
# DAY_PERIOD_SEC и RATE_MULT задаются ниже - через режим (normal/fast) или явно.

# Папка проекта = где лежит этот скрипт (там же ждем sensor_emulator.py).
WORKDIR="$(cd "$(dirname "$0")" && pwd)"

if [ ! -f "${WORKDIR}/sensor_emulator.py" ]; then
    echo "ОШИБКА: рядом нет sensor_emulator.py."
    echo "Скопируй его в ${WORKDIR} и запусти скрипт снова."
    exit 1
fi

# --- выбор режима времени (только если .env еще нет) ---
# Приоритет: явные DAY_PERIOD_SEC/RATE_MULT > MODE=fast|normal > вопрос в терминале > обычный.
# normal = реальное время (сутки = настоящие сутки), для Home Assistant.
# fast   = сутки за ~38 секунд (пресет .env.fast), для Python-визуализатора.
if [ ! -f "${WORKDIR}/.env" ]; then
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
fi

echo "=== 1. Системные пакеты (python3, venv, pip) ==="
sudo apt update
sudo apt install -y python3 python3-venv python3-pip

echo "=== 2. Виртуальное окружение и paho-mqtt 2.x ==="
python3 -m venv "${WORKDIR}/.venv"
"${WORKDIR}/.venv/bin/pip" install --upgrade pip
"${WORKDIR}/.venv/bin/pip" install "paho-mqtt>=2,<3"

echo "=== 3. Конфиг .env (создается, только если его еще нет) ==="
# .env - единый источник настроек, как в Docker-варианте. Правь его и перезапускай ./run_sensor.sh.
if [ ! -f "${WORKDIR}/.env" ]; then
    cat > "${WORKDIR}/.env" <<EOF
# Конфиг датчика-эмулятора. Меняй значения и перезапускай ./run_sensor.sh.
MQTT_HOST=${BROKER_IP}
MQTT_PORT=${MQTT_PORT}
MQTT_USER=${MQTT_USER}
MQTT_PASSWORD=${MQTT_PASSWORD}
TOPIC_PREFIX=${TOPIC_PREFIX}
# Длина модельных суток в секундах: 86400 = реальное время (24 ч), 37.5 = сутки за ~38 секунд (fast).
DAY_PERIOD_SEC=${DAY_PERIOD_SEC}
# Частота: для гладкой кривой держим RATE_MULT = 1800 / DAY_PERIOD_SEC (реальное время -> 0.02, fast -> 4).
RATE_MULT=${RATE_MULT}
TZ=Europe/Moscow
EOF
    echo "  создан ${WORKDIR}/.env"
else
    echo "  .env уже есть, не трогаю (правь вручную при необходимости)"
fi

echo "=== 4. Обертка запуска run_sensor.sh (читает .env) ==="
cat > "${WORKDIR}/run_sensor.sh" <<'EOF'
#!/usr/bin/env bash
# Запуск датчика-эмулятора. Все настройки берутся из .env рядом.
# Меняешь .env, перезапускаешь этот скрипт - новый режим применился.
cd "$(dirname "$0")"
set -a
. ./.env
set +a
exec ./.venv/bin/python sensor_emulator.py
EOF
chmod +x "${WORKDIR}/run_sensor.sh"

echo "=== 5. Проверка связи с брокером (адрес из .env) ==="
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
echo " Датчик готов. Настройки в файле .env (там же)."
echo " Брокер: ${MQTT_HOST}:${MQTT_PORT}, топики ${TOPIC_PREFIX}/<датчик>"
echo " Режим времени: DAY_PERIOD_SEC=${DAY_PERIOD_SEC} (86400 = реальное, 37.5 = fast)."
echo " Запуск (показывает консоль публикаций):"
echo "   ./run_sensor.sh"
echo "==================================================="
