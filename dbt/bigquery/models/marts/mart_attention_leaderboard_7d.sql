with window_bounds as (
    select
        date_sub(max(trade_date), interval 6 day) as window_start,
        max(trade_date) as window_end
    from {{ ref('mart_foreign_flow_daily') }}
),

attention as (
    select
        a.ticker,
        sum(a.articles) as articles_7d,
        sum(a.primary_articles) as primary_articles_7d,
        safe_divide(sum(a.mean_sentiment * a.articles), sum(a.articles)) as mean_sentiment_7d
    from {{ ref('mart_news_attention_daily') }} a
    cross join window_bounds w
    where a.trade_date between w.window_start and w.window_end
    group by a.ticker
),

flow as (
    select
        f.ticker,
        any_value(f.sector_idxic) as sector_idxic,
        sum(f.net_foreign_shares) as net_foreign_shares_7d,
        sum(f.net_foreign_value_est_idr) as net_foreign_value_est_7d_idr,
        sum(f.volume_shares) as volume_7d,
        sum(f.value_idr) as value_7d_idr
    from {{ ref('mart_foreign_flow_daily') }} f
    cross join window_bounds w
    where f.trade_date between w.window_start and w.window_end
    group by f.ticker
),

listed as (
    select distinct ticker from {{ source('gold', 'dim_security') }}
),

observed as (
    select count(*) as news_days_observed
    from {{ ref('mart_news_capture_days') }} c
    cross join window_bounds w
    where c.trade_date between w.window_start and w.window_end
),

subjects as (
    select
        a.*,
        f.* except (ticker),
        -- an index draws market wide news, so it competes separately
        if(l.ticker is null, 'INDEX', 'SECURITY') as subject_type
    from attention a
    left join flow f using (ticker)
    left join listed l using (ticker)
)

select
    w.window_start,
    w.window_end,
    o.news_days_observed,
    s.ticker,
    s.subject_type,
    s.sector_idxic,
    s.articles_7d,
    s.primary_articles_7d,
    round(s.mean_sentiment_7d, 4) as mean_sentiment_7d,
    if(
        s.subject_type = 'SECURITY',
        round(safe_divide(s.articles_7d, sum(if(s.subject_type = 'SECURITY', s.articles_7d, 0)) over ()), 4),
        null
    ) as attention_share,
    s.net_foreign_shares_7d,
    s.net_foreign_value_est_7d_idr,
    s.volume_7d,
    s.value_7d_idr,
    safe_divide(s.net_foreign_shares_7d, s.volume_7d) as foreign_intensity_7d,
    -- describes two signals disagreeing, never a call to act
    case
        when s.articles_7d >= 3 and s.mean_sentiment_7d > 0.3 and s.net_foreign_shares_7d < 0
            then 'POSITIVE_NEWS_FOREIGN_SELLING'
        when s.articles_7d >= 3 and s.mean_sentiment_7d < -0.3 and s.net_foreign_shares_7d > 0
            then 'NEGATIVE_NEWS_FOREIGN_BUYING'
    end as divergence
from subjects s
cross join window_bounds w
cross join observed o
