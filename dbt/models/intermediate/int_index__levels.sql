-- promotable index levels: drops quarantined rows, one row per (index_id, trade_date)
with promotable as (
    select * from {{ ref('stg_idx_summary__index_level') }}
    where len(_dq_flags) = 0
),

ranked as (
    select
        *,
        row_number() over (
            partition by index_id, trade_date
            order by _ingest_date desc, _ingest_run_id desc
        ) as rn
    from promotable
)

select * exclude (rn, _ingest_date, _ingest_run_id, _dq_flags)
from ranked
where rn = 1
