-- flags a computable adjustment still unresolved past the grace window
{% set max_pending_days = 5 %}

select ticker, trade_date
from {{ ref('int_price__adjustment_factors') }}
where has_unsettled_adjustment
    and trade_date < current_date - interval {{ max_pending_days }} day
