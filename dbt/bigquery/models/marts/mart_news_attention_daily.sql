{{
    config(
        partition_by={'field': 'trade_date', 'data_type': 'date'},
        cluster_by=['ticker'],
    )
}}
with latest_read as (
    select *
    from {{ source('gold', 'fct_article_ticker_sentiment') }}
    where trade_date >= date_sub(current_date('Asia/Jakarta'), interval 55 day)
    qualify row_number() over (partition by item_id, ticker order by generated_at desc) = 1
)

select
    trade_date,
    ticker,
    count(*) as articles,
    countif(relevance = 'PRIMARY') as primary_articles,
    round(avg(sentiment_score), 4) as mean_sentiment,
    countif(sentiment_label = 'BULLISH') as bullish_articles,
    countif(sentiment_label = 'BEARISH') as bearish_articles,
    countif(sentiment_label = 'NEUTRAL') as neutral_articles,
    max(data_as_of) as data_as_of
from latest_read
group by trade_date, ticker
