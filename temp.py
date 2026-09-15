"""Prenesi slovenske urne cene za januar–avgust 2026."""

import os
import tempfile
from pathlib import Path

import pandas as pd

from api_client import scrap_data


FOLDER = Path(__file__).resolve().parent
OUTPUT = FOLDER / "prices_2026_01_08.xlsx"
START = pd.Timestamp("2026-01-01", tz="Europe/Ljubljana")
END = pd.Timestamp("2026-09-01", tz="Europe/Ljubljana")


def main():
    scrap_data(str(OUTPUT), START, END)

    # Energy-Charts lahko vključi tudi končni datum; obdržimo natanko osem mesecev.
    data = pd.read_excel(OUTPUT)
    times = pd.to_datetime(data["time"])
    selected = data.loc[(times >= START.tz_localize(None)) & (times < END.tz_localize(None))]
    if selected.empty:
        raise ValueError("Za obdobje januar–avgust 2026 ni bilo podatkov.")

    if len(selected) != len(data):
        fd, temp_path = tempfile.mkstemp(dir=FOLDER, suffix=".xlsx")
        os.close(fd)
        try:
            selected.to_excel(temp_path, index=False)
            os.replace(temp_path, OUTPUT)
        finally:
            if os.path.exists(temp_path):
                os.remove(temp_path)

    print(f"Shranjeno {len(selected)} zapisov: {OUTPUT}")


if __name__ == "__main__":
    main()
