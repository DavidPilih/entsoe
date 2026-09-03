import json
import paho.mqtt.client as mqtt
from datetime import datetime
from zoneinfo import ZoneInfo

import thingsboard_send_data


BROKER = "10.188.20.3"
PORT = 1884

TOPIC_RESPONSE = "controllers/IQFleks/Entsoe/energy_prices/res"

USERNAME = "iqfleks_mqtt"
PASSWORD = "iqfleks_pass"

DEVICE_ID = "23a78dc0-8fd7-11f1-b714-4132923502b2"

STEP_MS = 15 * 60 * 1000
SLOTS_PER_DAY = 96

result = None


def interval_to_timestamp(interval):
    year = datetime.now().year
    dt = datetime.strptime(f"{year}-{interval}", "%Y-%m-%d %H:%M")
    dt = dt.replace(tzinfo=ZoneInfo("Europe/Ljubljana"))
    return int(dt.timestamp() * 1000)


def get_day_start_ts(all_ts):
    # vzame najzgodnejši timestamp med vsemi intervali in ga zaokroži na polnoč
    earliest = min(all_ts)
    dt = datetime.fromtimestamp(earliest / 1000, tz=ZoneInfo("Europe/Ljubljana"))
    midnight = dt.replace(hour=0, minute=0, second=0, microsecond=0)
    return int(midnight.timestamp() * 1000)


def convert_intervals(data):
    charging_ts = {interval_to_timestamp(i) for i in data.get("charging_intervals", [])}
    discharging_ts = {interval_to_timestamp(i) for i in data.get("discharging_intervals", [])}

    all_ts = charging_ts | discharging_ts
    if not all_ts:
        raise ValueError("Ni ne charging ne discharging intervalov v prejetih podatkih")

    day_start_ts = get_day_start_ts(all_ts)

    output = []
    for i in range(SLOTS_PER_DAY):
        slot_ts = day_start_ts + i * STEP_MS

        if slot_ts in charging_ts:
            value = 1
        elif slot_ts in discharging_ts:
            value = -1
        else:
            value = 0

        output.append({"ts": slot_ts, "values": {"schedule": value}})

    if len(output) != SLOTS_PER_DAY:
        raise ValueError(f"Pričakovanih {SLOTS_PER_DAY} vrednosti za dan, dobljenih {len(output)}")

    return output


def on_connect(client, userdata, flags, reason_code, properties=None):
    if reason_code == 0:
        print("Uspešno povezan na MQTT")
        client.subscribe(TOPIC_RESPONSE)
        print("Poslušam:")
        print(TOPIC_RESPONSE)
    else:
        print("Napaka pri povezavi z MQTT:", reason_code)


def on_message(client, userdata, msg):
    global result

    try:
        raw_message = msg.payload.decode()

        print("\n==============================")
        print("Prejeto sporočilo:")
        print(raw_message)
        print("==============================")
        result = json.loads(raw_message)

        print("\nShranjeno v result:")
        print(result)

        converted_data = convert_intervals(result)

        print("\nPretvorjeni podatki:")
        print(json.dumps(converted_data, indent=4))

        thingsboard_send_data.send_tb_device(converted_data, DEVICE_ID)

        print("\nPodatki poslani na ThingsBoard.")

    except json.JSONDecodeError as e:
        print("Prejeti message ni veljaven JSON:", e)

    except Exception as e:
        print("NAPAKA:", type(e).__name__, "-", str(e))


client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)

client.username_pw_set(USERNAME, PASSWORD)

client.on_connect = on_connect
client.on_message = on_message


if __name__ == "__main__":

    try:
        client.connect(BROKER, PORT, 60)
        print("Response listener zagnan")
        client.loop_forever()

    except KeyboardInterrupt:
        print("Ustavljen")

    except Exception as e:
        print("MQTT program se je ustavil:", type(e).__name__, "-", str(e))

    finally:
        client.disconnect()
        print("Program ustavljen")