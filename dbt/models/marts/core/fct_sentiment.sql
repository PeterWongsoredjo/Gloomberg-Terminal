{{
    config(
        materialized='incremental',
        incremental_strategy='delete+insert',
        unique_key=['security_id', 'trade_date', 'prompt_version'],
        on_schema_change='fail',
    )
}}
-- grain: one row per (security_id, trade_date, prompt_version); the latest analysis wins
with artifacts as (
    select * from {{ ref('stg_agent_artifact__sentiment') }}
),

security as (
    select * from {{ ref('dim_security') }}
),

resolved as (
    select
        s.security_id,
        a.window_from as trade_date,
        a.prompt_version,
        a.artifact_id,
        a.ticker,
        a.sentiment_score,
        a.sentiment_label,
        a.drivers,
        a.evidence_item_ids,
        a.confidence,
        a.provider,
        a.model,
        a.generated_at,
        a.quality_flags as dq_flags,
        row_number() over (
            partition by s.security_id, a.window_from, a.prompt_version
            order by a.generated_at desc
        ) as _rn
    -- as-of join: the security version in force on the window date
    from artifacts a
    join security s
        on a.ticker = s.ticker
        and a.window_from >= cast(s.effective_from as date)
        and (s.effective_to is null or a.window_from < cast(s.effective_to as date))
)

select
    security_id,
    trade_date,
    prompt_version,
    artifact_id,
    ticker,
    sentiment_score,
    sentiment_label,
    drivers,
    evidence_item_ids,
    confidence,
    provider,
    model,
    generated_at,
    cast(generated_at as timestamp) as data_as_of,
    dq_flags
from resolved
where _rn = 1
