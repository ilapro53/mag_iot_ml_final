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
import bisect
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

# Актуаторы: на каком графике рисовать полосу-подсветку, цвет полосы и подпись.
ACTUATORS = ["heater", "vent", "lamp"]
ACT_AXIS = {"heater": "temperature", "vent": "humidity", "lamp": "light"}
ACT_COLOR = {"heater": "#FF7043", "vent": "#42A5F5", "lamp": "#FFD54F"}
ACT_TITLE = {"heater": "обогрев", "vent": "проветривание", "lamp": "досветка"}
ACT_ALPHA = 0.30
LIGHT_OUT_COLOR = "#8B6914"   # свет снаружи - вторая линия на графике освещенности


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
    import matplotlib.patches as mpatches
    from matplotlib.lines import Line2D

    lock = threading.Lock()
    buffers = {s: deque(maxlen=args.maxlen) for s in SENSORS}   # (ts, tod, value) - история точек
    light_out_buf = deque(maxlen=args.maxlen)                   # свет снаружи (ts, tod, value)
    act_events = {a: deque(maxlen=args.maxlen) for a in ACTUATORS}  # (ts, вкл?) события актуаторов
    anomaly_total = {s: 0 for s in SENSORS}                     # счетчик аномалий с начала запуска
    weather_now = {"name": ""}                                  # последняя известная погода

    def on_message(_c, _u, msg):
        leaf = msg.topic.split("/")[-1]
        text = msg.payload.decode()
        now = time.time()                          # общие часы визуализатора для всех топиков
        # актуаторы: payload "ON"/"OFF" (не JSON)
        if leaf in ACTUATORS:
            with lock:
                act_events[leaf].append((now, text.strip().upper() in ("ON", "1", "TRUE")))
            return
        try:
            data = json.loads(text)
            name = data.get("sensor") or leaf
            value = float(data["value"])
            tod = float(data.get("tod", 0.0))     # время суток 0..24 (для подписей оси)
            w = data.get("weather")
            with lock:
                if w:
                    weather_now["name"] = w
                if name == "light_out":
                    light_out_buf.append((now, tod, value))
                elif name in buffers:
                    buffers[name].append((now, tod, value))
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
            snap_out = list(light_out_buf)
            act_snap = {a: list(act_events[a]) for a in ACTUATORS}
            total = dict(anomaly_total)
        all_pts = [p for s in SENSORS for p in snap[s]] + snap_out
        have = bool(all_pts)
        if have:
            # развернуть "время суток" (0..24, циклично) в МОНОТОННЫЕ модельные часы,
            # тогда окно и ось работают в часах независимо от полуночи и скорости времени.
            simh = {}
            offset = 0.0
            prev = None
            order = sorted(all_pts, key=lambda q: q[0])       # по времени приема ts
            for p in order:
                tod = p[1]
                if prev is not None and tod < prev - 12.0:   # перешли полночь вперед
                    offset += 24.0
                simh[id(p)] = tod + offset
                prev = tod
            h_now = max(simh.values())
            h_min = min(simh.values())
            h_start = max(h_now - args.window, h_min)         # максимум args.window часов по X
            # отображение времени приема ts -> модельные часы simh (для полос актуаторов)
            ts_arr = [p[0] for p in order]
            simh_arr = [simh[id(p)] for p in order]

            def ts_to_simh(t):
                if t <= ts_arr[0]:
                    return simh_arr[0]
                if t >= ts_arr[-1]:
                    return simh_arr[-1]
                i = bisect.bisect_left(ts_arr, t)
                t0, t1 = ts_arr[i - 1], ts_arr[i]
                s0, s1 = simh_arr[i - 1], simh_arr[i]
                return s0 if t1 == t0 else s0 + (s1 - s0) * (t - t0) / (t1 - t0)

            def on_spans(events):
                """Интервалы (ts_on, ts_off), когда актуатор включен; открытый - до настоящего."""
                spans, on_start = [], None
                for ts, st in sorted(events, key=lambda e: e[0]):
                    if st and on_start is None:
                        on_start = ts
                    elif not st and on_start is not None:
                        spans.append((on_start, ts))
                        on_start = None
                if on_start is not None:
                    spans.append((on_start, ts_arr[-1]))
                return spans
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
                # полосы-подсветка: периоды работы актуаторов (обогрев/проветривание/досветка)
                for a in ACTUATORS:
                    if ACT_AXIS[a] != s:
                        continue
                    for t_on, t_off in on_spans(act_snap[a]):
                        x0 = max(ts_to_simh(t_on), h_start)
                        x1 = max(ts_to_simh(t_off), h_start)
                        if x1 > x0:
                            ax.axvspan(x0, x1, facecolor=ACT_COLOR[a], alpha=ACT_ALPHA, zorder=1)
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
                # свет снаружи - вторая линия на графике освещенности
                if s == "light":
                    pts_o = [p for p in snap_out if simh[id(p)] >= h_start]
                    if pts_o:
                        xo = [simh[id(p)] for p in pts_o]
                        yo = [p[2] for p in pts_o]
                        ax.plot(xo, yo, "--", color=LIGHT_OUT_COLOR, lw=1.4, zorder=3)
                # легенда: линии света внутри/снаружи и что означают полосы актуаторов
                handles = []
                if s == "light":
                    handles.append(Line2D([0], [0], color=COLORS["light"], lw=1.6, label="внутри"))
                    handles.append(Line2D([0], [0], color=LIGHT_OUT_COLOR, lw=1.4, ls="--", label="снаружи"))
                for a in ACTUATORS:
                    if ACT_AXIS[a] == s:
                        handles.append(mpatches.Patch(facecolor=ACT_COLOR[a], alpha=ACT_ALPHA, label=ACT_TITLE[a]))
                if handles:
                    ax.legend(handles=handles, loc="upper left", fontsize=7, framealpha=0.7)
                ax.set_xticks(tick_h)
                ax.set_xticklabels(tick_lab, rotation=45, ha="right", fontsize=7)
            else:
                ax.set_title(f"{TITLES[s]}: ждем данные...")
            ax.set_ylabel(UNITS[s])
            ax.set_xlabel("время суток")
            ax.grid(True, alpha=0.3, zorder=1)
        wtxt = f"   |   Погода: {weather_now['name']}" if weather_now["name"] else ""
        fig.suptitle(f"IoT-ферма (MQTT): зеленая зона — норма, красные точки — аномалии, "
                     f"полосы — работа актуаторов{wtxt}", fontsize=11)
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
