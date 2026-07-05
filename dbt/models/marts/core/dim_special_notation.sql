-- Notasi Khusus letter dictionary
select
    notation_code,
    description_en,
    description_id
from {{ ref('ref_special_notation') }}
