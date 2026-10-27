"""Load landed daily files into the warehouse EXACTLY as delivered (append, no dedupe, no fixes).

Local:  DuckDB schema raw.            python -m pipelines.load_to_warehouse --landing landing
Cloud:  BigQuery dataset raw (WAREHOUSE=bigquery). If LANDING_BUCKET is set, files are pulled from
        gs://$LANDING_BUCKET/inbox/<table>/date=YYYY-MM-DD.csv and, once loaded, moved to loaded/ so a
        nightly run never loads the same delivery twice.

Idempotency is deliberately NOT forced on re-delivery: a file dropped into inbox/ twice loads twice,
which is the real-world fault the Data Quality agent exists to catch.
Not executed in the build session (needs duckdb / google-cloud-bigquery / google-cloud-storage).
"""

from __future__ import annotations

import argparse
import os
from pathlib import Path


def _load_bigquery(landing: str) -> int:
    from google.cloud import bigquery, storage

    project = os.environ["GCP_PROJECT"]
    bq = bigquery.Client(project=project)
    bucket_name = os.environ.get("LANDING_BUCKET")
    gcs_blobs: dict[Path, object] = {}
    bucket = None
    if bucket_name:
        gcs = storage.Client(project=project)
        bucket = gcs.bucket(bucket_name)
        for blob in gcs.list_blobs(bucket_name, prefix="inbox/"):
            if not blob.name.endswith(".csv"):
                continue
            local = Path(landing) / blob.name.removeprefix("inbox/")
            local.parent.mkdir(parents=True, exist_ok=True)
            blob.download_to_filename(str(local))
            gcs_blobs[local] = blob

    n = 0
    for f in sorted(Path(landing).rglob("date=*.csv")):
        table = f.parent.name
        cfg = bigquery.LoadJobConfig(
            source_format=bigquery.SourceFormat.CSV,
            skip_leading_rows=1,
            autodetect=True,  # ignored for existing tables: BigQuery appends using the table schema
            write_disposition=bigquery.WriteDisposition.WRITE_APPEND,
        )
        with open(f, "rb") as fh:
            bq.load_table_from_file(fh, f"{project}.raw.{table}", job_config=cfg).result()
        blob = gcs_blobs.get(f)
        if blob is not None and bucket is not None:
            bucket.rename_blob(blob, blob.name.replace("inbox/", "loaded/", 1))
        n += 1
    return n


def _load_duckdb(landing: str) -> int:
    import duckdb

    from common.config import DUCKDB_PATH

    n = 0
    con = duckdb.connect(str(DUCKDB_PATH))
    con.execute("create schema if not exists raw")
    for f in sorted(Path(landing).rglob("date=*.csv")):
        table = f.parent.name
        con.execute(f"create table if not exists raw.{table} as select * from read_csv_auto('{f}') where 1=0")
        con.execute(f"insert into raw.{table} select * from read_csv_auto('{f}')")
        n += 1
    con.close()
    return n


def load(landing: str) -> int:
    if os.environ.get("WAREHOUSE", "duckdb") == "bigquery":
        return _load_bigquery(landing)
    return _load_duckdb(landing)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--landing", default="landing")
    print(f"loaded {load(ap.parse_args().landing)} files")
