{% snapshot snap_security %}
{{
    config(
        target_schema='snapshots',
        unique_key='security_id',
        strategy='check',
        check_cols=['ticker', 'board', 'sector_idxic', 'isin'],
    )
}}

select * from {{ ref('int_security__scd_prep') }}
{% endsnapshot %}
