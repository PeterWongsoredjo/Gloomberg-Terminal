-- grain: one row per news item_id; the news feed the sentiment agent reads from Gold
with items as (
    select
        item_id,
        trade_date,
        source,
        lang,
        title,
        summary,
        url,
        published_at,
        row_number() over (partition by item_id order by published_at desc) as _rn
    from {{ ref('stg_news_rss') }}
),

-- the newest verdict for each article, whichever prompt version wrote it
verdicts as (
    select
        cast(item_id as varchar) as item_id,
        ticker_sentiments,
        row_number() over (partition by item_id order by generated_at desc) as _rn
    from {{ ref('stg_agent_artifact__article_sentiment') }}
),

exploded as (
    select item_id, unnest(ticker_sentiments) as entry
    from verdicts
    where _rn = 1
),

codes as (
    select distinct
        item_id,
        upper(trim(cast(entry.ticker as varchar))) as ticker
    from exploded
),

-- the issuers the model concluded the article is about
tagged as (
    select item_id, list(ticker order by ticker) as tickers
    from codes
    where ticker is not null and ticker <> ''
    group by item_id
)

select
    i.item_id,
    i.trade_date,
    i.source,
    i.lang,
    i.title,
    i.summary,
    i.url,
    i.published_at,
    coalesce(t.tickers, cast([] as varchar[])) as tickers
from items i
left join tagged t on i.item_id = t.item_id
where i._rn = 1
