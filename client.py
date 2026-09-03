import json
import math
from datetime import datetime, timedelta
from concurrent.futures import ThreadPoolExecutor

import paho.mqtt.client as mqtt

from algo import main


topic_inp = "controllers/IQFleks/Entsoe/energy_prices/req"
topic_res = "controllers/IQFleks/Entsoe/energy_prices/res"

executor = ThreadPoolExecutor(max_workers=20)


def process_request(payload):
    unique_id = payload.get("unique_id")

    try:
        if "help" in payload:
            sendData({
                "success": True,
                "unique_id": unique_id,
                "help": {
                    "required": ["unique_id", "capacity", "power", "minimum_profit"],
                    "optional": ["date", "latitude", "longitude", "start_time", "soc", "next_day", "power_factor"]
                }
            })
            return

        required = ["capacity", "power", "minimum_profit", "unique_id"]
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
        minimum_profit = payload["minimum_profit"]
        date = payload.get("date", def_date)
        lat = payload.get("latitude", 46.0569)
        lng = payload.get("longitude", 14.5058)
        from_time = payload.get("start_time", def_time)
        soc = payload.get("soc", 0)
        next_day = payload.get("next_day", False)

        power_factor = payload.get("power_factor", 1)
        power *= power_factor

        print(f"Začenjam zahtevek: {unique_id}")

        result = main(capacity, power, minimum_profit, date, lat, lng, from_time, soc, next_day)

        if not isinstance(result, dict):
            result = {"result": result}

        result["success"] = True
        result["unique_id"] = unique_id

        sendData(result)

        print(f"Končan zahtevek: {unique_id}")

    except Exception as e:
        print("NAPAKA:", type(e).__name__, "-", str(e), "- unique_id:", unique_id)

        error = {
            "success": False,
            "unique_id": unique_id,
            "error": type(e).__name__,
            "message": str(e)
        }

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

        error = {
            "success": False,
            "unique_id": None,
            "error": type(e).__name__,
            "message": str(e)
        }

        try:
            sendData(error)
        except Exception as send_error:
            print("Napaka pri pošiljanju napake:", type(send_error).__name__, "-", str(send_error))


client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
client.username_pw_set("iqfleks_mqtt", "iqfleks_pass")
client.on_connect = on_connect
client.on_message = on_message


if __name__ == "__main__":
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

        try:
            client.disconnect()
        except Exception:
            pass

        print("Program ustavljen")