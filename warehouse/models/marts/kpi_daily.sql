-- One definition of every KPI (mirrors tools/metrics.py). Ratios are computed from sums, never averaged.
with s as (
    select store_id, date, channel, sum(transactions) as transactions, sum(units) as units, sum(net_sales) as net_sales
    from {{ ref('fct_sales_daily') }} group by 1, 2, 3
), v as (
    select store_id, date, channel, sum(visits) as visits from {{ ref('fct_visits_daily') }} group by 1, 2, 3
)
select
    s.store_id, s.date, s.channel, s.net_sales, s.transactions, s.units, v.visits,
    s.transactions * 1.0 / nullif(v.visits, 0)  as conversion,
    s.net_sales * 1.0 / nullif(s.transactions, 0) as avg_basket,
    s.units * 1.0 / nullif(s.transactions, 0)    as units_per_txn,
    s.net_sales * 1.0 / nullif(s.units, 0)       as avg_price_per_unit
from s left join v using (store_id, date, channel)
