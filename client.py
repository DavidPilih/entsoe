import math
import os
import sys
import queue
import json
import time
from datetime import date, timedelta
import pandas as pd
import psycopg2
from psycopg2.extras import execute_values
from dotenv import load_dotenv
from thingsboard_send_data import send_tb_device
from prepare_data import main

load_dotenv()

db_username = os.getenv("USER_DB")
db_password = os.getenv("PASSWORD_DB")

if not db_username or not db_password:
    sys.exit("NAPAKA: manjkata USER_DB in/ali PASSWORD_DB.")

DB_CONFIG = {
    "host": "10.188.20.3",
    "port": 5432,
    "dbname": "thingsboard",
    "user": db_username,
    "password": db_password,
}

db_queue = queue.Queue()

def init_db():
    conn = psycopg2.connect(**DB_CONFIG)
    conn.close()

def fetch_device_id(name):
    conn = psycopg2.connect(**DB_CONFIG)
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT id FROM device WHERE name = %s", (name,))
            row = cur.fetchone()
    finally:
        conn.close()
    if row is None:
        raise ValueError(f"Naprava z imenom '{name}' ne obstaja v tabeli device.")
    return row[0]

def convert_timestamp(timestamp):
    ts = pd.to_datetime(timestamp)
    if ts.tzinfo is None:
        ts = ts.tz_localize("Europe/Ljubljana")
    return int(ts.timestamp() * 1000)

def fetch_schedule(device_id, from_time, next_day=False):
    today = pd.Timestamp.now(tz="Europe/Ljubljana").normalize()
    start_ts = convert_timestamp(f"{today.strftime('%Y-%m-%d')} {from_time}")
    end_ts = convert_timestamp(today + pd.DateOffset(days=2 if next_day else 1))
    conn = psycopg2.connect(**DB_CONFIG)
    try:
        with conn.cursor() as cur:
            cur.execute(
                'SELECT "timestamp", "protocol" FROM device_energy_schedule '
                'WHERE device_id = %s AND "protocol" IS NOT NULL '
                'AND "timestamp" >= %s AND "timestamp" < %s '
                'ORDER BY "timestamp"',
                (device_id, start_ts, end_ts),
            )
            return [{"ts": ts, "protocol": protocol} for ts, protocol in cur.fetchall()]
    finally:
        conn.close()


def fetch_manual_schedule(device_id, start, end):
    """Prebere tudi ročne ukaze pred from_time, saj določajo lastništvo dneva."""
    conn = psycopg2.connect(**DB_CONFIG)
    try:
        with conn.cursor() as cur:
            cur.execute(
                'SELECT "timestamp", manual FROM device_energy_schedule '
                'WHERE device_id = %s AND manual IS NOT NULL '
                'AND "timestamp" >= %s AND "timestamp" < %s '
                'ORDER BY "timestamp"',
                (device_id, convert_timestamp(start), convert_timestamp(end)),
            )
            return {int(ts): value for ts, value in cur.fetchall()}
    finally:
        conn.close()


def schedule_end_soc(schedule, soc, capacity, power, min_soc, max_soc, forecast=None):
    """Ocena SOC po 15-minutnih ukazih, z omejitvijo po vsakem ukazu."""
    soc = min(max(float(soc), float(min_soc)), float(max_soc))
    for item in schedule:
        soc += float(item["value"]) * float(power) * 0.25 / float(capacity)
        soc = min(max(soc, float(min_soc)), float(max_soc))
        if forecast is not None:
            forecast.append({
                "ts": convert_timestamp(item["timestamp"]) + 15 * 60 * 1000,
                "values": {"forecasted_soc[%]": soc * 100},
            })
    return soc


def fetch_device_regimes(device_id):
    conn = psycopg2.connect(**DB_CONFIG)
    try:
        with conn.cursor() as cur:
            cur.execute(
                'SELECT a.json_v, a.str_v FROM attribute_kv a '
                'JOIN key_dictionary kd ON kd.key_id = a.attribute_key '
                'WHERE a.entity_id = %s AND a.attribute_type = %s '
                'AND kd.key = %s ORDER BY a.last_update_ts DESC LIMIT 1',
                (device_id, 3, "device_energy_protocol"),
            )
            row = cur.fetchone()
    finally:
        conn.close()

    if row is None:
        raise ValueError("Naprava nima shared atributa device_energy_protocol.")
    value = row[0] if row[0] is not None else row[1]
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError as exc:
            raise ValueError("Atribut device_energy_protocol ni veljaven JSON.") from exc
    if not isinstance(value, dict):
        raise ValueError("Atribut device_energy_protocol mora biti JSON objekt.")
    return value


