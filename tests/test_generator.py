"""Phase 1 tests: determinism, integrity, and that every planted incident is really in the data."""

from __future__ import annotations

import pandas as pd
import pytest

from generator import catalog as C
from generator.build import build_tables
from generator.world import load_spec

D = 7 * C.N_WEEKS
S = len(C.STORES)


def ts(x) -> pd.Timestamp:
    return pd.Timestamp(x)


# ---- determinism and integrity -------------------------------------------------------
def test_same_seed_same_data(built):
    _, truth = built
    _, truth2 = build_tables()
    assert truth["fingerprint"] == truth2["fingerprint"]


def test_row_counts(tables):
    ch, dp, pay, prod = len(C.CHANNELS), len(C.DAYPARTS), len(C.PAYMENTS), len(C.PRODUCTS)
    spec = load_spec()
    inc2 = next(i for i in spec["incidents"] if i["id"] == 2)
    n_dup = len(inc2["scope"]["stores"])
    n_missing = 4 * 2  # region of 4 stores x 2 days
    assert len(tables["dim_store"]) == 24
    assert len(tables["dim_product"]) == 15
    assert len(tables["raw_sales_txn"]) == (S * D - n_missing) * ch * dp * pay + n_dup * ch * dp * pay
    assert len(tables["raw_sales_lines"]) == (S * D - n_missing) * ch * prod + n_dup * ch * prod
    assert len(tables["raw_visits"]) == (S * D - n_missing) * ch * dp


def test_no_nulls_in_keys(tables):
    for name, cols in {
        "raw_sales_txn": ["store_id", "date", "channel", "daypart", "payment_type"],
        "raw_sales_lines": ["store_id", "date", "channel", "product_id"],
        "raw_visits": ["store_id", "date", "channel", "daypart"],
    }.items():
        assert not tables[name][cols].isna().any().any(), name


def test_lines_reconcile_with_transactions(tables):
    t = tables["raw_sales_txn"]["net_sales"].sum()
    lines = tables["raw_sales_lines"]["net_sales"].sum()
    assert abs(t - lines) / t < 1e-4


def test_window_is_81_whole_weeks(tables):
    dates = tables["raw_sales_txn"]["date"]
    assert dates.min() == ts("2025-03-31") and dates.max() == ts("2026-10-18")


# ---- ground truth ---------------------------------------------------------------------
def test_truth_has_13_incidents_with_computed_impacts(truth):
    assert [i["id"] for i in truth["incidents"]] == list(range(1, 14))
    for inc in truth["incidents"]:
        assert isinstance(inc["planted_impact_dollars"], float)
    # impacts come from counterfactual runs, so real-effect incidents are non-zero
    nonzero = {i["id"] for i in truth["incidents"] if i["planted_impact_dollars"] != 0}
    assert nonzero == {1, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13}


def test_four_incidents_held_out(truth):
    assert len(truth["held_out_ids"]) == 4


def test_ten_quiet_weeks_do_not_overlap_incidents(truth):
    quiet = [ts(w) for w in truth["quiet_weeks"]]
    assert len(quiet) == 10
    for w in quiet:
        assert w.weekday() == 0
        week_end = w + pd.Timedelta(days=6)
        for inc in truth["incidents"]:
            a, b = ts(inc["date_from"]), ts(inc["date_to"])
            assert week_end < a or w > b, f"quiet week {w.date()} overlaps incident {inc['id']}"
        # and no public holiday inside a quiet week
    for h in C.HOLIDAYS:
        for w in quiet:
            assert not (w <= ts(h[0]) <= w + pd.Timedelta(days=6))


def test_incident_13_split_adds_up(truth):
    inc = next(i for i in truth["incidents"] if i["id"] == 13)
    parts = inc["impact_breakdown"]
    assert parts["weather"] < 0 and parts["pos_outage"] < 0
    assert abs(sum(parts.values()) - inc["planted_impact_dollars"]) / abs(inc["planted_impact_dollars"]) < 0.05


