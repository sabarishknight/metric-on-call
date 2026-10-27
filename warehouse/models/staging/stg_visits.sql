select
    load_id,
    store_id,
    cast(date as date) as date,
    channel,
    daypart,
    cast(visits as integer) as visits,
    row_number() over (partition by store_id, date, channel, daypart order by load_id) as dup_rank
from {{ source('raw', 'raw_visits') }}
