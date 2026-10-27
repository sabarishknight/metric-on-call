"""Build the synthetic chain, plant the incidents, compute ground truth.

    python -m generator.build

Writes DuckDB (if installed) + CSVs under data/, and data/incident_truth.json.
Same seed -> byte-identical tables (see the fingerprint in the truth file).
"""

from __future__ import annotations

import datetime as dt
import hashlib

import numpy as np
import pandas as pd

from common.store import save_tables
from generator import catalog as C
from generator.world import ALL_EFFECTS, Noise, Sim, load_spec, make_noise, simulate

EFFECT_KEYS: dict[int, frozenset[str]] = {
    1: frozenset({"1"}),
    4: frozenset({"4"}),
    5: frozenset({"5"}),
    6: frozenset({"6"}),
    7: frozenset({"7"}),
    8: frozenset({"8"}),
    9: frozenset({"9"}),
    10: frozenset({"10"}),
    11: frozenset({"11"}),
    12: frozenset({"12"}),
    13: frozenset({"13w", "13p"}),
}
PRODUCT_ID = [p[0] for p in C.PRODUCTS]
PRODUCT_CATEGORY = {p[0]: p[2] for p in C.PRODUCTS}


# --------------------------------------------------------------------------------------
# Fact frames from a simulation
# --------------------------------------------------------------------------------------
def _grid(*axes) -> pd.DataFrame:
    return pd.MultiIndex.from_product(list(axes)).to_frame(index=False)


def clean_frames(sim: Sim) -> dict[str, pd.DataFrame]:
    dates = pd.to_datetime(sim.dates)
    txn = _grid(sim.store_ids, dates, C.CHANNELS, C.DAYPARTS, C.PAYMENTS)
    txn.columns = ["store_id", "date", "channel", "daypart", "payment_type"]
    txn["transactions"] = sim.txn.ravel()
    txn["units"] = np.round(sim.units_cell.ravel(), 2)
    txn["net_sales"] = np.round(sim.sales_cell.ravel(), 2)

    lines = _grid(sim.store_ids, dates, C.CHANNELS, PRODUCT_ID)
    lines.columns = ["store_id", "date", "channel", "product_id"]
    lines["units"] = np.round(sim.lines_units.ravel(), 2)
    lines["net_sales"] = np.round(sim.lines_sales.ravel(), 2)

    vis = _grid(sim.store_ids, dates, C.CHANNELS, C.DAYPARTS)
    vis.columns = ["store_id", "date", "channel", "daypart"]
    vis["visits"] = np.rint(sim.visits.ravel()).astype(np.int64)
    return {"raw_sales_txn": txn, "raw_sales_lines": lines, "raw_visits": vis}


def _load_id(d: pd.Series, replay: bool = False) -> pd.Series:
    return "L" + d.dt.strftime("%Y%m%d") + ("R" if replay else "")


# --------------------------------------------------------------------------------------
# Raw defects (incidents 2 and 3)
# --------------------------------------------------------------------------------------
def inject_defects(frames: dict[str, pd.DataFrame], spec: dict) -> dict[str, pd.DataFrame]:
    inc = {i["id"]: i for i in spec["incidents"]}
    store_region = {s[0]: s[2] for s in C.STORES}
    region_by_name = {v[0]: k for k, v in C.REGIONS.items()}
    out: dict[str, pd.DataFrame] = {}
    for name, df in frames.items():
        df = df.copy()
        df.insert(0, "load_id", _load_id(df["date"]))
        out[name] = df

    # incident 3: last two days missing for one region (data never arrived)
    p3 = inc[3]
    region = region_by_name[p3["scope"]["region"]]
    d0, d1 = pd.Timestamp(p3["date_from"]), pd.Timestamp(p3["date_to"])
    for name, df in out.items():
        drop = df["date"].between(d0, d1) & df["store_id"].map(store_region).eq(region)
        out[name] = df[~drop].reset_index(drop=True)

    # incident 2: one day loaded twice for five stores (sales tables only)
    p2 = inc[2]
    day = pd.Timestamp(p2["date_from"])
    for name in ("raw_sales_txn", "raw_sales_lines"):
        df = out[name]
        dup = df[(df["date"] == day) & df["store_id"].isin(p2["scope"]["stores"])].copy()
        dup["load_id"] = _load_id(dup["date"], replay=True)
        out[name] = pd.concat([df, dup], ignore_index=True)
    return out


