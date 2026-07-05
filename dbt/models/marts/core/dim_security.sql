-- SCD-2 security identity (CT-004), one row per version, sourced from the snapshot
select
    (hash(security_id::varchar || coalesce(dbt_valid_from::varchar, 'x')) >> 1)::bigint as security_sk,
    security_id,
    ticker,
    isin,
    board,
    is_fca,
    sector_idxic,
    special_notation,
    listing_date,
    delisting_date,
    -- the first version is valid from the security's listing date, not the snapshot
    -- load time, so trades that predate the first snapshot still join as-of
    case
        when dbt_valid_from = min(dbt_valid_from) over (partition by security_id)
            then coalesce(cast(listing_date as timestamp), timestamp '1970-01-01')
        else dbt_valid_from
    end as effective_from,
    dbt_valid_to as effective_to,
    (dbt_valid_to is null) as is_current
from {{ ref('snap_security') }}
