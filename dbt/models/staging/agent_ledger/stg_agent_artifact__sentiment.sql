-- typed 1:1 view of landed SENTIMENT artifacts, value fields unpacked
select
    artifact_id,
    run_id,
    ticker,
    cast(window_from as date) as window_from,
    cast(window_to as date) as window_to,
    prompt_version,
    provider,
    model,
    cast(value.sentiment_score as double) as sentiment_score,
    cast(value.sentiment_label as varchar) as sentiment_label,
    cast(value.drivers as varchar[]) as drivers,
    cast(value.evidence_item_ids as varchar[]) as evidence_item_ids,
    cast(confidence as double) as confidence,
    cast(quality_flags as varchar[]) as quality_flags,
    cast(generated_at as timestamp) as generated_at,
    {{ ingest_run_id_from('filename') }} as _ingest_run_id
from {{ source('bronze_agent_artifact', 'sentiment') }}