def load_log(raw: dict[str, pd.DataFrame]) -> pd.DataFrame:
    parts = []
    for name, df in raw.items():
        g = df.groupby(["load_id", "date", "store_id"], as_index=False).size()
        g = g.rename(columns={"size": "row_count"})
        g.insert(0, "source_table", name)
        replay = g["load_id"].str.endswith("R")
        g["loaded_at"] = g["date"] + pd.Timedelta(days=1, hours=6)
        g.loc[replay, "loaded_at"] += pd.Timedelta(hours=2, minutes=15)
        parts.append(g)
    log = pd.concat(parts, ignore_index=True)
    return log[["load_id", "source_table", "date", "store_id", "row_count", "loaded_at"]]


# --------------------------------------------------------------------------------------
# Dimensions and context
# --------------------------------------------------------------------------------------
def dimensions(rng: np.random.Generator) -> dict[str, pd.DataFrame]:
    stores = pd.DataFrame(
        [
            {
                "store_id": sid,
                "store_name": name,
                "region_id": rid,
                "region_name": C.REGIONS[rid][0],
                "opened_at": dt.date(2015, 1, 1) + dt.timedelta(days=int(rng.integers(0, 3300))),
                "sqm": int(rng.integers(70, 260)),
            }
            for sid, name, rid in C.STORES
        ]
    )
    products = pd.DataFrame(
        [
            {"product_id": p[0], "product_name": p[1], "category": p[2], "list_price": p[3]}
            for p in C.PRODUCTS
        ]
    )
    return {"dim_store": stores, "dim_product": products}


