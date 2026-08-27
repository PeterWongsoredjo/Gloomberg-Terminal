-- typed IDX daily index levels; COMPOSITE is renamed to the canonical IHSG
-- the endpoint takes no date, so the row's own Date is the only truth about when it applies.
-- that Date is an IDX calendar date already, never a UTC instant, so it is parsed not shifted
with raw as (
    select
        unnest(data) as rec,
        ingest_date as _ingest_date,
        source_version,
        filename
    from {{ source('bronze_idx_summary', 'index_level') }}
),

typed as (
    select
        case when rec.IndexCode = 'COMPOSITE' then 'IHSG' else cast(rec.IndexCode as varchar) end as index_id,
        try_cast(rec.Date as date) as trade_date,
        _ingest_date,
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
)

select
    *,
    -- an undated or unreadable row is quarantined, never filed under the capture day
    case
        when trade_date is null then ['SCHEMA_DRIFT_QUARANTINE']::varchar[]
        else []::varchar[]
    end as _dq_flags
from typed
