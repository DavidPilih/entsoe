"""Zadnje moči in 15-minutna napoved porabe iz delavniškega/vikend profila."""

import argparse
import json
import math
import os
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import psycopg2
import pandas as pd
import plotly.graph_objects as go
from dotenv import load_dotenv


GROUP = "03"
SOURCES = {
    "grid_power": (f"{GROUP}-eastron", "power_active_total[kW]"),
    "battery_power": (f"{GROUP}-enerArk", "active_power_total[kW]"),
    "solar_power": (f"{GROUP}-deyeString", "power_active_total[kW]"),
}

QUERY = """
SELECT t.dbl_v, t.long_v, t.str_v, t.ts
FROM ts_kv_latest t
JOIN device d ON d.id = t.entity_id
JOIN key_dictionary kd ON kd.key_id = t."key"
WHERE d.name = %s AND kd.key = %s
ORDER BY t.ts DESC
"""


def connect_db():
    load_dotenv(Path(__file__).with_name(".env"))
    if not os.getenv("USER_DB") or not os.getenv("PASSWORD_DB"):
        raise ValueError("V .env manjkata USER_DB in/ali PASSWORD_DB.")

    conn = psycopg2.connect(
        host="10.188.20.3",
        port=5432,
        dbname="thingsboard",
        user=os.environ["USER_DB"],
        password=os.environ["PASSWORD_DB"],
        connect_timeout=10,
        options="-c statement_timeout=60000",
    )
    conn.set_session(readonly=True, isolation_level="REPEATABLE READ")
    return conn


def fetch_latest():
    conn = connect_db()
    readings = {}
    try:
        conn.set_session(readonly=True, isolation_level="REPEATABLE READ")
        with conn.cursor() as cursor:
            for label, (device, key) in SOURCES.items():
                cursor.execute(QUERY, (device, key))
                rows = cursor.fetchall()
                if len(rows) != 1:
                    raise ValueError(
                        f"{device} / {key}: pričakovana ena meritev, dobljenih {len(rows)}."
                    )
                dbl_v, long_v, str_v, timestamp = rows[0]
                raw = next((v for v in (dbl_v, long_v, str_v) if v is not None), None)
                try:
                    value = float(raw)
                except (TypeError, ValueError) as exc:
                    raise ValueError(f"{device} / {key}: meritev ni število.") from exc
                if not math.isfinite(value):
                    raise ValueError(f"{device} / {key}: meritev ni končno število.")
                readings[label] = (value, timestamp)
    finally:
        conn.close()
    return readings


def fetch_history(start, end, minutes=15):
    """Povprečje vsakega vira v skupnih časovnih intervalih (kW)."""
    if minutes not in (1, 15):
        raise ValueError("Podprta sta 1- in 15-minutni interval.")
    bucket_ms = minutes * 60000
    conn = connect_db()
    frames = []
    try:
        with conn.cursor() as cursor:
            for label, (device, key) in SOURCES.items():
                cursor.execute("""
                    SELECT d.id, kd.key_id FROM device d CROSS JOIN key_dictionary kd
                    WHERE d.name = %s AND kd.key = %s
                """, (device, key))
                sources = cursor.fetchall()
                if len(sources) != 1:
                    raise ValueError(f"Vir {device} / {key} ni enolično določen.")
                entity_id, key_id = sources[0]
                cursor.execute("""
                    SELECT (ts / %s) * %s AS bucket,
                           AVG(COALESCE(dbl_v, long_v::double precision)) AS power
                    FROM ts_kv
                    WHERE entity_id = %s AND "key" = %s AND ts >= %s AND ts < %s
                    GROUP BY bucket ORDER BY bucket
                """, (bucket_ms, bucket_ms, entity_id, key_id, int(start.timestamp() * 1000), int(end.timestamp() * 1000)))
                rows = cursor.fetchall()
                index = pd.to_datetime([r[0] for r in rows], unit="ms", utc=True).tz_convert("Europe/Ljubljana")
                frames.append(pd.Series([r[1] for r in rows], index=index, name=label, dtype=float))
    finally:
        conn.close()
    history = pd.concat(frames, axis=1, sort=True).reindex(pd.date_range(start, end, freq=f"{minutes}min", inclusive="left"))
    history = history.replace([float("inf"), float("-inf")], float("nan"))
    history["consumption_kw"] = history[list(SOURCES)].sum(axis=1, min_count=3)
    history.index.name = "time"
    return history


