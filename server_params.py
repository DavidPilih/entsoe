import json
import threading
from pathlib import Path

import paho.mqtt.client as mqtt


client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
topic = "controllers/IQFleks/Entsoe/energy_prices/paramss/req"
REQUESTS_FILE = Path(__file__).with_name("requests_params.json")


def on_connect(client, userdata, flags, reason_code, properties=None):
    print("Connected to MQTT, reason code:", reason_code)


def connect_to_mqtt():
    client.username_pw_set("iqfleks_mqtt", "iqfleks_pass")
    client.on_connect = on_connect
    client.connect("10.188.20.3", 1884, 60)
    client.loop_start()


def sendData():
    try:
        with REQUESTS_FILE.open("r", encoding="utf-8") as file:
            data = json.load(file)
        if not isinstance(data, dict):
            raise ValueError("requests_params.json mora vsebovati en JSON objekt.")
        payload = json.dumps(data, ensure_ascii=False)
        info = client.publish(topic, payload)
        if info.rc == mqtt.MQTT_ERR_SUCCESS:
            print("Poslano na", topic, ":", payload)
        else:
            print("NAPAKA pri pošiljanju:", mqtt.error_string(info.rc))
    except Exception as exc:
        print(f"NAPAKA: {type(exc).__name__}: {exc}")
    finally:
        threading.Timer(10, sendData).start()


def main():
    connect_to_mqtt()
    threading.Timer(3, sendData).start()
    print("Pošiljanje podatkov na", topic)


if __name__ == "__main__":
    main()
