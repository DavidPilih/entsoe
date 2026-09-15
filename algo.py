import pandas as pd
from pathlib import Path
import json
from typing import List, Dict, Any, Tuple
import traceback
from api_client import scrap_data
from api_client import scrap_data_sun, get_sun_forecast
import time
from filelock import FileLock
import os
import math

from graph import graph_plot



# Ocena: 1000 W/m² pomeni polno razpoložljivo moč polnjenja.
SOLAR_REFERENCE_W_M2 = 1000.0
ENERGY_STEPS = 100  # Ločljivost: 1 % energije enega 15-minutnega intervala.


def solar_percentages(forecast, timestamps):
    radiation = pd.to_numeric(forecast["shortwave_radiation"], errors="coerce")
    # Open-Meteo podaja povprečje PRETEKLE ure; 13:00 velja za 12:00–13:00.
    hour_starts = pd.to_datetime(forecast["time"]) - pd.Timedelta(hours=1)
    hourly = pd.Series(radiation.to_numpy(), index=hour_starts)
    values = hourly.reindex(pd.DatetimeIndex(timestamps).floor("h"))
    if values.isna().any():
        raise ValueError("Sončna napoved ne vsebuje veljavnega obsevanja za vse intervale.")
    return (values.clip(lower=0, upper=SOLAR_REFERENCE_W_M2)
            / SOLAR_REFERENCE_W_M2 * 100).round().astype(int).tolist()


def charge_fraction(sun_percent, sun_factor):
    return min(1.0, sun_percent / 100 * sun_factor)


def optimize_trades(data: List[Dict[str, Any]], max_positions: float, minimum_profit: float, buy_mask: List[bool], trade_mask: List[bool], initial_position: float = 0, force_flat_end: bool = False, sun_percent=None, *, margin: float, sun_factor: float = 1.2) -> List[Dict[str, Any]]:
    if isinstance(margin, bool) or not isinstance(margin, (int, float)) or not 0 <= margin <= 1:
        raise ValueError("margin mora biti število med 0 in 1 (0.1 = 10 %).")
    if isinstance(sun_factor, bool) or not isinstance(sun_factor, (int, float)) or not math.isfinite(sun_factor) or sun_factor < 0:
        raise ValueError("sun_factor mora biti končno nenegativno število.")
    prices = [float(d["price"]) for d in data]
    T = len(prices)
    scale = ENERGY_STEPS
    K = round(max_positions * scale)
    initial = round(initial_position * scale)
    if K < 0 or not 0 <= initial <= K:
        raise ValueError("Začetna energija mora biti znotraj kapacitete.")
    if sun_percent is None:
        sun_percent = [100] * T
    if not (len(sun_percent) == len(buy_mask) == len(trade_mask) == T):
        raise ValueError("Maske in sončna napoved morajo ustrezati številu intervalov.")
    if any(not 0 <= value <= 100 for value in sun_percent):
        raise ValueError("Odstotek sonca mora biti med 0 in 100.")
    charge_fractions = [charge_fraction(value, sun_factor) for value in sun_percent]
    charge_steps = [round(value * scale) for value in charge_fractions]
    neg = float("-inf")
    future = [0.0 if not force_flat_end or k == 0 else neg for k in range(K + 1)]
    decision = [bytearray(K + 1) for _ in range(T)]

    for t in range(T - 1, -1, -1):
        current = future.copy()
        if trade_mask[t]:
            for k in range(K + 1):
                # Ob polni bateriji se zadnji polnilni interval skrajša.
                added = min(charge_steps[t], K - k)
                if buy_mask[t] and added > 0:
                    value = (-(prices[t] * (1 + margin) + minimum_profit)
                             * added / scale + future[k + added])
                    if value > current[k]:
                        current[k] = value
                        decision[t][k] = 1
                # Zadnji praznilni interval lahko izprazni manj kot poln interval.
                removed = min(scale, k)
                if removed > 0:
                    value = prices[t] * (1 - margin) * removed / scale + future[k - removed]
                    if value > current[k]:
                        current[k] = value
                        decision[t][k] = 2
        future = current

    if future[initial] == neg:
        raise ValueError("Baterije v razpoložljivih intervalih ni mogoče izprazniti.")
    orders = []
    k = initial
    for t in range(T):
        action = decision[t][k]
        amount = 0
        if action == 1:
            amount = min(charge_steps[t], K - k)
            k += amount
        elif action == 2:
            amount = min(scale, k)
            k -= amount
        orders.append({"time": data[t]["time"], "price": prices[t],
                       "order": ("hold", "buy", "sell")[action],
                       "sun_percent": sun_percent[t], "charge_fraction": charge_fractions[t],
                       "energy_fraction": amount / scale})
    return orders


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


