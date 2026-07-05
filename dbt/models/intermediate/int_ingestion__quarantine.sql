-- quarantined-row counts per ingestion partition, for telemetry
select 'idx_summary' as source, 'daily_trade' as dataset, trade_date, count(*) as quarantine_count
from {{ ref('stg_idx_summary__daily_trade') }}
where len(_dq_flags) > 0
group by trade_date

union all

select 'company_profile' as source, 'profiles' as dataset, _ingest_date as trade_date, count(*) as quarantine_count
from {{ ref('stg_company_profile') }}
where len(_dq_flags) > 0
group by _ingest_date
