"""
Эмулятор тепличной фермы - 4 датчика и ПОГОДА, публикация в MQTT-брокер.

"Вариант Б": 4 независимых publisher'а, каждый в свой топик и со своим интервалом.
Данные реалистичны и СВЯЗАНЫ между собой:
  - суточный цикл день/ночь (мягкий шум, без скачков);
  - ПОГОДА меняется каждые 8 модельных часов, ходит по кругу
        Облачно, Пасмурно, Дождь, Пасмурно, Облачно, Ясно (замыкается),
    шаг только на соседнее состояние: вперед 50%, остаться 30%, назад 20%.
    Влияет на температуру и освещенность (переходы плавные);
  - CO2 зависит от освещенности (больше света - меньше CO2, фотосинтез);
  - температура зависит от влажности (выше влажность - чуть прохладнее).
Можно "вколоть" аномалию на лету: имя датчика в топик farm/ilya/control
(например: mosquitto_pub -t farm/ilya/control -m temperature -u mqtt_exp -P pass_mqtt).

Конфигурация из окружения (.env): MQTT_HOST, MQTT_PORT, MQTT_USER, MQTT_PASSWORD,
TOPIC_PREFIX, RUN_DURATION_SEC, DAY_PERIOD_SEC, RATE_MULT.
"""
import json
import math
import os
import random
import sys
import threading
import time
from dataclasses import dataclass

from paho.mqtt import client as mqtt_client

# ---------- конфигурация ----------
MQTT_HOST = os.getenv("MQTT_HOST", "localhost")
MQTT_PORT = int(os.getenv("MQTT_PORT", "1883"))
MQTT_USER = os.getenv("MQTT_USER", "mqtt_exp")
MQTT_PASSWORD = os.getenv("MQTT_PASSWORD", "pass_mqtt")
TOPIC_PREFIX = os.getenv("TOPIC_PREFIX", "farm/ilya")
RUN_DURATION_SEC = float(os.getenv("RUN_DURATION_SEC", "0"))    # 0 = бесконечно
DAY_PERIOD_SEC = float(os.getenv("DAY_PERIOD_SEC", "300"))      # длина "суток" в секундах
RATE_MULT = float(os.getenv("RATE_MULT", "1"))                  # во сколько раз чаще публиковать
STUDENT_TAG = TOPIC_PREFIX.rstrip("/").split("/")[-1] or "ilya"

QOS = 1
N_FORCE = 5             # сколько показаний подряд гнать аномалию после инъекции
WEATHER_PERIOD_H = 8.0  # погода меняется каждые 8 модельных часов
WEATHER_RAMP_H = 2.0    # плавный переход погоды за 2 модельных часа
P_FORWARD = 0.50        # вероятность шага ВПЕРЕД по кругу погоды
P_STAY = 0.30           # вероятность ОСТАТЬСЯ в текущей погоде
# назад = 1 - P_FORWARD - P_STAY = 0.20
HUM_RISE_TAU_H = 0.8    # влажность при дожде поднимается БЫСТРО (постоянная времени, мод.ч)
HUM_FALL_TAU_H = 6.0    # ... и опускается МЕДЛЕННО после дождя


# ---------- погода ----------
@dataclass
class Weather:
    name: str
    temp_mult: float    # множитель к температуре
    light_mult: float   # множитель к освещенности
    hum_add: float = 0.0  # добавка к влажности, % (дождь поднимает к аномалии)


# Круг погоды (вперед), 6 позиций: Облачно, Пасмурно, Дождь, Пасмурно, Облачно, Ясно (замыкается).
# Пасмурно и Облачно повторяются по бокам - к Дождю и Ясно погода приходит постепенно.
WEATHER = [
    Weather("⛅ Облачно с прояснениями", 1.00, 1.00, 0.0),   # 0
    Weather("☁️ Пасмурно", 0.95, 0.90, 5.0),   # 1  темп -5%, свет -10%, влажнее
    Weather("🌧️ Дождь", 0.85, 0.39, 12.0),   # 2  темп -15%, свет x0.39 (полдень примерно как 4 утра при облачно), влажность к аномалии
    Weather("☁️ Пасмурно", 0.95, 0.90, 5.0),   # 3  (повтор Пасмурно)
    Weather("⛅ Облачно с прояснениями", 1.00, 1.00, 0.0),   # 4  (повтор Облачно)
    Weather("☀️ Ясно", 1.15, 1.25, -3.0),   # 5  темп +15%, свет +25%, суше
]
# prev/cur - для плавного перехода между состояниями; ts - момент последней смены
weather_state = {"prev": 0, "cur": 0, "ts": time.time()}
weather_lock = threading.Lock()


