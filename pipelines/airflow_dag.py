"""Optional Airflow DAG (the Cloud Scheduler + Cloud Run job in infra/ is the default path)."""

from datetime import datetime

from airflow import DAG
from airflow.operators.bash import BashOperator

with DAG("metric_on_call_nightly", start_date=datetime(2025, 7, 1), schedule="30 2 * * *", catchup=False) as dag:
    load = BashOperator(task_id="load", bash_command="python -m pipelines.load_to_warehouse --landing /data/landing")
    build = BashOperator(task_id="dbt_build", bash_command="cd warehouse && dbt build --profiles-dir .")
    scan = BashOperator(task_id="investigate", bash_command="python -m evals.run --config scripted --smoke")
    load >> build >> scan
