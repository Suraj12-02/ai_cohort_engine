-- =====================================================================
-- OPTIONAL: Range partitioning strategy for `orders` by order_date (yearly).
-- This is a bonus optimization to demonstrate partition pruning on top of
-- indexing. It rebuilds `orders` as a partitioned table, so run it only
-- after you've captured your "indexed, non-partitioned" benchmark numbers
-- if you want a clean 3-way before/after/partitioned comparison.
--
-- Usage: psql cohort_engine -f db/partitioning.sql
-- =====================================================================

BEGIN;

ALTER TABLE orders RENAME TO orders_legacy;

CREATE TABLE orders (
    order_id             BIGSERIAL,
    customer_id          BIGINT NOT NULL REFERENCES customers(customer_id),
    order_date           TIMESTAMP NOT NULL,
    order_amount         NUMERIC(10,2) NOT NULL,
    product_category     VARCHAR(60) NOT NULL,
    is_repeat_purchase    BOOLEAN NOT NULL DEFAULT FALSE,
    channel               VARCHAR(20),
    PRIMARY KEY (order_id, order_date)
) PARTITION BY RANGE (order_date);

-- Create yearly partitions covering 2018-2027; adjust as needed for your
-- generated data's date range.
DO $$
DECLARE
    yr INT;
BEGIN
    FOR yr IN 2018..2027 LOOP
        EXECUTE format(
            'CREATE TABLE IF NOT EXISTS orders_%1$s PARTITION OF orders
                FOR VALUES FROM (%2$L) TO (%3$L);',
            yr,
            format('%s-01-01', yr),
            format('%s-01-01', yr + 1)
        );
    END LOOP;
END $$;

-- Backfill from the legacy table
INSERT INTO orders (order_id, customer_id, order_date, order_amount,
                     product_category, is_repeat_purchase, channel)
SELECT order_id, customer_id, order_date, order_amount,
       product_category, is_repeat_purchase, channel
FROM orders_legacy;

-- Re-apply indexes per-partition (Postgres propagates indexes created on
-- the parent to all partitions automatically for CREATE INDEX ON orders)
CREATE INDEX IF NOT EXISTS idx_orders_customer_id ON orders (customer_id);
CREATE INDEX IF NOT EXISTS idx_orders_customer_date_amount
    ON orders (customer_id, order_date DESC) INCLUDE (order_amount);

DROP TABLE orders_legacy;

ANALYZE orders;

COMMIT;

-- Verify partition pruning:
-- EXPLAIN ANALYZE SELECT * FROM orders WHERE order_date >= '2025-01-01' AND order_date < '2026-01-01';
