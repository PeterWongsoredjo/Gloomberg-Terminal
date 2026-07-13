{% macro resolve_trade_date(utc_expr) %}
  ((({{ utc_expr }})::timestamp + interval 7 hour)::date)
{% endmacro %}
