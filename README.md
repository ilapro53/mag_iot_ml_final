# IoT-ферма - зачетная работа

Реально работающий IoT-стенд: эмулятор тепличной фермы публикует показания 4 датчиков в
**MQTT-брокер**, их получает **Home Assistant** (история-графики) и отдельный **Python-визуализатор**
(4 живых графика matplotlib с детекцией аномалий).

Поток данных:

1. Датчик-эмулятор (4 датчика, JSON, топики `farm/ilya/{temperature,humidity,light,co2}`) публикует в брокер.
2. MQTT-брокер Mosquitto (авторизация, порт 1883) раздает сообщения подписчикам.
3. Home Assistant получает данные и строит историю-графики.
4. Python-визуализатор `visualize.py` строит 4 графика с детекцией аномалий.

Датчики фермы: **температура** (°C), **влажность** (%), **освещенность** (lux), **CO₂** (ppm).
Значения реалистичны: суточный цикл день/ночь, **погода** (☀️ ⛅ ☁️ 🌧️), связи между датчиками,
мягкий шум, без скачков (подробнее - раздел "Модель данных и погода").
Сообщения - JSON `{sensor, unit, value, n, tod, weather, ts}`
(`tod` - время суток 0-24 ч для оси графиков, `weather` - текущая погода).

---

## Требования

- **Docker Desktop** (брокер, датчик и Home Assistant поднимаются в контейнерах).
- **uv** - только для запуска визуализатора с хоста (живое окно). Для PNG можно и без него (через Docker).

## Быстрый старт

```powershell
docker compose up -d --build      # первый раз (соберет образ датчика); дальше можно просто up -d
docker compose ps                 # все 3 сервиса должны быть Up
```

Поднимаются: `farm-mosquitto`, `farm-sensor`, `farm-homeassistant`.

---

## Проверка по компонентам

### 1. Брокер работает
```powershell
docker compose logs mosquitto      # ждем 'mosquitto version 2.x running' и 'listen socket on port 1883'

# тест авторизации (в двух терминалах):
docker compose exec mosquitto mosquitto_sub -t test -u mqtt_exp -P pass_mqtt -v
docker compose exec mosquitto mosquitto_pub -t test -m "Hello MQTT!" -u mqtt_exp -P pass_mqtt
# аноним без пароля должен получить отказ (allow_anonymous false):
docker compose exec mosquitto mosquitto_pub -t test -m x
```

### 2. Датчик генерит и публикует данные
```powershell
docker compose logs -f sensor                       # лог публикаций 4 датчиков (Ctrl+C - выход из лога)

# убедиться, что брокер раздает показания фермы:
docker compose exec mosquitto mosquitto_sub -t "farm/ilya/#" -u mqtt_exp -P pass_mqtt -v
```

### 3. Home Assistant получает данные
1. Открыть **http://localhost:8123**, пройти онбординг (создать аккаунт).
2. Настройки, **Устройства и службы**, **Добавить интеграцию**, **MQTT**, ввести вручную:
   - Брокер: `mosquitto`, Порт: `1883`, Логин: `mqtt_exp`, Пароль: `pass_mqtt`.
3. Если 4 сенсора не появились сразу - `docker compose restart homeassistant`.
4. Настройки, Устройства и службы, **Объекты**, поиск "теплиц" - 4 сенсора со значениями.
   Клик по сенсору открывает **график истории**.

### 4. Визуализатор - 4 графика (Python)

**Живое окно (с хоста, через uv):**
```powershell
uv sync                            # один раз: создаст окружение из pyproject.toml / uv.lock
uv run viz/visualize.py            # окно с 4 живыми графиками (подключается к localhost:18883)
```

**Сохранить PNG-артефакт для портала:**
```powershell
# вариант через uv:
uv run viz/visualize.py --save viz/out/graphs.png --duration 60
# вариант через Docker (если на хосте нет Python):
docker compose --profile tools run --rm viz        # результат: viz/out/graphs.png
```

> Ось X - **время суток** (окно прокручивается, новые точки справа, текущая погода в заголовке).
> Опции: `--window 24` (ширина окна по X в модельных часах, по умолчанию 48),
> `--host` / `--port` (если брокер не на `localhost:18883`).

---

## Управление стендом

