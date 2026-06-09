#!/usr/bin/env bash
# Настройка Python-визуализатора (matplotlib, 4 живых графика) на Ubuntu-ВМ.
# Обычно ставится на брокер-ВМ: там брокер - это localhost, и есть рабочий стол для окна графиков.
#
# ВАЖНО: рядом с этим скриптом должен лежать visualize.py.
# Запускать БЕЗ sudo (sudo нужен только для apt внутри):
#   chmod +x 03_viz_setup.sh
#   ./03_viz_setup.sh
# Если визуализатор не на брокер-ВМ - задать адрес брокера:
#   BROKER_IP=192.168.2.205 ./03_viz_setup.sh

set -e

# --- параметры ---
BROKER_IP="${BROKER_IP:-localhost}"   # на брокер-ВМ брокер - это localhost
MQTT_PORT="${MQTT_PORT:-1883}"        # на ВМ порт 1883 (18883 был только в Docker на Windows)

WORKDIR="$(cd "$(dirname "$0")" && pwd)"

if [ ! -f "${WORKDIR}/visualize.py" ]; then
    echo "ОШИБКА: рядом нет visualize.py."
    echo "Скопируй его в ${WORKDIR} и запусти скрипт снова."
    exit 1
fi

echo "=== 1. Системные пакеты (python3, venv, pip, tk для окна, шрифты эмодзи) ==="
sudo apt update
sudo apt install -y python3 python3-venv python3-pip python3-tk fonts-noto-color-emoji

echo "=== 2. Виртуальное окружение (paho-mqtt 2.x, matplotlib) ==="
python3 -m venv "${WORKDIR}/.venv"
"${WORKDIR}/.venv/bin/pip" install --upgrade pip
"${WORKDIR}/.venv/bin/pip" install "paho-mqtt>=2,<3" "matplotlib>=3.7"

echo "=== 3. Обертка запуска run_viz.sh (host/port брокера зашиты) ==="
cat > "${WORKDIR}/run_viz.sh" <<EOF
#!/usr/bin/env bash
# Запуск визуализатора. Адрес брокера зашит, доп. флаги передаются дальше (\$@), напр.:
#   ./run_viz.sh --window 24                 окно поуже по времени
#   ./run_viz.sh --save graphs.png --duration 60   сохранить PNG без окна
cd "\$(dirname "\$0")"
exec ./.venv/bin/python visualize.py --host "${BROKER_IP}" --port "${MQTT_PORT}" "\$@"
EOF
chmod +x "${WORKDIR}/run_viz.sh"

echo
echo "==================================================="
echo " Визуализатор готов. Брокер: ${BROKER_IP}:${MQTT_PORT}"
echo " Живое окно с 4 графиками (нужен рабочий стол ВМ, не SSH без X):"
echo "   ${WORKDIR}/run_viz.sh"
echo " Сохранить PNG без окна:"
echo "   ${WORKDIR}/run_viz.sh --save graphs.png --duration 60"
echo "==================================================="
