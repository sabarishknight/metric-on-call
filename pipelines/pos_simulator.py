"""Replay the synthetic chain day by day into a landing folder, as a POS feed would deliver it.

    python -m pipelines.pos_simulator --out landing/ --from 2025-07-20 --to 2025-07-26
Each day is written to <out>/<table>/date=YYYY-MM-DD.csv (the layout GCS would hold).
"""

from __future__ import annotations

import argparse
from pathlib import Path

from common.store import load_tables

FEED_TABLES = ["raw_sales_txn", "raw_sales_lines", "raw_visits", "raw_load_log"]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="landing")
    ap.add_argument("--from", dest="d0", required=True)
    ap.add_argument("--to", dest="d1", required=True)
    a = ap.parse_args()
    tables = load_tables()
    n = 0
    for t in FEED_TABLES:
        df = tables[t].copy()
        df["date"] = df["date"].astype(str)
        for day, g in df[(df["date"] >= a.d0) & (df["date"] <= a.d1)].groupby("date"):
            p = Path(a.out) / t / f"date={day}.csv"
            p.parent.mkdir(parents=True, exist_ok=True)
            g.to_csv(p, index=False)
            n += 1
    print(f"wrote {n} daily files to {a.out}")


if __name__ == "__main__":
    main()
