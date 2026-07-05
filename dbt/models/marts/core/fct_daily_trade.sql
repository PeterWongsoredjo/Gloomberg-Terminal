{{
    config(
        materialized='incremental',
        incremental_strategy='delete+insert',
        unique_key=['security_id', 'trade_date'],
        on_schema_change='fail',
    )
}}
-- grain: one row per (security_id, trade_date), corporate-action adjusted
with trade as (
    select * from {{ ref('int_daily_trade__deduped') }}
),

security as (
    select * from {{ ref('dim_security') }}
),

flow as (
    select * from {{ ref('int_flow__foreign_net') }}
),

adjust as (
    select * from {{ ref('int_price__adjustment_factors') }}
),

coverage as (
    select trade_date, status
    from {{ ref('int_ingestion__coverage') }}
    where source = 'idx_summary' and dataset = 'daily_trade'
),

base as (
    select
        s.security_id,
        t.trade_date,
        t.open_idr,
        t.high_idr,
        t.low_idr,
        t.close_idr,
        t.previous_idr,
        t.change_idr,
        t.volume_shares,
        t.value_idr,
        t.frequency,
        f.net_foreign_idr,
        s.is_fca,
        coalesce(a.cumulative_factor, 1.0) as adjustment_factor,
        coalesce(a.has_pending_adjustment, false) as has_pending,
        c.status as coverage_status
    from trade t
    -- as-of join: the security version in force on the trade_date
    join security s
        on t.ticker = s.ticker
        and t.trade_date >= cast(s.effective_from as date)
        and (s.effective_to is null or t.trade_date < cast(s.effective_to as date))
    left join flow f on t.ticker = f.ticker and t.trade_date = f.trade_date
    left join adjust a on t.ticker = a.ticker and t.trade_date = a.trade_date
    left join coverage c on t.trade_date = c.trade_date
)

select
    security_id,
    trade_date,
    open_idr,
    high_idr,
    low_idr,
    close_idr,
    previous_idr,
    change_idr,
    round(close_idr * adjustment_factor)::bigint as close_adj_idr,
    adjustment_factor,
    round(volume_shares / nullif(adjustment_factor, 0))::bigint as volume_adj_shares,
    volume_shares,
    value_idr,
    frequency,
    net_foreign_idr,
    (
        round(close_idr * adjustment_factor)
        / nullif(lag(round(close_idr * adjustment_factor))
            over (partition by security_id order by trade_date), 0) - 1
    )::decimal(18, 8) as daily_return,
    case when has_pending then 'ADJUSTMENT_PENDING' else 'CLEAN' end as price_series_integrity,
    list_concat(
        case when has_pending then ['ADJUSTMENT_PENDING']::varchar[] else []::varchar[] end,
        case when coverage_status = 'PARTIAL' then ['COVERAGE_GAP']::varchar[] else []::varchar[] end,
        case when is_fca then ['FCA_PRICING']::varchar[] else []::varchar[] end
    ) as dq_flags
from base
