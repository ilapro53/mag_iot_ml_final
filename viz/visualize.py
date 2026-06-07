# /// script
# requires-python = ">=3.10"
# dependencies = [
#   "paho-mqtt>=2.0",
#   "matplotlib>=3.7",
# ]
# ///
"""
Визуализация IoT-фермы: MQTT-подписчик и 4 графика matplotlib.

Два режима:
  1) Живое окно (на ХОСТЕ):
        python viz/visualize.py
     4 графика обновляются в реальном времени, аномалии красным.
  2) Сохранить картинку (headless, в т.ч. в Docker):
        python viz/visualize.py --save out/graphs.png --duration 60

По умолчанию подключается к localhost:18883 (порт брокера, опубликованный на хост).
В контейнере передаем --host mosquitto --port 1883.
"""
import argparse
import json
import os
import threading
import time
from collections import deque

from paho.mqtt import client as mqtt_client

SENSORS = ["temperature", "humidity", "light", "co2"]
# "Комфортные" диапазоны теплицы - значения вне них считаем аномалией.
NORMAL = {
    "temperature": (18.0, 28.0),
    "humidity": (50.0, 80.0),
    "light": (5000.0, 50000.0),
    "co2": (600.0, 1200.0),
}
UNITS = {"temperature": "°C", "humidity": "%", "light": "lux", "co2": "ppm"}
TITLES = {"temperature": "Температура", "humidity": "Влажность",
          "light": "Освещенность", "co2": "CO₂"}
COLORS = {"temperature": "tab:red", "humidity": "tab:blue",
          "light": "tab:orange", "co2": "tab:green"}
# Масштаб Y по каждому датчику - чтобы зоны нормы и аномалии всегда были видны.
YLIM = {
    "temperature": (12.0, 34.0),
    "humidity": (40.0, 90.0),
    "light": (0.0, 68000.0),
    "co2": (400.0, 1500.0),
}
ZONE_OK = "#77FFA4"     # зона нормы (зеленая)
ZONE_BAD = "#FFB2B2"    # вне нормы (красная), снизу и сверху
ZONE_ALPHA = 0.8        # насыщенность зон (1.0 = сплошной цвет)


def _hhmm(h):
    """Часы 0..24 в строку формата 'ЧЧ:ММ'."""
    return f"{int(h) % 24:02d}:{int((h % 1) * 60):02d}"


def parse_args():
    p = argparse.ArgumentParser(description="Визуализация фермы (MQTT, 4 графика)")
    p.add_argument("--host", default=os.getenv("VIZ_MQTT_HOST", "localhost"))
    p.add_argument("--port", type=int, default=int(os.getenv("VIZ_MQTT_PORT", "18883")))
    p.add_argument("--user", default=os.getenv("MQTT_USER", "mqtt_exp"))
    p.add_argument("--password", default=os.getenv("MQTT_PASSWORD", "pass_mqtt"))
    p.add_argument("--prefix", default=os.getenv("TOPIC_PREFIX", "farm/ilya"))
    p.add_argument("--maxlen", type=int, default=2000, help="сколько точек хранить в буфере (история)")
    p.add_argument("--window", type=float, default=48.0,
                   help="макс. ширина окна по X в ЧАСАХ модельного времени (одинаково для всех графиков)")
    p.add_argument("--save", metavar="PATH", help="сохранить PNG и выйти (headless)")
    p.add_argument("--duration", type=float, default=60.0, help="сек сбора для --save")
    return p.parse_args()