def build_profile(history):
    valid = history[["consumption_kw"]].dropna().copy()
    if valid.empty:
        raise ValueError("Ni skupnih 15-minutnih meritev za vse tri vire.")
    valid["date"] = valid.index.date
    valid["weekend"] = valid.index.dayofweek >= 5
    valid["slot"] = valid.index.hour * 4 + valid.index.minute // 15
    # Ob jesenskem premiku ure podvojene termine najprej povprečimo po dnevu.
    daily = valid.groupby(["date", "weekend", "slot"])["consumption_kw"].mean().reset_index()
    grouped = daily.groupby(["weekend", "slot"])["consumption_kw"]
    profile = grouped.agg(["mean", "count"])
    profile["p10_30d"] = grouped.quantile(0.10)
    profile["p90_30d"] = grouped.quantile(0.90)
    history_end = history.index.max() + pd.Timedelta(minutes=15)
    recent = valid.loc[valid.index >= history_end - pd.Timedelta(days=7)]
    recent_grouped = recent.groupby(["weekend", "slot"])["consumption_kw"]
    profile["min_7d"] = recent_grouped.min()
    profile["max_7d"] = recent_grouped.max()
    # Pas P10–P90 razširimo toliko, da zajame vse vrednosti zadnjega tedna.
    # Če v zadnjem tednu ni podatka, ostaneta meji daljšega obdobja.
    profile["lower"] = profile[["p10_30d", "min_7d"]].min(axis=1)
    profile["upper"] = profile[["p90_30d", "max_7d"]].max(axis=1)
    return profile.reindex(pd.MultiIndex.from_product([[False, True], range(96)], names=["weekend", "slot"]))


def improve_upper_bound(profile, minute_history, training_start, training_end):
    """Vrhovom popolnih intervalov dodaj toleranco časa ±30 minut."""
    result = profile.copy()
    result["old_upper"] = result["upper"]
    minutes = minute_history.loc[(minute_history.index >= training_start) &
                                 (minute_history.index < training_end), "consumption_kw"]
    peaks = minutes.resample("15min").agg(["max", "count"])
    peaks = peaks.loc[peaks["count"] == 15].copy()
    peaks["weekend"] = peaks.index.dayofweek >= 5
    peaks["slot"] = peaks.index.hour * 4 + peaks.index.minute // 15
    grouped = peaks.groupby(["weekend", "slot"])["max"]
    result["peak_p90"] = grouped.quantile(0.9)
    recent = peaks.loc[peaks.index >= training_end - pd.Timedelta(days=7)]
    result["peak_max_7d"] = recent.groupby(["weekend", "slot"])["max"].max()
    result["peak_count"] = grouped.count().reindex(result.index, fill_value=0)
    base = result[["peak_p90", "peak_max_7d"]].max(axis=1)
    for weekend in (False, True):
        values = base.loc[weekend]
        for slot in range(96):
            result.loc[(weekend, slot), "peak_upper_30min"] = values.reindex(
                [(slot + offset) % 96 for offset in (-2, -1, 0, 1, 2)]).max()
    result["upper"] = result[["old_upper", "peak_upper_30min"]].max(axis=1)
    return result


