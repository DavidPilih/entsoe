from entsoe import EntsoePandasClient
import requests
import pandas as pd
import os
import tempfile
from dotenv import load_dotenv
import json
from datetime import datetime
from filelock import FileLock

load_dotenv()

API_KEY = os.getenv("ENTSOE_API_KEY")

if not API_KEY:
    raise ValueError("ENTSOE_API_KEY ni nastavljen v .env")

entsoe_client = EntsoePandasClient(api_key=API_KEY)


def scrap_data(filename, start, end):
    lock_path = filename + ".lock"
    os.makedirs(os.path.dirname(filename), exist_ok=True)

    with FileLock(lock_path, timeout=120):

        if os.path.exists(filename):
            print("Podatki že obstajajo (pridobil drug worker):", filename)
            return

        df = None

        try:
            print("Pridobivam cene iz Energy-Charts..." + str(start))

            start_str = pd.Timestamp(start).strftime("%Y-%m-%d")
            end_str = pd.Timestamp(end).strftime("%Y-%m-%d")

            url = "https://api.energy-charts.info/v2/price"
            params = {
                "bzn": "SI",
                "start": start_str,
                "end": end_str
            }

            response = requests.get(url, params=params, timeout=30)
            response.raise_for_status()
            data = response.json()

            records = []
            for item in data.get("data", []):
                ts = item["timestamp"]
                price = item["values"].get("day_ahead_price")
                records.append({"time": ts, "price": price})

            if not records:
                raise ValueError("Energy-Charts ni vrnil nobenih podatkov.")

            df = pd.DataFrame(records)
            df["time"] = pd.to_datetime(df["time"]).dt.tz_localize(None)

            print("Število cen (Energy-Charts):", len(df))

        except Exception as e:
            print(f"Energy-Charts ni uspel ({e}), poskušam ENTSO-E...")

            #ENTSO-E
            print("Pridobivam cene iz ENTSO-E..." + str(start))
            country_code = "SI"
            prices = entsoe_client.query_day_ahead_prices(country_code, start, end)

            print("Število cen (ENTSO-E):", len(prices))

            if prices.empty:
                raise ValueError("Niti Energy-Charts niti ENTSO-E nista vrnila podatkov.")

            df = prices.reset_index()
            df.columns = ["time", "price"]
            df["time"] = df["time"].dt.tz_localize(None)

        # Shranjevanje
        target_dir = os.path.dirname(os.path.abspath(filename))
        fd, tmp_path = tempfile.mkstemp(dir=target_dir, suffix=".xlsx")
        os.close(fd)
        try:
            df.to_excel(tmp_path, index=False, engine="openpyxl")
            os.replace(tmp_path, filename)
        except Exception:
            if os.path.exists(tmp_path):
                os.remove(tmp_path)
            raise

        print("Cene shranjene v:", filename)




def scrap_data_sun(lat, lng, date_start, date_end):
    path = "cache/sun_data/sun_data.json"
    lock_path = path + ".lock"
    os.makedirs(os.path.dirname(path), exist_ok=True)

    loc_key = f"{lat}_{lng}"

    with FileLock(lock_path, timeout=120):
        # Ponovno preveri znotraj locka, morda je drug worker že poskrbel za te podatke
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as file:
                sun_data = json.load(file)
        else:
            sun_data = {}

        print("Pridobivam podatke iz SUNRISE-SUNSET...")

        url = "https://api.sunrisesunset.io/json"
        params = {
            "lat": lat, "lng": lng,
            "date_start": date_start, "date_end": date_end,
            "timezone": "Europe/Ljubljana",
        }

        response = requests.get(url, params=params, timeout=30)
        response.raise_for_status()
        result = response.json()

        for day in result["results"]:
            date_key = day["date"]

            if date_key not in sun_data:
                sun_data[date_key] = {}

            sunrise_time = datetime.strptime(day["sunrise"], "%I:%M:%S %p")
            sunset_time = datetime.strptime(day["sunset"], "%I:%M:%S %p")

            sunrise_minutes = sunrise_time.hour * 60 + sunrise_time.minute
            fwh_minutes = ((sunrise_minutes + 14) // 15) * 15
            sunset_minutes = sunset_time.hour * 60 + sunset_time.minute
            lwh_minutes = (sunset_minutes // 15) * 15

            sun_data[date_key][loc_key] = {
                "fwh": fwh_minutes // 15,
                "lwh": lwh_minutes // 15,
            }

        target_dir = os.path.dirname(os.path.abspath(path))
        fd, tmp_path = tempfile.mkstemp(dir=target_dir, suffix=".json")
        os.close(fd)
        try:
            with open(tmp_path, "w", encoding="utf-8") as file:
                json.dump(sun_data, file, indent=4, ensure_ascii=False)
            os.replace(tmp_path, path)
        except Exception:
            if os.path.exists(tmp_path):
                os.remove(tmp_path)
            raise

        print("Sun podatki shranjeni.")

