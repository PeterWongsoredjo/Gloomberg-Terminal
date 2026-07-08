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
        tickers,
        row_number() over (partition by item_id order by published_at desc) as _rn
    from {{ ref('stg_news_rss') }}
)

select
    item_id,
    trade_date,
    source,
    lang,
    title,
    summary,
    url,
    published_at,
    tickers
from items
where _rn = 1
