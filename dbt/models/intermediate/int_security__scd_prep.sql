-- one current-state row per security: identity plus board, sector, and ISIN
{{ config(materialized='view') }}

with profile as (
    select * from {{ ref('stg_company_profile') }}
    where len(_dq_flags) = 0
),

profile_one as (
    select *, row_number() over (partition by ticker order by _ingest_run_id desc) as rn
    from profile
),

sector as (
    select sector_idxic, sector_name_id from {{ ref('ref_sector_idxic') }}
),

ksei as (
    select ticker, isin from (
        select ticker, isin,
            row_number() over (partition by ticker order by _ingest_run_id desc) as rn
        from {{ ref('stg_ksei__master_securities') }}
    ) where rn = 1
)

select
    -- identity is keyed on the stable ISIN so a ticker rename versions the same
    -- security; tickers without an ISIN yet fall back to a namespaced ticker key
    (hash(coalesce(k.isin, 'ticker:' || p.ticker)) >> 1)::bigint as security_id,
    p.ticker,
    k.isin,
    p.board,
    (p.board = 'WATCHLIST') as is_fca,
    s.sector_idxic,
    []::varchar[] as special_notation,
    p.listing_date,
    cast(null as date) as delisting_date
from profile_one p
left join sector s on p.sector_name_id = s.sector_name_id
left join ksei k on p.ticker = k.ticker
where p.rn = 1
