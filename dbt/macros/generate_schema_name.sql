{#
  Use a model's configured schema verbatim (e.g. quarantine) instead of prefixing it
  with the target schema. Models without a custom schema fall back to the target.
#}
{% macro generate_schema_name(custom_schema_name, node) -%}
    {%- if custom_schema_name is none -%}
        {{ target.schema }}
    {%- else -%}
        {{ custom_schema_name | trim }}
    {%- endif -%}
{%- endmacro %}
