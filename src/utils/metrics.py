"""
Reusable, dashboard-facing SQL metric queries.

These are the "production" versions of the analytics — written to run
efficiently against the INDEXED schema (post db/indexes.sql). The
deliberately unoptimized versions used for the benchmark story live in
src/query_benchmarker.py / db/benchmark_queries.sql.
"""

CLV_BY_CHANNEL_SQL = """
SELECT
    c.acquisition_channel,
    COUNT(DISTINCT c.customer_id)                                   AS customers,
    COALESCE(SUM(o.order_amount), 0)                                AS total_revenue,
    COALESCE(SUM(o.order_amount) / NULLIF(COUNT(DISTINCT c.customer_id), 0), 0) AS clv_per_customer
FROM customers c
LEFT JOIN orders o ON o.customer_id = c.customer_id
GROUP BY c.acquisition_channel
ORDER BY clv_per_customer DESC;
"""

MONTHLY_RETENTION_SQL = """
WITH first_order AS (
    SELECT customer_id, MIN(DATE_TRUNC('month', order_date)) AS cohort_month
    FROM orders
    GROUP BY customer_id
),
activity AS (
    SELECT
        f.cohort_month,
        DATE_TRUNC('month', o.order_date) AS activity_month,
        COUNT(DISTINCT o.customer_id)      AS active_customers
    FROM orders o
    JOIN first_order f ON f.customer_id = o.customer_id
    GROUP BY f.cohort_month, DATE_TRUNC('month', o.order_date)
)
SELECT
    cohort_month,
    activity_month,
    active_customers,
    EXTRACT(YEAR FROM AGE(activity_month, cohort_month)) * 12
        + EXTRACT(MONTH FROM AGE(activity_month, cohort_month)) AS months_since_signup
FROM activity
ORDER BY cohort_month, activity_month;
"""

CHURN_RISK_COHORT_SQL = """
WITH customer_spend AS (
    SELECT
        customer_id,
        COUNT(*)             AS order_count,
        SUM(order_amount)    AS lifetime_spend,
        MAX(order_date)      AS last_order_date
    FROM orders
    GROUP BY customer_id
)
SELECT
    CASE
        WHEN lifetime_spend >= 1000 THEN 'high_value'
        WHEN lifetime_spend >= 300  THEN 'mid_value'
        ELSE 'low_value'
    END AS spend_tier,
    COUNT(*) FILTER (WHERE last_order_date < CURRENT_DATE - INTERVAL '90 days') AS at_risk_customers,
    COUNT(*)                                                                     AS total_customers,
    ROUND(
        100.0 * COUNT(*) FILTER (WHERE last_order_date < CURRENT_DATE - INTERVAL '90 days')
        / NULLIF(COUNT(*), 0), 1
    ) AS churn_risk_pct
FROM customer_spend
GROUP BY spend_tier
ORDER BY churn_risk_pct DESC;
"""

CAMPAIGN_ROI_SQL = """
SELECT
    ce.channel,
    COUNT(DISTINCT ce.customer_id)                             AS customers_engaged,
    COUNT(DISTINCT ce.customer_id) FILTER (WHERE ce.converted) AS conversions,
    ROUND(
        100.0 * COUNT(DISTINCT ce.customer_id) FILTER (WHERE ce.converted)
        / NULLIF(COUNT(DISTINCT ce.customer_id), 0), 2
    ) AS conversion_rate_pct,
    COALESCE(SUM(o.order_amount), 0)                           AS attributed_revenue
FROM campaign_engagements ce
LEFT JOIN orders o
       ON o.customer_id = ce.customer_id
      AND o.order_date BETWEEN ce.clicked_at AND ce.clicked_at + INTERVAL '7 days'
WHERE ce.clicked_at IS NOT NULL
GROUP BY ce.channel
ORDER BY attributed_revenue DESC;
"""

REPEAT_PURCHASE_90D_SQL = """
WITH order_gaps AS (
    SELECT
        customer_id,
        order_date,
        LAG(order_date) OVER (PARTITION BY customer_id ORDER BY order_date) AS prev_order_date
    FROM orders
)
SELECT
    COUNT(*) FILTER (
        WHERE prev_order_date IS NOT NULL
          AND order_date - prev_order_date <= INTERVAL '90 days'
    )::float / NULLIF(COUNT(*) FILTER (WHERE prev_order_date IS NOT NULL), 0) * 100 AS repeat_purchase_90d_pct
FROM order_gaps;
"""

TABLE_ROW_COUNTS_SQL = """
SELECT 'customers' AS table_name, COUNT(*) AS row_count FROM customers
UNION ALL
SELECT 'orders', COUNT(*) FROM orders
UNION ALL
SELECT 'campaign_engagements', COUNT(*) FROM campaign_engagements;
"""
