with scored_index as (
    select distinct ticker, trade_date
    from {{ ref('fct_article_ticker_sentiment') }}
    where ticker = 'IHSG' and relevance <> 'INCIDENTAL'
),

rolled_up as (
    select distinct ticker, trade_date
    from {{ ref('fct_sentiment') }}
    where ticker = 'IHSG'
)

select s.ticker, s.trade_date
from scored_index s
left join rolled_up r on s.ticker = r.ticker and s.trade_date = r.trade_date
where r.ticker is null