# ---------- датчики ----------
@dataclass
class SensorConfig:
    name: str
    unit: str
    night: float    # значение "ночью"
    day: float      # значение "в полдень"
    sigma: float    # сила мягкого шума
    lo: float       # нижняя физическая граница (clamp)
    hi: float       # верхняя физическая граница (clamp)
    interval: float  # период публикации, сек

    @property
    def client_id(self) -> str:
        return f"farm-{STUDENT_TAG}-{self.name}"

    @property
    def topic(self) -> str:
        return f"{TOPIC_PREFIX}/{self.name}"


# поля SensorConfig: name, unit, night, day, sigma, lo, hi, interval
SENSORS = [
    SensorConfig("temperature", "°C", 21.5, 24.5, 0.25, 10.0, 35.0, 2.0),
    SensorConfig("co2", "ppm", 1450.0, 720.0, 15.0, 380.0, 1500.0, 5.0),  # 1450 темно ночью, 720 светло днем
    SensorConfig("humidity", "%", 70.0, 62.0, 1.0, 35.0, 90.0, 3.0),
    SensorConfig("light", "lux", 8000.0, 42000.0, 1200.0, 200.0, 70000.0, 1.5),
]
# Глобально менять частоту публикации (RATE_MULT из .env): 4 = в 4 раза чаще.
if RATE_MULT and RATE_MULT != 1.0:
    for _cfg in SENSORS:
        _cfg.interval = max(0.05, round(_cfg.interval / RATE_MULT, 4))

# текущие значения датчиков - для связей (температура от влажности, CO2 от света)
current = {s.name: (s.night + s.day) / 2.0 for s in SENSORS}
current_lock = threading.Lock()

START = time.time()           # момент старта (переопределяется в main())
START_TOD_H = 0.0             # реальное время суток (часы 0..24) на старте, задается в main()
stop_flag = threading.Event()
force = {s.name: 0 for s in SENSORS}
force_lock = threading.Lock()


def _model_frac() -> float:
    """Доля модельных суток 0..1: СТАРТ от реального времени суток, дальше ускоренно по DAY_PERIOD_SEC."""
    return (START_TOD_H / 24.0 + (time.time() - START) / DAY_PERIOD_SEC) % 1.0


def daylight() -> float:
    """Освещенность суток 0..1: 0 - полночь, 1 - полдень (плавно)."""
    return 0.5 * (1.0 - math.cos(2.0 * math.pi * _model_frac()))


def clock_hours() -> float:
    """Время суток 0..24 (для оси графиков)."""
    return round(_model_frac() * 24.0, 3)


def weather_mults():
    """Текущие множители погоды (плавно интерполируются от prev к cur) и имя состояния."""
    with weather_lock:
        p = WEATHER[weather_state["prev"]]
        c = WEATHER[weather_state["cur"]]
        ts = weather_state["ts"]
        name = c.name
    ramp = max(1e-6, WEATHER_RAMP_H / 24.0 * DAY_PERIOD_SEC)
    f = max(0.0, min(1.0, (time.time() - ts) / ramp))
    tm = p.temp_mult + (c.temp_mult - p.temp_mult) * f
    lm = p.light_mult + (c.light_mult - p.light_mult) * f
    ha_target = c.hum_add    # мгновенная цель: динамику влажности (быстро вверх, медленно вниз) задает датчик
    return tm, lm, ha_target, name