def main():
    args = parse_args()

    import matplotlib
    if args.save:
        matplotlib.use("Agg")          # без окна, только файл
    # шрифт с запасными для эмодзи погоды (на Windows есть Segoe UI Emoji/Symbol)
    matplotlib.rcParams["font.family"] = ["DejaVu Sans", "Segoe UI Emoji", "Segoe UI Symbol"]
    import matplotlib.pyplot as plt

    lock = threading.Lock()
    buffers = {s: deque(maxlen=args.maxlen) for s in SENSORS}   # (ts, tod, value) - история точек
    anomaly_total = {s: 0 for s in SENSORS}                     # счетчик аномалий с начала запуска
    weather_now = {"name": ""}                                  # последняя известная погода

    def on_message(_c, _u, msg):
        try:
            data = json.loads(msg.payload.decode())
            name = data.get("sensor") or msg.topic.split("/")[-1]
            if name not in buffers:
                return
            value = float(data["value"])
            tod = float(data.get("tod", 0.0))     # время суток 0..24 (для подписей оси)
            ts = float(data.get("ts", 0.0))       # реальная метка времени (общая ось для всех датчиков)
            w = data.get("weather")
            with lock:
                buffers[name].append((ts, tod, value))
                if w:
                    weather_now["name"] = w
                lo, hi = NORMAL[name]
                if value < lo or value > hi:
                    anomaly_total[name] += 1
        except Exception as e:
            print("parse error:", e)

    client = mqtt_client.Client(mqtt_client.CallbackAPIVersion.VERSION2, client_id="farm-visualizer")
    if args.user:
        client.username_pw_set(args.user, args.password)
    client.on_message = on_message
    print(f"Подключение к {args.host}:{args.port}, топик {args.prefix}/# ...")
    client.connect(args.host, args.port, 60)
    client.subscribe(f"{args.prefix}/#", qos=1)
    client.loop_start()

    def draw(fig, axes):
        with lock:
            snap = {s: list(buffers[s]) for s in SENSORS}
            total = dict(anomaly_total)
        all_pts = [p for s in SENSORS for p in snap[s]]
        have = bool(all_pts)
        if have:
            # развернуть "время суток" (0..24, циклично) в МОНОТОННЫЕ модельные часы,
            # тогда окно и ось работают в часах независимо от полуночи и скорости времени.
            simh = {}
            offset = 0.0
            prev = None
            for p in sorted(all_pts, key=lambda q: q[0]):    # по реальному времени ts
                tod = p[1]
                if prev is not None and tod < prev - 12.0:   # перешли полночь вперед
                    offset += 24.0
                simh[id(p)] = tod + offset
                prev = tod
            h_now = max(simh.values())
            h_min = min(simh.values())
            h_start = max(h_now - args.window, h_min)         # максимум args.window часов по X
            # общие метки времени, одинаковы на всех графиках (оси синхронизированы)
            n_ticks = 15
            span = h_now - h_start
            tick_h = [h_start + span * i / (n_ticks - 1) for i in range(n_ticks)]
            tick_lab = [_hhmm(h % 24.0) for h in tick_h]
            # позиции полуночи (00:00) в окне, модельные часы кратны 24
            midnights = []
            k = int(h_start // 24)
            while 24 * k <= h_now:
                if 24 * k >= h_start:
                    midnights.append(24 * k)
                k += 1
        for ax, s in zip(axes.flat, SENSORS):
            ax.clear()
            lo, hi = NORMAL[s]
            ymin, ymax = YLIM[s]
            # фон: красная зона снизу и сверху (вне нормы), зеленая в середине (норма)
            ax.axhspan(ymin, lo, facecolor=ZONE_BAD, alpha=ZONE_ALPHA, zorder=0)
            ax.axhspan(lo, hi, facecolor=ZONE_OK, alpha=ZONE_ALPHA, zorder=0)
            ax.axhspan(hi, ymax, facecolor=ZONE_BAD, alpha=ZONE_ALPHA, zorder=0)
            ax.set_ylim(ymin, ymax)
            if have:
                ax.set_xlim(h_start, h_now)             # одно окно (в часах) на все 4 графика
                for mx in midnights:                    # серый пунктир на каждой полуночи 00:00
                    ax.axvline(mx, color="gray", linestyle="--", linewidth=1.0, alpha=0.7, zorder=2)
                pts = [p for p in snap[s] if simh[id(p)] >= h_start]
                if pts:
                    xs = [simh[id(p)] for p in pts]     # x = модельные часы (монотонно)
                    ys = [p[2] for p in pts]
                    ax.plot(xs, ys, "-o", color=COLORS[s], ms=3, lw=1.6, zorder=3)
                    bad = [(simh[id(p)], p[2]) for p in pts if p[2] < lo or p[2] > hi]
                    if bad:
                        bx, by = zip(*bad)
                        ax.scatter(bx, by, color="red", edgecolors="black",
                                   linewidths=0.5, s=55, zorder=5)
                    cur = ys[-1]
                    warn = "  [!]" if (cur < lo or cur > hi) else ""
                    ax.set_title(f"{TITLES[s]}: {cur} {UNITS[s]}{warn}  (аномалий: {total[s]})")
                else:
                    ax.set_title(f"{TITLES[s]}: нет данных в окне  (аномалий: {total[s]})")
                ax.set_xticks(tick_h)
                ax.set_xticklabels(tick_lab, rotation=45, ha="right", fontsize=7)
            else:
                ax.set_title(f"{TITLES[s]}: ждем данные...")
            ax.set_ylabel(UNITS[s])
            ax.set_xlabel("время суток")
            ax.grid(True, alpha=0.3, zorder=1)
        wtxt = f"   |   Погода: {weather_now['name']}" if weather_now["name"] else ""
        fig.suptitle(f"IoT-ферма (MQTT): зеленая зона — норма, красные точки — аномалии{wtxt}",
                     fontsize=12)
        fig.tight_layout(rect=[0, 0, 1, 0.96])

    fig, axes = plt.subplots(2, 2, figsize=(13, 8))

    if args.save:
        print(f"Сбор данных {args.duration} с ...")
        time.sleep(args.duration)
        draw(fig, axes)
        out = os.path.abspath(args.save)
        os.makedirs(os.path.dirname(out), exist_ok=True)
        fig.savefig(out, dpi=110)
        print(f"Сохранено: {out}")
    else:
        from matplotlib.animation import FuncAnimation
        anim = FuncAnimation(fig, lambda _f: draw(fig, axes), interval=1000, cache_frame_data=False)
        plt.show()       # блокирует, окно живет; закрытие окна завершает программу
        del anim

    client.loop_stop()
    client.disconnect()


if __name__ == "__main__":
    main()
