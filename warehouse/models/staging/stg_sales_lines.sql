select
    load_id,
    store_id,
    cast(date as date) as date,
    channel,
    product_id,
    cast(units as integer) as units,
    cast(net_sales as double) as net_sales,
    row_number() over (partition by store_id, date, channel, product_id order by load_id) as dup_rank
from {{ source('raw', 'raw_sales_lines') }}
