-- deduped index levels, one row per (index_id, trade_date)
select * exclude (rn, _ingest_run_id)
from (
    select
        *,
        row_number() over (partition by index_id, trade_date order by _ingest_run_id desc) as rn
    from {{ ref('stg_idx_summary__index_level') }}
)
where rn = 1
