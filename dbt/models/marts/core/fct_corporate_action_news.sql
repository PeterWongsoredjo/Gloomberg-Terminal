with events as (
    select * from {{ ref('fct_corporate_action') }}
),

anchor as (
    select max(trade_date) as as_of from {{ ref('fct_daily_trade') }}
),

windowed as (
    select e.*
    from events e
    cross join anchor a
    where e.event_type is not null
      and e.effective_trade_date between a.as_of - 90 and a.as_of + 7
),

labelled as (
    select
        *,
        strftime(effective_trade_date, '%d %b %Y') as effective_label,
        case event_type
            when 'STOCK_SPLIT' then 'stock split'
            when 'REVERSE_SPLIT' then 'reverse stock split'
            when 'BONUS_SHARES' then 'bonus share issue'
            when 'STOCK_DIVIDEND' then 'stock dividend'
            when 'RIGHTS_ISSUE' then 'rights issue'
            when 'DELISTING' then 'delisting'
            when 'RELISTING' then 'relisting'
            else lower(replace(event_type, '_', ' '))
        end as event_label,
        (shares_before > 0 and shares_after > 0 and shares_before <> shares_after) as shares_changed
    from windowed
),

described as (
    select
        *,
        'IDX has recorded a ' || event_label || ' for ' || ticker
            || '. The effective trading date is ' || effective_label || '.'
            || case
                when shares_changed
                    then ' Shares outstanding go from ' || cast(shares_before as varchar)
                        || ' to ' || cast(shares_after as varchar)
                        || ', a factor of '
                        || cast(round(cast(shares_after as double) / shares_before, 4) as varchar) || '.'
                else ''
            end
            || case
                when price_factor is not null and price_factor <> 1.0
                    then ' Historical prices are adjusted by a factor of '
                        || cast(round(price_factor, 6) as varchar) || '.'
                else ''
            end
            || case
                when adjustment_pending
                    then ' The price adjustment factor is not yet determinable'
                        || ' from the published share counts.'
                else ''
            end as summary
    from labelled
)

select
    'corp_action:' || event_id as item_id,
    effective_trade_date as trade_date,
    'idx_corporate_action' as source,
    'en' as lang,
    ticker || ' ' || event_label || ', effective ' || effective_label as title,
    summary,
    cast(null as varchar) as url,
    cast(effective_trade_date as timestamp) as published_at,
    [ticker]::varchar[] as tickers,
    'CORPORATE_ACTION' as item_type,
    event_id,
    event_type,
    ticker,
    security_id,
    effective_trade_date
from described
