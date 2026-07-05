{#  flags ingestion runs whose universe coverage fell below the threshold #}
{% test coverage_ok(model, column_name, threshold=0.95) %}

select *
from {{ model }}
where {{ column_name }} < {{ threshold }}

{% endtest %}
