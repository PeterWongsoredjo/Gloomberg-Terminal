{{
    config(
        materialized='incremental',
        incremental_strategy='delete+insert',
        unique_key=['ticker', 'trade_date', 'prompt_version'],
        on_schema_change='fail',
    )
}}
with mentions as (
    select * from {{ ref('fct_article_ticker_sentiment') }}
),

weighted as (
    select
        ticker,
        security_id,
        trade_date,
        item_id,
        artifact_id,
        sentiment_score,
        article_confidence,
        generated_at,
        case relevance
            when 'PRIMARY' then 1.0
            when 'SECONDARY' then 0.55
            when 'INCIDENTAL' then 0.20
            else 0.0
        end
        * pow(
            0.5,
            greatest(
                0,
                date_diff('hour', cast(published_at as timestamp), cast(trade_date as timestamp) + interval 1 day)
            ) / 6.0
        )
        * article_confidence as weight
    from mentions
),

rescaled as (
    select
        *,
        weight / max(weight) over (partition by ticker, trade_date) as w
    from weighted
    where weight > 0
),

scored as (
    select
        ticker,
        trade_date,
        max(security_id) as security_id,
        sum(w * sentiment_score) / sum(w) as sentiment_score,
        sum(w * article_confidence) / sum(w) as raw_confidence,
        pow(sum(w), 2) / nullif(sum(w * w), 0) as effective_n,
        list(item_id order by w desc) as evidence_item_ids,
        max(generated_at) as generated_at,
        arg_max(artifact_id, w) as artifact_id
    from rescaled
    group by ticker, trade_date
    having sum(w) > 0
)

select
    security_id,
    ticker,
    trade_date,
    'rollup-v1' as prompt_version,
    artifact_id,
    round(sentiment_score, 4) as sentiment_score,
    case
        when sentiment_score >= 0.15 then 'BULLISH'
        when sentiment_score <= -0.15 then 'BEARISH'
        else 'NEUTRAL'
    end as sentiment_label,
    []::varchar[] as drivers,
    evidence_item_ids[1:10] as evidence_item_ids,
    round(raw_confidence * least(1.0, effective_n / 3.0), 4) as confidence,
    'rollup' as provider,
    'deterministic' as model,
    generated_at,
    cast(generated_at as timestamp) as data_as_of,
    ['DERIVED_ESTIMATE']::varchar[] as dq_flags
from scored
