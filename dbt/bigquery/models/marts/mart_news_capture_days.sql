-- no row means unobserved, not a quiet news day
select
    trade_date,
    count(*) as articles_captured,
    count(distinct source) as sources_captured
from {{ source('gold', 'fct_news_item') }}
where trade_date >= date_sub(current_date('Asia/Jakarta'), interval 55 day)
group by trade_date
