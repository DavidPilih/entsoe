import pandas as pd
from pathlib import Path
import json
from typing import List, Dict, Any, Tuple
import traceback
from api_client import scrap_data
from api_client import scrap_data_sun
import time
from filelock import FileLock
import tempfile
import os

from graph import graph_plot

MARGIN = 0.1  # V PROCENTIH 0.1=10


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


def load_price_data(filename: str, start: pd.Timestamp, end: pd.Timestamp) -> List[Tuple[pd.Timestamp, float]]:
    filepath = Path(filename)
    lock_path = filename + ".lock"

    if not filepath.exists():
        scrap_data(filename, start, end)

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

    now = pd.Timestamp(date, tz="Europe/Ljubljana")

    start = now + pd.Timedelta(days=0)
    end = now + pd.Timedelta(days=1)

    tomorrow_start = now + pd.Timedelta(days=1)
    tomorrow_end = now + pd.Timedelta(days=2)

    filename = "cache/prices_data/prices_" + start.strftime("%Y-%m-%d") + ".xlsx"

    filename_tomorrow = "cache/prices_data/prices_" + tomorrow_start.strftime("%Y-%m-%d") + ".xlsx"
    data_today = load_price_data(filename, start, end)

    have_tomorrow = False
    data_tomorrow: List[Tuple[pd.Timestamp, float]] = []

    if include_next_day:
        try:
            data_tomorrow = load_price_data(filename_tomorrow, tomorrow_start, tomorrow_end)
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

        #za mqtt
        result = {
            "charging_intervals": charging_times,
            "discharging_intervals": discharging_times,
            "combined_with_tomorrow": have_tomorrow,
        }

        #za database
        database_data = []
        for order in orders:
            if order["order"] == "buy":
                action = 1
            elif order["order"] == "sell":
                action = -1
            else:
                action = 0

            database_data.append({
                "device_id": "",
                "timestamp": order["time"],
                "value": action
            })

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
        graph_plot(
            timestamps,
            prices_all,
            orders,
            start,
            filename_png,
            day_boundary=n_today if have_tomorrow else None,
            end_date=tomorrow_start if have_tomorrow else None,
            fwh=fwh,
            lwh=lwh,
            fwh_tom=fwh_tom,
            lwh_tom=lwh_tom,
            tomorrow_date=tomorrow_start if have_tomorrow else None,

            capacity=capacity,
            power=power,
            intervals_needed=intervals_needed,
            minimum_profit=minimum_profit,
            soc=soc,
            initial_position=initial_position,
            from_time=from_time,
            include_next_day=include_next_day
        )

    return database_data

if __name__ == "__main__":

    try:
        
        result = main(capacity=10, power=5, minimum_profit=10, date="2026-05-14", lat=46.8894, lng=15.458, from_time="00:00", soc="0.0", include_next_day=False)
        print("uspelo")
    except Exception as e:
        print("neuspelo")
        print(traceback.format_exc())