import json
import paho.mqtt.client as mqtt
import threading
import random
from datetime import datetime, timedelta

client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
topic = "controllers/IQFleks/Entsoe/energy_prices/req"

def on_connect(client, userdata, flags, reason_code, properties=None):
    print("Connected to MQTT")

def connect_to_mqtt():
    client.username_pw_set("iqfleks_mqtt", "iqfleks_pass")
    client.on_connect = on_connect
    client.connect("10.188.20.3", 1884, 60)
    client.loop_start()


def generate_data():
    start_date = datetime(2026, 8, 14)
    end_date = datetime(2026, 8, 19)

    date = start_date + timedelta(
        days=random.randint(0, (end_date - start_date).days)
    )


    
    ratio = random.choice([1,2,3])

    power = round(random.uniform(2, 4), 2)
    capacity = round(power * ratio, 2)


    data = {
        "capacity": capacity,
        "power": power,
        "minimum_profit": round(random.uniform(1, 50), 2),
        "date": date.strftime("%Y-%m-%d"),
        "latitude": round(random.uniform(45.4, 46.9), 4),
        "longitude": round(random.uniform(13.4, 16.6), 4),
        "start_time": f"{random.randint(0, 6):02d}:{random.choice([0, 15, 30, 45]):02d}",
        "soc": round(random.uniform(0, 1), 2),
        "next_day": random.choice([True, False]),
    }

    return data


data = generate_data()

i = 1

def sendData():
    global i

    with open("req_data.json", "r") as file:
        data = json.load(file)
    # data = generate_data()
    # data["unique_id"] = i
    # i += 1

    data = json.dumps(data, ensure_ascii=False)

    client.publish(topic, data)

    print("poslano:", data)

    threading.Timer(10, sendData).start()


def main():
    connect_to_mqtt()

    t = threading.Timer(3, sendData)
    t.start()

    print("pošiljanje podatkov na " + topic)


if __name__ == "__main__":
    print("NEKAJ")
    main()