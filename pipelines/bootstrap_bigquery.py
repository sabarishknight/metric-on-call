"""One-off: load the full synthetic dataset (data/csv/*.csv from `make data`) into BigQuery dataset raw.

    GCP_PROJECT=metric-on-call python -m pipelines.bootstrap_bigquery

WRITE_TRUNCATE, so it is safe to re-run. Uses application-default credentials (gcloud auth
application-default login). The nightly loader (load_to_warehouse) only appends new daily deliveries.
"""

from __future__ import annotations

import os

from common.config import CSV_DIR
from common.store import TABLES


def main() -> None:
    from google.cloud import bigquery

    project = os.environ["GCP_PROJECT"]
    bq = bigquery.Client(project=project)
    for name in TABLES:
        path = CSV_DIR / f"{name}.csv"
        if not path.exists():
            raise SystemExit(f"{path} missing. Run `make data` first.")
        cfg = bigquery.LoadJobConfig(
            source_format=bigquery.SourceFormat.CSV,
            skip_leading_rows=1,
            autodetect=True,
            write_disposition=bigquery.WriteDisposition.WRITE_TRUNCATE,
        )
        with open(path, "rb") as fh:
            job = bq.load_table_from_file(fh, f"{project}.raw.{name}", job_config=cfg)
            job.result()
        print(f"loaded raw.{name}: {bq.get_table(f'{project}.raw.{name}').num_rows} rows")


if __name__ == "__main__":
    main()
