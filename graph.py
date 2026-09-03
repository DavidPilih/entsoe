import pandas as pd
from pathlib import Path
from typing import List, Dict, Any
import matplotlib
matplotlib.use("Agg")
from matplotlib.figure import Figure
import tempfile
import os


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
    include_next_day: bool = False
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

    fig = Figure(figsize=(16, 8))
    ax = fig.add_subplot(111)

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

    input_text = "\n".join(input_lines)

    fig.subplots_adjust(top=0.72, bottom=0.18, left=0.06, right=0.98)

    # fig.text(
    #     0.5,
    #     0.96,
    #     input_text,
    #     ha="center",
    #     va="top",
    #     fontsize=9,
    #     family="monospace",
    #     bbox=dict(
    #         boxstyle="round,pad=0.6",
    #         facecolor="whitesmoke",
    #         edgecolor="gray",
    #         linewidth=1
    #     )
    # )

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
            label="FWH jutri"
        )

        ax.axvline(
            lwh_x,
            color="purple",
            linestyle=":",
            linewidth=0.8,
            alpha=0.35,
            label="LWH jutri"
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

    ax.set_xlabel("Čas")
    ax.set_ylabel("Cena (EUR/MWh)")

    title = (
        f"Day-ahead cene za {"SLOVENIJO"} — "
        f"{start.strftime('%Y-%m-%d')}"
    )

    if end_date is not None:
        title += (
            f" in {end_date.strftime('%Y-%m-%d')}"
        )

    ax.set_title(title)

    ax.set_xticks(
        range(0, len(times_labels), 4)
    )

    ax.set_xticklabels(
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

    fig.tight_layout()

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