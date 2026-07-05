-- grain: one row per event_id; the resolved corporate-action ledger (CT-007)
with events as (
    select * from {{ ref('int_corporate_action__resolved') }}
),

security as (
    select security_id, ticker from {{ ref('dim_security') }} where is_current
)

select
    e.event_id,
    s.security_id,
    e.ticker,
    e.event_type,
    e.effective_trade_date,
    e.shares_before,
    e.shares_after,
    e.price_adjustment_required,
    e.price_factor,
    (e.price_adjustment_required and e.price_factor is null) as adjustment_pending
from events e
left join security s on e.ticker = s.ticker
