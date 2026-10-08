import json
import math
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import pandas as pd
import psycopg2
import paho.mqtt.client as mqtt
import client as request_client

# Skupine naprav, ki jih samodejna urna osvezitev obdeluje.
# Primer za vec skupin: ["03", "04", "05"]
DEVICE_TYPES = ["03"]

DB_CONFIG = request_client.DB_CONFIG
MQTT_USER = os.getenv("USER_MQTT")
MQTT_PASS = os.getenv("PASSWORD_MQTT")
MQTT_HOST = "10.188.20.3"
MQTT_PORT = 1884
topic_inp = "controllers/IQFleks/Entsoe/energy_prices/no_params/req"
topic_res = "controllers/IQFleks/Entsoe/energy_prices/no_params/res"
topic_status = "controllers/IQFleks/Entsoe/energy_prices/no_params/res/status"
WATCHDOG_INTERVAL_SECONDS = 10

# --- Globalne privzete vrednosti (uporabijo se, če naprava podatka nima) ---
DEFAULT_CAPACITY_KWH = float(os.getenv("DEFAULT_CAPACITY_KWH", 215))  # nazivna kapaciteta baterije
DEFAULT_POWER_KW = float(os.getenv("DEFAULT_POWER_KW", 100))          # nazivna moč PCS
DEFAULT_SOC = float(os.getenv("DEFAULT_SOC", 50))                     # začetni SOC v %

_device_locks = {}
_locks_guard = threading.Lock()
_refresh_status = {"successful": [], "unsuccessful": []}
_refresh_status_lock = threading.Lock()
_manual_status = {}
_manual_status_lock = threading.Lock()


def report_manual_status(mqtt_client, device_name, success, message):
    status = {
        "unique_id": device_name,
        "success": success,
        "timestamp": int(pd.Timestamp.now(tz="UTC").timestamp() * 1000),
    }
    with _manual_status_lock:
        _manual_status[device_name] = status
    print(f"{'OK' if success else 'NAPAKA'} ročni urnik {device_name}: {message}")
    try:
        info = mqtt_client.publish(topic_status, json.dumps({
            "event": "manual_schedule", "manual_schedule": {device_name: status},
        }, ensure_ascii=False), retain=False)
        if info.rc != mqtt.MQTT_ERR_SUCCESS:
            print("NAPAKA pri objavi statusa ročnega urnika:", mqtt.error_string(info.rc))
    except Exception as exc:
        print("NAPAKA pri objavi statusa ročnega urnika:", exc)


def device_lock(name):
    with _locks_guard:
        return _device_locks.setdefault(name, threading.Lock())


def set_refresh_status(successful, unsuccessful):
    with _refresh_status_lock:
        _refresh_status["successful"] = list(successful)
        _refresh_status["unsuccessful"] = list(unsuccessful)


def get_refresh_status():
    with _refresh_status_lock:
        return {
            "successful": list(_refresh_status["successful"]),
            "unsuccessful": list(_refresh_status["unsuccessful"]),
        }


def to_float(value):
    """Vrne float ali None, če vrednost ni veljavno število."""
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if math.isnan(number) or math.isinf(number):
        return None
    return number


def resolve_parameters(name, device_id):
    """Prebere capacity, power, soc; kar manjka ali je neveljavno, nadomesti s privzetim."""
    try:
        raw_capacity, raw_power, raw_soc = request_client.fetch_device_parameters(device_id)
    except Exception as exc:
        print(
            f"NAPAKA pri urni osvežitvi {name!r}: naprava nima vseh veljavnih "
            f"parametrov za kapaciteto, moč in SOC ({type(exc).__name__}: {exc}). "
            "Uporabljam privzete vrednosti."
        )
        return DEFAULT_CAPACITY_KWH, DEFAULT_POWER_KW, DEFAULT_SOC

    problems = []
    capacity = to_float(raw_capacity)
    if capacity is None or capacity <= 0:
        problems.append(f"total_capacity[kWh]={raw_capacity!r}")
        capacity = DEFAULT_CAPACITY_KWH

    power = to_float(raw_power)
    if power is None or power <= 0:
        problems.append(f"max_charge_power[kW]={raw_power!r}")
        power = DEFAULT_POWER_KW

    soc = to_float(raw_soc)
    if soc is None or soc < 0 or soc > 100:
        problems.append(f"SOC[%]={raw_soc!r}")
        soc = DEFAULT_SOC

    if problems:
        print(
            f"NAPAKA pri urni osvežitvi {name!r}: manjkajoči ali neveljavni "
            f"parametri: {', '.join(problems)}. Uporabljam privzete vrednosti."
        )

    return capacity, power, soc


