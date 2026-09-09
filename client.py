import json
import math
import os
import sys
import threading
import queue
from datetime import datetime, timedelta
from concurrent.futures import ThreadPoolExecutor
import pandas as pd
import paho.mqtt.client as mqtt
import psycopg2
from psycopg2.extras import execute_values
from dotenv import load_dotenv
from thingsboard_send_data import send_tb_device
from algo import main

load_dotenv()

topic_inp = "controllers/IQFleks/Entsoe/energy_prices/req"
topic_res = "controllers/IQFleks/Entsoe/energy_prices/res"

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

executor = ThreadPoolExecutor(max_workers=20)
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

def process_request(payload):
    unique_id = payload.get("unique_id")
    try:
        if "help" in payload:
            sendData({"success": True, "unique_id": unique_id, "help": {"required": ["unique_id", "capacity", "power"], "optional": ["minimum_profit", "date", "latitude", "longitude", "start_time", "soc", "next_day", "power_factor"]}})
            return

        required = ["capacity", "power", "unique_id"]
        missing = [key for key in required if key not in payload]

        if missing:
            raise ValueError(f"Manjkajoči podatki: {', '.join(missing)}.")

        now = datetime.now()
        rounded_minute = math.ceil(now.minute / 15) * 15

        if rounded_minute == 60:
            now = now.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)
        else:
            now = now.replace(minute=rounded_minute, second=0, microsecond=0)

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
        next_day = payload.get("next_day", False)
        power_factor = payload.get("power_factor", 1)

        power *= power_factor

        print(f"Začenjam zahtevek: {unique_id}")

        result, database_data = main(capacity, power, minimum_profit, date, lat, lng, from_time, soc, next_day)

        if not isinstance(result, dict):
            result = {"result": result}

        result["success"] = True
        result["unique_id"] = unique_id

        sendData(result)

        device_id = fetch_device_id(unique_id)
        save_result(device_id, database_data)

        database_data = to_data_points(database_data)

        send_tb_device(database_data, device_id)

        print(f"Končan zahtevek: {unique_id}")

    except Exception as e:
        print("NAPAKA:", type(e).__name__, "-", str(e), "- unique_id:", unique_id)
        error = {"success": False, "unique_id": unique_id, "error": type(e).__name__, "message": str(e)}
        try:
            sendData(error)
        except Exception as send_error:
            print("Napaka pri pošiljanju napake:", type(send_error).__name__, "-", str(send_error))

def on_connect(client, userdata, flags, reason_code, properties=None):
    if reason_code == 0:
        client.subscribe(topic_inp)
        print("Uspešno povezan na MQTT")
        print("Poslušam:", topic_inp)
    else:
        print("Napaka pri povezavi z MQTT:", reason_code)

def sendData(result):
    data = json.dumps(result, ensure_ascii=False)
    info = client.publish(topic_res, data)
    if info.rc == mqtt.MQTT_ERR_SUCCESS:
        print("Poslano:", data)
    else:
        print("NAPAKA pri pošiljanju:", mqtt.error_string(info.rc))

def on_message(client, userdata, msg):
    try:
        raw_message = msg.payload.decode()
        print("Prejeto sporočilo:", raw_message)
        payload = json.loads(raw_message)
        executor.submit(process_request, payload)
    except Exception as e:
        print("NAPAKA pri sprejemu:", type(e).__name__, "-", str(e))
        error = {"success": False, "unique_id": None, "error": type(e).__name__, "message": str(e)}
        try:
            sendData(error)
        except Exception as send_error:
            print("Napaka pri pošiljanju napake:", type(send_error).__name__, "-", str(send_error))

client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
client.username_pw_set("iqfleks_mqtt", "iqfleks_pass")
client.on_connect = on_connect
client.on_message = on_message

if __name__ == "__main__":
    init_db()
    db_writer_thread = threading.Thread(target=db_writer_loop, daemon=True)
    db_writer_thread.start()
    try:
        client.connect("10.188.20.3", 1884, 60)
        print("MQTT program zagnan")
        client.loop_forever()
    except KeyboardInterrupt:
        print("Ustavljen")
    except Exception as e:
        print("MQTT program se je ustavil:", type(e).__name__, "-", str(e))
    finally:
        print("Ustavljam ThreadPool...")
        executor.shutdown(wait=True)
        db_queue.put(None)
        db_writer_thread.join(timeout=5)
        try:
            client.disconnect()
        except Exception:
            pass
        print("Program ustavljen")