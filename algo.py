"""15-minutni algoritem za urnik baterije in energijsko bilanco."""
import math
import time
import numpy as np

INTERVAL_HOURS = 0.25
ENERGY_STEPS = 100


def validate(capacity, power, soc, min_soc, max_soc):
    values = (capacity, power, soc, min_soc, max_soc)
    if any(isinstance(v, bool) or not math.isfinite(float(v)) for v in values):
        raise ValueError('Energy parameters must be finite numbers.')
    if capacity <= 0 or power <= 0 or not 0 <= min_soc <= soc <= max_soc <= 1:
        raise ValueError('Invalid capacity, power or SOC limits.')


def interval_balance(time, price, solar_kw, consumption_kw, battery_kw, soc,
                     capacity, power, margin, minimum_profit):
    h = INTERVAL_HOURS
    charge, discharge = max(0., battery_kw), max(0., -battery_kw)
    grid = consumption_kw + battery_kw - solar_kw
    imported, exported = max(0., grid), max(0., -grid)
    energy_delta = battery_kw * h
    # Preserve the previous proportional charge penalty; prices are EUR/MWh.
    result_eur = (price * (1 - margin) * exported -
                  price * (1 + margin) * imported - minimum_profit * charge) * h / 1000
    return dict(time=time, price=float(price),
                order='buy' if battery_kw > 1e-9 else 'sell' if battery_kw < -1e-9 else 'hold',
                energy_fraction=float(abs(battery_kw) / power),
                solar_kw=float(solar_kw), consumption_kw=float(consumption_kw),
                battery_kw=float(battery_kw), grid_kw=float(grid),
                grid_import_kw=imported, grid_export_kw=exported,
                solar_to_load_kw=min(solar_kw, consumption_kw),
                solar_to_battery_kw=min(max(0., solar_kw - consumption_kw), charge),
                grid_to_battery_kw=max(0., charge - max(0., solar_kw - consumption_kw)),
                consumption_kwh=consumption_kw * h,
                battery_consumption_kwh=min(discharge, max(0., consumption_kw - solar_kw)) * h,
                grid_consumption_kwh=min(imported, max(0., consumption_kw - solar_kw)) * h,
                trade_energy_kwh=abs(battery_kw) * h,
                result_eur=float(result_eur), soc=float(soc + energy_delta / capacity))


def optimize_energy(data, capacity, power, soc, min_soc, max_soc, solar_kw,
                    consumption_kw, margin, minimum_profit, active=None,
                    manual=None):
    """DP maximizes horizon result, then minimizes grid exchange and cycling.

    States include both bounds and the exact initial energy. Manual setpoints
    bypass optimization and are limited by power and energy, interval by interval.
    No forced final SOC and no reserve preventing later grid purchases.
    """
    started = time.perf_counter()
    def progress(message):
        print(f"[algo +{time.perf_counter() - started:.2f}s] {message}", flush=True)

    progress("Preverjam podatke ...")
    validate(capacity, power, soc, min_soc, max_soc)
    n = len(data)
    active = list(active) if active is not None else [True] * n
    if not (len(solar_kw) == len(consumption_kw) == len(active) == n):
        raise ValueError('Forecast lengths must match prices.')
    if not 0 <= margin <= 1 or not math.isfinite(minimum_profit):
        raise ValueError('Invalid margin or minimum_profit.')
    if any(not math.isfinite(float(v)) or v < 0 for v in [*solar_kw, *consumption_kw]):
        raise ValueError('Solar and load must be finite and nonnegative.')
    prices = np.array([float(item['price']) for item in data])
    if not np.isfinite(prices).all():
        raise ValueError('Invalid prices.')
    if manual is not None and (len(manual) != n or any(not math.isfinite(float(v)) for v in manual)):
        raise ValueError('Invalid manual setpoints.')
    h = INTERVAL_HOURS
    low, high, initial = capacity * min_soc, capacity * max_soc, capacity * soc
    step = power * h / ENERGY_STEPS
    states = np.unique(np.r_[np.arange(low, high, step), high, initial])
    count = len(states)
    progress(f"Pripravljenih {n} intervalov in {count} možnih stanj baterije.")
    choices = []
    if manual is None:
        future = np.zeros(count)
        grid_future = np.zeros(count)
        cycle_future = np.zeros(count)
        indices = np.arange(count)
        # Extra slots cover the inserted initial energy and upper-bound state.
        reach = min(count - 1, ENERGY_STEPS + 2)
        offsets = [0] + [d for k in range(1, reach + 1) for d in (k, -k)]
        checkpoint = max(1, n // 4)
        for t in range(n - 1, -1, -1):
            best = np.full(count, -np.inf)
            best_grid = np.full(count, np.inf)
            best_cycle = np.full(count, np.inf)
            chosen = indices.copy()
            for offset in offsets if active[t] else [0]:
                src = indices[max(0, -offset):min(count, count - offset)]
                dst = src + offset
                delta = states[dst] - states[src]
                battery = delta / h
                valid = np.abs(battery) <= power + 1e-8
                src, dst, battery = src[valid], dst[valid], battery[valid]
                grid = consumption_kw[t] + battery - solar_kw[t]
                reward = (prices[t] * (1 - margin) * np.maximum(-grid, 0) -
                          prices[t] * (1 + margin) * np.maximum(grid, 0) -
                          minimum_profit * np.maximum(battery, 0)) * h / 1000
                score = future[dst] + reward
                exchanged = grid_future[dst] + np.abs(grid) * h
                cycled = cycle_future[dst] + np.abs(battery) * h
                equal = np.abs(score - best[src]) <= 1e-10
                better = ((score > best[src] + 1e-10) |
                          (equal & ((exchanged < best_grid[src] - 1e-10) |
                           ((np.abs(exchanged - best_grid[src]) <= 1e-10) & (cycled < best_cycle[src] - 1e-10)))))
                selected = src[better]
                best[selected], best_grid[selected], best_cycle[selected] = score[better], exchanged[better], cycled[better]
                chosen[selected] = dst[better]
            future, grid_future, cycle_future = best, best_grid, best_cycle
            choices.append(chosen)
            processed = n - t
            if processed == 1 or processed == n or processed % checkpoint == 0:
                progress(f"Optimizacija nazaj: {processed}/{n} intervalov.")
        choices.reverse()
    else:
        progress("Uporabljam ročni urnik; iskanje najboljše poti ni potrebno.")
    index = int(np.searchsorted(states, initial))
    energy = initial
    orders = []
    for t, item in enumerate(data):
        if manual is not None:
            requested = max(-1., min(1., float(manual[t]))) * power if active[t] else 0.
            battery = min(requested, (high - energy) / h) if requested >= 0 else max(requested, -(energy - low) / h)
        else:
            nxt = int(choices[t][index])
            delta = states[nxt] - states[index]
            battery = delta / h
            index = nxt
        row = interval_balance(item['time'], prices[t], solar_kw[t], consumption_kw[t], battery,
                               energy / capacity, capacity, power, margin, minimum_profit)
        energy = min(high, max(low, row['soc'] * capacity))
        row['soc'] = energy / capacity
        orders.append(row)
    progress(f"Urnik sestavljen ({len(orders)} intervalov).")
    return orders
