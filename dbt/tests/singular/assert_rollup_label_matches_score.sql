select ticker, trade_date, sentiment_score, sentiment_label
from {{ ref('fct_sentiment') }}
where sentiment_label <> case
    when sentiment_score >= 0.15 then 'BULLISH'
    when sentiment_score <= -0.15 then 'BEARISH'
    else 'NEUTRAL'
end
