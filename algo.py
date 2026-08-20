import pandas as pd
from pathlib import Path
import json
from typing import List, Dict, Any, Tuple
import matplotlib
matplotlib.use("Agg")
from matplotlib.figure import Figure
import traceback
from api_client import scrap_data
from api_client import scrap_data_sun
import time
from filelock import FileLock
import tempfile
import os

MARGIN = 0.1  # V PROCENTIH 0.1=10


def show_graph():
    pass
    # plt.show()


def optimize_trades(data: List[Dict[str, Any]], max_positions: int, minimum_profit: float, buy_mask: List[bool], trade_mask: List[bool], initial_position: int = 0, force_flat_end: bool = False) -> List[Dict[str, Any]]:

    prices = [float(d["price"]) for d in data]
    times = [d["time"] for d in data]

    T = len(prices)
    K = max_positions
    NEG = float("-inf")

    dp = [[0.0] * (K + 1) for _ in range(T + 1)]
    decision = [[None] * (K + 1) for _ in range(T + 1)]

    for k in range(K + 1):
        if force_flat_end and k != 0:
            dp[T][k] = NEG
        else:
            dp[T][k] = 0.0

    for t in range(T - 1, -1, -1):

        price = prices[t]

        can_trade = trade_mask[t]
        can_buy = can_trade and buy_mask[t]
        can_sell = can_trade

        for k in range(K + 1):

            best_value = dp[t + 1][k]
            best_action = "hold"

            if can_buy and k < K:
                value = -price * (1 + MARGIN) - minimum_profit + dp[t + 1][k + 1]
                if value > best_value:
                    best_value = value
                    best_action = "buy"

            if can_sell and k > 0:
                value = price * (1 - MARGIN) + dp[t + 1][k - 1]
                if value > best_value:
                    best_value = value
                    best_action = "sell"

            dp[t][k] = best_value
            decision[t][k] = best_action

    actions = []
    k = initial_position

    for t in range(T):
        act = decision[t][k]
        actions.append(act)

        if act == "buy":
            k += 1
        elif act == "sell":
            k -= 1

    return [{"time": times[i], "price": prices[i], "order": actions[i]} for i in range(T)]


def wh_to_hm(interval):
    minutes = interval * 15
    return f"{minutes // 60:02d}:{minutes % 60:02d}"


def graph_plot(
    timestamps: List[pd.Timestamp],
    prices_all: List[float],
    orders: List[Dict[str, Any]],
    country_code: str,
    start: pd.Timestamp,
    filename_png: str,
    fwh: str = None,
    lwh: str = None,
    fwh_tom: str = None,
    lwh_tom: str = None,
    tomorrow_date: pd.Timestamp = None,
    day_boundary: int = None,
    end_date: pd.Timestamp = None,

    # INPUT PODATKI
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

    # ---------------------------------------------------------
    # BUY / SELL TOČKE
    # ---------------------------------------------------------
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

    # ---------------------------------------------------------
    # GRAF
    # ---------------------------------------------------------
    fig = Figure(figsize=(16, 8))
    ax = fig.add_subplot(111)

    # ---------------------------------------------------------
    # INPUT PODATKI
    # ---------------------------------------------------------
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
            f"Country: {country_code}"
        ),
    ]

    # FWH / LWH
    fwh_text = wh_to_hm(fwh) if fwh is not None else "-"
    lwh_text = wh_to_hm(lwh) if lwh is not None else "-"

    input_lines.append(
        f"FWH: {fwh_text}    |    "
        f"LWH: {lwh_text}    |    "
        f"Include next day: {'DA' if include_next_day else 'NE'}"
    )

    # Jutrišnji dan
    if tomorrow_date is not None:
        tomorrow_text = tomorrow_date.strftime("%Y-%m-%d")

        input_lines.append(
            f"Tomorrow date: {tomorrow_text}"
        )

    # Jutrišnji FWH/LWH
    if fwh_tom is not None and lwh_tom is not None:
        input_lines.append(
            f"FWH jutri: {wh_to_hm(fwh_tom)}    |    "
            f"LWH jutri: {wh_to_hm(lwh_tom)}"
        )

    input_text = "\n".join(input_lines)

    # Prostor za input panel
    fig.subplots_adjust(
        top=0.72,
        bottom=0.18,
        left=0.06,
        right=0.98
    )

    # INPUT PANEL
    fig.text(
        0.5,
        0.96,
        input_text,
        ha="center",
        va="top",
        fontsize=9,
        family="monospace",
        bbox=dict(
            boxstyle="round,pad=0.6",
            facecolor="whitesmoke",
            edgecolor="gray",
            linewidth=1
        )
    )

    # ---------------------------------------------------------
    # CENA
    # ---------------------------------------------------------
    ax.plot(
        range(len(prices_all)),
        prices_all,
        color="steelblue",
        linewidth=1.6,
        label="Cena"
    )

    # ---------------------------------------------------------
    # BUY
    # ---------------------------------------------------------
    ax.scatter(
        buy_x,
        buy_prices,
        color="green",
        zorder=5,
        s=50,
        label="Nakup (polnjenje)"
    )

    # ---------------------------------------------------------
    # SELL
    # ---------------------------------------------------------
    ax.scatter(
        sell_x,
        sell_prices,
        color="red",
        zorder=5,
        s=50,
        label="Prodaja (praznjenje)"
    )

    # ---------------------------------------------------------
    # FWH / LWH DANES
    # ---------------------------------------------------------
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

    # ---------------------------------------------------------
    # FWH / LWH JUTRI
    # ---------------------------------------------------------
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

    # ---------------------------------------------------------
    # MEJA DNEVA
    # ---------------------------------------------------------
    if day_boundary is not None:

        ax.axvline(
            day_boundary - 0.5,
            color="gray",
            linestyle="--",
            alpha=0.6,
            label="Meja dneva"
        )

    # ---------------------------------------------------------
    # NASLOVI
    # ---------------------------------------------------------
    ax.set_xlabel("Čas")
    ax.set_ylabel("Cena (EUR/MWh)")

    title = (
        f"Day-ahead cene za {country_code} — "
        f"{start.strftime('%Y-%m-%d')}"
    )

    if end_date is not None:
        title += (
            f" in {end_date.strftime('%Y-%m-%d')}"
        )

    ax.set_title(title)

    # ---------------------------------------------------------
    # X OS
    # ---------------------------------------------------------
    ax.set_xticks(
        range(0, len(times_labels), 4)
    )

    ax.set_xticklabels(
        times_labels[::4],
        rotation=45,
        ha="right"
    )

    # ---------------------------------------------------------
    # GRID
    # ---------------------------------------------------------
    ax.grid(
        True,
        alpha=0.3
    )

    # ---------------------------------------------------------
    # LEGENDA
    # ---------------------------------------------------------
    ax.legend(
        loc="upper left",
        fontsize=8
    )

    # ---------------------------------------------------------
    # SHRANI GRAF
    # ---------------------------------------------------------
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


