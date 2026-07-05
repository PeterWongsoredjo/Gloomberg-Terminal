{{
    config(
        materialized='incremental',
        incremental_strategy='delete+insert',
        unique_key=['security_id', 'trade_date'],
        on_schema_change='fail',
    )
}}
-- grain: one row per (security_id, trade_date); foreign buy/sell/net value
with flow as (
    select * from {{ ref('int_flow__foreign_net') }}
),

security as (
    select * from {{ ref('dim_security') }}
)

select
    s.security_id,
    f.trade_date,
    f.foreign_buy_idr,
    f.foreign_sell_idr,
    f.net_foreign_idr
from flow f
join security s
    on f.ticker = s.ticker
    and f.trade_date >= cast(s.effective_from as date)
    and (s.effective_to is null or f.trade_date < cast(s.effective_to as date))