def easter_sunday(year):
    a = year % 19
    b = year // 100
    c = year % 100
    d = b // 4
    e = b % 4
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i = c // 4
    k = c % 4
    length = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * length) // 451
    month = (h + length - 7 * m + 114) // 31
    day = (h + length - 7 * m + 114) % 31 + 1
    return date(year, month, day)


def is_slovenian_holiday(target_date):
    fixed_holidays = {
        (1, 1), (1, 2), (2, 8), (4, 27), (5, 1), (5, 2),
        (6, 25), (8, 15), (10, 31), (11, 1), (12, 25), (12, 26),
    }
    easter = easter_sunday(target_date.year)
    movable_holidays = {easter, easter + timedelta(days=1), easter + timedelta(days=49)}
    return (target_date.month, target_date.day) in fixed_holidays or target_date in movable_holidays


def regime_for_date(regimes, target_date):
    date_key = target_date.isoformat()
    temporary = regimes.get("zacasno", [])
    if isinstance(temporary, list):
        for item in temporary:
            if (isinstance(item, dict) and item.get("datum") == date_key
                    and isinstance(item.get("rezim"), str) and item["rezim"]):
                return item["rezim"]

    permanent = regimes.get("stalno")
    if not isinstance(permanent, dict):
        permanent = regimes
    if is_slovenian_holiday(target_date):
        return permanent.get("prazniki") or permanent.get("holidays")

    weekday_keys = (
        ("pon", "monday"),
        ("tor", "tuesday"),
        ("sre", "wednesday"),
        ("cet", "thursday"),
        ("pet", "friday"),
        ("sob", "saturday"),
        ("ned", "sunday"),
    )
    current_key, legacy_key = weekday_keys[target_date.weekday()]
    return permanent.get(current_key) or permanent.get(legacy_key)


def selected_device_regimes(regimes, next_day=False, today=None):
    if today is None:
        today = pd.Timestamp.now(tz="Europe/Ljubljana").date()
    dates = [today]
    if next_day:
        dates.append(today + timedelta(days=1))
    return {
        target_date.isoformat(): regime_for_date(regimes, target_date)
        for target_date in dates
    }

def db_writer_loop():
    conn = psycopg2.connect(**DB_CONFIG)
    conn.autocommit = False
    while True:
        item = db_queue.get()
        if item is None:
            db_queue.task_done()
            break
        device_id, database_data = item
        try:
            rows = [
                (device_id, convert_timestamp(entry["timestamp"]),
                 None if entry.get("value") is None else float(entry["value"]))
                for entry in database_data
            ]
            with conn.cursor() as cur:
                execute_values(cur, "INSERT INTO device_energy_schedule (device_id, timestamp, auto) VALUES %s ON CONFLICT (device_id, timestamp) DO UPDATE SET auto = EXCLUDED.auto", rows)
            conn.commit()
            print(f"Shranjenih {len(rows)} vrstic v bazo za device_id={device_id}")
        except Exception as e:
            conn.rollback()
            print("NAPAKA pri shranjevanju v bazo:", type(e).__name__, "-", str(e))
        finally:
            db_queue.task_done()
    conn.close()

def save_result(device_id, database_data):
    if database_data:
        normalized = [
            {
                **entry,
                "value": None if entry.get("value") is None else float(entry["value"]),
            }
            for entry in database_data
        ]
        db_queue.put((device_id, normalized))

def to_data_points(data):
    return [{"ts": convert_timestamp(item["timestamp"]), "values": {"schedule_auto": item["value"]}} for item in data]


def balance_data_points(balance):
    return [{"ts": convert_timestamp(item['time']), "values": {
        'forecast_solar[kW]': item['solar_kw'],
        'forecast_battery[kW]': item['battery_kw'],
        'forecast_grid[kW]': item['grid_kw'],
        'forecast_result[EUR]': item['result_eur'],
        'forecast_solar_to_battery[kW]': item['solar_to_battery_kw'],
        'forecast_grid_to_battery[kW]': item['grid_to_battery_kw'],
    }} for item in balance]


def format_schedule_point(timestamp, value, power):
    power_setpoint = round(float(value) * float(power), 3)
    if power_setpoint == 0:
        power_setpoint = 0.0
        operation = "idle"
    elif power_setpoint > 0:
        operation = "charging"
    else:
        operation = "discharging"
    return {
        "timestamp": int(timestamp),
        "power_setpoint[kW]": power_setpoint,
        "operation": operation,
    }


