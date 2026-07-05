{{ config(schema='quarantine', materialized='table') }}
-- daily-trade rows that failed a cast or identity check; kept for forensics, out of Gold
select * from {{ ref('stg_idx_summary__daily_trade') }}
where len(_dq_flags) > 0
