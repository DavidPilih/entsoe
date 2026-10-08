import json
import math
import os
import sys
import queue
import threading
import time
from datetime import datetime, timedelta
from concurrent.futures import ThreadPoolExecutor

import psycopg2
from psycopg2.extras import execute_values
from dotenv import load_dotenv

from prepare_data import main as algo_main

load_dotenv()

db_username = os.getenv("USER_DB")
db_password = os.getenv("PASSWORD_DB")

if not db_username or not db_password:
    sys.exit(
        "NAPAKA: manjkata USER_DB in/ali PASSWORD_DB.\n"
        "Preveri, da obstaja .env datoteka v isti mapi kot ta skript z vsebino:\n"
        "  USER_DB=ime_uporabnika\n"
        "  PASSWORD_DB=geslo"
    )

DB_CONFIG = {
    "host": "10.188.20.3",
    "port": 5432,
    "dbname": "thingsboard",
    "user": db_username,
    "password": db_password,
}

CAPACITY_KEY = "total_capacity[kWh]"
POWER_KEY = "max_charge_power[kW]"

# d.id dodan, da lahko rezultat vsake naprave shranimo pod pravilen device_id
DEVICES_QUERY = """
SELECT DISTINCT ON (name, kd.key)
       name,
       long_v,
       kd.key,
       d.id AS device_id
FROM ts_kv_latest tkl
JOIN key_dictionary kd ON kd.key_id = tkl."key"
JOIN device d ON tkl.entity_id = d.id
where (kd.key like %s
or kd.key like %s)
and name like %s
ORDER BY name, kd.key, tkl.ts DESC
"""

executor = ThreadPoolExecutor(max_workers=20)
db_queue = queue.Queue()


def init_db():
    conn = psycopg2.connect(**DB_CONFIG)
    conn.close()


def db_writer_loop():
    conn = psycopg2.connect(**DB_CONFIG)
    conn.autocommit = False

    while True:
        item = db_queue.get()
        if item is None:  # signal za izhod
            db_queue.task_done()
            break

        device_id, database_data = item
        try:
            rows = [
                (device_id, entry.get("timestamp"), entry.get("value"))
                for entry in database_data
            ]
            with conn.cursor() as cur:
                execute_values(
                    cur,
                    """
                    INSERT INTO device_energy_schedule (device_id, timestamp, auto)
                    VALUES %s
                    ON CONFLICT (device_id, timestamp)
                    DO UPDATE SET auto = EXCLUDED.auto
                    """,
                    rows,
                )
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


def fetch_devices_power_capacity():
    devices = {}

    try:
        conn = psycopg2.connect(**DB_CONFIG)
    except Exception as e:
        sys.exit(f"NAPAKA pri povezavi na bazo: {e}")

    try:
        with conn.cursor() as cur:
            cur.execute(
                DEVICES_QUERY,
                (f"%{CAPACITY_KEY}%", f"%{POWER_KEY}%", "%Agg%"),
            )
            rows = cur.fetchall()
    finally:
        conn.close()

    for name, long_v, key, device_id in rows:
        if name not in devices:
            devices[name] = {"power": None, "capacity": None, "device_id": device_id}

        if key == CAPACITY_KEY:
            devices[name]["capacity"] = long_v
        elif key == POWER_KEY:
            devices[name]["power"] = long_v

    devices = {
        name: vals
        for name, vals in devices.items()
        if vals["power"] not in (None, 0) and vals["capacity"] not in (None, 0)
    }

    return devices


def process_device(name, device_id, power, capacity):
    try:
        now = datetime.now()
        rounded_minute = math.ceil(now.minute / 15) * 15

        if rounded_minute == 60:
            now = now.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)
        else:
            now = now.replace(minute=rounded_minute, second=0, microsecond=0)

        date = now.strftime("%Y-%m-%d")
        from_time = now.strftime("%H:%M")

        minimum_profit = 0.8
        lat = 46.0569
        lng = 14.5058
        soc = 0
        next_day = False

        print(f"Začenjam obdelavo naprave: {name}")

        result, database_data = algo_main(
            capacity, power, minimum_profit, date, lat, lng, from_time, soc, next_day
        )

        if not isinstance(result, dict):
            result = {"result": result}

        result["success"] = True
        result["unique_id"] = name

        print("Rezultat:", json.dumps(result, ensure_ascii=False))
        save_result(device_id, database_data)

        print(f"Končana obdelava naprave: {name}")

    except Exception as e:
        print("NAPAKA pri obdelavi naprave:", name, "-", type(e).__name__, "-", str(e))


def process_all_devices(devices):
    futures = []
    for name, vals in devices.items():
        futures.append(
            executor.submit(
                process_device, name, vals["device_id"], vals["power"], vals["capacity"]
            )
        )
    return futures


def seconds_until_next_hour():
    now = datetime.now()
    next_hour = (now + timedelta(hours=1)).replace(minute=0, second=0, microsecond=0)
    return (next_hour - now).total_seconds()


def main():
    init_db()

    db_writer_thread = threading.Thread(target=db_writer_loop, daemon=True)
    db_writer_thread.start()

    try:
        while True:
            print("Branje naprav iz baze...")
            devices = fetch_devices_power_capacity()

            if not devices:
                print("Ni najdenih naprav s power in capacity vrednostmi.")
            else:
                print(f"Najdenih {len(devices)} naprav:")
                for name, vals in devices.items():
                    print(f"  {name}: power={vals['power']}, capacity={vals['capacity']}, device_id={vals['device_id']}")

                process_all_devices(devices)
                print("Obdelava zagnana za vse naprave.")

            wait_seconds = seconds_until_next_hour()
            next_run = datetime.now() + timedelta(seconds=wait_seconds)
            print(f"Čakam do naslednje polne ure ({next_run.strftime('%H:%M:%S')}), to je {wait_seconds:.0f}s...")
            time.sleep(wait_seconds)

    except KeyboardInterrupt:
        print("Ustavljen")

    finally:
        print("Ustavljam ThreadPool...")
        executor.shutdown(wait=True)

        db_queue.put(None)
        db_writer_thread.join(timeout=5)

        print("Program ustavljen")


if __name__ == "__main__":
    main()