```powershell
docker compose up -d           # запустить
docker compose down            # остановить (данные истории HA сохраняются)
docker compose down -v         # остановить и стереть тома (история HA и база брокера)
docker compose restart sensor  # перезапустить отдельный сервис
docker compose logs -f <сервис># смотреть логи: mosquitto | sensor | homeassistant
```

## Конфигурация - `.env`

| Переменная | Значение | Назначение |
|------------|----------|------------|
| `MQTT_HOST` | `mosquitto` | хост брокера (имя сервиса внутри Docker; на ВМ - IP брокера) |
| `MQTT_PORT` | `1883` | порт брокера внутри сети Docker |
| `MQTT_HOST_PORT` | `18883` | порт брокера, опубликованный на хост (Windows зарезервировал 1883) |
| `MQTT_USER` / `MQTT_PASSWORD` | `mqtt_exp` / `pass_mqtt` | учетка MQTT |
| `TOPIC_PREFIX` | `farm/ilya` | префикс топиков: `farm/ilya/<датчик>` |
| `HA_HOST_PORT` | `8123` | порт веб-интерфейса Home Assistant |
| `DAY_PERIOD_SEC` | `300` | длина модельных "суток" в секундах (меньше - время идет быстрее) |
| `RATE_MULT` | `1` | множитель частоты публикации датчиков (4 - в 4 раза чаще) |

С хоста: брокер - `localhost:18883`, Home Assistant - `http://localhost:8123`.
После правки `.env` перезапусти датчик: `docker compose up -d --force-recreate sensor`.

### Скорость времени и пресеты

Суточный цикл **стартует от реального времени суток** (запустил стенд в 14:30 - модель начинает
с 14:30), дальше идет со скоростью `DAY_PERIOD_SEC`. Часовой пояс датчика - `TZ` в
`docker-compose.yml` (по умолчанию Москва, UTC+3; поменяй под свой).

Нюанс: **Home Assistant пишет историю по реальному времени**, а Python-визуализатор по
модельному (из поля `tod`). Поэтому при быстрой симуляции в HA сутки "сжимаются". Под это есть
два готовых пресета, копируешь нужный поверх `.env`:

| Пресет | DAY_PERIOD | Для чего |
|--------|-----------|----------|
| `.env.fast` | 9.375 c (сутки за ~9 с) | Python-графики, циклы видно сразу |
| `.env.realtime` | 1800 c (сутки за 30 мин) | Home Assistant, плавный суточный ход в реальном времени |

```powershell
copy .env.realtime .env        # или .env.fast
docker compose up -d --force-recreate sensor
```
(для HA дай поработать около 30-60 мин, в истории появится 1-2 плавных суточных цикла)

## Как считаются аномалии (в visualize.py)

Пороговая логика по "комфортной зоне" теплицы (зеленая полоса на графике):

| Датчик | Норма | Аномалия |
|--------|-------|----------|
| Температура | 18-28 °C | вне диапазона |
| Влажность | 50-80 % | вне диапазона |
| Освещенность | 5 000-50 000 lux | вне диапазона |
| CO₂ | 600-1200 ppm | вне диапазона |

Значение вне зоны рисуется **красной точкой**; `[!]` в заголовке - текущее значение вне нормы;
"(аномалий: N)" - накопительный счетчик с начала запуска. Пороги меняются в словаре `NORMAL`
в [viz/visualize.py](viz/visualize.py).

## Модель данных и погода

Данные не случайны - это связанная модель тепличного климата.

**Суточный цикл.** Каждый датчик плавно ходит между "ночным" и "дневным" значением
(длина суток - `DAY_PERIOD_SEC`), с мягким шумом без скачков. Цикл **стартует от реального
времени суток** в момент запуска.

**Колесо погоды.** 6-позиционный круг, состояние меняется каждые **8 модельных часов**
(вперед 50%, остаться 30%, назад 20%), только на соседнюю позицию. Порядок по кругу:

```
⛅ Облачно, ☁️ Пасмурно, 🌧️ Дождь, ☁️ Пасмурно, ⛅ Облачно, ☀️ Ясно, и снова сначала.
```

Пасмурно и Облачно повторяются по бокам, поэтому к 🌧️ Дождю и ☀️ Ясно погода приходит
постепенно (а к Дождю - всегда через Пасмурно).

