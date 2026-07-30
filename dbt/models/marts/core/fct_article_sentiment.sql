{{
    config(
        materialized='incremental',
        incremental_strategy='delete+insert',
        unique_key=['item_id', 'prompt_version'],
        on_schema_change='fail',
    )
}}
with artifacts as (
    select * from {{ ref('stg_agent_artifact__article_sentiment') }}
),

ranked as (
    select
        *,
        row_number() over (
            partition by item_id, prompt_version order by generated_at desc
        ) as _rn
    from artifacts
)

select
    item_id,
    window_from as trade_date,
    prompt_version,
    artifact_id,
    run_id,
    sentiment_score,
    sentiment_label,
    rationale,
    drivers,
    dropped_tickers,
    confidence,
    provider,
    model,
    generated_at,
    cast(generated_at as timestamp) as data_as_of,
    quality_flags as dq_flags
from ranked
where _rn = 1
