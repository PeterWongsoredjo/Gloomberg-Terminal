{{ config(schema='quarantine', materialized='table') }}
-- index rows whose own Date was absent or unreadable; kept for forensics, out of Gold
select * from {{ ref('stg_idx_summary__index_level') }}
where len(_dq_flags) > 0