| Погода | Температура | Освещенность | Влажность |
|--------|-------------|--------------|-----------|
| ⛅ Облачно с прояснениями | x1.00 | x1.00 | +0 |
| ☀️ Ясно | +15 % | +25 % | -3 (суше) |
| ☁️ Пасмурно | -5 % | -10 % | +5 (влажнее) |
| 🌧️ Дождь | -15 % | x0.39 (полдень примерно как 4 утра при ⛅) | +12 (до аномалии) |

Переходы по температуре и свету плавные (около 2 модельных часов). Влажность при дожде
**подскакивает резко**, а после **спадает медленно** (лишняя влага выходит долго).

**Связи между датчиками:**

- **CO₂ зависит от освещенности**: больше света, активнее фотосинтез, меньше CO₂.
- **температура зависит от влажности**: выше влажность, чуть прохладнее.
- **CO₂ зависит от погоды (ночью)**: чем темнее, тем выше CO₂. Ночью при **🌧️ Дожде** (очень темно) CO₂ **часто** критический (>1200), при **☁️ Пасмурно** **иногда**, при **☀️ Ясно** и **⛅ Облачно** **никогда**. Днем CO₂ всегда в норме.

Текущая погода видна в заголовке `visualize.py` и в каждом MQTT-сообщении (поле `weather`).
Все параметры (уставки, проценты погоды, вероятности) - в начале
[sensor/sensor_emulator.py](sensor/sensor_emulator.py).

### Инъекция аномалии (демонстрация детектора)

Можно вручную "сломать" датчик - он на 5 показаний выдаст значение вне нормы:
```powershell
docker compose exec mosquitto mosquitto_pub -t farm/ilya/control -m temperature -u mqtt_exp -P pass_mqtt
```
Вместо `temperature` - любой датчик (`humidity`, `light`, `co2`), `clear` - сброс.
На графиках (и в Home Assistant) загорятся красные точки.

## Структура проекта

```
.
├── docker-compose.yml              весь стенд (mosquitto, sensor, homeassistant, viz)
├── .env                            активный конфиг (копия одного из пресетов)
├── .env.example                    шаблон конфига
├── .env.fast / .env.realtime       пресеты скорости времени (Python-графики / Home Assistant)
├── pyproject.toml / uv.lock        зависимости визуализатора (paho-mqtt, matplotlib)
├── broker/mosquitto.conf           конфиг брокера
├── sensor/                         датчик-эмулятор (sensor_emulator.py и Dockerfile)
├── homeassistant/                  config/configuration.yaml (5 MQTT-сенсоров), dashboard-teplitsa.yaml
├── scripts/                        установка на Ubuntu-ВМ (01_broker_setup.sh, 02_sensor_setup.sh)
├── viz/                            visualize.py (4 графика), run.ps1, Dockerfile
└── local/                          личные материалы (задание, prev/, транскрипты) - в .gitignore
```

## Развертывание на 3 Ubuntu-ВМ (сетевой мост)

Альтернатива Docker - 3 виртуалки: брокер, датчик, Home Assistant. У всех **сетевой мост**
(не NAT), VPN на хосте выключен (он ломает мост). Репозиторий публичный, поэтому файлы
тянутся прямо с GitHub - копировать вручную не нужно. Нужен `wget` (обычно есть; если нет -
`sudo apt install -y wget`).

Датчик и визуализатор ставятся через **uv** (он сам приносит нужный Python и зависимости,
кэширует - **sudo не нужен**). Только брокеру нужен `sudo` (ставит системную службу Mosquitto).

### 1. Брокер (Mosquitto)

На ВМ-брокере одной вставкой - скачать скрипт и поднять брокер:

```bash
wget -O 01_broker_setup.sh https://raw.githubusercontent.com/ilapro53/mag_iot_ml_final/main/scripts/01_broker_setup.sh
sudo bash 01_broker_setup.sh
```

Скрипт ставит Mosquitto, заводит логин `mqtt_exp` / `pass_mqtt`, порт 1883, `allow_anonymous false`,
сам себя проверяет (pub/sub плюс отказ анониму) и в конце печатает **IP брокера** - запиши его,
он нужен датчику и Home Assistant.

Посмотреть, что происходит на брокере:

```bash
journalctl -u mosquitto -f                                               # служебный лог: подключения, ошибки
mosquitto_sub -h localhost -t 'farm/ilya/#' -u mqtt_exp -P pass_mqtt -v   # сами данные датчиков в реальном времени
```

