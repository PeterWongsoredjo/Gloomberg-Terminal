-- every dashboard number travels with its age
with latest_price_coverage as (
    select status, coverage_ratio
    from {{ source('gold', 'agg_pipeline_telemetry') }}
    where source = 'idx_summary' and dataset = 'daily_trade'
    qualify row_number() over (order by trade_date desc, data_as_of desc) = 1
),

domains as (
    select 'prices' as domain, max(trade_date) as latest_trade_date
    from {{ source('gold', 'fct_daily_trade') }}
    union all
    select 'news', max(trade_date)
    from {{ source('gold', 'fct_news_item') }}
    union all
    select 'sentiment', max(trade_date)
    from {{ source('gold', 'fct_article_ticker_sentiment') }}
)

select
    d.domain,
    d.latest_trade_date,
    date_diff(current_date('Asia/Jakarta'), d.latest_trade_date, day) as days_behind,
    (select max(published_at) from {{ source('gold', 'publish_manifest') }}) as mirrored_at,
    c.status as price_coverage_status,
    c.coverage_ratio as price_coverage_ratio
from domains d
cross join latest_price_coverage c
