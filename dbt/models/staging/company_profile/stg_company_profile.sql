-- typed IDX company profiles; maps the Indonesian board name to the CT-004 enum
with raw as (
    select
        unnest(data) as rec,
        ingest_date as _ingest_date,
        source_version,
        filename
    from {{ source('bronze_company_profile', 'profiles') }}
),

typed as (
    select
        upper(trim(rec.KodeEmiten)) as ticker,
        _ingest_date,
        cast(rec.NamaEmiten as varchar) as company_name,
        cast(rec.PapanPencatatan as varchar) as board_label,
        cast(rec.Sektor as varchar) as sector_name_id,
        try_cast(rec.TanggalPencatatan as date) as listing_date,
        coalesce(try_cast(rec.EfekEmiten_Saham as boolean), false) as is_equity,
        {{ ingest_run_id_from('filename') }} as _ingest_run_id
    from raw
),

boarded as (
    select
        *,
        case board_label
            when 'Utama' then 'MAIN'
            when 'Pengembangan' then 'DEVELOPMENT'
            when 'Akselerasi' then 'ACCELERATION'
            when 'Ekonomi Baru' then 'NEW_ECONOMY'
            when 'Pemantauan Khusus' then 'WATCHLIST'
            else null
        end as board
    from typed
)

select
    * exclude (board_label),
    board_label,
    -- an unrecognized board is quarantined, never guessed at
    case
        when board_label is not null and board is null then ['SCHEMA_DRIFT_QUARANTINE']::varchar[]
        else []::varchar[]
    end as _dq_flags
from boarded
where is_equity