Первая команда - лог службы (кто подключился, старт/стоп). Вторая - подписчик: показывает
приходящие показания всех 4 датчиков (`#` - все подтопики `farm/ilya/...`, `-v` - выводить имя
топика). Это просмотр, на работу стенда не влияет (Ctrl+C закрывает, данные идут дальше).

### 2. Датчик (эмулятор)

На ВМ-датчике одной вставкой - скачать, настроить и запустить (подставь IP брокера вместо примера):

```bash
mkdir -p ~/farm-sensor && \
wget -O ~/farm-sensor/sensor_emulator.py https://raw.githubusercontent.com/ilapro53/mag_iot_ml_final/main/sensor/sensor_emulator.py && \
wget -O ~/farm-sensor/02_sensor_setup.sh https://raw.githubusercontent.com/ilapro53/mag_iot_ml_final/main/scripts/02_sensor_setup.sh && \
BROKER_IP=192.168.2.205 bash ~/farm-sensor/02_sensor_setup.sh && \
~/farm-sensor/run_sensor.sh
```

При установке скрипт спросит **режим времени**:

- **обычный** - реальное время, сутки = настоящие сутки, для Home Assistant (`DAY_PERIOD_SEC=86400`, `RATE_MULT=0.02`);
- **fast** - ускоренный, сутки за ~9 секунд, пресет `.env.fast`, для Python-визуализатора (`DAY_PERIOD_SEC=9.375`, `RATE_MULT=16`).

Скрипт ставит `uv` (он приносит Python и `paho-mqtt` из PEP 723-метаданных эмулятора), кладет
настройки в `~/farm-sensor/.env`, создает обертку `run_sensor.sh` и запускает датчик (видно
консоль публикаций). sudo не нужен.

**Ответить заранее (без вопроса)** - задать режим переменной `MODE`, напр. строку запуска для fast:

```bash
BROKER_IP=192.168.2.205 MODE=fast bash ~/farm-sensor/02_sensor_setup.sh
```

(`MODE=normal` - обычный режим; можно и точные значения `DAY_PERIOD_SEC=... RATE_MULT=...`.)

Дальше режим меняется правкой `~/farm-sensor/.env` и перезапуском `~/farm-sensor/run_sensor.sh`.
Частоту держи по правилу `RATE_MULT = 1800 / DAY_PERIOD_SEC` (реальное время -> `0.02`, fast -> `16`),
тогда суточная кривая гладкая. Для Home Assistant лучше реальное время или `DAY_PERIOD_SEC=1800`
(ось HA реальная, при fast сутки сильно сжимаются).

### 3. Home Assistant

Отдельная ВМ с Home Assistant OS. Добавить интеграцию **MQTT** (IP брокера, порт 1883,
`mqtt_exp` / `pass_mqtt`), в `configuration.yaml` дописать блок `mqtt:` из
[homeassistant/config/configuration.yaml](homeassistant/config/configuration.yaml) (6 сенсоров
вкл. свет снаружи и погоду, плюс 3 переключателя-актуатора), перезапустить. Готовый дашборд
(гейджи, погода, графики истории) - [homeassistant/dashboard-teplitsa.yaml](homeassistant/dashboard-teplitsa.yaml).

Логику актуаторов (обогрев/проветривание/досветка) задают **автоматизации Home Assistant**
(Настройки -> Автоматизации): триггер по критическому значению сенсора -> включить/выключить
переключатель. Примеры - в разделе "Актуаторы" ниже.

> Внимание: для ВМ Home Assistant не используй "Сохранить состояние" - при возобновлении
> прыгают часы и ломается история. Выключай ВМ штатно или оставляй запущенной.

### 4. Python-визуализатор (опционально, второй способ показа)

`visualize.py` - 4 живых графика matplotlib с подсветкой аномалий, второй способ визуализации
помимо Home Assistant. Ставится на **брокер-ВМ** (там брокер - это localhost, и есть рабочий
стол для окна). Одной вставкой - скачать, настроить и запустить окно:

```bash
sudo apt install -y fonts-symbola && \
mkdir -p ~/farm-viz && \
wget -O ~/farm-viz/visualize.py https://raw.githubusercontent.com/ilapro53/mag_iot_ml_final/main/viz/visualize.py && \
wget -O ~/farm-viz/03_viz_setup.sh https://raw.githubusercontent.com/ilapro53/mag_iot_ml_final/main/scripts/03_viz_setup.sh && \
bash ~/farm-viz/03_viz_setup.sh && \
~/farm-viz/run_viz.sh
```

