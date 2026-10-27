"""Warehouse access.

Tables are written to DuckDB (when installed) and always to CSV. `load_tables()` reads DuckDB if
the file exists and the library is importable, otherwise the CSVs. The analysis tools work on
pandas DataFrames, so the same code runs against both.

Table names (schema `raw` in DuckDB):
  dimensions : dim_store, dim_product
  facts      : raw_sales_txn, raw_sales_lines, raw_visits, raw_load_log
  context    : ctx_promos, ctx_price_changes, ctx_ops_log, ctx_store_events, ctx_holidays,
               ctx_weather
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pandas as pd

from common.config import CSV_DIR, DUCKDB_PATH, TRUTH_PATH

DATE_COLUMNS = {
    "date",
    "date_from",
    "date_to",
    "loaded_at",
    "opened_at",
    "closed_at",
    "start_date",
    "end_date",
    "effective_date",
}

TABLES = [
    "dim_store",
    "dim_product",
    "raw_sales_txn",
    "raw_sales_lines",
    "raw_visits",
    "raw_load_log",
    "ctx_promos",
    "ctx_price_changes",
    "ctx_ops_log",
    "ctx_store_events",
    "ctx_holidays",
    "ctx_weather",
]


def save_tables(tables: dict[str, pd.DataFrame], truth: dict | None = None) -> dict[str, str]:
    """Write all tables to CSV (always) and DuckDB (if available). Returns where they went."""
    written: dict[str, str] = {}
    CSV_DIR.mkdir(parents=True, exist_ok=True)
    for name, df in tables.items():
        df.to_csv(CSV_DIR / f"{name}.csv", index=False)
    written["csv"] = str(CSV_DIR)

    try:
        import duckdb  # noqa: PLC0415
    except ImportError:
        duckdb = None
    if duckdb is not None:
        DUCKDB_PATH.parent.mkdir(parents=True, exist_ok=True)
        if DUCKDB_PATH.exists():
            DUCKDB_PATH.unlink()
        con = duckdb.connect(str(DUCKDB_PATH))
        con.execute("create schema if not exists raw")
        for name, df in tables.items():
            con.register("_df", df)
            con.execute(f"create or replace table raw.{name} as select * from _df")
            con.unregister("_df")
        con.close()
        written["duckdb"] = str(DUCKDB_PATH)

    if truth is not None:
        TRUTH_PATH.parent.mkdir(parents=True, exist_ok=True)
        TRUTH_PATH.write_text(json.dumps(truth, indent=2, default=str))
        written["truth"] = str(TRUTH_PATH)
    return written


def _coerce(df: pd.DataFrame) -> pd.DataFrame:
    for col in df.columns:
        if col in DATE_COLUMNS:
            df[col] = pd.to_datetime(df[col], errors="coerce")
    return df


def _load_bigquery() -> dict[str, pd.DataFrame]:
    """Read every table from the BigQuery `raw` dataset (WAREHOUSE=bigquery).

    Needs `pip install metric-on-call[cloud]`, GCP_PROJECT and application-default credentials.
    Every query carries a hard `maximum_bytes_billed` cap (BQ_MAX_BYTES_BILLED).
    """
    from google.cloud import bigquery  # noqa: PLC0415

    from common.config import get_settings  # noqa: PLC0415

    project = os.environ["GCP_PROJECT"]
    client = bigquery.Client(project=project)
    cfg = bigquery.QueryJobConfig(maximum_bytes_billed=get_settings().max_bytes_billed)
    out: dict[str, pd.DataFrame] = {}
    for name in TABLES:
        out[name] = _coerce(client.query(f"select * from `{project}.raw.{name}`", job_config=cfg).to_dataframe())
    return out


def load_tables(
    duckdb_path: Path | None = None, csv_dir: Path | None = None
) -> dict[str, pd.DataFrame]:
    if duckdb_path is None and csv_dir is None and os.environ.get("WAREHOUSE", "duckdb") == "bigquery":
        return _load_bigquery()
    duckdb_path = duckdb_path or DUCKDB_PATH
    csv_dir = csv_dir or CSV_DIR
    if duckdb_path.exists():
        try:
            import duckdb  # noqa: PLC0415

            con = duckdb.connect(str(duckdb_path), read_only=True)
            out = {name: _coerce(con.execute(f"select * from raw.{name}").df()) for name in TABLES}
            con.close()
            return out
        except ImportError:
            pass
    if not csv_dir.exists():
        raise FileNotFoundError("No data found. Run `make data` first.")
    return {name: _coerce(pd.read_csv(csv_dir / f"{name}.csv")) for name in TABLES}


def load_truth(path: Path | None = None) -> dict:
    path = path or TRUTH_PATH
    if not path.exists():
        raise FileNotFoundError("No incident truth file. Run `make data` first.")
    return json.loads(path.read_text())
