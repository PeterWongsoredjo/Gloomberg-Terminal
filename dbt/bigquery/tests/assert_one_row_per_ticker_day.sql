-- a ticker counted twice on one day would double its attention
select 'mart_foreign_flow_daily' as model, trade_date, ticker, count(*) as n
from {{ ref('mart_foreign_flow_daily') }}
group by trade_date, ticker
having count(*) > 1

union all

select 'mart_news_attention_daily', trade_date, ticker, count(*)
from {{ ref('mart_news_attention_daily') }}
group by trade_date, ticker
having count(*) > 1
