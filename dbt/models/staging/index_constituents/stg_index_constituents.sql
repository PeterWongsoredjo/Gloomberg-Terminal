-- typed index membership, one row per (index, ticker, review period)
with raw as (
    select
        unnest(data) as rec,
        source_version,
        filename
    from {{ source('bronze_index_constituents', 'membership') }}
)

select
    cast(rec.index_id as varchar) as index_id,
    upper(trim(rec.ticker)) as ticker,
    try_cast(rec.effective_from as date) as effective_from,
    try_cast(rec.effective_to as date) as effective_to,
    cast(rec.review_period as varchar) as review_period,
    {{ ingest_run_id_from('filename') }} as _ingest_run_id
from raw
