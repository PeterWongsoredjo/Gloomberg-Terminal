{% macro dividend_artifacts_landed() %}
    {#- true once a filing has been read, so a fresh box builds before the first one -#}
    {%- if not execute -%}
        {{ return(true) }}
    {%- endif -%}
    {%- set counted -%}
        select count(*) as n
        from glob('s3://gloomberg-bronze/agent_artifact/cash_dividend/*/*/*.json.zst')
    {%- endset -%}
    {%- set result = run_query(counted) -%}
    {{ return(result and result.columns[0].values()[0] > 0) }}
{% endmacro %}
