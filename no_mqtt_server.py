import os
import sys
import json
import random
import threading
from datetime import datetime, timedelta
import time
import psycopg2
import paho.mqtt.client as mqtt
from dotenv import load_dotenv

load_dotenv()

db_username = os.getenv("USER_DB")
db_password = os.getenv("PASSWORD_DB")

mqtt_username = os.getenv("USER_MQTT")
mqtt_password = os.getenv("PASSWORD_MQTT")

if not db_username or not db_password:
    sys.exit(
        "NAPAKA: manjkata USER_DB in/ali PASSWORD_DB.\n"
        "Preveri, da obstaja .env datoteka v isti mapi kot client.py z vsebino:\n"
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

# CAPACITY_KEY = "total_capacity[kWh]"
# POWER_KEY = "max_charge_power[kW]"

CAPACITY_KEY = "total_capacity[kWh]"
POWER_KEY = "max_charge_power[kW]"
SOC_KEY = "SOC[%]"

DEVICES_QUERY = """
SELECT DISTINCT ON (name, kd.key)
       name,
       long_v,
       dbl_v,
       kd.key
FROM ts_kv_latest tkl
JOIN key_dictionary kd ON kd.key_id = tkl."key"
JOIN device d ON tkl.entity_id = d.id
where (kd.key like %s
or kd.key like %s
or kd.key like %s)
and name like '%%Agg%%'
ORDER BY name, kd.key, tkl.ts DESC
"""

client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
MQTT_USER = mqtt_username
MQTT_PASS = mqtt_password
MQTT_HOST = "10.188.20.3"
MQTT_PORT = 1884

topic_inp = "controllers/IQFleks/Entsoe/energy_prices/req"

SEND_INTERVAL_SECONDS = 1


def on_connect(client, userdata, flags, reason_code, properties=None):
    print("Connected to MQTT, reason code:", reason_code)


def connect_to_mqtt():
    client.username_pw_set(MQTT_USER, MQTT_PASS)
    client.on_connect = on_connect
    client.connect(MQTT_HOST, MQTT_PORT, 60)
    client.loop_start()


def fetch_devices_power_capacity():
    devices = {}

    try:
        conn = psycopg2.connect(**DB_CONFIG)
    except Exception as e:
        sys.exit(f"NAPAKA pri povezavi na bazo: {e}")

    try:
        with conn.cursor() as cur:
            params = (CAPACITY_KEY, POWER_KEY, SOC_KEY)

            print("SQL QUERY:")
            print(cur.mogrify(DEVICES_QUERY, params).decode())

            cur.execute(DEVICES_QUERY, params)
            rows = cur.fetchall()

            print("REZULTAT IZ DB:")
            for row in rows:
                print(f"  name={row[0]}, long_v={row[1]}, dbl_v={row[2]}, key={row[3]}")
            print(f"Skupaj vrstic iz DB: {len(rows)}")

    finally:
        conn.close()

    for name, long_v, dbl_v, key in rows:
        if name not in devices:
            devices[name] = {"power": None, "capacity": None, "soc": None}

        value = long_v if long_v is not None else dbl_v

        if key == CAPACITY_KEY:
            devices[name]["capacity"] = value
        elif key == POWER_KEY:
            devices[name]["power"] = value
        elif key == SOC_KEY:
            devices[name]["soc"] = float(value) / 100 if value is not None else None

    print("PROCESIRANE NAPRAVE:")
    for name, vals in devices.items():
        print(f"  {name}: {vals}")

    devices = {
        name: vals
        for name, vals in devices.items()
        if vals["power"] not in (None, 0)
        and vals["capacity"] not in (None, 0)
        and vals["soc"] is not None
    }

    print("NAPRAVE PO FILTRU:")
    for name, vals in devices.items():
        print(f"  {name}: {vals}")

    return devices

def generate_data(name, power, capacity, soc):
    data = {
        "unique_id": name,
        "capacity": capacity,
        "power": power,
        "soc": soc
    }

    return data


def send_for_device(name, power, capacity, soc):
    data = generate_data(name, power, capacity, soc)
    payload = json.dumps(data, ensure_ascii=False)

    client.publish(topic_inp, payload)
    print(f"poslano na {topic_inp}:", payload)


def start_sending_all(devices):
    for name, vals in devices.items():
        delay = random.uniform(0, 1)
        threading.Timer(3 + delay, send_for_device, args=(name, vals["power"], vals["capacity"], vals["soc"])).start()


def seconds_until_next_hour():
    now = datetime.now()
    next_hour = (now + timedelta(hours=1)).replace(minute=0, second=0, microsecond=0)
    return (next_hour - now).total_seconds()


def main():
    connect_to_mqtt()

    while True:
        print("Branje naprav iz baze...")
        devices = fetch_devices_power_capacity()

        if not devices:
            print("Ni najdenih naprav s power in capacity vrednostmi.")
        else:
            print(f"Najdenih {len(devices)} naprav:")
            for name, vals in devices.items():
                print(f"  {name}: power={vals['power']}, capacity={vals['capacity']}, soc={vals['soc']}")

            start_sending_all(devices)
            print("Pošiljanje podatkov zagnano za vse naprave.")
            time.sleep(3 + 1 + 2)

        wait_seconds = seconds_until_next_hour()
        next_run = datetime.now() + timedelta(seconds=wait_seconds)
        print(f"Čakam do naslednje polne ure ({next_run.strftime('%H:%M:%S')}), to je {wait_seconds:.0f}s...")
        time.sleep(wait_seconds)

if __name__ == "__main__":
    main()