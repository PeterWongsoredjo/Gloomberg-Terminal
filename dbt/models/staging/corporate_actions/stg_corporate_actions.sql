-- typed IDX share-count actions; maps the Indonesian action label to the CT-007 enum
with raw as (
    select
        unnest(data) as rec,
        source_version,
        filename
    from {{ source('bronze_corporate_actions', 'issued_history') }}
),

typed as (
    select
        cast(rec.KodeEmiten as varchar) as ticker,
        cast(rec.JenisTindakan as varchar) as action_label,
        {{ resolve_trade_date('rec.TanggalPencatatan') }} as effective_trade_date,
        {{ safe_bigint('rec.JumlahSaham') }} as shares_delta,
        {{ safe_bigint('rec.JumlahSahamSetelahTindakan') }} as shares_after,
        'idx_ca:' || cast(rec.id as varchar) as event_id,
        source_version,
        {{ ingest_run_id_from('filename') }} as _ingest_run_id
    from raw
)

select
    *,
    -- only price-mutating and lifecycle actions are modeled; the rest map to NULL
    case action_label
        when 'stockSplit' then 'STOCK_SPLIT'
        when 'reverseStock' then 'REVERSE_SPLIT'
        when 'sahamBonus' then 'BONUS_SHARES'
        when 'hmetd' then 'RIGHTS_ISSUE'
        when 'Dividen Saham' then 'STOCK_DIVIDEND'
        when 'dividenSaham' then 'STOCK_DIVIDEND'
        when 'delist' then 'DELISTING'
        when 'partialRelisting' then 'RELISTING'
        else null
    end as event_type
from typed