def process_request(payload, *, dry_run=False, manual_end=None):
    started = time.perf_counter()
    def progress(message):
        print(f"[client +{time.perf_counter() - started:.2f}s] {message}", flush=True)

    progress("Začetek obdelave zahtevka.")
    if not isinstance(payload, dict):
        raise ValueError("Zahtevek mora biti slovar.")
    unique_id = payload.get("unique_id")
    required = ["capacity", "power", "unique_id"]
    missing = [key for key in required if key not in payload]

    if missing:
        raise ValueError(f"Manjkajoči podatki: {', '.join(missing)}.")


    device_id = None
    now = pd.Timestamp.now(tz="UTC").ceil("15min").tz_convert("Europe/Ljubljana")
    def_date = now.strftime("%Y-%m-%d")
    def_time = now.strftime("%H:%M")
    # def_time = "00:00"


    capacity = payload["capacity"]
    power = payload["power"]
    minimum_profit = payload.get("minimum_profit", 0.8)
    date = payload.get("date", def_date)
    lat = payload.get("latitude", 46.0569)
    lng = payload.get("longitude", 14.5058)
    from_time = payload.get("start_time", def_time)
    soc = payload.get("soc", 0)
    min_soc = payload.get("min_soc", 0)
    max_soc = payload.get("max_soc", 1)
    next_day = payload.get("next_day", False)
    use_sun_data = payload.get("use_sun_data", True)
    sun_factor = payload.get("sun_factor", 1.2)
    power_factor = payload.get("power_factor", 1)
    margin = payload.get("margin", 0.1)
    use_regime = payload.get("use_regime", False)
    # power_factor določa največjo moč polnjenja in praznjenja.
    power *= power_factor
    use_consumption = payload.get('use_consumption', True)

    print(
        f"Začetni podatki [{unique_id}]: "
        f"capacity={capacity} kWh, power={power} kW (power_factor={power_factor}), "
        f"soc={soc}, min_soc={min_soc}, max_soc={max_soc} (SOC v območju 0–1), "
        f"date={date}, start_time={from_time}, next_day={next_day}, "
        f"latitude={lat}, longitude={lng}, minimum_profit={minimum_profit}, "
        f"margin={margin}, use_consumption={use_consumption}, "
        f"use_sun_data={use_sun_data}, sun_factor={sun_factor}, use_regime={use_regime}"
    )

    schedule = []
    selected_regimes = {}
    if use_regime:
        device_id = fetch_device_id(unique_id)
        device_regimes = fetch_device_regimes(device_id)
        selected_regimes = selected_device_regimes(device_regimes, next_day)

    print(f"Začenjam zahtevek: {unique_id}")

    if device_id is None:
        progress("Iščem ID hranilnika ...")
        device_id = fetch_device_id(unique_id)
        progress("ID hranilnika najden.")
    day = pd.Timestamp(date, tz="Europe/Ljubljana").normalize()
    start = pd.Timestamp(f"{day.strftime('%Y-%m-%d')} {from_time}", tz="Europe/Ljubljana")
    start = start.tz_convert("UTC").ceil("15min").tz_convert("Europe/Ljubljana")
    end = day + pd.DateOffset(days=2 if next_day else 1)
    if manual_end is not None:
        end = max(end, pd.Timestamp(manual_end))
    progress("Berem ročni urnik ...")
    manual = fetch_manual_schedule(device_id, day, end)
    progress(f"Ročni urnik prebran ({len(manual)} zapisov).")
    manual_days = {
        pd.Timestamp(ts, unit="ms", tz="UTC").tz_convert("Europe/Ljubljana").date()
        for ts in manual
    }

    if manual_days:
        periods = []

        for current in pd.date_range(day, end, freq="D", inclusive="left"):
            konec_dneva = current + pd.DateOffset(days=1)

            if konec_dneva > start:
                zacetek_obdobja = max(start, current)
                periods.append((zacetek_obdobja, konec_dneva, False))

    else:
        periods = [(start, end, next_day)] if start < end else []

    database_data = []
    response_data = []
    publish_data = []
    energy_balance = []
    soc_forecast = [{
        "ts": convert_timestamp(start),
        "values": {"forecasted_soc[%]": min(max(float(soc), float(min_soc)), float(max_soc)) * 100},
    }] if periods else []
    solar_device = None
    for period_start, period_end, include_next_day in periods:
        is_manual = period_start.date() in manual_days
        progress(f"Pripravljam obdobje {period_start}–{period_end} ...")
        calculation, calculated = main(
            capacity, power, minimum_profit, period_start.strftime("%Y-%m-%d"),
            lat, lng, period_start.strftime("%H:%M"), soc, include_next_day,
            use_sun_data=use_sun_data, margin=margin, sun_factor=sun_factor,
            min_soc=min_soc, max_soc=max_soc,
            use_consumption=use_consumption, unique_id=unique_id,
            graph_parameters={"power_factor": power_factor, "use_regime": use_regime},
            manual_schedule=manual if is_manual else None, render_graph=not dry_run,
        )
        progress(f"Obdobje izračunano ({len(calculated)} intervalov).")
        start_ms, end_ms = convert_timestamp(period_start), convert_timestamp(period_end)
        calculated = sorted(
            (item for item in calculated
             if start_ms <= convert_timestamp(item["timestamp"]) < end_ms),
            key=lambda item: convert_timestamp(item["timestamp"]),
        )
        solar_device = calculation['solar_device']
        effective = calculated
        # Manual commands retain ownership: never overwrite them or publish auto commands for that day.
        if not is_manual:
            database_data.extend(calculated)
            publish_data.extend(calculated)
        balance = [item for item in calculation["energy_balance"]
                   if start_ms <= convert_timestamp(item["time"]) < end_ms]
        energy_balance.extend(balance)
        soc_forecast.extend({
            "ts": convert_timestamp(item["time"]) + 15 * 60 * 1000,
            "values": {"forecasted_soc[%]": float(item["soc"]) * 100},
        } for item in balance)
        if balance:
            soc = balance[-1]["soc"]
        response_data.extend(effective)

    result = {
        "success": True,
        "unique_id": unique_id,
        "data": [
            format_schedule_point(
                convert_timestamp(item["timestamp"]), item["value"], power
            )
            for item in response_data
        ],
    }

    result['energy_balance'] = energy_balance
    result['solar_device'] = solar_device
    result['dry_run'] = dry_run
    telemetry = to_data_points(publish_data) + soc_forecast + balance_data_points(energy_balance)
    if energy_balance:
        telemetry.append({"ts": convert_timestamp(start), "values": {
            "battery_model_power[kW]": float(power),
            "battery_model_capacity[kWh]": float(capacity),
            "battery_model_min_soc[%]": float(min_soc) * 100,
            "battery_model_max_soc[%]": float(max_soc) * 100,
        }})
    if dry_run:
        result['telemetry'] = telemetry
    if not dry_run:
        progress("Predajam urnik SQL zapisovalniku ...")
        save_result(device_id, database_data)
        progress("SQL korak končan.")
        if telemetry:
            progress(f"Pošiljam {len(telemetry)} telemetrijskih točk v ThingsBoard ...")
            send_tb_device(telemetry, device_id)
            progress("ThingsBoard pošiljanje končano.")
    print(f"Končan zahtevek: {unique_id}")
    progress("Celoten zahtevek končan.")
    return result


