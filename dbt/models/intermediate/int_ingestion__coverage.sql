-- current ingestion coverage, the newest run wins per feed and date
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
    dq_flags,
    declared_record_total,
    completed_at
from {{ ref('stg_manifests') }}
qualify row_number() over (
    partition by source, dataset, trade_date
    order by completed_at desc, ingest_run_id desc
) = 1
