# BI layer

Point Looker Studio / Metabase / Superset at the `analytics.kpi_daily` table (DuckDB file or BigQuery).
Dimensions: store_id, date, channel. Metrics: net_sales, transactions, units, visits, conversion, avg_basket,
units_per_txn, avg_price_per_unit (ratios are computed from sums; do not average them in the BI tool).

Incident annotations: `outputs/annotations.jsonl` (written when a human approves a brief) has one row per
approved incident: `from`, `to`, `label`, `impact`, `confidence`. Join on date to draw event bands on the net_sales chart.
