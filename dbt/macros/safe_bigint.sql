{#
  Integer-IDR cast that fails closed. Works whether the underlying JSON column read
  as a real number or as text (mixed payloads unify to VARCHAR). A value that cannot
  be parsed returns NULL so the row can be flagged and quarantined, never coerced to 0.
#}
{% macro safe_bigint(expr) %}
  try_cast(try_cast({{ expr }} as double) as bigint)
{% endmacro %}


{#
  Pulls the CT-003 ingest_run_id out of the Bronze object filename for lineage.
  Object keys look like part-{run_id}-{seq}.json.zst.
#}
{% macro ingest_run_id_from(filename_expr) %}
  regexp_extract({{ filename_expr }}, 'part-([0-9A-Z]+)-\d+\.json\.zst', 1)
{% endmacro %}