# ---- every incident is really in the data ---------------------------------------------
def _sales(df, **flt):
    m = pd.Series(True, index=df.index)
    for k, v in flt.items():
        m &= df[k].isin(v) if isinstance(v, (list, set, tuple)) else df[k] == v
    return df[m]


def test_incident_1_card_collapse(tables):
    t = tables["raw_sales_txn"]
    stores = ["WS01", "WS02", "WS03"]
    out = _sales(t, store_id=stores, channel="in_store", payment_type="card")
    day = out[out["date"] == ts("2025-07-23")]["transactions"].sum()
    normal = out[out["date"] == ts("2025-07-16")]["transactions"].sum()  # prior Wednesday
    assert day < 0.25 * normal
    control = _sales(t, store_id="WS04", channel="in_store", payment_type="card")
    assert control[control["date"] == ts("2025-07-23")]["transactions"].sum() > 0.6 * control[
        control["date"] == ts("2025-07-16")
    ]["transactions"].sum()


def test_incident_2_duplicate_load(tables):
    t = tables["raw_sales_txn"]
    key = ["store_id", "date", "channel", "daypart", "payment_type"]
    dups = t[t.duplicated(key, keep=False)]
    assert set(dups["date"]) == {ts("2025-08-26")}
    assert dups["store_id"].nunique() == 5
    assert set(dups["load_id"]) == {"L20250826", "L20250826R"}


def test_incident_3_missing_region_days(tables):
    t = tables["raw_sales_txn"]
    ss = t[t["store_id"].str.startswith("SS")]
    assert ss["date"].max() == ts("2026-10-16")
    others = t[~t["store_id"].str.startswith("SS")]
    assert others["date"].max() == ts("2026-10-18")


def test_incident_4_promo_logged_and_visible_in_mix(tables):
    promos = tables["ctx_promos"]
    assert (promos["promo_name"] == "Muffin 2-for-1").sum() == 8
    lines = tables["raw_sales_lines"]
    promo_stores = ["CB01", "CB02", "ES01", "ES02", "ES03", "NS01", "NS02", "NS03"]
    ctrl = ["IW01", "IW02", "IW03", "IW04", "WS01", "WS02", "WS03", "WS04"]

    def share(stores, product, a, b):
        x = lines[lines["store_id"].isin(stores) & lines["date"].between(ts(a), ts(b))]
        return x[x["product_id"] == product]["units"].sum() / x["units"].sum()

    muffin = share(promo_stores, "muffin", "2025-10-13", "2025-10-26") / share(ctrl, "muffin", "2025-10-13", "2025-10-26")
    croissant = share(promo_stores, "croissant", "2025-10-13", "2025-10-26") / share(ctrl, "croissant", "2025-10-13", "2025-10-26")
    assert muffin > 1.4 and croissant < 0.9


def test_incident_5_price_change_logged(tables):
    pc = tables["ctx_price_changes"]
    row = pc.iloc[0]
    assert row["product_id"] == "coffee_large"
    assert abs((row["new_price"] - row["old_price"]) - 0.5) < 0.011
    lines = tables["raw_sales_lines"]
    large = lines[lines["product_id"] == "coffee_large"].assign(p=lambda d: d["net_sales"] / d["units"].where(d["units"] > 0))
    before = large[large["date"].between(ts("2025-11-17"), ts("2025-11-30"))]["p"].median()
    after = large[large["date"].between(ts("2025-12-01"), ts("2025-12-14"))]["p"].median()
    assert 0.45 < after - before < 0.6


