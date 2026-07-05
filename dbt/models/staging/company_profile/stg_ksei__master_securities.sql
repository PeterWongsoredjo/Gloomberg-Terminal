-- typed KSEI master securities; the ISIN authority, filtered to ordinary equities
with raw as (
    select
        unnest(data) as rec,
        source_version,
        filename
    from {{ source('bronze_ksei', 'master_securities') }}
)

select
    upper(trim(rec.Code)) as ticker,
    -- keep only well-formed Indonesian ISINs; anything else is tracked as NULL
    case
        when regexp_matches(cast(rec.IsinCode as varchar), '^ID[A-Z0-9]{9}[0-9]$')
            then cast(rec.IsinCode as varchar)
        else null
    end as isin,
    cast(rec.Sector as varchar) as ksei_sector,
    cast(rec.Status as varchar) as status,
    {{ ingest_run_id_from('filename') }} as _ingest_run_id
from raw
where upper(cast(rec.Type as varchar)) = 'EQUITY'
