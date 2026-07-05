-- flags a daily move beyond the ARA/ARB limits in force, which is impossible
-- in-session and so signals bad data or an unapplied corporate action
with bands as (
    select * from {{ ref('dim_price_band') }} where is_current
)

select
    f.security_id,
    f.trade_date,
    f.previous_idr,
    f.close_idr,
    (f.close_idr - f.previous_idr)::decimal / nullif(f.previous_idr, 0) as pct_move
from {{ ref('fct_daily_trade') }} f
join bands b
    on f.previous_idr >= b.price_low_idr
    and (b.price_high_idr is null or f.previous_idr < b.price_high_idr)
where f.previous_idr is not null and f.previous_idr > 0
    and (
        (f.close_idr - f.previous_idr)::decimal / f.previous_idr > b.ara_pct
        or (f.previous_idr - f.close_idr)::decimal / f.previous_idr > b.arb_pct
    )
