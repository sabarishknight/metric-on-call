-- grain: store x date x channel x daypart x payment_type
select store_id, date, channel, daypart, payment_type, transactions, units, net_sales
from {{ ref('stg_sales_txn') }}
where dup_rank = 1