def test_incident_6_decline_has_no_log_entry(tables):
    t = tables["raw_visits"]
    v = t[t["store_id"] == "IW04"]
    early = v[v["date"].between(ts("2026-01-05"), ts("2026-01-18"))]["visits"].sum()
    late = v[v["date"].between(ts("2026-03-09"), ts("2026-03-22"))]["visits"].sum()
    peer = t[t["store_id"] == "IW01"]
    peer_ratio = peer[peer["date"].between(ts("2026-03-09"), ts("2026-03-22"))]["visits"].sum() / peer[
        peer["date"].between(ts("2026-01-05"), ts("2026-01-18"))
    ]["visits"].sum()
    assert (late / early) / peer_ratio < 0.88
    ops, ev = tables["ctx_ops_log"], tables["ctx_store_events"]
    window = (ts("2026-01-19"), ts("2026-03-22"))
    for df in (ops, ev):
        hit = df[df["store_id"].isin(["IW04", "SS03", "ALL"]) & df["date_from"].between(*window)]
        # only minor noise entries may exist; nothing high severity / closure explains it
        if "severity" in hit:
            assert not (hit["severity"] == "high").any()
        if "event_type" in hit:
            assert not (hit["event_type"] == "planned_closure").any()


def test_incident_7_heavy_rain_recorded(tables):
    wx = tables["ctx_weather"]
    wk = wx[wx["date"].between(ts("2025-11-24"), ts("2025-11-28"))]
    assert wk["rain_mm"].min() >= 25


def test_incident_8_easter_in_calendar_and_in_visits(tables):
    h = tables["ctx_holidays"]
    assert (ts("2026-04-03") in set(h["date"])) and (ts("2025-04-18") in set(h["date"]))
    v = tables["raw_visits"]
    chain = v.groupby("date")["visits"].sum()
    assert chain[ts("2026-04-05")] < 0.8 * chain[ts("2026-03-29")]  # Easter Sunday vs previous Sunday


def test_incident_9_stockout(tables):
    lines = tables["raw_sales_lines"]
    oat = lines[(lines["product_id"] == "oat_flat_white") & (lines["store_id"] == "NS01")]
    during = oat[oat["date"].between(ts("2026-05-26"), ts("2026-05-29"))]["units"].mean()
    before = oat[oat["date"].between(ts("2026-05-12"), ts("2026-05-25"))]["units"].mean()
    assert during < 0.2 * before
    ops = tables["ctx_ops_log"]
    assert ops["description"].str.contains("Oat milk").sum() == 6


def test_incident_10_delivery_zero(tables):
    t = tables["raw_sales_txn"]
    d = t[(t["date"] == ts("2026-07-01")) & (t["channel"] == "delivery")]
    assert d["net_sales"].sum() == 0
    assert t[(t["date"] == ts("2026-07-01")) & (t["channel"] == "in_store")]["net_sales"].sum() > 0


def test_incident_11_store_closed(tables):
    v = tables["raw_visits"]
    closed = v[(v["store_id"] == "IW03") & v["date"].between(ts("2026-08-03"), ts("2026-08-07"))]
    assert closed["visits"].sum() == 0
    ev = tables["ctx_store_events"]
    assert ((ev["store_id"] == "IW03") & (ev["event_type"] == "planned_closure")).any()


def test_incident_12_simpson(tables):
    t = tables["raw_sales_txn"].merge(tables["dim_store"][["store_id", "region_id"]], on="store_id")
    pre = t[t["date"].between(ts("2026-07-27"), ts("2026-08-16"))]
    win = t[t["date"].between(ts("2026-08-17"), ts("2026-09-06"))]

    def basket(df):
        return df["net_sales"].sum() / df["transactions"].sum()

    assert basket(win) > basket(pre)
    falls = [basket(win[win["region_id"] == r]) < basket(pre[pre["region_id"] == r]) for r in C.REGIONS]
    assert all(falls), "basket must fall within every region while rising overall"


def test_incident_13_two_causes(tables):
    wx = tables["ctx_weather"]
    wk = wx[wx["date"].between(ts("2026-09-15"), ts("2026-09-17"))]
    assert wk["rain_mm"].min() >= 20
    ops = tables["ctx_ops_log"]
    hit = ops[(ops["date_from"] == ts("2026-09-16")) & (ops["system"] == "pos")]
    assert set(hit["store_id"]) == {"ES02", "ES03"}


@pytest.mark.parametrize("col", ["transactions", "units", "net_sales"])
def test_no_negative_values(tables, col):
    assert (tables["raw_sales_txn"][col] >= 0).all()