def baseline(cfg: SensorConfig, d: float, tm: float, lm: float, ha: float, cur: dict) -> float:
    """Целевое значение датчика: суточный ход + погода + связи между датчиками."""
    dn = cfg.night + (cfg.day - cfg.night) * d           # суточный ход
    if cfg.name == "light":
        return dn * lm                                   # погода влияет на свет
    if cfg.name == "temperature":
        hum = cur.get("humidity", 66.0)
        return dn * tm - 0.15 * (hum - 66.0)             # погода и влияние влажности
    if cfg.name == "co2":
        # CO2 зависит от УРОВНЯ освещенности (база без шума, чистое разделение погод),
        # круто при низком свете: дождь ночью (очень темно) часто >1200; пасмурно ночью иногда;
        # ясно и облачно с прояснениями никогда (светлее).
        light_base = (8000.0 + 34000.0 * d) * lm         # база освещенности (как у датчика light) * погода
        ln = max(0.0, min(1.0, (light_base - 2000.0) / 15000.0))
        return cfg.night - (cfg.night - cfg.day) * ln    # 1450 (темно) .. 720 (светло)
    return dn + ha                                       # humidity: дождь поднимает к аномалии


def connect(client_id: str) -> mqtt_client.Client:
    """Подключение к нашему брокеру с авторизацией и авто-reconnect; ждем готовности брокера."""
    client = mqtt_client.Client(mqtt_client.CallbackAPIVersion.VERSION2, client_id=client_id)
    client.username_pw_set(MQTT_USER, MQTT_PASSWORD)
    client.reconnect_delay_set(min_delay=1, max_delay=10)
    while not stop_flag.is_set():
        try:
            client.connect(MQTT_HOST, MQTT_PORT, keepalive=60)
            break
        except Exception as e:
            print(f"  [{client_id}] брокер недоступен ({e}); повтор через 2 с...", flush=True)
            stop_flag.wait(2)
    client.loop_start()
    return client


def weather_controller():
    """Каждые WEATHER_PERIOD_H модельных часов: вперед 50%, остаться 30%, назад 20%."""
    period_real = WEATHER_PERIOD_H / 24.0 * DAY_PERIOD_SEC
    print(f"  [weather] старт: {WEATHER[0].name}; смена каждые {WEATHER_PERIOD_H:.0f} мод.ч "
          f"(около {period_real:.1f} с реального)", flush=True)
    while not stop_flag.is_set():
        stop_flag.wait(period_real)
        if stop_flag.is_set():
            break
        r = random.random()
        step = 1 if r < P_FORWARD else (0 if r < P_FORWARD + P_STAY else -1)
        if step == 0:
            with weather_lock:
                name = WEATHER[weather_state["cur"]].name
            print(f"  [weather] остается {name}", flush=True)
            continue
        with weather_lock:
            weather_state["prev"] = weather_state["cur"]
            weather_state["cur"] = (weather_state["cur"] + step) % len(WEATHER)
            weather_state["ts"] = time.time()
            name = WEATHER[weather_state["cur"]].name
        print(f"  [weather] стало {name} ({'вперед' if step > 0 else 'назад'})", flush=True)


