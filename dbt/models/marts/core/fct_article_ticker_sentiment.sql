{{
    config(
        materialized='incremental',
        incremental_strategy='delete+insert',
        unique_key=['item_id', 'ticker', 'prompt_version'],
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
),

exploded as (
    select
        item_id,
        window_from as trade_date,
        prompt_version,
        artifact_id,
        confidence as article_confidence,
        generated_at,
        unnest(ticker_sentiments) as entry
    from ranked
    where _rn = 1
),

typed as (
    select
        item_id,
        trade_date,
        prompt_version,
        artifact_id,
        article_confidence,
        generated_at,
        upper(trim(cast(entry.ticker as varchar))) as ticker,
        cast(entry.sentiment_score as double) as sentiment_score,
        cast(entry.sentiment_label as varchar) as sentiment_label,
        cast(entry.relevance as varchar) as relevance
    from exploded
),

security as (
    select * from {{ ref('dim_security') }}
),

news as (
    select item_id, published_at from {{ ref('fct_news_item') }}
)

select
    t.item_id,
    t.ticker,
    s.security_id,
    t.trade_date,
    t.prompt_version,
    t.artifact_id,
    t.sentiment_score,
    t.sentiment_label,
    t.relevance,
    t.article_confidence,
    coalesce(cast(n.published_at as timestamp), cast(t.generated_at as timestamp)) as published_at,
    t.generated_at,
    cast(t.generated_at as timestamp) as data_as_of
from typed t
left join news n on t.item_id = n.item_id
left join security s
    on t.ticker = s.ticker
    and t.trade_date >= cast(s.effective_from as date)
    and (s.effective_to is null or t.trade_date < cast(s.effective_to as date))
