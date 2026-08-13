-- grain: one row per declared dividend line; never a share-count action, never price-adjusting
with events as (
    select * from {{ ref('stg_agent_artifact__cash_dividend') }}
),

latest as (
    select * from (
        select
            *,
            row_number() over (
                partition by filing_id, event_seq
                order by generated_at desc, _ingest_run_id desc
            ) as _rn
        from events
    ) where _rn = 1
),

security as (
    select security_id, ticker from {{ ref('dim_security') }} where is_current
),

-- a correction restates an earlier filing, and both statements are real history
restated as (
    select
        l.*,
        row_number() over (
            partition by l.filing_ticker, l.dividend_kind, coalesce(l.ex_date, l.payment_date)
            order by l.window_from desc, l.generated_at desc
        ) as _restatement_rn
    from latest l
)

select
    d.filing_id || ':' || cast(d.event_seq as varchar) as dividend_id,
    d.filing_id,
    d.event_seq,
    s.security_id,
    d.filing_ticker as ticker,
    d.dividend_kind,
    d.currency,
    d.amount_text,
    d.amount_per_share_sen,
    d.ex_date,
    d.recording_date,
    d.payment_date,
    d.source_span,
    d.event_confidence,
    d.confidence,
    d.window_from as trade_date,
    d.prompt_version,
    d.provider,
    d.model,
    d.artifact_id,
    d.run_id,
    d.generated_at,
    cast(d.generated_at as timestamp) as data_as_of,
    d.quality_flags as dq_flags,
    (d._restatement_rn = 1) as is_current_statement
from restated d
left join security s on d.filing_ticker = s.ticker
