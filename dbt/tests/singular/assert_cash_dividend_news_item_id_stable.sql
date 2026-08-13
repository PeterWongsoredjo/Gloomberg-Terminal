-- the feed prefix is a contract, and a dividend item must never collide with another feed
select item_id
from {{ ref('fct_cash_dividend_news') }}
where item_id <> 'cash_dividend:' || dividend_id

union all

select n.item_id
from {{ ref('fct_news_item') }} n
join {{ ref('fct_cash_dividend_news') }} c on n.item_id = c.item_id

union all

select a.item_id
from {{ ref('fct_corporate_action_news') }} a
join {{ ref('fct_cash_dividend_news') }} c on a.item_id = c.item_id