def fetch_device_names():
    if not DEVICE_TYPES:
        return []
    device_patterns = [f"%{device_type}%" for device_type in DEVICE_TYPES]
    conn = psycopg2.connect(**DB_CONFIG)
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT DISTINCT name FROM device "
                "WHERE name LIKE ANY(%s) AND name LIKE %s "
                "AND name NOT LIKE %s ORDER BY name",
                (device_patterns, "%enerArk%", "%controllers%"), #02, Agg, controllers
            )
            return [row[0] for row in cur.fetchall()]
    finally:
        conn.close()


def wait_for_writes(writer_thread):
    pending = request_client.db_queue
    with pending.all_tasks_done:
        while pending.unfinished_tasks:
            if not writer_thread.is_alive():
                raise RuntimeError("Zapisovalna nit baze ne deluje.")
            pending.all_tasks_done.wait(timeout=0.5)


def update_device(name, device_id, writer_thread, payload=None):
    started = time.perf_counter()
    print(f"[server {name} +0.00s] Berem parametre hranilnika ...", flush=True)
    if not writer_thread.is_alive():
        raise RuntimeError("Zapisovalna nit baze ne deluje.")
    capacity, power, soc = resolve_parameters(name, device_id)
    print(f"[server {name} +{time.perf_counter() - started:.2f}s] Parametri prebrani.", flush=True)
    request = dict(payload or {})
    request["unique_id"] = name
    request.setdefault("capacity", capacity)
    request.setdefault("power", power)
    request.setdefault("soc", soc)
    try:
        print(f"[server {name} +{time.perf_counter() - started:.2f}s] Začenjam pripravo in izračun ...", flush=True)
        result = request_client.process_request(request)
        print(f"[server {name} +{time.perf_counter() - started:.2f}s] Izračun in objava končana.", flush=True)
        return result
    finally:
        print(f"[server {name} +{time.perf_counter() - started:.2f}s] Čakam SQL vrsto ...", flush=True)
        wait_for_writes(writer_thread)
        print(f"[server {name} +{time.perf_counter() - started:.2f}s] Zahtevek zaključen.", flush=True)


def refresh_all_devices(mqtt_client, writer_thread, stopping):
    started = time.perf_counter()
    print("[server +0.00s] Iščem naprave za urno osvežitev ...", flush=True)
    device_names = fetch_device_names()
    print(f"[server +{time.perf_counter() - started:.2f}s] Najdene naprave: {device_names}", flush=True)
    successful = []
    unsuccessful = []
    missing_types = [
        device_type
        for device_type in DEVICE_TYPES
        if not any(device_type in name for name in device_names)
    ]
    for device_type in missing_types:
        missing_name = f"{device_type}-Agg"
        message = (
            f"NAPAKA pri urni osvežitvi: naprava tipa {device_type!r} "
            "z oznako 'Agg' ne obstaja."
        )
        print(message)
        send_response(mqtt_client, {
            "success": False,
            "unique_id": missing_name,
            "error": "DeviceNotFound",
            "message": message,
        })
        unsuccessful.append(missing_name)
    if not device_names:
        set_refresh_status(successful, unsuccessful)
        return
    for name in device_names:
        if stopping.is_set():
            break
        try:
            with device_lock(name):
                device_id = request_client.fetch_device_id(name)
                result = update_device(name, device_id, writer_thread)
                send_response(mqtt_client, result)
                successful.append(name)
        except Exception as exc:
            print(f"NAPAKA pri urni osvežitvi {name!r}: {type(exc).__name__}: {exc}")
            send_response(mqtt_client, {
                "success": False,
                "unique_id": name,
                "error": type(exc).__name__,
                "message": str(exc),
            })
            unsuccessful.append(name)
    set_refresh_status(successful, unsuccessful)


def seconds_until_next_hour():
    now = pd.Timestamp.now(tz="UTC")
    next_hour = now.floor("h") + pd.Timedelta(hours=1)
    return (next_hour - now).total_seconds()


def hourly_loop(mqtt_client, writer_thread, stopping):
    while not stopping.is_set():
        try:
            refresh_all_devices(mqtt_client, writer_thread, stopping)
        except Exception as exc:
            print(f"NAPAKA pri urni osvežitvi: {type(exc).__name__}: {exc}")
            set_refresh_status([], [f"{device_type}-Agg" for device_type in DEVICE_TYPES])
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
            device_id = request_client.fetch_device_id(unique_id)
            return update_device(unique_id, device_id, writer_thread, payload)
    except Exception as exc:
        print(f"NAPAKA za {unique_id!r}: {type(exc).__name__}: {exc}")
        return {"success": False, "unique_id": unique_id,
                "error": type(exc).__name__, "message": str(exc)}


