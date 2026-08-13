-- typed view of landed CASH_DIVIDEND artifacts, one row per declared dividend line
{%- if dividend_artifacts_landed() %}

with artifacts as (
    select
        artifact_id,
        run_id,
        ticker as filing_ticker,
        cast(window_from as date) as window_from,
        cast(window_to as date) as window_to,
        prompt_version,
        provider,
        model,
        cast(value.filing_id as varchar) as filing_id,
        cast(value.outcome as varchar) as outcome,
        cast(value.filing_confidence as double) as filing_confidence,
        value.events as events,
        cast(confidence as double) as confidence,
        cast(quality_flags as varchar[]) as quality_flags,
        cast(generated_at as timestamp) as generated_at,
        {{ ingest_run_id_from('filename') }} as _ingest_run_id
    from {{ source('bronze_agent_artifact', 'cash_dividend') }}
),

-- a filing that declared nothing is a real answer with no lines to unnest
exploded as (
    select
        a.* exclude (events),
        unnest(a.events) as event,
        -- zero based, to match the seq the postgres projection writes
        generate_subscripts(a.events, 1) - 1 as event_seq
    from artifacts a
    where a.outcome = 'EXTRACTED'
)

select
    artifact_id,
    run_id,
    filing_id,
    event_seq,
    filing_ticker,
    cast(event.ticker as varchar) as event_ticker,
    cast(event.dividend_kind as varchar) as dividend_kind,
    cast(event.currency as varchar) as currency,
    cast(event.amount_text as varchar) as amount_text,
    cast(event.amount_per_share_sen as bigint) as amount_per_share_sen,
    try_cast(event.ex_date as date) as ex_date,
    try_cast(event.recording_date as date) as recording_date,
    try_cast(event.payment_date as date) as payment_date,
    cast(event.source_span as varchar) as source_span,
    cast(event.confidence as double) as event_confidence,
    window_from,
    window_to,
    prompt_version,
    provider,
    model,
    confidence,
    filing_confidence,
    quality_flags,
    generated_at,
    _ingest_run_id
from exploded

{%- else %}

-- no filing has been read yet, which is a real state and not a build failure
select
    cast(null as varchar) as artifact_id,
    cast(null as varchar) as run_id,
    cast(null as varchar) as filing_id,
    cast(null as bigint) as event_seq,
    cast(null as varchar) as filing_ticker,
    cast(null as varchar) as event_ticker,
    cast(null as varchar) as dividend_kind,
    cast(null as varchar) as currency,
    cast(null as varchar) as amount_text,
    cast(null as bigint) as amount_per_share_sen,
    cast(null as date) as ex_date,
    cast(null as date) as recording_date,
    cast(null as date) as payment_date,
    cast(null as varchar) as source_span,
    cast(null as double) as event_confidence,
    cast(null as date) as window_from,
    cast(null as date) as window_to,
    cast(null as varchar) as prompt_version,
    cast(null as varchar) as provider,
    cast(null as varchar) as model,
    cast(null as double) as confidence,
    cast(null as double) as filing_confidence,
    cast(null as varchar[]) as quality_flags,
    cast(null as timestamp) as generated_at,
    cast(null as varchar) as _ingest_run_id
where false

{%- endif %}
