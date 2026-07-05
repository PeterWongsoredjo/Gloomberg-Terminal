-- IDX-IC sector dimension (canonical code + localized names)
select
    sector_idxic,
    sector_name_en,
    sector_name_id
from {{ ref('ref_sector_idxic') }}