def fetch_device_parameters(device_id):
    """Vrne kapaciteto (kWh), moč (kW) in SOC kot delež 0–1."""
    keys = ("max_available_charge_power[kW]", "max_charge_discharge_power[kW]", "SOC[%]")
    conn = psycopg2.connect(**DB_CONFIG)
    try:
        with conn.cursor() as cur:
            cur.execute(
                'SELECT DISTINCT ON (kd.key) kd.key, tkl.long_v, tkl.dbl_v '
                'FROM ts_kv_latest tkl JOIN key_dictionary kd ON kd.key_id = tkl."key" '
                'WHERE tkl.entity_id = %s AND kd.key IN (%s, %s, %s) '
                'ORDER BY kd.key, tkl.ts DESC',
                (device_id, *keys),
            )
            values = {key: long_v if long_v is not None else dbl_v
                      for key, long_v, dbl_v in cur.fetchall()}
    finally:
        conn.close()
    problems = []
    for key in keys:
        value = values.get(key)
        if value is None:
            problems.append(f"{key}: podatek manjka ali je NULL")
        elif not isinstance(value, (int, float)) or not math.isfinite(value):
            problems.append(f"{key}: neveljavna številčna vrednost")
        elif key == "SOC[%]" and not 0 <= value <= 100:
            problems.append(f"{key}: mora biti med 0 in 100")
        elif key != "SOC[%]" and value <= 0:
            problems.append(f"{key}: mora biti večje od 0")
    if problems:
        raise ValueError("; ".join(problems))
    return values[keys[0]], values[keys[1]], float(values[keys[2]]) / 100
