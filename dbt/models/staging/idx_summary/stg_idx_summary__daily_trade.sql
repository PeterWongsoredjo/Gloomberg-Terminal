-- typed 1:1 view of the IDX daily summary; casts fail closed, never coerce
with raw as (
    select
        unnest(data) as rec,
        ingest_date as trade_date,
        source_version,
        filename
    from {{ source('bronze_idx_summary', 'daily_trade') }}
),

typed as (
    select
        upper(trim(rec.StockCode)) as ticker,
        trade_date,
        source_version,
        {{ safe_bigint('rec.Previous') }} as previous_idr,
        {{ safe_bigint('rec.OpenPrice') }} as open_idr,
        {{ safe_bigint('rec.High') }} as high_idr,
        {{ safe_bigint('rec.Low') }} as low_idr,
        {{ safe_bigint('rec.Close') }} as close_idr,
        {{ safe_bigint('rec.Change') }} as change_idr,
        {{ safe_bigint('rec.Volume') }} as volume_shares,
        {{ safe_bigint('rec.Value') }} as value_idr,
        {{ safe_bigint('rec.Frequency') }} as frequency,
        {{ safe_bigint('rec.ForeignBuy') }} as foreign_buy_idr,
        {{ safe_bigint('rec.ForeignSell') }} as foreign_sell_idr,
        {{ safe_bigint('rec.ListedShares') }} as listed_shares,
        {{ safe_bigint('rec.Bid') }} as bid_idr,
        {{ safe_bigint('rec.Offer') }} as offer_idr,
        cast(rec.Remarks as varchar) as remarks,
        {{ ingest_run_id_from('filename') }} as _ingest_run_id,
        rec.Close as _raw_close
    from raw
)

select
    * exclude (_raw_close),
    list_concat(
        case
            when regexp_matches(ticker, '^[A-Z]{4}$') then []::varchar[]
            else ['SCHEMA_DRIFT_QUARANTINE']::varchar[]
        end,
        case
            when _raw_close is not null and close_idr is null then ['SCHEMA_DRIFT_QUARANTINE']::varchar[]
            else []::varchar[]
        end
    ) as _dq_flags
from typed
