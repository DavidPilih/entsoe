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
import psycopg2
from dotenv import load_dotenv

from graph import graph_plot
from algo import optimize_energy



# Ocena: 1000 W/m² pomeni polno razpoložljivo moč polnjenja.
SOLAR_REFERENCE_W_M2 = 1000.0
ENERGY_STEPS = 100  # Ločljivost: 1 % energije enega 15-minutnega intervala.

load_dotenv()


def fetch_solar_parameters(unique_id):
    """Vrne trenutno nazivno moč; ničlo ob communication.error nadomesti s 100 kW."""
    with Path(__file__).with_name('solar_devices.json').open(encoding='utf-8') as stream:
        mapping = json.load(stream)
    solar_name = mapping.get(unique_id)
    if not solar_name:
        raise ValueError(f'Manjka povezava sončne za hranilnik {unique_id!r} v solar_devices.json.')
    solar_id = fetch_consumption_device_id(solar_name)
    conn = psycopg2.connect(**database_config(), connect_timeout=10)
    try:
        conn.set_session(readonly=True)
        with conn.cursor() as cur:
            cur.execute('SELECT t.dbl_v, t.long_v FROM ts_kv_latest t '
                        'JOIN key_dictionary kd ON kd.key_id=t."key" '
                        'WHERE t.entity_id=%s AND kd.key=%s LIMIT 1',
                        (solar_id, 'rated_power[kW]'))
            row = cur.fetchone()
    finally:
        conn.close()
    value = (row[0] if row[0] is not None else row[1]) if row else None
    if value is None or isinstance(value, bool):
        raise ValueError(f'Manjka veljaven rated_power[kW] za {solar_name}.')
    try:
        value = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f'Manjka veljaven rated_power[kW] za {solar_name}.') from exc
    if not math.isfinite(value) or value < 0:
        raise ValueError(f'Manjka veljaven rated_power[kW] za {solar_name}.')
    if value == 0:
        value = 100.0
        print(f'rated_power[kW] za {solar_name} je 0; uporabljam nadomestno moč 100 kW.', flush=True)
    return dict(device_id=str(solar_id), name=solar_name, rated_power_kw=value)


def database_config():
    username = os.getenv("USER_DB")
    password = os.getenv("PASSWORD_DB")
    if not username or not password:
        raise RuntimeError("Manjkata USER_DB in/ali PASSWORD_DB.")
    return {
        "host": "10.188.20.3",
        "port": 5432,
        "dbname": "thingsboard",
        "user": username,
        "password": password,
    }


def timestamp_ms(timestamp):
    value = pd.Timestamp(timestamp)
    if value.tzinfo is None:
        value = value.tz_localize("Europe/Ljubljana")
    return int(value.timestamp() * 1000)


def fetch_consumption_device_id(unique_id):
    conn = psycopg2.connect(**database_config(), connect_timeout=10)
    try:
        conn.set_session(readonly=True)
        with conn.cursor() as cur:
            cur.execute("SELECT id FROM device WHERE name = %s", (unique_id,))
            row = cur.fetchone()
    finally:
        conn.close()
    if row is None:
        raise ValueError(f"Naprava z imenom '{unique_id}' ne obstaja v tabeli device.")
    return row[0]


def fetch_consumption_forecast(device_id, timestamps):
    if not timestamps:
        return []
    expected = [timestamp_ms(timestamp) for timestamp in timestamps]
    conn = psycopg2.connect(**database_config(), connect_timeout=10)
    try:
        conn.set_session(readonly=True)
        with conn.cursor() as cur:
            cur.execute("SET LOCAL statement_timeout = '60s'")
            cur.execute(
                'SELECT t.ts, t.dbl_v, t.long_v FROM ts_kv t '
                'JOIN key_dictionary kd ON kd.key_id=t."key" '
                'WHERE t.entity_id=%s AND kd.key=%s AND t.ts >= %s AND t.ts <= %s',
                (device_id, "forecast_consumption[kW]", min(expected), max(expected)),
            )
            values = {
                int(ts): dbl if dbl is not None else lng
                for ts, dbl, lng in cur.fetchall()
            }
    finally:
        conn.close()

    result = []
    for timestamp in expected:
        value = values.get(timestamp)
        if (isinstance(value, bool) or not isinstance(value, (float, int))
                or not math.isfinite(value)):
            readable = pd.Timestamp(timestamp, unit="ms", tz="UTC").isoformat()
            raise ValueError(
                f"Manjka veljaven forecast_consumption[kW] za {readable}."
            )
        result.append(max(0.0, float(value)))
    return result


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


