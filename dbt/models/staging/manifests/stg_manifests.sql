-- one row per CT-003 ingestion run; the coverage and lineage anchor for telemetry
select
    ingest_run_id,
    source,
    dataset,
    cast(trade_date as date) as trade_date,
    source_version,
    status,
    cast(record_count as bigint) as record_count,
    cast(quality.expected_universe as bigint) as expected_universe,
    cast(quality.observed_universe as bigint) as observed_universe,
    cast(quality.coverage_ratio as decimal(6, 4)) as coverage_ratio,
    quality.missing_tickers as missing_tickers,
    cast(completed_at as timestamp) as completed_at
from {{ source('bronze_manifests', 'manifests') }}
