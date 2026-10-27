"""Data Quality Auditor tools: "Before we panic, is the data right?"

Checks run on the data AS LOADED (raw tables), the way dbt tests and freshness checks would:
  duplicates, missing store-days, freshness, row-count shape, null spikes, negative values and
  line-vs-transaction reconciliation. The same rules exist as dbt tests in warehouse/ (Phase 2).

A blocking finding means the investigation must STOP and raise a ticket instead of telling a
business story.
"""

from __future__ import annotations

import pandas as pd

from tools.data import Warehouse, to_ts

TXN_KEY = ["store_id", "date", "channel", "daypart", "payment_type"]
LINE_KEY = ["store_id", "date", "channel", "product_id"]
VISIT_KEY = ["store_id", "date", "channel", "daypart"]
EXPECTED_ROWS_PER_STORE_DAY = {"raw_sales_txn": 18, "raw_sales_lines": 30, "raw_visits": 6}


def _range(dates) -> dict:
    dates = sorted(pd.to_datetime(list(dates)))
    return {"from": dates[0].date().isoformat(), "to": dates[-1].date().isoformat()} if dates else {}


def check_duplicates(wh: Warehouse, d0, d1) -> list[dict]:
    out = []
    for name, df, key in (
        ("raw_sales_txn", wh.txn, TXN_KEY),
        ("raw_sales_lines", wh.lines, LINE_KEY),
        ("raw_visits", wh.visits, VISIT_KEY),
    ):
        w = df[df["date"].between(d0, d1)]
        dup = w[w.duplicated(key, keep=False)]
        if dup.empty:
            continue
        extra = w[w.duplicated(key, keep="first")]
        out.append(
            {
                "check": "unique_key",
                "table": name,
                "severity": "blocking",
                "dates": _range(dup["date"]),
                "stores": sorted(dup["store_id"].unique().tolist()),
                "duplicate_rows": int(len(extra)),
                "phantom_net_sales": round(float(extra["net_sales"].sum()), 2) if "net_sales" in extra else None,
                "detail": f"{len(extra)} duplicated rows on the natural key; load_ids {sorted(dup['load_id'].unique().tolist())}",
                "likely_cause": "pipeline_duplicate_load",
            }
        )
    return out


def check_missing_store_days(wh: Warehouse, d0, d1) -> list[dict]:
    expected = wh.stores["store_id"].tolist()
    dates = pd.date_range(d0, d1)
    present = wh.txn[wh.txn["date"].between(d0, d1)][["store_id", "date"]].drop_duplicates()
    full = pd.MultiIndex.from_product([expected, dates], names=["store_id", "date"]).to_frame(index=False)
    miss = full.merge(present, how="left", indicator=True)
    miss = miss[miss["_merge"] == "left_only"].drop(columns="_merge")
    if miss.empty:
        return []
    miss = miss.merge(wh.stores[["store_id", "region_id", "region_name"]], on="store_id")
    # is it explained by a planned closure? (closed stores still report zero rows in this chain,
    # so a planned closure never excuses MISSING rows; we only note it)
    return [
        {
            "check": "missing_store_days",
            "table": "raw_sales_txn",
            "severity": "blocking",
            "dates": _range(miss["date"]),
            "stores": sorted(miss["store_id"].unique().tolist()),
            "regions": sorted(miss["region_name"].unique().tolist()),
            "missing_store_days": int(len(miss)),
            "detail": f"{len(miss)} store-days have no rows at all (expected {len(expected)} stores per day)",
            "likely_cause": "pipeline_missing_data",
        }
    ]


def check_freshness(wh: Warehouse) -> list[dict]:
    last = wh.load_log.groupby("store_id")["date"].max()
    lag = (last.max() - last).dt.days
    late = lag[lag > 0]
    if late.empty:
        return []
    regions = wh.stores.set_index("store_id").loc[late.index, "region_name"].unique().tolist()
    return [
        {
            "check": "freshness",
            "table": "raw_load_log",
            "severity": "blocking",
            "stores": sorted(late.index.tolist()),
            "regions": sorted(regions),
            "max_lag_days": int(late.max()),
            "latest_date_in_chain": last.max().date().isoformat(),
            "detail": f"{len(late)} stores are behind the rest of the chain by up to {int(late.max())} day(s)",
            "likely_cause": "pipeline_missing_data",
        }
    ]


