{{
    config(
        materialized='incremental',
        incremental_strategy='delete+insert',
        unique_key=['index_id', 'trade_date'],
        on_schema_change='fail',
    )
}}
-- grain: one row per (index_id, trade_date); IHSG and other index levels
select
    index_id,
    trade_date,
    prev_level,
    high_level,
    low_level,
    close_level,
    change_level,
    num_constituents,
    volume_shares,
    value_idr,
    frequency,
    market_cap_idr
from {{ ref('int_index__levels') }}
