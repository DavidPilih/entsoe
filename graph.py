import pandas as pd
from pathlib import Path
from typing import List, Dict, Any
import matplotlib
matplotlib.use("Agg")
from matplotlib.figure import Figure
import tempfile
import os
import math
import textwrap
import matplotlib.pyplot as plt

def forecast_soc(timestamps, orders, soc, intervals_needed, forecast_start, use_sun_data=False, min_soc=0, max_soc=1):
    """SOC (%) na mejah prihodnjih intervalov; vhodni soc je delež 0–1."""
    soc = float(soc)
    intervals_needed = float(intervals_needed)
    if not math.isfinite(soc) or not 0 <= soc <= 1:
        raise ValueError("soc mora biti delež med 0 in 1.")
    if not math.isfinite(intervals_needed) or intervals_needed <= 0:
        raise ValueError("intervals_needed mora biti večji od 0.")
    if len(timestamps) != len(orders):
        raise ValueError("Urnik in časovne oznake morajo imeti enako dolžino.")
    value = soc * 100
    step = 100 / intervals_needed
    xs, ys = [], []
    for i, (timestamp, order) in enumerate(zip(timestamps, orders)):
        interval_start = pd.Timestamp(timestamp)
        interval_end = interval_start + pd.Timedelta(minutes=15)
        if interval_end <= forecast_start:
            continue
        begin = max(interval_start, forecast_start)
        fraction = float((interval_end - begin) / pd.Timedelta(minutes=15))
        x = i + 1 - fraction
        if not xs:
            xs.append(x)
            ys.append(value)
        if "soc" in order:
            value = float(order["soc"]) * 100
            xs.append(i + 1)
            ys.append(value)
            continue
        rate = 0
        if order["order"] == "buy":
            sun = float(order.get("sun_percent", 100)) if use_sun_data else 100
            if not math.isfinite(sun) or not 0 <= sun <= 100:
                raise ValueError("sun_percent mora biti med 0 in 100.")
            rate = step * float(order.get("energy_fraction", order.get("charge_fraction", sun / 100)))
        elif order["order"] == "sell":
            rate = -step * float(order.get("energy_fraction", 1))
        raw = value + rate * fraction
        if rate and (raw > max_soc * 100 or raw < min_soc * 100):
            limit = max_soc * 100 if raw > max_soc * 100 else min_soc * 100
            hit = x + (limit - value) / rate
            if x < hit < i + 1:
                xs.append(hit)
                ys.append(limit)
        value = min(max_soc * 100, max(min_soc * 100, raw))
        xs.append(i + 1)
        ys.append(value)
    return xs, ys


def wh_to_hm(interval):
    minutes = interval * 15
    return f"{minutes // 60:02d}:{minutes % 60:02d}"