def check_row_shape(wh: Warehouse, d0, d1) -> list[dict]:
    out = []
    for name, df in (("raw_sales_txn", wh.txn), ("raw_sales_lines", wh.lines), ("raw_visits", wh.visits)):
        w = df[df["date"].between(d0, d1)]
        per = w.groupby(["store_id", "date"]).size()
        exp = EXPECTED_ROWS_PER_STORE_DAY[name]
        bad = per[per != exp]
        if not bad.empty:
            dates = bad.index.get_level_values("date")
            out.append(
                {
                    "check": "row_count_shape",
                    "table": name,
                    "severity": "warning",
                    "dates": _range(dates),
                    "store_days": int(len(bad)),
                    "detail": f"{len(bad)} store-days do not have the expected {exp} rows",
                }
            )
    return out


def check_nulls_and_negatives(wh: Warehouse, d0, d1) -> list[dict]:
    out = []
    w = wh.txn[wh.txn["date"].between(d0, d1)]
    for col in ["store_id", "channel", "daypart", "payment_type", "net_sales", "transactions"]:
        share = float(w[col].isna().mean())
        if share > 0.001:
            out.append(
                {"check": "null_spike", "table": "raw_sales_txn", "severity": "blocking", "column": col,
                 "null_share": round(share, 4), "detail": f"{share:.1%} of {col} values are null"}
            )
    for col in ["net_sales", "transactions", "units"]:
        n = int((w[col] < 0).sum())
        if n:
            out.append(
                {"check": "negative_values", "table": "raw_sales_txn", "severity": "blocking", "column": col,
                 "rows": n, "detail": f"{n} negative values in {col}"}
            )
    return out


def check_reconciliation(wh: Warehouse, d0, d1) -> list[dict]:
    a = wh.txn[wh.txn["date"].between(d0, d1)].groupby("date")["net_sales"].sum()
    b = wh.lines[wh.lines["date"].between(d0, d1)].groupby("date")["net_sales"].sum()
    diff = ((a - b).abs() / a.where(a > 0)).dropna()
    bad = diff[diff > 0.005]
    if bad.empty:
        return []
    return [
        {
            "check": "reconciliation_lines_vs_transactions",
            "severity": "blocking",
            "dates": _range(bad.index),
            "detail": f"line-level and transaction-level net sales disagree by >0.5% on {len(bad)} day(s)",
        }
    ]


def run_checks(wh: Warehouse, date_from, date_to) -> dict:
    """Run every check for a window and return a verdict the Supervisor can route on."""
    d0, d1 = to_ts(date_from), to_ts(date_to)
    findings: list[dict] = []
    findings += check_duplicates(wh, d0, d1)
    findings += check_missing_store_days(wh, d0, d1)
    # freshness only matters if the window reaches the end of loaded data
    if d1 >= wh.last_date - pd.Timedelta(days=1):
        findings += check_freshness(wh)
    findings += check_row_shape(wh, d0, d1)
    findings += check_nulls_and_negatives(wh, d0, d1)
    findings += check_reconciliation(wh, d0, d1)

    # de-duplicate "same cause" findings for the headline
    blocking = [f for f in findings if f["severity"] == "blocking"]
    causes = sorted({f.get("likely_cause") for f in blocking if f.get("likely_cause")})
    clean = not blocking
    verdict = {
        "window": {"from": d0.date().isoformat(), "to": d1.date().isoformat()},
        "clean": clean,
        "verdict": "clean" if clean else "STOP_DATA_QUALITY",
        "likely_causes": causes,
        "n_findings": len(findings),
        "findings": findings,
    }
    if not clean:
        affected = sorted({s for f in blocking for s in f.get("stores", [])})
        verdict["ticket"] = {
            "title": "Data quality failure blocks KPI investigation",
            "summary": "; ".join(f["detail"] for f in blocking),
            "affected_stores": affected,
            "recommended_action": "Fix or reload the affected loads, then re-run the investigation.",
        }
    return verdict
