-- foreign flow lives inside the daily summary; net = buy minus sell value
select
    ticker,
    trade_date,
    foreign_buy_idr,
    foreign_sell_idr,
    coalesce(foreign_buy_idr, 0) - coalesce(foreign_sell_idr, 0) as net_foreign_idr
from {{ ref('int_daily_trade__deduped') }}
