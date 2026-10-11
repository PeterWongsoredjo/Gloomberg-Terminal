-- prices from an uncleared day must always carry a flag
select f.security_id, f.trade_date, c.status
from {{ ref('fct_daily_trade') }} f
join {{ ref('int_ingestion__coverage') }} c
    on c.trade_date = f.trade_date
    and c.source = 'idx_summary'
    and c.dataset = 'daily_trade'
where c.status <> 'SUCCESS'
    and len(f.dq_flags) = 0
