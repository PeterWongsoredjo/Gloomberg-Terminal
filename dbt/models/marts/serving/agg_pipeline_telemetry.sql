-- Data Telemetry projection: coverage, quarantine counts, and freshness per run
with manifests as (
    select * from {{ ref('int_ingestion__coverage') }}
),

quarantine as (
    select * from {{ ref('int_ingestion__quarantine') }}
)

select
    m.source,
    m.dataset,
    m.trade_date,
    m.status,
    m.coverage_ratio,
    m.record_count,
    m.expected_universe,
    m.observed_universe,
    m.missing_tickers,
    coalesce(q.quarantine_count, 0) as quarantine_count,
    m.completed_at as data_as_of,
    case when m.status = 'PARTIAL' then ['COVERAGE_GAP']::varchar[] else []::varchar[] end as dq_flags
from manifests m
left join quarantine q
    on m.source = q.source and m.dataset = q.dataset and m.trade_date = q.trade_date