def context_tables(spec: dict, sim_actual: Sim, rng: np.random.Generator) -> dict[str, pd.DataFrame]:
    inc = {i["id"]: i for i in spec["incidents"]}
    store_ids = [s[0] for s in C.STORES]

    # promos ---------------------------------------------------------------------------
    promos = []
    p4 = inc[4]
    for s in p4["scope"]["stores"]:
        promos.append(("PR-2025-10-MUFFIN", "Muffin 2-for-1", p4["date_from"], p4["date_to"], s, "muffin", "2-for-1"))
    for s in ("WS01", "WS02", "WS03", "WS04"):
        promos.append(("PR-2025-05-COOKIE", "Free cookie with large coffee", dt.date(2025, 5, 12), dt.date(2025, 5, 25), s, "cookie", "bundle"))
    for s in ("CB01", "CB02", "CB03", "CB04", "IW01", "IW02"):
        promos.append(("PR-2026-01-LUNCH", "Back-to-school lunch bundle", dt.date(2026, 1, 27), dt.date(2026, 2, 9), s, "sandwich", "bundle"))
    for s in store_ids:
        promos.append(("PR-2026-02-LOYALTY", "Loyalty double points", dt.date(2026, 2, 2), dt.date(2026, 2, 15), s, None, "loyalty"))
    ctx_promos = pd.DataFrame(
        promos, columns=["promo_id", "promo_name", "date_from", "date_to", "store_id", "product_id", "mechanic"]
    )

    # price changes (only incident 5 is logged; its size comes from the same price model) ---
    p5 = inc[5]
    years = (pd.Timestamp(p5["date_from"]) - pd.Timestamp(C.WINDOW_START)).days / 365.25
    old = round(5.40 * (1 + 0.025 * years), 2)
    ctx_prices = pd.DataFrame(
        [
            {
                "change_id": "PC-2025-12-LARGE",
                "effective_date": p5["date_from"],
                "product_id": "coffee_large",
                "old_price": old,
                "new_price": round(old + p5["params"]["price_delta"], 2),
                "scope": "all stores",
            }
        ]
    )

    # ops log --------------------------------------------------------------------------
    ops = []

    def add(d0, d1, store, system, sev, text):
        ops.append((d0, d1, store, system, sev, text))

    p1 = inc[1]
    for s in p1["scope"]["stores"]:
        add(p1["date_from"], p1["date_to"], s, "pos", "high", "Card terminals down after a firmware fault. Cash only until a vendor fix.")
    p9 = inc[9]
    for s in p9["scope"]["stores"]:
        add(p9["date_from"], p9["date_to"], s, "supply", "medium", "Oat milk supplier shortfall. Oat flat white unavailable.")
    p10 = inc[10]
    add(p10["date_from"], p10["date_to"], "ALL", "delivery_partner", "high", "Delivery partner integration down. No orders received.")
    p13 = inc[13]
    for s in p13["scope"]["outage_stores"]:
        add(p13["params"]["outage_day"], p13["params"]["outage_day"], s, "pos", "high", "Card terminal outage during the lunch peak.")
    minor = [
        ("printer", "low", "Receipt printer paper jam, resolved same day."),
        ("wifi", "low", "Guest wifi dropped for an hour."),
        ("refrigeration", "medium", "Display fridge compressor serviced; stock moved to the back fridge."),
        ("signage", "low", "Menu board bulb replaced."),
        ("pos", "low", "Scheduled POS software patch, no customer impact."),
    ]
    for i in range(14):
        sysname, sev, text = minor[i % len(minor)]
        day = C.WINDOW_START + dt.timedelta(days=int(rng.integers(10, 7 * C.N_WEEKS - 10)))
        add(day, day, store_ids[int(rng.integers(0, len(store_ids)))], sysname, sev, text)
    ctx_ops = pd.DataFrame(ops, columns=["date_from", "date_to", "store_id", "system", "severity", "description"])
    ctx_ops.insert(0, "log_id", [f"OPS-{i + 1:03d}" for i in range(len(ctx_ops))])

    # store events ---------------------------------------------------------------------
    ev = []
    p11 = inc[11]
    for s in p11["scope"]["stores"]:
        ev.append((s, p11["date_from"], p11["date_to"], "planned_closure", "Refurbishment closure (new counter and ovens)."))
    for _ in range(8):
        day = C.WINDOW_START + dt.timedelta(days=int(rng.integers(10, 7 * C.N_WEEKS - 10)))
        ev.append((store_ids[int(rng.integers(0, len(store_ids)))], day, day, "local_event", "Local market or school event nearby."))
    ctx_events = pd.DataFrame(ev, columns=["store_id", "date_from", "date_to", "event_type", "description"])
    ctx_events.insert(0, "event_id", [f"EV-{i + 1:03d}" for i in range(len(ctx_events))])

    # holidays -------------------------------------------------------------------------
    ctx_holidays = pd.DataFrame(
        [{"date": d, "holiday_name": n, "kind": k} for d, n, k, _ in C.HOLIDAYS]
    )

    # weather (the recorded actuals, including any planted storms) ----------------------
    region_ids = list(C.REGIONS)
    dates = pd.to_datetime(sim_actual.dates)
    wx = _grid(dates, region_ids)
    wx.columns = ["date", "region_id"]
    wx["rain_mm"] = np.round(sim_actual.rain.ravel(), 1)
    wx["temp_c"] = np.round(sim_actual.temp.ravel(), 1)

    for df in (ctx_promos, ctx_ops, ctx_events):
        for c in ("date_from", "date_to"):
            df[c] = pd.to_datetime(df[c])
    ctx_prices["effective_date"] = pd.to_datetime(ctx_prices["effective_date"])
    ctx_holidays["date"] = pd.to_datetime(ctx_holidays["date"])
    return {
        "ctx_promos": ctx_promos,
        "ctx_price_changes": ctx_prices,
        "ctx_ops_log": ctx_ops,
        "ctx_store_events": ctx_events,
        "ctx_holidays": ctx_holidays,
        "ctx_weather": wx,
    }


# --------------------------------------------------------------------------------------
# Ground truth
# --------------------------------------------------------------------------------------
def fingerprint(tables: dict[str, pd.DataFrame]) -> dict[str, str]:
    out = {}
    for name in sorted(tables):
        h = pd.util.hash_pandas_object(tables[name], index=False).values.tobytes()
        out[name] = hashlib.sha256(h).hexdigest()[:16]
    return out


