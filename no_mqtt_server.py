import json
import os
import threading
from concurrent.futures import ThreadPoolExecutor

import pandas as pd
import psycopg2
import paho.mqtt.client as mqtt
import client as request_client

DB_CONFIG = request_client.DB_CONFIG
MQTT_USER = os.getenv("USER_MQTT")
MQTT_PASS = os.getenv("PASSWORD_MQTT")
MQTT_HOST = "10.188.20.3"
MQTT_PORT = 1884
topic_inp = "controllers/IQFleks/Entsoe/energy_prices/no_params/req"
topic_res = "controllers/IQFleks/Entsoe/energy_prices/no_params/res"

_device_locks = {}
_locks_guard = threading.Lock()


def device_lock(name):
    with _locks_guard:
        return _device_locks.setdefault(name, threading.Lock())


def fetch_device_names():
    conn = psycopg2.connect(**DB_CONFIG)
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT DISTINCT name FROM device WHERE name LIKE %s AND name LIKE %s AND name NOT LIKE %s ORDER BY name", ("%02%", "%Agg%", "%controllers%")) 
            return [row[0] for row in cur.fetchall()] 
    finally:
        conn.close()


def fetch_today_schedule(device_id, start, end):
    conn = psycopg2.connect(**DB_CONFIG)
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT timestamp, auto FROM device_energy_schedule "
                "WHERE device_id = %s AND timestamp >= %s AND timestamp < %s "
                "ORDER BY timestamp",
                (device_id, int(start.timestamp() * 1000), int(end.timestamp() * 1000)),
            )
            return [{"ts": int(ts), "value": float(value)} for ts, value in cur.fetchall()
                    if value is not None]
    finally:
        conn.close()


def wait_for_writes(writer_thread):
    pending = request_client.db_queue
    with pending.all_tasks_done:
        while pending.unfinished_tasks:
            if not writer_thread.is_alive():
                raise RuntimeError("Zapisovalna nit baze ne deluje.")
            pending.all_tasks_done.wait(timeout=0.5)


def update_device(name, device_id, writer_thread):
    if not writer_thread.is_alive():
        raise RuntimeError("Zapisovalna nit baze ne deluje.")
    capacity, power, soc = request_client.fetch_device_parameters(device_id)
    try:
        return request_client.process_request(
            {"unique_id": name, "capacity": capacity, "power": power, "soc": soc}
        )
    finally:
        wait_for_writes(writer_thread)


def refresh_all_devices(writer_thread, stopping):
    device_names = fetch_device_names()
    for name in device_names:
        if stopping.is_set():
            break
        try:
            with device_lock(name):
                device_id = request_client.fetch_device_id(name)
                update_device(name, device_id, writer_thread)
        except Exception as exc:
            print(f"NAPAKA pri urni osvežitvi {name!r}: {type(exc).__name__}: {exc}")


def seconds_until_next_hour():
    now = pd.Timestamp.now(tz="UTC")
    next_hour = now.floor("h") + pd.Timedelta(hours=1)
    return (next_hour - now).total_seconds()


def hourly_loop(writer_thread, stopping):
    while not stopping.is_set():
        try:
            refresh_all_devices(writer_thread, stopping)
        except Exception as exc:
            print(f"NAPAKA pri urni osvežitvi: {type(exc).__name__}: {exc}")
        stopping.wait(seconds_until_next_hour())


def get_device_response(payload, writer_thread):
    unique_id = None
    try:
        if not isinstance(payload, dict):
            raise ValueError("Zahtevek mora biti JSON objekt z unique_id.")
        unique_id = payload.get("unique_id")
        if not isinstance(unique_id, str) or not unique_id.strip():
            raise ValueError("unique_id mora biti neprazno ime naprave.")
        with device_lock(unique_id):
            start = pd.Timestamp.now(tz="Europe/Ljubljana").normalize()
            end = start + pd.DateOffset(days=1)
            device_id = request_client.fetch_device_id(unique_id)
            data = fetch_today_schedule(device_id, start, end)
            if not data:
                update_device(unique_id, device_id, writer_thread)
                data = fetch_today_schedule(device_id, start, end)
                if not data:
                    raise ValueError("Po izračunu v bazi ni razporeda za današnji dan.")
        return {"success": True, "unique_id": unique_id, "data": data}
    except Exception as exc:
        print(f"NAPAKA za {unique_id!r}: {type(exc).__name__}: {exc}")
        return {"success": False, "unique_id": unique_id,
                "error": type(exc).__name__, "message": str(exc)}


def send_response(client, result):
    try:
        info = client.publish(topic_res, json.dumps(result, ensure_ascii=False), retain=False)
        if info.rc != mqtt.MQTT_ERR_SUCCESS:
            print("NAPAKA pri pošiljanju:", mqtt.error_string(info.rc))
    except Exception as exc:
        print("NAPAKA pri pošiljanju:", exc)


def process_message(client, writer_thread, payload):
    send_response(client, get_device_response(payload, writer_thread))


def on_connect(client, userdata, flags, reason_code, properties=None):
    if reason_code == 0:
        client.subscribe(topic_inp)
        print("Poslušam:", topic_inp)
    else:
        print("NAPAKA povezave MQTT:", reason_code)


def on_message(client, userdata, msg):
    if userdata["stopping"].is_set():
        return
    try:
        payload = json.loads(msg.payload.decode("utf-8"))
        userdata["executor"].submit(process_message, client, userdata["writer"], payload)
    except Exception as exc:
        send_response(client, {"success": False, "unique_id": None,
                               "error": type(exc).__name__, "message": str(exc)})


def main():
    stopping = threading.Event()
    executor = ThreadPoolExecutor(max_workers=20)
    writer = None
    scheduler = None
    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
    try:
        request_client.init_db()
        writer = threading.Thread(target=request_client.db_writer_loop, daemon=True)
        writer.start()
        client.username_pw_set(MQTT_USER, MQTT_PASS)
        client.user_data_set({"executor": executor, "writer": writer, "stopping": stopping})
        client.on_connect = on_connect
        client.on_message = on_message
        client.connect(MQTT_HOST, MQTT_PORT, 60)
        client.loop_start()
        scheduler = threading.Thread(target=hourly_loop, args=(writer, stopping))
        scheduler.start()
        while not stopping.wait(1):
            if not writer.is_alive():
                raise RuntimeError("Zapisovalna nit baze se je ustavila.")
    except KeyboardInterrupt:
        print("Ustavljanje strežnika...")
    except Exception as exc:
        print(f"NAPAKA strežnika: {type(exc).__name__}: {exc}")
    finally:
        stopping.set()
        if scheduler is not None:
            scheduler.join()
        executor.shutdown(wait=True)
        if writer is not None and writer.is_alive():
            request_client.db_queue.put(None)
            writer.join()
        client.disconnect()
        client.loop_stop()


if __name__ == "__main__":
    main()
