-- conformed WIB date dimension: a full spine with calendar exceptions applied
with spine as (
    {{ dbt_utils.date_spine(
        datepart="day",
        start_date="cast('2024-01-01' as date)",
        end_date="cast('2027-01-01' as date)"
    ) }}
),

calendar as (
    select * from {{ ref('ref_market_calendar') }}
),

joined as (
    select
        cast(s.date_day as date) as trade_date,
        -- weekends are non-trading by default; the seed overrides for holidays
        coalesce(c.is_trading_day, dayofweek(cast(s.date_day as date)) not in (0, 6)) as is_trading_day,
        coalesce(c.session_template, 'NORMAL') as session_template,
        c.holiday_reason
    from spine s
    left join calendar c on cast(s.date_day as date) = c.trade_date
)

select
    trade_date,
    is_trading_day,
    session_template,
    holiday_reason,
    extract(year from trade_date)::int as year,
    extract(quarter from trade_date)::int as quarter,
    extract(month from trade_date)::int as month,
    extract(week from trade_date)::int as week,
    extract(dow from trade_date)::int as dow,
    -- previous trading day from the calendar, never just date minus one
    max(case when is_trading_day then trade_date end)
        over (order by trade_date rows between unbounded preceding and 1 preceding)
        as prev_trading_date
from joined
