import json
import math
from datetime import datetime, timedelta
from concurrent.futures import ThreadPoolExecutor, as_completed

from algo import main

INPUT_FILE = "requests.json"      # tukaj vpišeš pot do svoje JSON datoteke
OUTPUT_FILE = "results.json"      # sem se shranijo rezultati
MAX_WORKERS = 20


def process_one(payload):
    unique_id = payload.get("unique_id")

    try:
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

        print(f"Končan zahtevek: {unique_id}")

        return result

    except Exception as e:
        print("NAPAKA:", type(e).__name__, "-", str(e), "- unique_id:", unique_id)

        return {
            "success": False,
            "unique_id": unique_id,
            "error": type(e).__name__,
            "message": str(e)
        }


def run_batch():
    with open(INPUT_FILE, "r", encoding="utf-8") as f:
        requests = json.load(f)

    if not isinstance(requests, list):
        requests = [requests]

    print(f"Nalagam {len(requests)} zahtevkov iz {INPUT_FILE} ...")

    results = []

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        future_to_id = {
            executor.submit(process_one, req): req.get("unique_id")
            for req in requests
        }

        for future in as_completed(future_to_id):
            results.append(future.result())

    order = {req.get("unique_id"): i for i, req in enumerate(requests)}
    results.sort(key=lambda r: order.get(r.get("unique_id"), 0))
    
if __name__ == "__main__":
    run_batch()