-- one manifest row per ingestion run, typed by the source schema
{{ config(materialized='table') }}

select
    ingest_run_id,
    source,
    dataset,
    trade_date,
    source_version,
    status,
    record_count,
    quality.expected_universe as expected_universe,
    quality.observed_universe as observed_universe,
    cast(quality.coverage_ratio as decimal(6, 4)) as coverage_ratio,
    quality.missing_tickers as missing_tickers,
    completed_at
from {{ source('bronze_manifests', 'manifests') }}
