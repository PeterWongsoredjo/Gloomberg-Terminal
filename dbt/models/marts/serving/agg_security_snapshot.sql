-- Insight Panel header: current identity plus the latest close per security
with latest as (
    select security_id, max(trade_date) as trade_date
    from {{ ref('fct_daily_trade') }}
    group by security_id
),

last_close as (
    select f.security_id, f.trade_date, f.close_idr, f.close_adj_idr, f.price_series_integrity, f.dq_flags
    from {{ ref('fct_daily_trade') }} f
    join latest using (security_id, trade_date)
)

select
    s.security_id,
    s.ticker,
    s.isin,
    s.board,
    s.sector_idxic,
    s.is_fca,
    s.special_notation,
    s.listing_date,
    c.trade_date,
    c.close_idr,
    c.close_adj_idr,
    c.price_series_integrity,
    cast(c.trade_date as timestamp) as data_as_of,
    c.dq_flags
from {{ ref('dim_security') }} s
-- a security enters the snapshot with its first priced close
join last_close c on s.security_id = c.security_id
where s.is_current
