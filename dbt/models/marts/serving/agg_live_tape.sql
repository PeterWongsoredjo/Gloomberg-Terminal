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
),

-- the price band that governs a security's reference (previous) price
banded as (
    select
        t.*,
        b.ara_pct,
        b.arb_pct
    from tape t
    left join {{ ref('dim_price_band') }} b
        on b.is_current
        and t.previous_idr >= b.price_low_idr
        and (b.price_high_idr is null or t.previous_idr < b.price_high_idr)
)

select
    t.security_id,
    s.ticker,
    s.board,
    s.is_fca,
    s.special_notation,
    s.sector_idxic,
    t.trade_date,
    t.close_idr,
    t.close_adj_idr,
    t.previous_idr,
    t.change_idr,
    -- signed daily move as a fraction; null previous stays null, never divide by zero
    case when t.previous_idr > 0 then t.change_idr::double / t.previous_idr end as change_pct,
    t.volume_shares,
    t.value_idr,
    t.net_foreign_idr,
    -- ARA is a gain locked at the upper band, ARB a loss at the lower band; a flat
    -- price is never at a limit, which also avoids a false hit when rounding collapses
    -- the band at very low prices
    case
        when t.ara_pct is null or t.previous_idr is null then 'NONE'
        when t.change_idr > 0 and t.close_idr >= floor(t.previous_idr * (1 + t.ara_pct)) then 'ARA'
        when t.change_idr < 0 and t.close_idr <= ceil(t.previous_idr * (1 - t.arb_pct)) then 'ARB'
        else 'NONE'
    end as at_price_limit,
    t.price_series_integrity,
    coalesce(m.data_as_of, cast(t.trade_date as timestamp)) as data_as_of,
    t.dq_flags
from banded t
join {{ ref('dim_security') }} s on t.security_id = s.security_id and s.is_current
left join freshness m on t.trade_date = m.trade_date