def getwh(date, lat, lng):
    loc_key = f"{lat}_{lng}"
    path = "cache/sun_data/sun_data.json"
    lock_path = path + ".lock"

    def _read():
        with open(path, "r", encoding="utf-8") as file:
            return json.load(file)

    with FileLock(lock_path, timeout=60):
        try:
            data = _read()
            has_entry = date in data and loc_key in data[date]
        except FileNotFoundError:
            has_entry = False

    if not has_entry:
        scrap_data_sun(lat, lng, date, date)
        with FileLock(lock_path, timeout=60):
            data = _read()

    entry = data[date][loc_key]
    fwh = entry["fwh"]
    lwh = entry["lwh"]

    return fwh, lwh


def load_price_data(filename: str, country_code: str, start: pd.Timestamp, end: pd.Timestamp) -> List[Tuple[pd.Timestamp, float]]:
    filepath = Path(filename)
    lock_path = filename + ".lock"

    if not filepath.exists():
        scrap_data(filename, country_code, start, end)

    with FileLock(lock_path, timeout=60):
        df = pd.read_excel(filename)
        df["time"] = pd.to_datetime(df["time"])

    return [(row["time"], float(row["price"])) for _, row in df.iterrows()]


def main(capacity, power, minimum_profit, date, lat, lng, from_time, soc, include_next_day: bool = True):

    capacity = float(capacity)
    power = float(power)
    minimum_profit = float(minimum_profit)
    soc = float(soc)

    intervals_needed = int(capacity / power * 4)
    initial_position = round(intervals_needed * soc)

    from_hour, from_minute = from_time.split(":")
    from_t = int(from_hour) * 4 + int(from_minute) // 15

    # print("Potrebnih intervalov:", intervals_needed)
    # print("Že kupljenih intervalov (SOC):", initial_position)
    # print("Trgovanje dovoljeno od intervala:", from_t)

    now = pd.Timestamp(date, tz="Europe/Ljubljana")

    start = now + pd.Timedelta(days=0)
    end = now + pd.Timedelta(days=1)

    tomorrow_start = now + pd.Timedelta(days=1)
    tomorrow_end = now + pd.Timedelta(days=2)

    country_code = "SI"

    filename = "cache/prices_data/prices_" + start.strftime("%Y-%m-%d") + ".xlsx"

    filename_tomorrow = "cache/prices_data/prices_" + tomorrow_start.strftime("%Y-%m-%d") + ".xlsx"
    data_today = load_price_data(filename, country_code, start, end)

    have_tomorrow = False
    data_tomorrow: List[Tuple[pd.Timestamp, float]] = []

    if include_next_day:
        try:
            data_tomorrow = load_price_data(filename_tomorrow, country_code, tomorrow_start, tomorrow_end)
            if len(data_tomorrow) > 0:
                have_tomorrow = True
        except Exception as e:
            have_tomorrow = False
    else:
        pass
        # print("include_next_day=False, jutrišnji dan se ne preverja.")

    combined = data_today + (data_tomorrow if have_tomorrow else [])
    n_today = len(data_today)

    date_tomorrow = (pd.Timestamp(date) + pd.Timedelta(days=1)).strftime("%Y-%m-%d")

    fwh, lwh = getwh(date, lat, lng)

    fwh_tom, lwh_tom = (None, None)
    if have_tomorrow:
        fwh_tom, lwh_tom = getwh(date_tomorrow, lat, lng)

    buy_mask = []
    trade_mask = []

    for i, (ts, price) in enumerate(combined):
        t_of_day = ts.hour * 4 + ts.minute // 15

        if i < n_today:
            buy_mask.append(fwh <= t_of_day <= lwh)
            trade_mask.append(t_of_day >= from_t)
        else:
            buy_mask.append(fwh_tom <= t_of_day <= lwh_tom)
            trade_mask.append(True)

    trade_data = [{"time": ts.strftime("%m-%d %H:%M"), "price": price} for ts, price in combined]

    suffix_2day = "_2day" if have_tomorrow else ""

    filename_json = "cache/intervals_json/intervals_" + str(intervals_needed) + "_minprofit_" + str(minimum_profit) + "_date_" + start.strftime("%Y-%m-%d") + f"_lat_{lat}_lng_{lng}" + f"_from_{from_time.replace(':', '')}" + f"_soc_{soc}" + suffix_2day + ".json"

    filename_png = "graph_imgs/intervals_" + str(intervals_needed) + "_minprofit_" + str(minimum_profit) + "_date_" + start.strftime("%Y-%m-%d") + suffix_2day + ".png"

    filepath_json = Path(filename_json)
    lock_path_json = filename_json + ".lock"
    os.makedirs(filepath_json.parent, exist_ok=True)

    with FileLock(lock_path_json, timeout=300):

        if filepath_json.exists():
            with open(filepath_json, "r", encoding="utf-8") as file:
                print("cached json")
                return json.load(file)

        orders = optimize_trades(
            trade_data,
            max_positions=intervals_needed,
            minimum_profit=minimum_profit,
            buy_mask=buy_mask,
            trade_mask=trade_mask,
            initial_position=initial_position,
            force_flat_end=True,
        )

        charging_times = [o["time"] for o in orders if o["order"] == "buy"]
        discharging_times = [o["time"] for o in orders if o["order"] == "sell"]

        result = {
            "charging_intervals": charging_times,
            "discharging_intervals": discharging_times,
            "combined_with_tomorrow": have_tomorrow,
        }

        fd, tmp_path = tempfile.mkstemp(dir=str(filepath_json.parent), suffix=".json.tmp")
        os.close(fd)
        try:
            with open(tmp_path, "w", encoding="utf-8") as file:
                json.dump(result, file, indent=4)
            os.replace(tmp_path, filepath_json)
        except Exception:
            if os.path.exists(tmp_path):
                os.remove(tmp_path)
            raise

        timestamps = [c[0] for c in combined]
        prices_all = [c[1] for c in combined]
        print("graf")
        graph_plot(
            timestamps,
            prices_all,
            orders,
            country_code,
            start,
            filename_png,
            day_boundary=n_today if have_tomorrow else None,
            end_date=tomorrow_start if have_tomorrow else None,
            fwh=fwh,
            lwh=lwh,
            fwh_tom=fwh_tom,
            lwh_tom=lwh_tom,
            tomorrow_date=tomorrow_start if have_tomorrow else None,

            # INPUT PODATKI ZA GRAF
            capacity=capacity,
            power=power,
            intervals_needed=intervals_needed,
            minimum_profit=minimum_profit,
            soc=soc,
            initial_position=initial_position,
            from_time=from_time,
            include_next_day=include_next_day
        )

    return result


if __name__ == "__main__":

    try:
        result = main(capacity=10, power=5, minimum_profit=10, date="2026-05-14", lat=46.8894, lng=15.458, from_time="00:00", soc="0.0", include_next_day=False)
        print("uspelo")
    except Exception as e:
        print("neuspelo")
        print(traceback.format_exc())