-- heavily traded names our news never mentions
with bounds as (
    select date_sub(max(trade_date), interval 29 day) as since
    from {{ ref('mart_foreign_flow_daily') }}
),

turnover as (
    select
        f.ticker,
        any_value(f.sector_idxic) as sector_idxic,
        sum(f.value_idr) as value_30d
    from {{ ref('mart_foreign_flow_daily') }} f
    cross join bounds b
    where f.trade_date >= b.since
    group by f.ticker
),

ranked as (
    select *, rank() over (order by value_30d desc) as turnover_rank
    from turnover
),

mentioned as (
    select distinct a.ticker
    from {{ ref('mart_news_attention_daily') }} a
    cross join bounds b
    where a.trade_date >= b.since
)

select
    r.ticker,
    r.sector_idxic,
    r.value_30d,
    r.turnover_rank
from ranked r
left join mentioned m using (ticker)
where m.ticker is null
    and r.turnover_rank <= 100
