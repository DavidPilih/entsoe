import os
import json
import requests
import pandas as pd
from datetime import date, timedelta
from api_client import scrap_data, get_sun_forecast
import thingsboard_send_data

def build_filename(base_dir="cache/prices_data"):
    today = date.today()
    filename = f"prices_{today.year:04d}-{today.month:02d}-{today.day:02d}.xlsx"
    return os.path.join(base_dir, filename)


def get_prices_json(start, end, base_dir="cache/prices_data"):

    filename = build_filename(base_dir)

    if not os.path.exists(filename):
        print("error")
        scrap_data(filename, start, end)

    df = pd.read_excel(filename, engine="openpyxl")

    if "time" in df.columns:
        df["time"] = (
            pd.to_datetime(df["time"])
            .dt.tz_localize("Europe/Ljubljana", ambiguous="infer", nonexistent="shift_forward")
            .astype("int64") // 10**6
        )

    return json.loads(df.to_json(orient="records"))


def to_data_points(data):

    data_points = []
    for item in data:
        ts = int(item["time"])
        if ts < 10**12:  # manj kot 13 mest => verjetno sekunde, ne ms
            ts *= 1000
        data_points.append({"ts": ts, "values": {"price": item["price"]}})
    return data_points




def get_sun_data_points(price_points, start, end, lat, lng):
    if not price_points:
        return []

    # Tudi naslednja polnoč: obsevanje je povprečje predhodne ure.
    forecast = get_sun_forecast(
        lat, lng, start.strftime("%Y-%m-%d"), end.strftime("%Y-%m-%d")
    )
    hour_starts = pd.to_datetime(forecast["time"]) - pd.Timedelta(hours=1)
    radiation = pd.to_numeric(forecast["shortwave_radiation"], errors="coerce")
    hourly = pd.Series(radiation.to_numpy(), index=hour_starts)
    timestamps = pd.to_datetime([point["ts"] for point in price_points], unit="ms", utc=True)
    local_hours = timestamps.tz_convert("Europe/Ljubljana").tz_localize(None).floor("h")
    values = hourly.reindex(local_hours)
    if values.isna().any():
        raise ValueError("Sončna napoved ne vsebuje obsevanja za vse časovne žige cen.")

    # Enaka ocena kot v algo.py: 1000 W/m² = 100 %.
    percentages = (values.clip(lower=0, upper=1000) / 1000 * 100).round().astype(int)
    return [
        {"ts": point["ts"], "values": {"sun_percent": int(percent)}}
        for point, percent in zip(price_points, percentages)
    ]


def main(lat=46.0569, lng=14.5058):

    today = pd.Timestamp.now(tz="Europe/Ljubljana").normalize()
    start = today
    end = today + pd.Timedelta(days=1)

    data = get_prices_json(start, end)

    print("Prve 3 vrstice (data):", data[:3])

    data_points = to_data_points(data)
    print("Prve 3 data_points:", data_points[:3])
    ASSET_ID = "62720200-a29d-11f1-b7f5-15bc125d53d2"
    thingsboard_send_data.send_tb_asset(data_points , ASSET_ID)

    sun_data_points = get_sun_data_points(data_points, start, end, lat, lng)
    print("Prve 3 sun_data_points:", sun_data_points[:3])
    thingsboard_send_data.send_tb_asset(sun_data_points, ASSET_ID)



if __name__ == "__main__":
    main()
