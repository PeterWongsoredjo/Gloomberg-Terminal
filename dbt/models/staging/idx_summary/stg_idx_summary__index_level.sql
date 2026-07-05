-- typed IDX daily index levels; COMPOSITE is renamed to the canonical IHSG
with raw as (
    select
        unnest(data) as rec,
        ingest_date as trade_date,
        source_version,
        filename
    from {{ source('bronze_idx_summary', 'index_level') }}
)

select
    case when rec.IndexCode = 'COMPOSITE' then 'IHSG' else cast(rec.IndexCode as varchar) end as index_id,
    trade_date,
    source_version,
    try_cast(rec.Previous as decimal(18, 6)) as prev_level,
    try_cast(rec.Highest as decimal(18, 6)) as high_level,
    try_cast(rec.Lowest as decimal(18, 6)) as low_level,
    try_cast(rec.Close as decimal(18, 6)) as close_level,
    try_cast(rec.Change as decimal(18, 6)) as change_level,
    {{ safe_bigint('rec.NumberOfStock') }} as num_constituents,
    {{ safe_bigint('rec.Volume') }} as volume_shares,
    {{ safe_bigint('rec.Value') }} as value_idr,
    {{ safe_bigint('rec.Frequency') }} as frequency,
    {{ safe_bigint('rec.MarketCapital') }} as market_cap_idr,
    {{ ingest_run_id_from('filename') }} as _ingest_run_id
from raw
