"""
SQL Performance Optimization & Benchmarking Module.

1. Runs 3 complex, unindexed aggregate cohort queries against the raw
   schema, capturing wall-clock latency + EXPLAIN (ANALYZE, BUFFERS) plans.
2. Applies db/indexes.sql (B-Tree, composite, partial, BRIN indexes).
3. Re-runs the same 3 queries and captures the new latency + plans.
4. Writes benchmark_results.json and prints a before/after summary table.

Usage:
    python -m src.query_benchmarker --run-all
    python -m src.query_benchmarker --before-only
    python -m src.query_benchmarker --apply-indexes-only
"""
from __future__ import annotations

import argparse
import json
import pathlib
import time

from tabulate import tabulate

from src.utils.db import explain_analyze, execute_ddl
from src.utils.db import get_psycopg2_conn

REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
DB_DIR = REPO_ROOT / "db"
RESULTS_PATH = REPO_ROOT / "benchmark_results.json"

QUERIES = {
    "Q1_CLV_by_cohort_month": """
        SELECT
            c.acquisition_channel,
            DATE_TRUNC('month', c.signup_date) AS cohort_month,
            COUNT(DISTINCT c.customer_id)       AS customers,
            SUM(o.order_amount)                 AS total_revenue,
            SUM(o.order_amount) / NULLIF(COUNT(DISTINCT c.customer_id), 0) AS clv_per_customer,
            AVG(o.order_amount)                 AS avg_order_value
        FROM customers c
        JOIN orders o ON o.customer_id = c.customer_id
        GROUP BY c.acquisition_channel, DATE_TRUNC('month', c.signup_date)
        ORDER BY cohort_month DESC, clv_per_customer DESC;
    """,
    "Q2_high_churn_risk_cohort": """
        WITH customer_spend AS (
            SELECT
                o.customer_id,
                COUNT(*)              AS order_count,
                SUM(o.order_amount)   AS lifetime_spend,
                MAX(o.order_date)     AS last_order_date
            FROM orders o
            GROUP BY o.customer_id
        )
        SELECT
            CASE
                WHEN cs.lifetime_spend >= 1000 THEN 'high_value'
                WHEN cs.lifetime_spend >= 300  THEN 'mid_value'
                ELSE 'low_value'
            END AS spend_tier,
            COUNT(*)                                     AS at_risk_customers,
            AVG(cs.lifetime_spend)                        AS avg_lifetime_spend,
            AVG(CURRENT_DATE - cs.last_order_date::date)  AS avg_days_since_last_order
        FROM customer_spend cs
        WHERE cs.order_count >= 2
          AND cs.last_order_date < CURRENT_DATE - INTERVAL '90 days'
        GROUP BY spend_tier
        ORDER BY at_risk_customers DESC;
    """,
    "Q3_campaign_roi_multichannel": """
        SELECT
            ce.channel,
            ce.campaign_name,
            COUNT(DISTINCT ce.customer_id)                              AS customers_engaged,
            COUNT(DISTINCT ce.customer_id) FILTER (WHERE ce.converted)  AS conversions,
            COUNT(DISTINCT o.order_id)                                  AS attributed_orders,
            COALESCE(SUM(o.order_amount), 0)                            AS attributed_revenue
        FROM campaign_engagements ce
        LEFT JOIN orders o
               ON o.customer_id = ce.customer_id
              AND o.order_date BETWEEN ce.clicked_at AND ce.clicked_at + INTERVAL '7 days'
        WHERE ce.clicked_at IS NOT NULL
        GROUP BY ce.channel, ce.campaign_name
        ORDER BY attributed_revenue DESC;
    """,
}


def drop_all_indexes() -> None:
    """Drop non-PK indexes so a fresh 'before' run is truly unindexed."""
    drop_sql = """
    DO $$
    DECLARE
        r RECORD;
    BEGIN
        FOR r IN
            SELECT indexname FROM pg_indexes
            WHERE schemaname = 'public'
              AND indexname NOT LIKE '%_pkey'
        LOOP
            EXECUTE 'DROP INDEX IF EXISTS ' || quote_ident(r.indexname);
        END LOOP;
    END $$;
    """
    execute_ddl(drop_sql)
    print("Dropped all non-primary-key indexes for a clean 'before' benchmark.")


def apply_indexes() -> None:
    sql_text = (DB_DIR / "indexes.sql").read_text()
    execute_ddl(sql_text)
    print("Applied db/indexes.sql (B-Tree / composite / partial / BRIN indexes).")


def run_benchmark_pass(label: str) -> dict:
    print(f"\n=== Benchmark pass: {label} ===")
    results = {}
    for name, sql in QUERIES.items():
        print(f"  Running {name} ...")
        start = time.perf_counter()
        plan_json, latency = explain_analyze(sql)
        wall = time.perf_counter() - start
        results[name] = {
            "latency_seconds": round(latency, 4),
            "wall_clock_seconds": round(wall, 4),
            "planning_time_ms": plan_json.get("Planning Time"),
            "execution_time_ms": plan_json.get("Execution Time"),
            "plan": plan_json.get("Plan", {}),
        }
        print(f"    latency: {latency*1000:.1f} ms")
    return results


def print_summary(before: dict, after: dict) -> None:
    rows = []
    for name in QUERIES:
        b = before.get(name, {}).get("latency_seconds")
        a = after.get(name, {}).get("latency_seconds")
        speedup = (b / a) if (b and a) else None
        rows.append([
            name,
            f"{b*1000:.1f} ms" if b is not None else "n/a",
            f"{a*1000:.1f} ms" if a is not None else "n/a",
            f"{speedup:.1f}x" if speedup else "n/a",
        ])
    print("\n" + tabulate(
        rows,
        headers=["Query", "Before (unindexed)", "After (indexed)", "Speedup"],
        tablefmt="github",
    ))


def main():
    parser = argparse.ArgumentParser(description="Benchmark cohort queries before/after indexing")
    parser.add_argument("--run-all", action="store_true", help="Full before/after benchmark")
    parser.add_argument("--before-only", action="store_true", help="Only run the unindexed pass")
    parser.add_argument("--apply-indexes-only", action="store_true", help="Only apply db/indexes.sql")
    parser.add_argument("--skip-drop", action="store_true",
                         help="Don't drop existing indexes before the 'before' pass")
    args = parser.parse_args()

    if args.apply_indexes_only:
        apply_indexes()
        return

    results = {}

    if args.before_only or args.run_all:
        if not args.skip_drop:
            drop_all_indexes()
        results["before"] = run_benchmark_pass("BEFORE (no secondary indexes)")

    if args.run_all:
        apply_indexes()
        results["after"] = run_benchmark_pass("AFTER (indexed)")
        print_summary(results["before"], results["after"])

    RESULTS_PATH.write_text(json.dumps(results, indent=2, default=str))
    print(f"\nSaved detailed results to {RESULTS_PATH}")


if __name__ == "__main__":
    main()
