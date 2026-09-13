-- net foreign shares beyond volume would mean IDX changed units
select trade_date, ticker, net_foreign_shares, volume_shares
from {{ ref('mart_foreign_flow_daily') }}
where abs(net_foreign_shares) > volume_shares
