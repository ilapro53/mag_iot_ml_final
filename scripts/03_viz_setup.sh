#!/usr/bin/env bash
# Настройка Python-визуализатора (matplotlib, 4 графика) на Ubuntu-ВМ через uv.
# uv сам приносит Python и ставит paho-mqtt и matplotlib (метаданные PEP 723 в visualize.py). sudo НЕ нужен.
# Обычно ставится на брокер-ВМ: там брокер - localhost, и есть рабочий стол для окна графиков.
#
# ВАЖНО: рядом с этим скриптом должен лежать visualize.py.
# Запуск:
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

echo "=== 1. Установка uv (если еще нет) ==="
# Проверяем не "файл существует", а "uv реально выводит версию": битый файл
# (пустой после прерванной закачки) шелл молча исполняет как пустой скрипт.
export PATH="${HOME}/.local/bin:${PATH}"
if [ -z "$(uv --version 2>/dev/null)" ]; then
    rm -f "${HOME}/.local/bin/uv" "${HOME}/.local/bin/uvx"
    wget -qO- https://astral.sh/uv/install.sh | sh
fi
uv --version

echo "=== 2. Обертка запуска run_viz.sh (uv run, host/port брокера зашиты) ==="
cat > "${WORKDIR}/run_viz.sh" <<EOF
#!/usr/bin/env bash
# Запуск визуализатора через uv (paho-mqtt и matplotlib из PEP 723 в visualize.py).
# Адрес брокера зашит, доп. флаги передаются дальше (\$@), напр.:
#   ./run_viz.sh --window 24                       окно поуже по времени
#   ./run_viz.sh --save graphs.png --duration 60   сохранить PNG без окна
cd "\$(dirname "\$0")"
export PATH="\${HOME}/.local/bin:\${PATH}"
exec uv run visualize.py --host "${BROKER_IP}" --port "${MQTT_PORT}" "\$@"
EOF
chmod +x "${WORKDIR}/run_viz.sh"

echo
echo "==================================================="
echo " Визуализатор готов (через uv, без venv и sudo). Брокер: ${BROKER_IP}:${MQTT_PORT}"
echo " Живое окно с 4 графиками (нужен рабочий стол ВМ, не SSH без X):"
echo "   ${WORKDIR}/run_viz.sh"
echo " Сохранить PNG без окна:"
echo "   ${WORKDIR}/run_viz.sh --save graphs.png --duration 60"
echo " Если окно не открывается (FigureCanvasAgg is non-interactive) - нет tkinter: sudo apt install -y python3-tk"
echo " Для эмодзи погоды в заголовке (опц., monochrome, для matplotlib): sudo apt install -y fonts-symbola"
echo "==================================================="
