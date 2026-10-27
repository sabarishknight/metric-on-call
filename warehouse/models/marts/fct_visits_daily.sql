-- grain: store x date x channel x daypart
select store_id, date, channel, daypart, visits
from {{ ref('stg_visits') }}
where dup_rank = 1
