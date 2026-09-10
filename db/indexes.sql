-- =====================================================================
-- Index strategy applied AFTER the "before" benchmark run.
-- Targets the exact access paths used by Q1/Q2/Q3 in benchmark_queries.sql
-- =====================================================================

-- Q1: join + group by acquisition_channel / signup_date, join key customer_id
CREATE INDEX IF NOT EXISTS idx_orders_customer_id
    ON orders (customer_id);

CREATE INDEX IF NOT EXISTS idx_customers_channel_signup
    ON customers (acquisition_channel, signup_date);

-- Q2: aggregate by customer_id over orders, filter on last_order_date
-- Composite/covering index lets the planner aggregate via index-only scan
CREATE INDEX IF NOT EXISTS idx_orders_customer_date_amount
    ON orders (customer_id, order_date DESC) INCLUDE (order_amount);

-- Partial index: most queries only care about recent-ish orders for
-- churn windows; keeps the index smaller and faster to scan.
CREATE INDEX IF NOT EXISTS idx_orders_recent
    ON orders (order_date)
    WHERE order_date > (CURRENT_DATE - INTERVAL '400 days');

-- Q3: campaign engagement join/filter/group
CREATE INDEX IF NOT EXISTS idx_engagements_customer_id
    ON campaign_engagements (customer_id);

CREATE INDEX IF NOT EXISTS idx_engagements_clicked_channel_campaign
    ON campaign_engagements (channel, campaign_name)
    WHERE clicked_at IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_engagements_clicked_at
    ON campaign_engagements (clicked_at)
    WHERE clicked_at IS NOT NULL;

-- order_date range join predicate benefits from a BRIN index too (cheap,
-- great for large append-mostly time-series columns)
CREATE INDEX IF NOT EXISTS idx_orders_order_date_brin
    ON orders USING BRIN (order_date);

-- Refresh planner statistics so EXPLAIN ANALYZE reflects the new indexes
ANALYZE customers;
ANALYZE orders;
ANALYZE campaign_engagements;
