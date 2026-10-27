-- Line-level and ticket-level net sales must agree per day (to the cent, within rounding).
select t.date, t.net_sales as txn_total, l.net_sales as line_total
from (select date, sum(net_sales) as net_sales from {{ ref('fct_sales_daily') }} group by 1) t
join (select date, sum(net_sales) as net_sales from {{ ref('fct_sales_lines_daily') }} group by 1) l using (date)
where abs(t.net_sales - l.net_sales) > 1.0
