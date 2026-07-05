{#  flags any price that is not a whole multiple of its band tick #}
{% test tick_aligned(model, column_name) %}

with bands as (
    select * from {{ ref('dim_price_band') }} where is_current
)

select m.*
from {{ model }} m
join bands b
    on m.{{ column_name }} >= b.price_low_idr
    and (b.price_high_idr is null or m.{{ column_name }} < b.price_high_idr)
where m.{{ column_name }} is not null
    and m.{{ column_name }} % b.tick_idr <> 0

{% endtest %}