def compute_truth(spec: dict, noise: Noise, actual: Sim, raw_txn: pd.DataFrame, clean_txn: pd.DataFrame) -> dict:
    results = []
    for inc in spec["incidents"]:
        i = inc["id"]
        d0, d1 = inc["date_from"], inc["date_to"]
        entry = {
            "id": i,
            "name": inc["name"],
            "category": inc["category"],
            "difficulty": inc["difficulty"],
            "held_out": inc["held_out"],
            "date_from": d0,
            "date_to": d1,
            "scope": inc["scope"],
            "expected_stop_at_data_gate": inc["expected_stop_at_data_gate"],
            "expected_answer": inc.get("expected_answer"),
            "causes": inc.get("causes", [inc["category"]]),
        }
        if i in EFFECT_KEYS:
            cf = simulate(spec, noise, ALL_EFFECTS - EFFECT_KEYS[i])
            actual_sales = actual.total_sales(d0, d1)
            cf_sales = cf.total_sales(d0, d1)
            entry["actual_sales"] = round(actual_sales, 2)
            entry["counterfactual_sales"] = round(cf_sales, 2)
            entry["planted_impact_dollars"] = round(actual_sales - cf_sales, 2)
            entry["planted_impact_pct"] = round(100 * (actual_sales - cf_sales) / cf_sales, 2)
            if i == 13:
                parts = {}
                for label, key in (("weather", "13w"), ("pos_outage", "13p")):
                    only = simulate(spec, noise, ALL_EFFECTS - frozenset({"13w", "13p"}) | {key})
                    base = simulate(spec, noise, ALL_EFFECTS - frozenset({"13w", "13p"}))
                    parts[label] = round(only.total_sales(d0, d1) - base.total_sales(d0, d1), 2)
                entry["impact_breakdown"] = parts
        else:
            # data defects have no real business impact; record the distortion in loaded numbers
            ts = pd.Timestamp
            m_raw = raw_txn["date"].between(ts(d0), ts(d1))
            m_cln = clean_txn["date"].between(ts(d0), ts(d1))
            distortion = float(raw_txn.loc[m_raw, "net_sales"].sum() - clean_txn.loc[m_cln, "net_sales"].sum())
            entry["planted_impact_dollars"] = 0.0
            entry["planted_impact_pct"] = 0.0
            entry["reported_distortion_dollars"] = round(distortion, 2)
        results.append(entry)
    return {
        "seed": C.SEED,
        "window": {"start": C.WINDOW_START, "end": C.WINDOW_END, "weeks": C.N_WEEKS},
        "incidents": results,
        "quiet_weeks": spec["quiet_weeks"],
        "held_out_ids": [i["id"] for i in spec["incidents"] if i["held_out"]],
    }


# --------------------------------------------------------------------------------------
# Entry point
# --------------------------------------------------------------------------------------
def build_tables(seed: int = C.SEED) -> tuple[dict[str, pd.DataFrame], dict]:
    spec = load_spec()
    noise = make_noise(seed)
    actual = simulate(spec, noise, ALL_EFFECTS)
    clean = clean_frames(actual)
    raw = inject_defects(clean, spec)
    rng = np.random.default_rng(seed + 1)
    tables: dict[str, pd.DataFrame] = {}
    tables.update(dimensions(rng))
    tables.update(raw)
    tables["raw_load_log"] = load_log(raw)
    tables.update(context_tables(spec, actual, rng))
    truth = compute_truth(spec, noise, actual, raw["raw_sales_txn"], clean["raw_sales_txn"])
    truth["fingerprint"] = fingerprint(tables)
    truth["row_counts"] = {k: int(len(v)) for k, v in tables.items()}
    return tables, truth


def main() -> None:
    tables, truth = build_tables()
    where = save_tables(tables, truth)
    print("Built tables:")
    for name, n in truth["row_counts"].items():
        print(f"  {name:20s} {n:>10,d}")
    print("Wrote:", where)
    print("\nPlanted incidents (impact computed by counterfactual):")
    for inc in truth["incidents"]:
        flag = " [held out]" if inc["held_out"] else ""
        print(
            f"  #{inc['id']:<2d} {inc['category']:24s} ${inc['planted_impact_dollars']:>12,.0f}"
            f"  {inc['date_from']}..{inc['date_to']}{flag}"
        )


if __name__ == "__main__":
    main()