def graph_plot(
    timestamps: List[pd.Timestamp],
    prices_all: List[float],
    orders: List[Dict[str, Any]],
    start: pd.Timestamp,
    filename_png: str,
    fwh: str = None,
    lwh: str = None,
    fwh_tom: str = None,
    lwh_tom: str = None,
    tomorrow_date: pd.Timestamp = None,
    day_boundary: int = None,
    end_date: pd.Timestamp = None,

    capacity: float = None,
    power: float = None,
    intervals_needed: int = None,
    minimum_profit: float = None,
    soc: float = None,
    initial_position: int = None,
    from_time: str = None,
    include_next_day: bool = False,
    use_sun_data: bool = False,
    parameters=None,
    min_soc=0,
    max_soc=1,
):
    times_labels = [ts.strftime("%m-%d %H:%M") for ts in timestamps]

    buy_x = [
        i for i, o in enumerate(orders)
        if o["order"] == "buy"
    ]
    buy_prices = [
        prices_all[i]
        for i in buy_x
    ]

    sell_x = [
        i for i, o in enumerate(orders)
        if o["order"] == "sell"
    ]
    sell_prices = [
        prices_all[i]
        for i in sell_x
    ]

    has_balance = bool(orders) and all("solar_kw" in order for order in orders)
    fig = Figure(figsize=(16, 10 if has_balance else 8))
    if has_balance:
        ax, power_ax = fig.subplots(2, 1, sharex=True, height_ratios=[3, 1.5])
        edges = list(range(len(orders) + 1))
        for key, label, color in (
            ('solar_kw', 'Sončna proizvodnja', '#e5b000'),
            ('consumption_kw', 'Poraba objekta', '#15956b'),
            ('battery_kw', 'Hranilnik (+ polnjenje)', '#9c27b0'),
            ('grid_kw', 'Omrežje (+ nakup, - prodaja)', '#1565c0'),
        ):
            power_ax.stairs([order[key] for order in orders], edges,
                            color=color, linewidth=1.5, label=label)
        power_ax.axhline(0, color='gray', linewidth=.8)
        power_ax.set_ylabel('Moč [kW]')
        power_ax.set_title('Energijska bilanca', fontsize=10)
        power_ax.grid(True, alpha=.25)
        power_ax.legend(loc='upper left', fontsize=8, ncol=2)
        if day_boundary is not None:
            power_ax.axvline(day_boundary, color='gray', linestyle='--', alpha=.6)
        time_ax = power_ax
    else:
        ax = fig.add_subplot(111)
        time_ax = ax

    input_lines = [
        "INPUT PODATKI",
        (
            f"Capacity: {capacity} kWh    |    "
            f"Power: {power} kW    |    "
            f"Intervals: {intervals_needed}    |    "
            f"Min profit: {minimum_profit} EUR"
        ),
        (
            f"SOC: {soc:.2f}    |    "
            f"Initial position: {initial_position}    |    "
            f"From time: {from_time}    |    "
            f"Date: {start.strftime('%Y-%m-%d')}    |    "
        ),
    ]

    fwh_text = wh_to_hm(fwh) if fwh is not None else "-"
    lwh_text = wh_to_hm(lwh) if lwh is not None else "-"

    input_lines.append(
        f"FWH: {fwh_text}    |    "
        f"LWH: {lwh_text}    |    "
        f"Include next day: {'DA' if include_next_day else 'NE'}"
    )

    if tomorrow_date is not None:
        tomorrow_text = tomorrow_date.strftime("%Y-%m-%d")

        input_lines.append(
            f"Tomorrow date: {tomorrow_text}"
        )

    if fwh_tom is not None and lwh_tom is not None:
        input_lines.append(
            f"FWH jutri: {wh_to_hm(fwh_tom)}    |    "
            f"LWH jutri: {wh_to_hm(lwh_tom)}"
        )

    if parameters:
        input_lines = ["ZAČETNI PODATKI"] + textwrap.wrap(
            "  |  ".join(f"{key}={value}" for key, value in parameters.items()),
            width=150, break_long_words=False, break_on_hyphens=False,
        )
    input_text = "\n".join(input_lines)
    fig.text(0.06, 0.98, input_text, ha="left", va="top", fontsize=9,
             bbox=dict(facecolor="whitesmoke", edgecolor="gray", pad=6))

    fig.subplots_adjust(top=0.72, bottom=0.18, left=0.06, right=0.98)

    ax.plot(
        range(len(prices_all)),
        prices_all,
        color="steelblue",
        linewidth=1.6,
        label="Cena"
    )

    ax.scatter(
        buy_x,
        buy_prices,
        color="green",
        zorder=5,
        s=50,
        label="Nakup (polnjenje)"
    )

    ax.scatter(
        sell_x,
        sell_prices,
        color="red",
        zorder=5,
        s=50,
        label="Prodaja (praznjenje)"
    )

    # Začetni SOC velja ob from_time; zgodovinskega SOC ne rišemo.
    if soc is not None and intervals_needed is not None and len(timestamps):
        forecast_start = pd.Timestamp(start).normalize()
        if from_time:
            hour, minute = map(int, from_time.split(":"))
            forecast_start = forecast_start.replace(hour=hour, minute=minute)
        timestamp_tz = pd.Timestamp(timestamps[0]).tz
        if timestamp_tz is None:
            forecast_start = forecast_start.tz_localize(None)
        elif forecast_start.tz is None:
            forecast_start = forecast_start.tz_localize(timestamp_tz)
        else:
            forecast_start = forecast_start.tz_convert(timestamp_tz)
        soc_x, soc_y = forecast_soc(
            timestamps, orders, soc, intervals_needed, forecast_start, use_sun_data,
            min_soc, max_soc,
        )
        if soc_x:
            soc_ax = ax.twinx()
            soc_ax.plot(soc_x, soc_y, color="#e5ac00", linewidth=2, label="SOC napoved")
            soc_ax.set_ylim(0, 100)
            soc_ax.set_yticks([0, 20, 40, 60, 80, 100])
            soc_ax.set_ylabel("SOC (%)")
            soc_ax.axhline(min_soc * 100, color="gray", linestyle=":", linewidth=1)
            soc_ax.axhline(max_soc * 100, color="gray", linestyle=":", linewidth=1)
            soc_ax.grid(False)
            soc_ax.legend(loc="upper right", fontsize=8)

    if fwh is not None and lwh is not None:

        fwh_hm = wh_to_hm(fwh)
        lwh_hm = wh_to_hm(lwh)

        fwh_time = pd.to_datetime(
            f"{start.strftime('%Y-%m-%d')} {fwh_hm}"
        )

        lwh_time = pd.to_datetime(
            f"{start.strftime('%Y-%m-%d')} {lwh_hm}"
        )

        fwh_x = min(
            range(len(timestamps)),
            key=lambda i: abs(
                timestamps[i].replace(tzinfo=None) - fwh_time
            )
        )

        lwh_x = min(
            range(len(timestamps)),
            key=lambda i: abs(
                timestamps[i].replace(tzinfo=None) - lwh_time
            )
        )

        ax.axvline(
            fwh_x,
            color="purple",
            linestyle=":",
            linewidth=0.8,
            alpha=0.35,
            label="FWH danes"
        )

        ax.axvline(
            lwh_x,
            color="purple",
            linestyle=":",
            linewidth=0.8,
            alpha=0.35,
            label="LWH danes"
        )

        ax.axvspan(
            fwh_x,
            lwh_x,
            alpha=0.1,
            label="FWH-LWH območje"
        )

    if (
        fwh_tom is not None
        and lwh_tom is not None
        and tomorrow_date is not None
    ):

        fwh_tom_hm = wh_to_hm(fwh_tom)
        lwh_tom_hm = wh_to_hm(lwh_tom)

        fwh_tom_time = pd.to_datetime(
            f"{tomorrow_date.strftime('%Y-%m-%d')} {fwh_tom_hm}"
        )

        lwh_tom_time = pd.to_datetime(
            f"{tomorrow_date.strftime('%Y-%m-%d')} {lwh_tom_hm}"
        )

        fwh_tom_x = min(
            range(len(timestamps)),
            key=lambda i: abs(
                timestamps[i].replace(tzinfo=None) - fwh_tom_time
            )
        )

        lwh_tom_x = min(
            range(len(timestamps)),
            key=lambda i: abs(
                timestamps[i].replace(tzinfo=None) - lwh_tom_time
            )
        )

        ax.axvline(
            fwh_tom_x,
            color="purple",
            linestyle=":",
            linewidth=0.8,
            alpha=0.35,
            label=f"FWH jutri {fwh_tom_hm}"
        )

        ax.axvline(
            lwh_tom_x,
            color="purple",
            linestyle=":",
            linewidth=0.8,
            alpha=0.35,
            label=f"LWH jutri {lwh_tom_hm}"
        )

        ax.axvspan(
            fwh_tom_x,
            lwh_tom_x,
            alpha=0.1,
            label="FWH-LWH območje (jutri)"
        )

    if day_boundary is not None:

        ax.axvline(
            day_boundary - 0.5,
            color="gray",
            linestyle="--",
            alpha=0.6,
            label="Meja dneva"
        )

    time_ax.set_xlabel("Čas")
    ax.set_ylabel("Cena (EUR/MWh)")

    title = (
        f"Day-ahead cene za SLOVENIJO — "
        f"{start.strftime('%Y-%m-%d')}"
    )

    if end_date is not None:
        title += (
            f" in {end_date.strftime('%Y-%m-%d')}"
        )

    title += " | Sončna napoved: " + ("vključena" if use_sun_data else "izključena")
    ax.set_title(title)

    time_ax.set_xticks(
        range(0, len(times_labels), 4)
    )

    time_ax.set_xticklabels(
        times_labels[::4],
        rotation=45,
        ha="right"
    )

    ax.grid(
        True,
        alpha=0.3
    )

    ax.legend(
        loc="upper left",
        fontsize=8
    )

    fig.tight_layout(rect=(0, 0, 0.96, 0.85))

    graph_file = Path(filename_png)

    os.makedirs(
        graph_file.parent,
        exist_ok=True
    )

    fd, tmp_path = tempfile.mkstemp(
        dir=str(graph_file.parent),
        suffix=".png.tmp"
    )

    os.close(fd)

    try:

        fig.savefig(
            tmp_path,
            dpi=80,
            format="png"
        )

        os.replace(
            tmp_path,
            graph_file
        )

    except Exception:

        if os.path.exists(tmp_path):
            os.remove(tmp_path)

        raise
    print(
        "Graf shranjen:",
        graph_file
    )