def build_forecast(profile, start, end):
    index = pd.date_range(start, end, freq="15min", inclusive="left")
    index = index[index < end]
    keys = pd.MultiIndex.from_arrays([index.dayofweek >= 5, index.hour * 4 + index.minute // 15], names=["weekend", "slot"])
    selected = profile.reindex(keys)
    forecast = pd.DataFrame({"consumption_kw": selected["mean"].to_numpy(),
                             "lower_kw": selected["lower"].to_numpy(),
                             "upper_kw": selected["upper"].to_numpy(),
                             "sample_days": selected["count"].fillna(0).to_numpy()}, index=index)
    forecast.index.name = "time"
    return forecast


def plot_consumption(history, forecast, path, forecast_start, forecast_end):
    """En interaktiven graf 15-minutnih moči s percentili napovedi."""
    fig = go.Figure()

    def series(frame, column, minutes=15):
        # Dejanski čas ohranimo v epoch ms; prikaz časa je vedno Ljubljana.
        index = frame.index.append(pd.DatetimeIndex([frame.index[-1] + pd.Timedelta(minutes=minutes)]))
        # asi8 uporablja enoto indeksa (lahko us ali ns), zato je ne predpostavimo.
        x = [int(timestamp.timestamp() * 1000) for timestamp in index]
        y = [None if pd.isna(v) else float(v) for v in frame[column]]
        y.append(y[-1])
        labels = index.strftime("%d.%m.%Y %H:%M %z").tolist()
        return x, y, labels

    if not history.empty:
        x, y, labels = series(history, "consumption_kw")
        fig.add_trace(go.Scatter(x=x, y=y, customdata=labels, name="Dejanska poraba",
                                mode="lines", line=dict(color="#2563eb", width=1.7, shape="hv"),
                                connectgaps=False,
                                hovertemplate="%{customdata}<br>Poraba: %{y:.2f} kW<extra></extra>"))
    if not forecast.empty:
        x, low, labels = series(forecast, "lower_kw")
        _, high, _ = series(forecast, "upper_kw")
        _, mean, _ = series(forecast, "consumption_kw")
        fig.add_trace(go.Scatter(x=x, y=low, mode="lines", line=dict(width=0, shape="hv"),
                                showlegend=False, hoverinfo="skip", connectgaps=False))
        fig.add_trace(go.Scatter(x=x, y=high, mode="lines", line=dict(width=0, shape="hv"),
                                fill="tonexty", fillcolor="rgba(22,163,74,0.18)",
                                name="Razpon z minutnimi vrhovi ±30 min", hoverinfo="skip", connectgaps=False))
        fig.add_trace(go.Scatter(x=x, y=mean, mode="lines", name="Napoved (povprečje)",
                                line=dict(color="#16a34a", width=2, shape="hv"), connectgaps=False,
                                customdata=list(zip(labels, low, high)),
                                hovertemplate="%{customdata[0]}<br>Napoved: %{y:.2f} kW"
                                              "<br>Spodnja meja: %{customdata[1]:.2f} kW<br>Zgornja meja: %{customdata[2]:.2f} kW<extra></extra>"))
    begin_ms = int(history.index[0].timestamp() * 1000)
    end_ms = int(forecast_end.timestamp() * 1000)
    split_ms = int(forecast_start.timestamp() * 1000)
    fig.add_vline(x=split_ms, line_dash="dash", line_color="#64748b")
    fig.add_annotation(x=split_ms, y=1, yref="paper", text="Začetek napovedi", showarrow=False, yshift=15)
    fig.update_layout(template="plotly_white", title=dict(text=f"Skupina {GROUP} · Dejanska poraba in napoved", y=0.98, yanchor="top"),
                      height=680, margin=dict(l=65, r=30, t=130, b=50), dragmode="pan",
                      hovermode="x", legend=dict(orientation="h", y=1.12, x=0),
                      yaxis=dict(title="Moč [kW]", fixedrange=True),
                      xaxis=dict(type="linear", title="Europe/Ljubljana · 15-minutna povprečja",
                                 range=[split_ms - 2 * 86400000, min(end_ms, split_ms + 2 * 86400000)],
                                 rangeslider=dict(visible=True, range=[begin_ms, end_ms], thickness=0.13)))
    # JS formatiranje osi omogoča lokalni čas Ljubljana tudi na drugih računalnikih.
    script = """
    const graph = document.getElementById('{plot_id}');
    const fmt = new Intl.DateTimeFormat('sl-SI', {
      timeZone: 'Europe/Ljubljana', day: '2-digit', month: '2-digit', hour:'2-digit', minute:'2-digit', hourCycle:'h23'
    });
    function ticks() {
      const range = graph.layout.xaxis.range;
      const span = range[1] - range[0];
      const steps = [900000, 3600000, 10800000, 21600000, 43200000, 86400000, 172800000, 345600000, 604800000];
      const step = steps.find(v => span / v <= Math.max(3, graph.clientWidth / 130)) || steps[steps.length-1];
      const values = [];
      for (let v = Math.ceil(range[0] / step) * step; v <= range[1]; v += step) values.push(v);
      Plotly.relayout(graph, {'xaxis.tickmode':'array', 'xaxis.tickvals':values,
                              'xaxis.ticktext':values.map(v => fmt.format(new Date(v)))});
    }
    graph.on('plotly_relayout', ev => {
      if (Object.keys(ev).some(k => k.startsWith('xaxis.range') || k === 'xaxis.autorange')) ticks();
    });
    ticks();
    """
    fig.write_html(path, include_plotlyjs=True, full_html=True, post_script=script,
                   config=dict(scrollZoom=True, responsive=True, displaylogo=False,
                               modeBarButtonsToRemove=["select2d", "lasso2d"]))
    return fig


TELEMETRY_KEYS = {
    "consumption_kw": "forecast_consumption",
    "lower_kw": "forecast_consumption_lower",
    "upper_kw": "forecast_consumption_upper",
}


def forecast_data_points(forecast):
    """Trije ključi v kW za vsak 15-minutni časovni žig v epoch ms."""
    if forecast.empty or forecast.index.tz is None or not forecast.index.is_unique:
        raise ValueError("Napoved mora imeti podatke in enolične časovne žige s časovnim pasom.")
    points = []
    for timestamp, row in forecast.sort_index().iterrows():
        values = {key: float(row[column]) for column, key in TELEMETRY_KEYS.items()}
        if not all(math.isfinite(value) for value in values.values()):
            raise ValueError(f"Manjkajoča ali neveljavna napoved za {timestamp}; pošiljanje ustavljeno.")
        if row["lower_kw"] > row["upper_kw"]:
            raise ValueError(f"Obrnjeni meji za {timestamp}.")
        points.append({"ts": int(timestamp.timestamp() * 1000), "values": values})
    return points


def send_forecast(forecast, device_name):
    # Isti REST pomočnik kot no_mqtt_server -> client.process_request.
    from thingsboard_send_data import send_tb_device

    points = forecast_data_points(forecast)
    conn = connect_db()
    try:
        with conn.cursor() as cursor:
            cursor.execute("SELECT id FROM device WHERE name = %s", (device_name,))
            devices = cursor.fetchall()
            if len(devices) != 1:
                raise ValueError(f"Ciljna naprava {device_name!r} ni enolično določena.")
            device_id = str(devices[0][0])
    finally:
        conn.close()
    send_tb_device(points, device_id)
    print(f"Poslano: {device_name}, {len(points)} časovnih žigov, 3 ključi v kW.")
    return device_id


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--days", type=int, default=7, help="Število dni napovedi (privzeto 7).")
    parser.add_argument("--history-days", type=int, default=30, help="Število preteklih dni (privzeto 30).")
    parser.add_argument("--tb-device", default="03-enerArk", help="Ciljna ThingsBoard naprava (privzeto 03-enerArk).")
    parser.add_argument("--no-send", action="store_true", help="Samo izračun in graf, brez pošiljanja na ThingsBoard.")
    args = parser.parse_args()
    if args.history_days <= 0 or args.days <= 0:
        parser.error("Število dni mora biti pozitivno.")
    readings = fetch_latest()
    print(f"Skupina {GROUP} — zadnje razpoložljive meritve")
    for label, (value, timestamp) in readings.items():
        measured_at = datetime.fromtimestamp(timestamp / 1000, ZoneInfo("Europe/Ljubljana"))
        device, key = SOURCES[label]
        print(f"{label:13} = {value:+.2f} kW  ({device}, {key}, {measured_at.isoformat()})")
    grid, battery, solar = (readings[label][0] for label in SOURCES)
    consumption = grid + battery + solar
    print(f"consumption   = {grid:.2f} + ({battery:.2f}) + ({solar:.2f}) = {consumption:.2f} kW")
    now = pd.Timestamp.now(tz="UTC")
    end = now.floor("15min").tz_convert("Europe/Ljubljana")
    start = (end.tz_convert("UTC") - pd.Timedelta(days=args.history_days)).tz_convert("Europe/Ljubljana")
    print(f"Berem zgodovino od {start.isoformat()} do {end.isoformat()} ...", flush=True)
    history = fetch_history(start, end)
    print("Berem minutne podatke za zgornjo mejo ...", flush=True)
    minute_history = fetch_history(start, end, minutes=1)
    profile = improve_upper_bound(build_profile(history), minute_history, start, end)
    forecast_start = now.ceil("15min").tz_convert("Europe/Ljubljana")
    forecast_end = forecast_start + pd.DateOffset(days=args.days)
    forecast = build_forecast(profile, forecast_start, forecast_end)
    output = Path(__file__).parent / "output" / "consumption"
    output.mkdir(parents=True, exist_ok=True)
    history.to_csv(output / "history_15min.csv")
    profile.to_csv(output / "profile_15min.csv")
    forecast.to_csv(output / "forecast_15min.csv")
    chart = output / "consumption_interactive.html"
    plot_consumption(history, forecast, chart, forecast_start, forecast_end)
    points = forecast_data_points(forecast)
    (output / "forecast_telemetry.json").write_text(json.dumps(points, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
    if not args.no_send:
        send_forecast(forecast, args.tb_device)
    print(f"Popolni zgodovinski intervali: {history.consumption_kw.notna().sum()}/{len(history)}")
    print(f"Manjkajoči termini profila: {profile['mean'].isna().sum()}/192 (brez dopolnjevanja)")
    print(f"Napoved za naslednjih {args.days} dni ({len(forecast)} intervalov), prvih 8:")
    print(forecast.head(8).to_string())
    print(f"Interaktivni graf: {chart}\nPodatki CSV: {output}")


if __name__ == "__main__":
    try:
        main()
    except (psycopg2.Error, ValueError) as exc:
        print(f"NAPAKA: {exc}", file=sys.stderr)
        sys.exit(1)
