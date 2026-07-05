-- flags a price series left ADJUSTMENT_PENDING for too long, which means a required
-- corporate-action adjustment was never resolved
{% set max_pending_days = 5 %}

select security_id, trade_date, price_series_integrity
from {{ ref('fct_daily_trade') }}
where price_series_integrity = 'ADJUSTMENT_PENDING'
    and trade_date < current_date - interval {{ max_pending_days }} day
