-- =====================================================================
-- Three complex, deliberately UNINDEXED aggregate cohort queries.
-- Run against a fresh schema.sql (no indexes) to capture "before" timings,
-- then again after db/indexes.sql (+ optional partitioning.sql) is applied.
-- These are also embedded as Python strings in src/query_benchmarker.py
-- so they can be timed programmatically — kept here too for transparency
-- and so they can be run by hand with EXPLAIN ANALYZE.
-- =====================================================================

-- ---------------------------------------------------------------------
-- Q1: Customer Lifetime Value (CLV) by acquisition channel & cohort month
-- Anti-patterns: aggregates over full orders table, groups on
-- non-indexed derived expression, correlated nothing but wide scan.
-- ---------------------------------------------------------------------
EXPLAIN (ANALYZE, BUFFERS)
SELECT
    c.acquisition_channel,
    DATE_TRUNC('month', c.signup_date)      AS cohort_month,
    COUNT(DISTINCT c.customer_id)            AS customers,
    SUM(o.order_amount)                      AS total_revenue,
    SUM(o.order_amount) / NULLIF(COUNT(DISTINCT c.customer_id), 0) AS clv_per_customer,
    AVG(o.order_amount)                      AS avg_order_value
FROM customers c
JOIN orders o ON o.customer_id = c.customer_id
GROUP BY c.acquisition_channel, DATE_TRUNC('month', c.signup_date)
ORDER BY cohort_month DESC, clv_per_customer DESC;


-- ---------------------------------------------------------------------
-- Q2: High-churn-risk cohort — customers with >=2 orders historically
-- but NO purchase in the last 90 days, segmented by past spend tier.
-- Anti-patterns: NOT EXISTS subquery on unindexed customer_id/order_date,
-- window function over the full orders table.
-- ---------------------------------------------------------------------
EXPLAIN (ANALYZE, BUFFERS)
WITH customer_spend AS (
    SELECT
        o.customer_id,
        COUNT(*)                           AS order_count,
        SUM(o.order_amount)                AS lifetime_spend,
        MAX(o.order_date)                  AS last_order_date
    FROM orders o
    GROUP BY o.customer_id
)
SELECT
    CASE
        WHEN cs.lifetime_spend >= 1000 THEN 'high_value'
        WHEN cs.lifetime_spend >= 300  THEN 'mid_value'
        ELSE 'low_value'
    END AS spend_tier,
    COUNT(*)                                  AS at_risk_customers,
    AVG(cs.lifetime_spend)                    AS avg_lifetime_spend,
    AVG(CURRENT_DATE - cs.last_order_date::date) AS avg_days_since_last_order
FROM customer_spend cs
WHERE cs.order_count >= 2
  AND cs.last_order_date < CURRENT_DATE - INTERVAL '90 days'
GROUP BY spend_tier
ORDER BY at_risk_customers DESC;


-- ---------------------------------------------------------------------
-- Q3: Multi-channel campaign ROI — engagement -> conversion funnel by
-- channel, joined against order revenue attributable to the same
-- customer within 7 days of a campaign click.
-- Anti-patterns: 3-way join across all tables, date-range join predicate
-- with no covering index, GROUP BY on unindexed varchar column.
-- ---------------------------------------------------------------------
EXPLAIN (ANALYZE, BUFFERS)
SELECT
    ce.channel,
    ce.campaign_name,
    COUNT(DISTINCT ce.customer_id)                                   AS customers_engaged,
    COUNT(DISTINCT ce.customer_id) FILTER (WHERE ce.converted)       AS conversions,
    COUNT(DISTINCT o.order_id)                                       AS attributed_orders,
    COALESCE(SUM(o.order_amount), 0)                                 AS attributed_revenue
FROM campaign_engagements ce
LEFT JOIN orders o
       ON o.customer_id = ce.customer_id
      AND o.order_date BETWEEN ce.clicked_at AND ce.clicked_at + INTERVAL '7 days'
WHERE ce.clicked_at IS NOT NULL
GROUP BY ce.channel, ce.campaign_name
ORDER BY attributed_revenue DESC;
