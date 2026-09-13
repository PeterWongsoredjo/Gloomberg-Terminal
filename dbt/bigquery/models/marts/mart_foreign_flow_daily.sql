{{
    config(
        partition_by={'field': 'trade_date', 'data_type': 'date'},
        cluster_by=['ticker'],
    )
}}
-- 55 days keeps partitions inside the sandbox's 60 day life
with trade as (
    select *
    from {{ source('gold', 'fct_daily_trade') }}
    where trade_date >= date_sub(current_date('Asia/Jakarta'), interval 55 day)
),

security as (
    select * from {{ source('gold', 'dim_security') }}
)

select
    t.trade_date,
    s.ticker,
    s.sector_idxic,
    s.board,
    t.close_idr,
    t.daily_return,
    t.volume_shares,
    t.value_idr,
    t.frequency,
    -- gold labels these idr, IDX sends them as share counts
    t.net_foreign_idr as net_foreign_shares,
    -- shares times the day's average price, an estimate not exact
    cast(round(t.net_foreign_idr * safe_divide(t.value_idr, t.volume_shares)) as int64)
        as net_foreign_value_est_idr,
    safe_divide(t.net_foreign_idr, t.volume_shares) as foreign_intensity,
    cast(round(safe_divide(t.value_idr, t.frequency)) as int64) as avg_trade_value_idr,
    t.price_series_integrity,
    t.dq_flags
from trade t
-- the security version in force on each trade date
join security s
    on t.security_id = s.security_id
    and t.trade_date >= date(s.effective_from)
    and (s.effective_to is null or t.trade_date < date(s.effective_to))
