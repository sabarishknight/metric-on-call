-- Typed, NOT silently de-duplicated: duplicates are flagged (dup_rank > 1) so the unique test below
-- fails loudly and the Data Quality agent can see the fault. Marts keep dup_rank = 1 only.
select
    load_id,
    store_id,
    cast(date as date) as date,
    channel,
    daypart,
    payment_type,
    cast(transactions as integer) as transactions,
    cast(units as integer) as units,
    cast(net_sales as double) as net_sales,
    row_number() over (
        partition by store_id, date, channel, daypart, payment_type order by load_id
    ) as dup_rank
from {{ source('raw', 'raw_sales_txn') }}
