-- Live Tape projection: the latest trading row per security, with freshness
with latest as (
    select security_id, max(trade_date) as trade_date
    from {{ ref('fct_daily_trade') }}
    group by security_id
),

tape as (
    select f.*
    from {{ ref('fct_daily_trade') }} f
    join latest using (security_id, trade_date)
),

freshness as (
    select trade_date, max(completed_at) as data_as_of
    from {{ ref('int_ingestion__coverage') }}
    where source = 'idx_summary' and dataset = 'daily_trade'
    group by trade_date
)

select
    t.security_id,
    s.ticker,
    s.board,
    s.sector_idxic,
    t.trade_date,
    t.close_idr,
    t.close_adj_idr,
    t.change_idr,
    t.volume_shares,
    t.net_foreign_idr,
    t.price_series_integrity,
    coalesce(m.data_as_of, cast(t.trade_date as timestamp)) as data_as_of,
    t.dq_flags
from tape t
join {{ ref('dim_security') }} s on t.security_id = s.security_id and s.is_current
left join freshness m on t.trade_date = m.trade_date
