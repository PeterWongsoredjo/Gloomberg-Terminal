-- normalizes each modeled action and derives its price factor from share counts
with one as (
    select * from (
        select
            event_id, ticker, event_type, effective_trade_date, shares_delta, shares_after,
            row_number() over (partition by event_id order by _ingest_run_id desc) as rn
        from {{ ref('stg_corporate_actions') }}
        where event_type is not null
    ) where rn = 1
),

calc as (
    select
        *,
        (event_type in ('STOCK_SPLIT', 'REVERSE_SPLIT', 'BONUS_SHARES', 'STOCK_DIVIDEND', 'RIGHTS_ISSUE'))
            as price_adjustment_required,
        case
            when event_type = 'REVERSE_SPLIT' then shares_after + shares_delta
            else shares_after - shares_delta
        end as shares_before
    from one
)

select
    event_id,
    ticker,
    event_type,
    effective_trade_date,
    shares_before,
    shares_after,
    price_adjustment_required,
    -- a rights issue factor needs a TERP no feed provides
    (event_type <> 'RIGHTS_ISSUE') as factor_resolvable,
    case
        when event_type in ('STOCK_SPLIT', 'REVERSE_SPLIT', 'BONUS_SHARES', 'STOCK_DIVIDEND')
            and shares_before > 0 and shares_after > 0 and shares_before <> shares_after
            then (shares_before::decimal(38, 10) / shares_after)
        when event_type in ('DELISTING', 'RELISTING') then 1.0
        else null
    end as price_factor
from calc
