-- promotable daily trade: drops quarantined rows, one row per ticker and date
with promotable as (
    select * from {{ ref('stg_idx_summary__daily_trade') }}
    where len(_dq_flags) = 0
),

ranked as (
    select
        *,
        row_number() over (partition by ticker, trade_date order by _ingest_run_id desc) as rn
    from promotable
)

select * exclude (rn, _dq_flags)
from ranked
where rn = 1
