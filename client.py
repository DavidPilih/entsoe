import math
import os
import sys
import queue
import pandas as pd
import psycopg2
from psycopg2.extras import execute_values
from dotenv import load_dotenv
from thingsboard_send_data import send_tb_device
from algo import main

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
            rows = [(device_id, convert_timestamp(entry["timestamp"]), entry.get("value")) for entry in database_data]
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
        db_queue.put((device_id, database_data))

def to_data_points(data):
    return [{"ts": convert_timestamp(item["timestamp"]), "values": {"schedule_auto": item["value"]}} for item in data]

def fetch_consumption_forecast(device_id, timestamps):
    if not timestamps:
        return []
    expected = [convert_timestamp(t) for t in timestamps]
    conn = psycopg2.connect(**DB_CONFIG, connect_timeout=10)
    try:
        conn.set_session(readonly=True)
        with conn.cursor() as cur:
            cur.execute("SET LOCAL statement_timeout = '60s'")
            cur.execute('SELECT t.ts, t.dbl_v, t.long_v FROM ts_kv t '
                        'JOIN key_dictionary kd ON kd.key_id=t."key" '
                        'WHERE t.entity_id=%s AND kd.key=%s AND t.ts >= %s AND t.ts <= %s',
                        (device_id, 'forecast_consumption_upper', min(expected), max(expected)))
            values = {int(ts): dbl if dbl is not None else lng for ts, dbl, lng in cur.fetchall()}
    finally:
        conn.close()
    result = []
    for ts in expected:
        value = values.get(ts)
        if isinstance(value, bool) or not isinstance(value, (float, int)) or not math.isfinite(value):
            raise ValueError(f'Manjka veljaven forecast_consumption_upper za {pd.Timestamp(ts, unit="ms", tz="UTC").isoformat()}.')
        result.append(max(0., float(value)))
    return result


def process_request(payload):
    if not isinstance(payload, dict):
        raise ValueError("Zahtevek mora biti slovar.")
    unique_id = payload.get("unique_id")
    required = ["capacity", "power", "unique_id"]
    missing = [key for key in required if key not in payload]

    if missing:
        raise ValueError(f"Manjkajoči podatki: {', '.join(missing)}.")

    use_consumption = payload.get('use_consumption', False)
    if not isinstance(use_consumption, bool):
        raise ValueError('use_consumption mora biti boolean.')
    consumption_args = {}
    device_id = None
    if use_consumption:
        device_id = fetch_device_id(unique_id)
        consumption_args = dict(use_consumption=True,
                                consumption_loader=lambda times: fetch_consumption_forecast(device_id, times))


    now = pd.Timestamp.now(tz="UTC").ceil("15min").tz_convert("Europe/Ljubljana")
    def_date = now.strftime("%Y-%m-%d")
    def_time = now.strftime("%H:%M")

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
    power *= power_factor

    print(f"Začenjam zahtevek: {unique_id}")

    calculation, database_data = main(
        capacity, power, minimum_profit, date, lat, lng, from_time, soc, next_day,
        use_sun_data=use_sun_data, margin=margin, sun_factor=sun_factor,
        min_soc=min_soc, max_soc=max_soc,
        **consumption_args,
    )

    result = {
        "success": True,
        "unique_id": unique_id,
        "data": [
            {"timestamp": convert_timestamp(item["timestamp"]), "value": item["value"]}
            for item in database_data
        ],
    }

    if use_consumption:
        result['energy_balance'] = calculation['energy_balance']
    if device_id is None:
        device_id = fetch_device_id(unique_id)
    save_result(device_id, database_data)
    send_tb_device(to_data_points(database_data), device_id)
    print(f"Končan zahtevek: {unique_id}")
    return result


def fetch_device_parameters(device_id):
    keys = ("total_capacity[kWh]", "max_charge_power[kW]", "SOC[%]")
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
