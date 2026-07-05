-- effective-dated tick size and ARA/ARB limits per price band (CT-006)
select
    band_id,
    effective_from,
    effective_to,
    price_low_idr,
    price_high_idr,
    tick_idr,
    ara_pct,
    arb_pct,
    regulation_ref,
    (effective_to is null) as is_current
from {{ ref('ref_price_band') }}