def main(capacity, power, minimum_profit, date, lat, lng, from_time, soc, include_next_day: bool = True, use_sun_data: bool = False, *, margin: float, sun_factor: float = 1.2):

    if isinstance(margin, bool) or not isinstance(margin, (int, float)) or not 0 <= margin <= 1:
        raise ValueError("margin mora biti število med 0 in 1 (0.1 = 10 %).")
    if not isinstance(use_sun_data, bool):
        raise ValueError("use_sun_data mora biti boolean.")
    if isinstance(sun_factor, bool) or not isinstance(sun_factor, (int, float)) or not math.isfinite(sun_factor) or sun_factor < 0:
        raise ValueError("sun_factor mora biti končno nenegativno število.")

    capacity = float(capacity)
    power = float(power)
    minimum_profit = float(minimum_profit)
    soc = float(soc)

    if capacity <= 0 or power <= 0 or not 0 <= soc <= 1:
        raise ValueError("Kapaciteta in moč morata biti pozitivni, SOC pa med 0 in 1.")
    intervals_needed = capacity / power * 4
    initial_position = intervals_needed * soc

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

    sun_percent = None  # Brez napovedi: polna moč v dovoljenih dnevnih intervalih.
    if use_sun_data:
        # Dodatni dan zagotovi tudi povprečje za zadnjo uro izbranega dneva.
        forecast_end = ((pd.Timestamp(date_tomorrow) if have_tomorrow else pd.Timestamp(date))
                        + pd.Timedelta(days=1)).strftime("%Y-%m-%d")
        forecast = get_sun_forecast(lat, lng, date, forecast_end)
        sun_percent = solar_percentages(forecast, [ts for ts, _ in combined])

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

    trade_data = [{"time": ts.strftime("%Y-%m-%d %H:%M"), "price": price} for ts, price in combined]

    orders = optimize_trades(
        trade_data,
        max_positions=intervals_needed,
        minimum_profit=minimum_profit,
        buy_mask=buy_mask,
        trade_mask=trade_mask,
        initial_position=initial_position,
        force_flat_end=True,
        sun_percent=sun_percent,
        margin=margin,
        sun_factor=sun_factor,
    )

    charging_times = [{"time": o["time"], "value": o["charge_fraction"]}
                      for o in orders if o["order"] == "buy"]
    discharging_times = [{"time": o["time"], "value": -1}
                         for o in orders if o["order"] == "sell"]

    #za mqtt
    result = {
        "charging": charging_times,
        "discharging": discharging_times,
    }

    #za database
    from_dt = pd.Timestamp(f"{date} {from_time}", tz="Europe/Ljubljana")

    database_data = []
    for order in orders:
        order_time = pd.Timestamp(order["time"], tz="Europe/Ljubljana")

        if order_time < from_dt:
            continue

        if order["order"] == "buy":
            action = order["charge_fraction"]
        elif order["order"] == "sell":
            action = -1
        else:
            action = 0

        database_data.append({
            "device_id": "",
            "timestamp": order["time"],
            "value": action
        })

    suffix_2day = "_2day" if have_tomorrow else ""

    sun_suffix = "_sun" if use_sun_data else ""
    filename_png = "graph_imgs/intervals_" + str(intervals_needed) + "_minprofit_" + str(minimum_profit) + "_date_" + start.strftime("%Y-%m-%d") + suffix_2day + sun_suffix + ".png"
    os.makedirs(Path(filename_png).parent, exist_ok=True)

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
        include_next_day=include_next_day,
        use_sun_data=use_sun_data,
    )

    return result, database_data

if __name__ == "__main__":

    try:
        
        result = main(capacity=10, power=5, minimum_profit=10, date="2026-05-14", lat=46.8894, lng=15.458, from_time="00:00", soc="0.0", include_next_day=False, margin=0.1)
        print("uspelo")
    except Exception as e:
        print("neuspelo")
        print(traceback.format_exc())
