#!/usr/bin/env bash
# Настройка MQTT-брокера Mosquitto на Ubuntu-ВМ (iot1.1).
# Переносит Docker-вариант на чистую Ubuntu: логин/пароль mqtt_exp / pass_mqtt, порт 1883.
# Идемпотентен: можно перезапускать для перенастройки одной командой.
#
# Запуск целиком:
#   chmod +x 01_broker_setup.sh
#   sudo sh ./01_broker_setup.sh
# Поэтапно: стань root командой  sudo -i , затем копируй секции по одной.

set -e

# Если файл запущен не от root - перезапустить себя через sudo (нужно для ./01_broker_setup.sh).
# При ручном копировании секций этот блок не используется (там ты заранее делаешь sudo -i).
if [ "$(id -u)" -ne 0 ]; then
    exec sudo -E bash "$0" "$@"
fi

# --- параметры (меняй тут, если нужно перенастроить) ---
# Запись :- подставляет значение по умолчанию, если переменная не задана. Поэтому любая
# секция работает сама по себе, даже если скопировать ее в чистую сессию без этого блока.
MQTT_USER="${MQTT_USER:-mqtt_exp}"
MQTT_PASSWORD="${MQTT_PASSWORD:-pass_mqtt}"
MQTT_PORT="${MQTT_PORT:-1883}"

echo "=== 1. Установка Mosquitto ==="
apt update
apt install -y mosquitto mosquitto-clients

echo "=== 2. Конфиг брокера: /etc/mosquitto/conf.d/farm.conf ==="
# listener без адреса слушает все интерфейсы (сенсор и HA достучатся по IP).
# Persistence и логи уже заданы в дефолтном /etc/mosquitto/mosquitto.conf, тут не дублируем.
cat > /etc/mosquitto/conf.d/farm.conf <<EOF
listener ${MQTT_PORT:-1883}
allow_anonymous false
password_file /etc/mosquitto/passwd
EOF

echo "=== 3. Файл паролей (логин ${MQTT_USER:-mqtt_exp}) ==="
mosquitto_passwd -b -c /etc/mosquitto/passwd "${MQTT_USER:-mqtt_exp}" "${MQTT_PASSWORD:-pass_mqtt}"
chown mosquitto:mosquitto /etc/mosquitto/passwd
chmod 600 /etc/mosquitto/passwd

echo "=== 4. Фаервол (если ufw активен, открываем порт ${MQTT_PORT:-1883}) ==="
if command -v ufw >/dev/null 2>&1 && ufw status 2>/dev/null | grep -q "Status: active"; then
    ufw allow "${MQTT_PORT:-1883}"/tcp
fi

echo "=== 5. Запуск брокера ==="
systemctl enable mosquitto
systemctl restart mosquitto
sleep 1
systemctl status mosquitto --no-pager | head -6

echo "=== 6. Самопроверка: авторизованный pub/sub ==="
# Подписчик в фоне (выйдет после 1 сообщения или через 3 секунды), затем публикуем.
mosquitto_sub -h localhost -t farm/selftest -u "${MQTT_USER:-mqtt_exp}" -P "${MQTT_PASSWORD:-pass_mqtt}" -C 1 -W 3 > /tmp/mqtt_selftest 2>&1 &
SUB_PID=$!
sleep 0.5
mosquitto_pub -h localhost -t farm/selftest -m ok -u "${MQTT_USER:-mqtt_exp}" -P "${MQTT_PASSWORD:-pass_mqtt}"
wait "${SUB_PID}" 2>/dev/null || true
if grep -q "^ok$" /tmp/mqtt_selftest 2>/dev/null; then
    echo "  авторизованный pub/sub: OK"
else
    echo "  авторизованный pub/sub: ОШИБКА (детали в /tmp/mqtt_selftest)"
fi

echo "=== 7. Самопроверка: анонима без пароля не пускает ==="
if mosquitto_pub -h localhost -t farm/selftest -m x >/dev/null 2>&1; then
    echo "  ВНИМАНИЕ: аноним прошел (allow_anonymous false не сработал)"
else
    echo "  анонимный доступ отклонен: OK"
fi

echo
echo "==================================================="
echo " Брокер настроен. IP этой ВМ (адрес для HA и сенсора):"
hostname -I
echo " Логин: ${MQTT_USER:-mqtt_exp}   Пароль: ${MQTT_PASSWORD:-pass_mqtt}   Порт: ${MQTT_PORT:-1883}"
echo "==================================================="
