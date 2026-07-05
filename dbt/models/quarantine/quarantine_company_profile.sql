{{ config(schema='quarantine', materialized='table') }}
-- company profiles with an unrecognized board; kept for forensics, out of Gold
select * from {{ ref('stg_company_profile') }}
where len(_dq_flags) > 0
