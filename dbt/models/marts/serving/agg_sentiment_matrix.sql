-- Sentiment Matrix projection: latest sentiment per security with identity and freshness
with latest as (
    select security_id, max(trade_date) as trade_date
    from {{ ref('fct_sentiment') }}
    group by security_id
),

current_sentiment as (
    select f.*
    from {{ ref('fct_sentiment') }} f
    join latest using (security_id, trade_date)
),

ranked as (
    select
        s.*,
        row_number() over (partition by security_id order by generated_at desc) as _rn
    from current_sentiment s
)

select
    r.security_id,
    d.ticker,
    d.board,
    d.sector_idxic,
    r.trade_date,
    r.sentiment_score,
    r.sentiment_label,
    r.confidence,
    r.prompt_version,
    r.generated_at,
    cast(r.generated_at as timestamp) as data_as_of,
    r.dq_flags
from ranked r
join {{ ref('dim_security') }} d on r.security_id = d.security_id and d.is_current
where r._rn = 1
