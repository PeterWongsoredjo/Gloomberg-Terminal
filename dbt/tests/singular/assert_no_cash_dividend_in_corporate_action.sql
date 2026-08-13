-- cash dividends stay out of the share-count ledger, because that ledger adjusts prices
select event_id
from {{ ref('fct_corporate_action') }}
where event_type = 'CASH_DIVIDEND'
