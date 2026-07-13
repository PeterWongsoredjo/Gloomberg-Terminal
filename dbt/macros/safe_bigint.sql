{% macro safe_bigint(expr) %}
  try_cast(try_cast({{ expr }} as double) as bigint)
{% endmacro %}


{% macro ingest_run_id_from(filename_expr) %}
  regexp_extract({{ filename_expr }}, 'part-([0-9A-Z]+)-\d+\.json\.zst', 1)
{% endmacro %}