def publisher(cfg: SensorConfig):
    client = connect(cfg.client_id)
    noise = 0.0
    ha_tracked = WEATHER[weather_state["cur"]].hum_add   # добавка влажности (асимметрия: быстро вверх, медленно вниз)
    n = 0
    print(f"  [pub:{cfg.name}] старт, топик {cfg.topic}, каждые {cfg.interval} с", flush=True)
    try:
        while not stop_flag.is_set():
            n += 1
            tm, lm, ha_target, wname = weather_mults()
            if cfg.name == "humidity":
                # влажность: быстрый подъем при дожде, медленный спад после
                dt_h = cfg.interval / DAY_PERIOD_SEC * 24.0
                tau = HUM_RISE_TAU_H if ha_target > ha_tracked else HUM_FALL_TAU_H
                ha_tracked += (ha_target - ha_tracked) * min(1.0, dt_h / tau)
            with force_lock:
                forced = force[cfg.name] > 0
                if forced:
                    force[cfg.name] -= 1
            if forced:
                value = round(cfg.lo + (cfg.hi - cfg.lo) * 0.90, 2)   # аномалия: высоко, но видна на графике
            else:
                with current_lock:
                    cur = dict(current)
                base = baseline(cfg, daylight(), tm, lm, ha_tracked, cur)
                noise += -0.3 * noise + random.gauss(0.0, cfg.sigma)
                value = round(max(cfg.lo, min(cfg.hi, base + noise)), 2)
            with current_lock:
                current[cfg.name] = value
            payload = {
                "sensor": cfg.name,
                "unit": cfg.unit,
                "value": value,
                "n": n,
                "tod": clock_hours(),
                "weather": wname,
                "ts": round(time.time(), 2),
            }
            client.publish(cfg.topic, json.dumps(payload), qos=QOS)
            mark = " [АНОМАЛИЯ]" if forced else ""
            print(f"  pub {cfg.name} = {value} {cfg.unit} (#{n}) [{wname}]{mark}", flush=True)
            stop_flag.wait(cfg.interval)
    finally:
        client.loop_stop()
        client.disconnect()
        print(f"  [pub:{cfg.name}] остановлен (опубликовано {n})", flush=True)


def control_listener():
    """Слушает топик control: имя датчика - вколоть аномалию; 'clear' - сбросить."""
    names = {s.name for s in SENSORS}
    client = connect(f"farm-{STUDENT_TAG}-control")

    def on_msg(_c, _u, m):
        cmd = m.payload.decode().strip().lower()
        with force_lock:
            if cmd in ("clear", "reset"):
                for k in force:
                    force[k] = 0
                print("  [control] аномалии сброшены", flush=True)
            elif cmd in names:
                force[cmd] = N_FORCE
                print(f"  [control] инъекция аномалии: {cmd} на {N_FORCE} показаний", flush=True)
            else:
                print(f"  [control] не знаю команду '{cmd}' (датчики: {sorted(names)} | clear)", flush=True)

    client.on_message = on_msg
    client.subscribe(f"{TOPIC_PREFIX}/control", qos=1)
    print(f"  [control] слушаю {TOPIC_PREFIX}/control (инъекция аномалий)", flush=True)
    try:
        while not stop_flag.is_set():
            stop_flag.wait(1)
    finally:
        client.loop_stop()
        client.disconnect()


def main():
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")   # эмодзи в логах не валят вывод
    except Exception:
        pass
    global START, START_TOD_H
    START = time.time()
    lt = time.localtime(START)
    START_TOD_H = lt.tm_hour + lt.tm_min / 60.0 + lt.tm_sec / 3600.0   # старт суточного цикла = реальное время
    with weather_lock:
        weather_state["ts"] = START
    print(f"=== Эмулятор фермы, брокер {MQTT_HOST}:{MQTT_PORT} (user={MQTT_USER}) ===", flush=True)
    print(f"    сутки={DAY_PERIOD_SEC:.0f}с, частота x{RATE_MULT:g}, погода каждые {WEATHER_PERIOD_H:.0f} мод.ч",
          flush=True)
    for s in SENSORS:
        print(f"  {s.name}: {s.topic}  каждые {s.interval} с", flush=True)

    threads = [threading.Thread(target=publisher, args=(cfg,), name=cfg.name, daemon=True)
               for cfg in SENSORS]
    threads.append(threading.Thread(target=weather_controller, name="weather", daemon=True))
    threads.append(threading.Thread(target=control_listener, name="control", daemon=True))
    for t in threads:
        t.start()

    try:
        if RUN_DURATION_SEC > 0:
            stop_flag.wait(RUN_DURATION_SEC)
        else:
            while not stop_flag.is_set():
                time.sleep(0.5)
    except KeyboardInterrupt:
        print("\nОстановка по Ctrl+C...", flush=True)
    finally:
        stop_flag.set()
        for t in threads:
            t.join(timeout=3)
        print("Эмулятор остановлен.", flush=True)


if __name__ == "__main__":
    main()
