-- More than 1% null net_sales on any day is a feed fault.
select date, avg(case when net_sales is null then 1.0 else 0 end) as null_rate
from {{ ref('stg_sales_txn') }}
group by 1
having avg(case when net_sales is null then 1.0 else 0 end) > 0.01
