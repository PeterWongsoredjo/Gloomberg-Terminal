-- typed 1:1 view of landed INSIGHT artifacts, value fields unpacked
select
    artifact_id,
    run_id,
    ticker,
    cast(window_from as date) as window_from,
    cast(window_to as date) as window_to,
    prompt_version,
    provider,
    model,
    cast(value.headline as varchar) as headline,
    cast(value.narrative as varchar) as narrative,
    cast(value.contradictions as varchar[]) as contradictions,
    cast(value.watchpoints as varchar[]) as watchpoints,
    cast(confidence as double) as confidence,
    cast(quality_flags as varchar[]) as quality_flags,
    cast(generated_at as timestamp) as generated_at,
    {{ ingest_run_id_from('filename') }} as _ingest_run_id
from {{ source('bronze_agent_artifact', 'insight') }}