def optimize_consumption(data, capacity, power, soc, min_soc, max_soc,
                         buy_mask, trade_mask, sun_percent, sun_factor,
                         consumption_kw, tail_kw, margin, minimum_profit, *, solar_kw=None):
    """Compatibility entrypoint. Solar must be supplied as an independent kW series."""
    return optimize_energy(data, capacity, power, soc, min_soc, max_soc,
                           solar_kw if solar_kw is not None else [0.] * len(data),
                           consumption_kw, margin, minimum_profit, active=trade_mask)


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

    start_local = start.tz_localize(None)
    end_local = end.tz_localize(None)
    df = df[(df["time"] >= start_local) & (df["time"] < end_local)]

    return [(row["time"], float(row["price"])) for _, row in df.iterrows()]


def main(capacity, power, minimum_profit, date, lat, lng, from_time, soc, include_next_day: bool = True, use_sun_data: bool = False, *, margin: float, sun_factor: float = 1.2, min_soc: float = 0, max_soc: float = 1, use_consumption=False, unique_id=None, graph_parameters=None, manual_schedule=None, render_graph=True):
    started = time.perf_counter()
    def progress(message):
        print(f"[prepare_data +{time.perf_counter() - started:.2f}s] {message}", flush=True)

    progress("Preverjam vhodne podatke ...")
    if not isinstance(use_consumption, bool):
        raise ValueError('use_consumption mora biti boolean.')

    if isinstance(margin, bool) or not isinstance(margin, (int, float)) or not 0 <= margin <= 1:
        raise ValueError("margin mora biti število med 0 in 1 (0.1 = 10 %).")
    if not isinstance(use_sun_data, bool):
        raise ValueError("use_sun_data mora biti boolean.")
    if isinstance(sun_factor, bool) or not isinstance(sun_factor, (int, float)) or not math.isfinite(sun_factor) or sun_factor < 0:
        raise ValueError("sun_factor mora biti končno nenegativno število.")

    if isinstance(min_soc, bool) or isinstance(max_soc, bool):
        raise ValueError("min_soc in max_soc morata biti števili med 0 in 1.")

    capacity = float(capacity)
    power = float(power)
    minimum_profit = float(minimum_profit)
    soc = float(soc)
    min_soc = float(min_soc)
    max_soc = float(max_soc)

    if not all(math.isfinite(value) for value in (capacity, power, minimum_profit, soc, min_soc, max_soc)):
        raise ValueError("Številčni parametri morajo biti končne vrednosti.")
    if capacity <= 0 or power <= 0 or not 0 <= soc <= 1:
        raise ValueError("Kapaciteta in moč morata biti pozitivni, SOC pa med 0 in 1.")
    if not 0 <= min_soc <= max_soc <= 1:
        raise ValueError("Veljati mora 0 <= min_soc <= max_soc <= 1.")
    soc = min(max(soc, min_soc), max_soc)
    intervals_needed = capacity / power * 4
    initial_position = intervals_needed * soc

    from_hour, from_minute = from_time.split(":")
    from_t = int(from_hour) * 4 + int(from_minute) // 15

    now = pd.Timestamp(date, tz="Europe/Ljubljana")

    start = now + pd.Timedelta(days=0)
    end = now + pd.DateOffset(days=1)

    tomorrow_start = now + pd.DateOffset(days=1)
    tomorrow_end = now + pd.DateOffset(days=2)

    filename = "cache/prices_data/prices_" + start.strftime("%Y-%m-%d") + ".xlsx"

    filename_tomorrow = "cache/prices_data/prices_" + tomorrow_start.strftime("%Y-%m-%d") + ".xlsx"
    progress("Pridobivam današnje cene ...")
    data_today = load_price_data(filename, start, end)
    progress(f"Današnje cene pripravljene ({len(data_today)} intervalov).")

    have_tomorrow = False
    data_tomorrow: List[Tuple[pd.Timestamp, float]] = []

    if include_next_day:
        progress("Pridobivam jutrišnje cene ...")
        try:
            data_tomorrow = load_price_data(filename_tomorrow, tomorrow_start, tomorrow_end)
            if len(data_tomorrow) > 0:
                have_tomorrow = True
        except Exception as e:
            have_tomorrow = False
        progress(f"Jutrišnje cene: {'na voljo' if have_tomorrow else 'niso na voljo'}.")
    else:
        pass
        # print("include_next_day=False, jutrišnji dan se ne preverja.")

    combined = data_today + (data_tomorrow if have_tomorrow else [])
    n_today = len(data_today)

    date_tomorrow = (pd.Timestamp(date) + pd.Timedelta(days=1)).strftime("%Y-%m-%d")

    fwh = lwh = fwh_tom = lwh_tom = None
    progress("Berem povezavo in nazivno moč sončne ...")
    solar = fetch_solar_parameters(unique_id) if use_sun_data else None
    progress(f"Sončna pripravljena: {solar['name'] if solar else 'izključena'}.")
    sun_percent = [0] * len(combined)
    if use_sun_data:
        forecast_end = ((pd.Timestamp(date_tomorrow) if have_tomorrow else pd.Timestamp(date))
                        + pd.Timedelta(days=1)).strftime("%Y-%m-%d")
        progress("Pridobivam vremensko napoved ...")
        forecast = get_sun_forecast(lat, lng, date, forecast_end)
        progress("Vremenska napoved prejeta; pretvarjam v 15-minutne intervale ...")
        sun_percent = solar_percentages(forecast, [ts for ts, _ in combined])
        progress("Sončna napoved pripravljena.")
    solar_kw = [solar['rated_power_kw'] * percent / 100 if solar else 0.
                for percent in sun_percent]
    trade_mask = [i >= n_today or ts.hour * 4 + ts.minute // 15 >= from_t
                  for i, (ts, _) in enumerate(combined)]
    trade_data = [{"time": ts.isoformat(), "price": price} for ts, price in combined]
    loads = [0.] * len(combined)
    if use_consumption:
        if not unique_id:
            raise ValueError('Pri use_consumption=true manjka unique_id naprave.')
        progress("Iščem napravo in berem napoved porabe ...")
        device_id = fetch_consumption_device_id(unique_id)
        active_times = [ts for (ts, _), active in zip(combined, trade_mask) if active]
        values = iter(fetch_consumption_forecast(device_id, active_times))
        loads = [next(values) if active else 0. for active in trade_mask]
        progress(f"Napoved porabe pripravljena ({len(active_times)} aktivnih intervalov).")
    if not combined:
        raise ValueError('Manjkajo podatki cen.')
    # Manual mapping contains battery fractions by Unix timestamp in ms.
    manual = ([manual_schedule.get(timestamp_ms(ts), 0.) for ts, _ in combined]
              if manual_schedule is not None else None)
    progress("Začenjam optimizacijo baterije ...")
    orders = optimize_energy(trade_data, capacity, power, soc, min_soc, max_soc,
                             solar_kw, loads, margin, minimum_profit, active=trade_mask,
                             manual=manual)
    progress("Optimizacija baterije končana.")
    for order, percent in zip(orders, sun_percent):
        order['sun_percent'] = percent

    charging_times = [{"time": o["time"], "value": o["energy_fraction"]}
                      for o in orders if o["order"] == "buy"]
    discharging_times = [{"time": o["time"], "value": -o["energy_fraction"]}
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
            action = order["energy_fraction"]
        elif order["order"] == "sell":
            action = -order["energy_fraction"]
        else:
            action = 0

        database_data.append({
            "device_id": "",
            "timestamp": order["time"],
            "value": action
        })

    result['energy_balance'] = [o for o, active in zip(orders, trade_mask) if active]
    result['solar_device'] = solar

    suffix_2day = "_2day" if have_tomorrow else ""

    sun_suffix = "_sun" if use_sun_data else ""
    filename_png = "graph_imgs/intervals_" + str(intervals_needed) + "_minprofit_" + str(minimum_profit) + "_date_" + start.strftime("%Y-%m-%d") + suffix_2day + sun_suffix + ".png"
    os.makedirs(Path(filename_png).parent, exist_ok=True)

    timestamps = [c[0] for c in combined]
    prices_all = [c[1] for c in combined]
    if render_graph:
        progress("Rišem graf ...")
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
            min_soc=min_soc,
            max_soc=max_soc,
            parameters={
                "device": unique_id, "capacity[kWh]": capacity, "power[kW]": power,
                "soc[%]": soc * 100, "min_soc[%]": min_soc * 100, "max_soc[%]": max_soc * 100,
                "date": date, "start_time": from_time, "next_day": include_next_day,
                "latitude": lat, "longitude": lng, "minimum_profit": minimum_profit,
                "margin": margin, "use_consumption": use_consumption,
                "use_sun_data": use_sun_data, "solar": solar,
                **(graph_parameters or {}),
            },
        )
        progress("Graf končan.")

    progress("Priprava podatkov končana.")
    return result, database_data

if __name__ == "__main__":

    try:
        
        result = main(capacity=10, power=5, minimum_profit=10, date="2026-05-14", lat=46.8894, lng=15.458, from_time="00:00", soc="0.0", include_next_day=False, margin=0.1)
        print("uspelo")
    except Exception as e:
        print("neuspelo")
        print(traceback.format_exc())
