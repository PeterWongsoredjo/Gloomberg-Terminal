select item_id
from {{ ref('fct_corporate_action_news') }}
where item_id <> 'corp_action:' || event_id

union all

select n.item_id
from {{ ref('fct_news_item') }} n
join {{ ref('fct_corporate_action_news') }} c on n.item_id = c.item_id
