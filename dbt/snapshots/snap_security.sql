{% snapshot snap_security %}
{{
    config(
        target_schema='snapshots',
        unique_key='security_id',
        strategy='check',
        check_cols=['ticker', 'board', 'sector_idxic', 'isin'],
    )
}}
-- SCD-2 identity: a board transfer, notation change, or rename opens a new version
select * from {{ ref('int_security__scd_prep') }}
{% endsnapshot %}
