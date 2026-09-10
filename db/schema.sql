-- =====================================================================
-- AI Customer Cohort & Query Optimization Engine — Schema DDL
-- Target: PostgreSQL 14+
-- Intentionally created WITHOUT secondary indexes so that
-- query_benchmarker.py can demonstrate a real before/after story.
-- =====================================================================

DROP TABLE IF EXISTS orders CASCADE;
DROP TABLE IF EXISTS campaign_engagements CASCADE;
DROP TABLE IF EXISTS customers CASCADE;

CREATE TABLE customers (
    customer_id         BIGSERIAL PRIMARY KEY,
    first_name          VARCHAR(50)  NOT NULL,
    last_name           VARCHAR(50)  NOT NULL,
    email                VARCHAR(120) NOT NULL,
    signup_date          DATE         NOT NULL,
    city                 VARCHAR(80),
    state                VARCHAR(80),  
    country              VARCHAR(80),
    age                  SMALLINT,
    gender               VARCHAR(20),
    acquisition_channel  VARCHAR(40)  -- organic, paid_search, social, referral, email
);

CREATE TABLE campaign_engagements (
    engagement_id   BIGSERIAL PRIMARY KEY,
    customer_id     BIGINT NOT NULL REFERENCES customers(customer_id),
    channel         VARCHAR(20) NOT NULL,   -- email | sms | whatsapp
    campaign_name   VARCHAR(120) NOT NULL,
    sent_at         TIMESTAMP NOT NULL,
    opened_at       TIMESTAMP,
    clicked_at      TIMESTAMP,
    converted       BOOLEAN NOT NULL DEFAULT FALSE
);

CREATE TABLE orders (
    order_id             BIGSERIAL PRIMARY KEY,
    customer_id          BIGINT NOT NULL REFERENCES customers(customer_id),
    order_date           TIMESTAMP NOT NULL,
    order_amount         NUMERIC(10,2) NOT NULL,
    product_category     VARCHAR(60) NOT NULL,
    is_repeat_purchase    BOOLEAN NOT NULL DEFAULT FALSE,
    channel               VARCHAR(20)   -- web, app, store
);

-- Helpful views (kept lightweight; heavy lifting happens in benchmarked
-- queries so the perf story stays visible).
CREATE OR REPLACE VIEW v_customer_order_summary AS
SELECT
    c.customer_id,
    COUNT(o.order_id)              AS total_orders,
    COALESCE(SUM(o.order_amount),0) AS total_spend,
    MAX(o.order_date)               AS last_order_date
FROM customers c
LEFT JOIN orders o ON o.customer_id = c.customer_id
GROUP BY c.customer_id;

-- Row count sanity checks
-- SELECT 'customers' AS tbl, COUNT(*) FROM customers
-- UNION ALL SELECT 'orders', COUNT(*) FROM orders
-- UNION ALL SELECT 'campaign_engagements', COUNT(*) FROM campaign_engagements;
