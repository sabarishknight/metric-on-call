-- Every trading store-day should have the same number of sales rows; a different count means a missing or doubled load.
select store_id, date, count(*) as n
from {{ ref('stg_sales_txn') }}
group by 1, 2
having count(*) <> 18
