{#  every security must have exactly one current version #}
{% test scd_one_current(model, column_name) %}

select {{ column_name }}
from {{ model }}
where is_current
group by {{ column_name }}
having count(*) <> 1

{% endtest %}
