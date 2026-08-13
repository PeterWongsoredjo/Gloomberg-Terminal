-- grain: one row per current dividend statement; a filed dividend shaped as a feed item
with dividends as (
    select * from {{ ref('fct_cash_dividend') }} where is_current_statement
),

anchor as (
    select max(trade_date) as as_of from {{ ref('fct_daily_trade') }}
),

-- a filing often states no dates, so the announcement day anchors the feed
windowed as (
    select d.*, coalesce(d.ex_date, d.payment_date, d.trade_date) as feed_date
    from dividends d
    cross join anchor a
    where coalesce(d.ex_date, d.payment_date, d.trade_date) between a.as_of - 90 and a.as_of + 30
),

labelled as (
    select
        *,
        case dividend_kind
            when 'INTERIM' then 'interim cash dividend'
            when 'FINAL' then 'final cash dividend'
            when 'SPECIAL' then 'special cash dividend'
            else 'cash dividend'
        end as event_label,
        case
            when currency = 'IDR' and amount_per_share_sen % 100 = 0
                then 'Rp ' || cast(amount_per_share_sen // 100 as varchar)
            when currency = 'IDR'
                then 'Rp ' || printf('%.2f', amount_per_share_sen / 100.0)
            else amount_text
        end as amount_label
    from windowed
),

described as (
    select
        *,
        ticker || ' has declared an ' || event_label || ' of ' || amount_label || ' per share.'
            || case
                when ex_date is not null
                    then ' The ex-date is ' || strftime(ex_date, '%d %b %Y') || '.'
                else ''
            end
            || case
                when payment_date is not null
                    then ' Payment is on ' || strftime(payment_date, '%d %b %Y') || '.'
                else ''
            end
            || ' Historical prices are not adjusted for cash dividends.' as summary
    from labelled
)

select
    'cash_dividend:' || dividend_id as item_id,
    feed_date as trade_date,
    'idx_dividend_filing' as source,
    'en' as lang,
    ticker || ' ' || event_label || ' ' || amount_label || ' per share' as title,
    summary,
    cast(null as varchar) as url,
    cast(feed_date as timestamp) as published_at,
    [ticker]::varchar[] as tickers,
    'CORPORATE_ACTION' as item_type,
    dividend_id,
    ticker,
    security_id,
    ex_date,
    payment_date
from described