Скрипт ставит `uv` (он приносит Python, `paho-mqtt` и `matplotlib` из PEP 723-метаданных
визуализатора), кладет обертку `run_viz.sh` (адрес брокера `localhost:1883` зашит) и открывает
окно с 4 графиками. sudo не нужен. Окну нужен **рабочий стол ВМ** - запускай в графической
сессии, не по SSH без X. Шрифт `fonts-symbola` (monochrome-эмодзи погоды в заголовке, их рисует
matplotlib) ставится первой строкой команды выше - это единственный `sudo`-шаг визуализатора.

Полезные флаги (передаются дальше через обертку):

```bash
~/farm-viz/run_viz.sh --window 24                    # окно поуже по времени (часы модельного времени)
~/farm-viz/run_viz.sh --save ~/farm-viz/graphs.png --duration 60   # сохранить PNG без окна (для портала)
```

Если визуализатор на отдельной ВМ (не на брокере) - укажи IP брокера при установке:
`BROKER_IP=192.168.2.205 bash ~/farm-viz/03_viz_setup.sh`.

### Актуаторы (управление из Home Assistant)

Эмулятор кроме датчиков даёт 3 управляемых устройства: **обогрев**, **проветривание**,
**досветка**. Их состоянием управляет Home Assistant: шлёт `ON/OFF` в `farm/ilya/<актуатор>/set`,
эмулятор применяет эффект к модели (обогрев греет, проветривание сушит и сбивает CO2, досветка
поднимает свет внутри) и отвечает состоянием в `farm/ilya/<актуатор>`. В HA это переключатели
`switch.*` (уже в `configuration.yaml`).

Чтобы знать, когда гасить досветку, есть два датчика света: **внутри** (`farm/ilya/light`) и
**снаружи** (`farm/ilya/light_out`). Логику пишут в **Настройки -> Автоматизации** (триггер по
критическому значению -> включить/выключить переключатель). На каждый актуатор - пара
"включить/выключить" с гистерезисом. **Полный комплект из 6 автоматизаций -
в [homeassistant/automations-teplitsa.yaml](homeassistant/automations-teplitsa.yaml)**;
ниже примеры (вставляются в режиме YAML у новой автоматизации):

```yaml
# Обогрев ВКЛ при низкой температуре
alias: Обогрев вкл
mode: single
triggers:
  - trigger: numeric_state
    entity_id: sensor.temperatura_teplitsy
    below: 18
conditions: []
actions:
  - action: switch.turn_on
    target:
      entity_id: switch.obogrev_teplitsy
```

```yaml
# Обогрев ВЫКЛ когда прогрелось (гистерезис: вкл < 18, выкл > 22)
alias: Обогрев выкл
mode: single
triggers:
  - trigger: numeric_state
    entity_id: sensor.temperatura_teplitsy
    above: 22
conditions: []
actions:
  - action: switch.turn_off
    target:
      entity_id: switch.obogrev_teplitsy
```

```yaml
# Досветка ВКЛ: внутри темно И снаружи света не хватает
alias: Досветка вкл
mode: single
triggers:
  - trigger: numeric_state
    entity_id: sensor.osveshchennost_teplitsy
    below: 6000
conditions:
  - condition: numeric_state
    entity_id: sensor.osveshchennost_snaruzhi
    below: 9000
actions:
  - action: switch.turn_on
    target:
      entity_id: switch.dosvetka_teplitsy
```

```yaml
# Досветка ВЫКЛ когда снаружи стало светло
alias: Досветка выкл
mode: single
triggers:
  - trigger: numeric_state
    entity_id: sensor.osveshchennost_snaruzhi
    above: 14000
conditions: []
actions:
  - action: switch.turn_off
    target:
      entity_id: switch.dosvetka_teplitsy
```

**Проветривание** - по аналогии: ВКЛ при `sensor.vlazhnost_teplitsy` выше 80 или
`sensor.co2_teplitsy` выше 1200; ВЫКЛ когда влажность ниже 70 и CO2 ниже 1000. Несколько триггеров
в одной автоматизации срабатывают по "или"; для "и" на выключение используй два `condition`.

> entity_id у тебя могут отличаться (HA транслитерирует имена) - проверь в Инструменты
> разработчика -> Состояния и подставь свои.
