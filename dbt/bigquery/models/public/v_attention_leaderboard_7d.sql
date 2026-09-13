-- authorized on the mart, so readers never touch it directly
{{ config(grant_access_to=[{'project': target.project, 'dataset': target.schema}]) }}

select * from {{ ref('mart_attention_leaderboard_7d') }}
