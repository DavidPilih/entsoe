from entsoe import EntsoePandasClient
import pandas as pd
from pathlib import Path
import math
import json
import os
from dotenv import load_dotenv
from typing import List, Dict, Any
import matplotlib.pyplot as plt

load_dotenv()

API_KEY = os.getenv("ENTSOE_API_KEY")
MARGIN = 0.1 #V PROCENTIH 0.1=10

if not API_KEY:
    raise ValueError("ENTSOE_API_KEY ni nastavljen v .env")

entsoe_client = EntsoePandasClient(api_key=API_KEY)


def parse_price(p):
    return float(p)


def show_graph():
    plt.show()

def optimize_trades(
    data: List[Dict[str, Any]],
    max_positions: int,
    minimum_profit: float,
    force_flat_end: bool = False,
) -> List[Dict[str, Any]]:

    prices = [parse_price(d["price"]) for d in data]
    times = [d["time"] for d in data]

    T = len(prices)
    K = max_positions
    NEG = float("-inf")

    dp = [
        [0.0] * (K + 1)
        for _ in range(T + 1)
    ]

    decision = [
        [None] * (K + 1)
        for _ in range(T + 1)
    ]

    for k in range(K + 1):
        if force_flat_end and k != 0:
            dp[T][k] = NEG
        else:
            dp[T][k] = 0.0

    for t in range(T - 1, -1, -1):

        price = prices[t]

        for k in range(K + 1):

            # nic
            best_value = dp[t + 1][k]
            best_action = "hold"

            # nakup - tu se zaračuna fiksni strošek trejda
            if k < K:
                value = (
                    -price * (1 + MARGIN)
                    - minimum_profit
                    + dp[t + 1][k + 1]
                )

                if value > best_value:
                    best_value = value
                    best_action = "buy"

            # prodaja
            if k > 0:

                value = (
                    price * (1 - MARGIN)
                    + dp[t + 1][k - 1]
                )

                if value > best_value:
                    best_value = value
                    best_action = "sell"

            dp[t][k] = best_value
            decision[t][k] = best_action

    # Rekonstrukcija odločitev
    actions = []
    k = 0

    for t in range(T):

        act = decision[t][k]

        actions.append(act)

        if act == "buy":
            k += 1

        elif act == "sell":
            k -= 1

    return [
        {
            "time": times[i],
            "price": prices[i],
            "order": actions[i],
        }
        for i in range(T)
    ]


def scrap_data(filename, country_code, start, end):

    print("Pridobivam cene iz ENTSO-E...")

    prices = entsoe_client.query_day_ahead_prices(
        country_code,
        start,
        end,
    )

    print("Število cen:", len(prices))

    if prices.empty:
        raise ValueError("ENTSO-E ni vrnil nobenih podatkov.")

    df = prices.reset_index()

    df.columns = [
        "time",
        "price",
    ]

    # Odstrani timezone
    df["time"] = df["time"].dt.tz_localize(None)

    df.to_excel(
        filename,
        index=False,
    )

    print("Cene shranjene v:", filename)

def graph_plot(data, orders, country_code, start, filename_png):
    print("graph")

    times_labels = [f"{t//4:02d}:{t%4*15:02d}" for t, v in data]
    prices_all = [v for t, v in data]

    buy_x = [i for i, o in enumerate(orders) if o["order"] == "buy"]
    buy_prices = [prices_all[i] for i in buy_x]

    sell_x = [i for i, o in enumerate(orders) if o["order"] == "sell"]
    sell_prices = [prices_all[i] for i in sell_x]

    fig, ax = plt.subplots(figsize=(16, 6))
    ax.plot(range(len(data)), prices_all, color="steelblue", linewidth=1.6, label="Cena")
    ax.scatter(buy_x, buy_prices, color="green", zorder=5, s=50, label="Nakup (polnjenje)")
    ax.scatter(sell_x, sell_prices, color="red", zorder=5, s=50, label="Prodaja (praznjenje)")

    ax.set_xlabel("Čas")
    ax.set_ylabel("Cena (EUR/MWh)")
    ax.set_title(f"Day-ahead cene za {country_code} — {start.strftime('%Y-%m-%d')}")
    ax.set_xticks(range(0, len(times_labels), 4))
    ax.set_xticklabels(times_labels[::4], rotation=45, ha="right")
    ax.legend()
    ax.grid(True, alpha=0.3)

    plt.tight_layout()

    graph_file = Path(filename_png)

    plt.savefig(graph_file, dpi=150, bbox_inches="tight")
    plt.close(fig)

    print("Graf shranjen:", graph_file)


def main(capacity, power, minimum_profit):

    capacity = float(capacity)
    power = float(power)
    minimum_profit = float(minimum_profit)

    intervals_needed = (int)(capacity/power*4)

    print("Potrebnih intervalov:",intervals_needed,)

    now = pd.Timestamp.now(tz="Europe/Ljubljana").normalize()
    start = now + pd.Timedelta(days=0)
    end = now + pd.Timedelta(days=1)

    country_code = "SI"

    filename = ("cache/prices_data/prices_"+ start.strftime("%Y-%m-%d")+ ".xlsx")
    filename_json = (
        "cache/intervals_json/intervals_"
        + str(intervals_needed)
        + "_minprofit_" + str(minimum_profit)
        + "_date_" + start.strftime("%Y-%m-%d") + ".json"
    )
    filename_png= (
        "graph_imgs/intervals_"
        + str(intervals_needed)
        + "_minprofit_" + str(minimum_profit)
        + "_date_" + start.strftime("%Y-%m-%d") + ".png"
    )
    filepath = Path(filename)
    filepath_json = Path(filename_json)

    if filepath_json.exists():
        print("cahced od jsona")
        with open(filepath_json, "r", encoding="utf-8") as file:
            return json.load(file)

    if not filepath.exists():
        scrap_data(filename,country_code,start,end,)
    else:
        print("Podatki že v cache:",filename)

    df = pd.read_excel(filename)
    df["time"] = pd.to_datetime(df["time"])

    data = []

    for _, row in df.iterrows():
        t = row["time"]
        pos = (t.hour * 4 + t.minute // 15 )

        data.append([
                pos,
                float(row["price"]),
            ])

    fwh = 7 * 4
    lwh = 16 * 4 #ne rabi več

    trade_data = []

    for t, price in data:
        time_string = (f"{t // 4:02d}:"f"{t % 4 * 15:02d}")
        trade_data.append({"time": time_string,"price": price,})

    orders = optimize_trades(
        trade_data,
        max_positions=intervals_needed,
        minimum_profit=minimum_profit,
        force_flat_end=True,
    )

    charging_times = [ o["time"] for o in orders if o["order"] == "buy"]

    discharging_times = [o["time"] for o in orders if o["order"] == "sell"]

    result = {"charging_intervals": charging_times,"discharging_intervals": discharging_times,}

    with open(filename_json,"w",encoding="utf-8",) as file:

        json.dump(result,file,indent=4,)

    graph_plot(data, orders, country_code, start ,filename_png)

    return result

if __name__ == "__main__":
    result = main(capacity=10, power=5, minimum_profit=10)
    print(json.dumps(result))