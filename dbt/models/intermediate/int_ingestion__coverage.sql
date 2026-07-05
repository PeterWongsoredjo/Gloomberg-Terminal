-- ingestion run coverage and freshness, one row per manifest
select
    ingest_run_id,
    source,
    dataset,
    trade_date,
    status,
    record_count,
    expected_universe,
    observed_universe,
    coverage_ratio,
    missing_tickers,
    completed_at
from {{ ref('stg_manifests') }}