def publish_manual_schedule(mqtt_client, device_name, device_id, entries):
    """Oblikuje shranjene ročne intervale in jih pošlje brez izračuna urnika."""
    try:
        capacity, power, soc = request_client.fetch_device_parameters(device_id)
        today = pd.Timestamp.now(tz="Europe/Ljubljana").normalize()
        tomorrow_ms = request_client.convert_timestamp(today + pd.DateOffset(days=1))
        manual_end = max((pd.Timestamp(ts, unit='ms', tz='UTC').tz_convert('Europe/Ljubljana').normalize()
                          + pd.DateOffset(days=1) for ts, _ in entries), default=today + pd.DateOffset(days=1))
        result = request_client.process_request({
            "unique_id": device_name, "capacity": capacity, "power": power, "soc": soc,
            "next_day": any(ts >= tomorrow_ms for ts, _ in entries),
        }, manual_end=manual_end)
        data = result["data"]
        send_response(mqtt_client, result, raise_on_error=True)
    except Exception as exc:
        report_manual_status(mqtt_client, device_name, False, f"{type(exc).__name__}: {exc}")
        return
    report_manual_status(mqtt_client, device_name, True,
                         f"Urnik shranjen; MQTT je sprejel objavo {len(data)} intervalov.")


def send_response(client, result, *, raise_on_error=False):
    try:
        unique_id = result.get("unique_id") if isinstance(result, dict) else None
        group = unique_id.split("-", 1)[0].strip() if isinstance(unique_id, str) else ""
        response_topic = f"{topic_res}/{group or 'unknown'}"
        info = client.publish(response_topic, json.dumps(result, ensure_ascii=False), retain=False)
        if info.rc != mqtt.MQTT_ERR_SUCCESS:
            raise RuntimeError(mqtt.error_string(info.rc))
    except Exception as exc:
        if raise_on_error:
            raise
        print("NAPAKA pri pošiljanju:", exc)


def watchdog_loop(client, stopping):
    watchdog = True
    while not stopping.is_set():
        try:
            refresh_status = get_refresh_status()
            with _manual_status_lock:
                manual_status = dict(_manual_status)
            payload = {
                "watchdog": watchdog,
                "successful": refresh_status["successful"],
                "unsuccessful": refresh_status["unsuccessful"],
                "manual_schedule": manual_status,
            }
            info = client.publish(topic_status, json.dumps(payload), retain=False)
            if info.rc != mqtt.MQTT_ERR_SUCCESS:
                print("NAPAKA watchdog:", mqtt.error_string(info.rc))
        except Exception as exc:
            print("NAPAKA watchdog:", exc)
        watchdog = not watchdog
        if stopping.wait(WATCHDOG_INTERVAL_SECONDS):
            break


def process_message(client, writer_thread, payload):
    send_response(client, get_device_response(payload, writer_thread))


def on_connect(client, userdata, flags, reason_code, properties=None):
    if reason_code == 0:
        client.subscribe(topic_inp)
        client.subscribe(topic_status)
        print("Poslušam:", topic_inp)
    else:
        print("NAPAKA povezave MQTT:", reason_code)


def on_message(client, userdata, msg):
    if userdata["stopping"].is_set():
        return
    try:
        payload = json.loads(msg.payload.decode("utf-8"))
        if msg.topic == topic_status:
            if isinstance(payload, dict) and payload.get("event") == "manual_schedule":
                statuses = payload.get("manual_schedule", {})
                if isinstance(statuses, dict):
                    for name, status in statuses.items():
                        if isinstance(status, dict) and status.get("unique_id") == name:
                            with _manual_status_lock:
                                _manual_status[name] = status
                            print(f"Ročni urnik {name}: {status.get('message', '')}")
            return
        userdata["executor"].submit(process_message, client, userdata["writer"], payload)
    except Exception as exc:
        send_response(client, {"success": False, "unique_id": None,
                               "error": type(exc).__name__, "message": str(exc)})


def main():
    stopping = threading.Event()
    executor = ThreadPoolExecutor(max_workers=20)
    writer = None
    scheduler = None
    watchdog = None
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
        scheduler = threading.Thread(target=hourly_loop, args=(client, writer, stopping))
        scheduler.start()
        watchdog = threading.Thread(target=watchdog_loop, args=(client, stopping))
        watchdog.start()
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
        if watchdog is not None:
            watchdog.join()
        executor.shutdown(wait=True)
        if writer is not None and writer.is_alive():
            request_client.db_queue.put(None)
            writer.join()
        client.disconnect()
        client.loop_stop()


if __name__ == "__main__":
    main()
