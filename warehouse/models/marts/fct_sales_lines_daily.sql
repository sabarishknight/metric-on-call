-- grain: store x date x channel x product
select store_id, date, channel, product_id, units, net_sales
from {{ ref('stg_sales_lines') }}
where dup_rank = 1
