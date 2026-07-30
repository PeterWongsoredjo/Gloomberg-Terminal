with latest as (
    select ticker, max(trade_date) as trade_date
    from {{ ref('fct_sentiment') }}
    group by ticker
),

current_sentiment as (
    select f.*
    from {{ ref('fct_sentiment') }} f
    join latest using (ticker, trade_date)
),

ranked as (
    select
        s.*,
        row_number() over (partition by ticker order by generated_at desc) as _rn
    from current_sentiment s
)

select
    r.security_id,
    r.ticker,
    coalesce(d.board, 'INDEX') as board,
    coalesce(d.sector_idxic, 'INDEX') as sector_idxic,
    r.trade_date,
    r.sentiment_score,
    r.sentiment_label,
    r.confidence,
    r.prompt_version,
    r.generated_at,
    cast(r.generated_at as timestamp) as data_as_of,
    r.dq_flags
from ranked r
left join {{ ref('dim_security') }} d on r.security_id = d.security_id and d.is_current
where r._rn = 1
