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


def scrap_data(filename, country_code, start, end):
    lock_path = filename + ".lock"
    os.makedirs(os.path.dirname(filename), exist_ok=True)

    with FileLock(lock_path, timeout=120):

        if os.path.exists(filename):
            print("Podatki že obstajajo (pridobil drug worker):", filename)
            return

        print("Pridobivam cene iz ENTSO-E..." + str(start))

        prices = entsoe_client.query_day_ahead_prices(country_code, start, end)

        print("Število cen:", len(prices))

        if prices.empty:
            raise ValueError("ENTSO-E ni vrnil nobenih podatkov.")

        df = prices.reset_index()
        df.columns = ["time", "price"]
        df["time"] = df["time"].dt.tz_localize(None)

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
    """
    sun_data.json je EN skupni file za vse workerje in vse (lat,lng,date)
    kombinacije -> potreben je read-modify-write pod istim lockom,
    sicer se writei enega workerja pobrišejo z writei drugega.
    """
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