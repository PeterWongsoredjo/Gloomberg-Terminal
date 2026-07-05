-- cumulative back-adjustment factor per ticker and date; flags unresolved adjustments
with trade_dates as (
    select distinct ticker, trade_date from {{ ref('int_daily_trade__deduped') }}
),

events as (
    select * from {{ ref('int_corporate_action__resolved') }}
    where price_adjustment_required
),

-- an event back-adjusts every price strictly before its effective date
scoped as (
    select
        d.ticker,
        d.trade_date,
        e.price_factor
    from trade_dates d
    join events e
        on d.ticker = e.ticker
        and e.effective_trade_date > d.trade_date
)

select
    ticker,
    trade_date,
    coalesce(exp(sum(ln(price_factor)) filter (where price_factor is not null)), 1.0)
        as cumulative_factor,
    bool_or(price_factor is null) as has_pending_adjustment
from scoped
group by ticker, trade_date
