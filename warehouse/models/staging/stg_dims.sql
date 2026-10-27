select s.store_id, s.store_name, s.region_id, s.region_name, cast(s.opened_at as date) as opened_at, s.sqm
from {{ source('raw', 'dim_store') }} s
