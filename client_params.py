import json
import math
from datetime import datetime, timedelta
from concurrent.futures import ThreadPoolExecutor
import paho.mqtt.client as mqtt
import pandas as pd
from dotenv import load_dotenv
from prepare_data import main

load_dotenv()

topic_inp = "controllers/IQFleks/Entsoe/energy_prices/params/req"
topic_res = "controllers/IQFleks/Entsoe/energy_prices/params/res"

executor = ThreadPoolExecutor(max_workers=20)


def convert_timestamp(timestamp):
    ts = pd.to_datetime(timestamp)
    if ts.tzinfo is None:
        ts = ts.tz_localize("Europe/Ljubljana")
    return int(ts.timestamp() * 1000)

def process_request(payload):
    unique_id = payload.get("unique_id")
    try:
        if "help" in payload:
            sendData({"success": True, "unique_id": unique_id, "help": {"required": ["unique_id", "capacity", "power"], "optional": ["minimum_profit", "date", "latitude", "longitude", "start_time", "soc", "min_soc", "max_soc", "next_day", "power_factor", "use_sun_data", "sun_factor", "margin", "use_consumption"], "defaults": {"min_soc": 0, "max_soc": 1, "use_sun_data": False, "sun_factor": 1.2, "margin": 0.1, "use_consumption": True}}})
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
        min_soc = payload.get("min_soc", 0)
        max_soc = payload.get("max_soc", 1)
        next_day = payload.get("next_day", False)
        use_sun_data = payload.get("use_sun_data", False)
        sun_factor = payload.get("sun_factor", 1.2)

        if not isinstance(use_sun_data, bool):
            raise ValueError("use_sun_data mora biti JSON boolean (true ali false).")
        power_factor = payload.get("power_factor", 1)
        margin = payload.get("margin", 0.1)
        if isinstance(margin, bool) or not isinstance(margin, (int, float)) or not 0 <= margin <= 1:
            raise ValueError("margin mora biti število med 0 in 1 (0.1 = 10 %).")

        power *= power_factor

        print(f"Začenjam zahtevek: {unique_id}")

        _, database_data = main(
            capacity, power, minimum_profit, date, lat, lng, from_time, soc, next_day,
            use_sun_data=use_sun_data, margin=margin, sun_factor=sun_factor,
            min_soc=min_soc, max_soc=max_soc,
            unique_id=unique_id, use_consumption=payload.get('use_consumption', True),
        )

        result = {
            "success": True,
            "unique_id": unique_id,
            "data": [
                {"timestamp": convert_timestamp(item["timestamp"]), "value": item["value"]}
                for item in database_data
            ],
        }

        sendData(result)

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
