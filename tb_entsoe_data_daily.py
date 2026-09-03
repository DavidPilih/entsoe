import os
import json
import requests
import pandas as pd
from datetime import date, timedelta
from api_client import scrap_data
import thingsboard_send_data

def build_filename(base_dir="cache/prices_data"):
    today = date.today()
    filename = f"prices_{today.year:04d}-{today.month:02d}-{today.day:02d}.xlsx"
    return os.path.join(base_dir, filename)


def get_prices_json(start, end, base_dir="cache/prices_data"):

    filename = build_filename(base_dir)

    if not os.path.exists(filename):
        print("error")
        # scrap_data(filename, start, end)

    df = pd.read_excel(filename, engine="openpyxl")

    if "time" in df.columns:
        df["time"] = pd.to_datetime(df["time"]).astype("datetime64[ms]").astype("int64")

    return json.loads(df.to_json(orient="records"))


def to_data_points(data):

    data_points = []
    for item in data:
        ts = int(item["time"])
        if ts < 10**12:  # manj kot 13 mest => verjetno sekunde, ne ms
            ts *= 1000
        data_points.append({"ts": ts, "values": {"price": item["price"]}})
    return data_points




def main():

    today = pd.Timestamp.now(tz="Europe/Ljubljana").normalize()
    start = today
    end = today + pd.Timedelta(days=1)

    data = get_prices_json(start, end)

    print("Prve 3 vrstice (data):", data[:3])

    data_points = to_data_points(data)
    print("Prve 3 data_points:", data_points[:3])
    ASSET_ID = "62720200-a29d-11f1-b7f5-15bc125d53d2"
    thingsboard_send_data.send_tb_asset(data_points , ASSET_ID)




if __name__ == "__main__":
    main()