{#
  The single place a UTC instant becomes a WIB trade_date. Never inline +7h anywhere else.
  WIB is UTC+7 with no DST, so the whole calendar/partition key is (utc + 7h)::date.
#}
{% macro resolve_trade_date(utc_expr) %}
  ((({{ utc_expr }})::timestamp + interval 7 hour)::date)
{% endmacro %}
