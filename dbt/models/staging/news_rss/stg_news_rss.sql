-- typed 1:1 view of normalized news items, one row per article
select
    item_id,
    source,
    lang,
    title,
    summary,
    url,
    cast(published_at as timestamp) as published_at,
    ingest_date as trade_date,
    source_version,
    {{ ingest_run_id_from('filename') }} as _ingest_run_id
from {{ source('bronze_news_rss', 'items') }}
