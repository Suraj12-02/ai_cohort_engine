"""
Synthetic Data Pipeline — generates a realistic retail relational dataset:

    customers               (~N rows)
    campaign_engagements    (~N * engagements_per_customer rows, email/sms/whatsapp)
    orders                  (~N * orders_per_customer rows, power-law skewed)

Designed to scale to 1M-10M+ total rows. Uses batched `COPY FROM STDIN`
(via psycopg2.copy_expert with an in-memory CSV buffer) instead of ORM
inserts — this is the only practical way to load millions of rows in
minutes rather than hours.

Usage:
    python -m src.data_generator --customers 200000 --orders-per-customer 3 --engagements-per-customer 4
    python -m src.data_generator --customers 1500000 --orders-per-customer 4 --engagements-per-customer 4 --batch-size 50000
"""
from __future__ import annotations

import argparse
import csv
import io
import random
import time
from datetime import datetime, timedelta

import numpy as np
from faker import Faker
from tqdm import tqdm

from src.utils.db import get_psycopg2_conn

fake = Faker()
Faker.seed(42)
random.seed(42)
np.random.seed(42)

ACQUISITION_CHANNELS = ["organic", "paid_search", "social", "referral", "email"]
GENDERS = ["male", "female", "non_binary", "prefer_not_to_say"]
PRODUCT_CATEGORIES = [
    "apparel", "electronics", "home_goods", "beauty", "grocery",
    "sports", "toys", "books", "footwear", "accessories",
]
ORDER_CHANNELS = ["web", "app", "store"]
ENGAGEMENT_CHANNELS = ["email", "sms", "whatsapp"]
CAMPAIGN_NAMES = [
    "summer_sale", "black_friday", "new_arrivals", "winback_30d",
    "loyalty_rewards", "flash_sale_weekend", "back_to_school", "holiday_gift_guide",
]

SIGNUP_START = datetime(2019, 1, 1)
SIGNUP_END = datetime(2026, 6, 1)


def _random_date(start: datetime, end: datetime) -> datetime:
    delta = end - start
    seconds = random.randint(0, int(delta.total_seconds()))
    return start + timedelta(seconds=seconds)


def _copy_csv(conn, table: str, columns: list[str], rows: list[tuple]) -> None:
    """Bulk-load rows into a table via COPY FROM STDIN (fast path)."""
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerows(rows)
    buf.seek(0)
    with conn.cursor() as cur:
        cur.copy_expert(
            f"COPY {table} ({', '.join(columns)}) FROM STDIN WITH (FORMAT csv, NULL '')",
            buf,
        )
    conn.commit()


def generate_customers(n: int, batch_size: int) -> None:
    print(f"[1/3] Generating {n:,} customers...")
    columns = [
        "first_name", "last_name", "email", "signup_date",
        "city", "state", "country", "age", "gender", "acquisition_channel",
    ]
    with get_psycopg2_conn() as conn:
        batch = []
        for i in tqdm(range(n), desc="customers"):
            first = fake.first_name()
            last = fake.last_name()
            email = f"{first.lower()}.{last.lower()}{i}@example.com"
            signup = _random_date(SIGNUP_START, SIGNUP_END).date()
            batch.append((
                first, last, email, signup,
                fake.city(), fake.state(), "USA",
                random.randint(18, 75),
                random.choice(GENDERS),
                random.choice(ACQUISITION_CHANNELS),
            ))
            if len(batch) >= batch_size:
                _copy_csv(conn, "customers", columns, batch)
                batch = []
        if batch:
            _copy_csv(conn, "customers", columns, batch)
    print(f"  -> customers loaded.")


def _customer_id_range() -> tuple[int, int]:
    with get_psycopg2_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT MIN(customer_id), MAX(customer_id) FROM customers;")
            lo, hi = cur.fetchone()
    return lo, hi


def generate_orders(avg_orders_per_customer: int, batch_size: int) -> None:
    """
    Power-law skewed: most customers order a few times, a small segment
    orders much more often (realistic "whale" behavior) — good fodder for
    CLV / churn cohort analysis.
    """
    lo, hi = _customer_id_range()
    n_customers = hi - lo + 1
    print(f"[2/3] Generating orders for {n_customers:,} customers "
          f"(avg {avg_orders_per_customer}/customer)...")

    columns = ["customer_id", "order_date", "order_amount", "product_category",
               "is_repeat_purchase", "channel"]

    with get_psycopg2_conn() as conn:
        batch = []
        total_written = 0
        for cust_id in tqdm(range(lo, hi + 1), desc="orders (by customer)"):
            # Power-law: draw order count from a shifted exponential so most
            # customers get 0-3 orders, a tail gets 10-20+.
            n_orders = int(np.random.exponential(scale=avg_orders_per_customer))
            n_orders = max(0, min(n_orders, 40))
            prev_date = None
            for k in range(n_orders):
                order_date = _random_date(SIGNUP_START, datetime(2026, 9, 1))
                is_repeat = prev_date is not None and (order_date - prev_date).days <= 90
                prev_date = order_date if prev_date is None or order_date > prev_date else prev_date
                amount = round(float(np.random.gamma(shape=2.0, scale=35.0) + 5), 2)
                batch.append((
                    cust_id, order_date, amount,
                    random.choice(PRODUCT_CATEGORIES),
                    is_repeat,
                    random.choice(ORDER_CHANNELS),
                ))
            if len(batch) >= batch_size:
                _copy_csv(conn, "orders", columns, batch)
                total_written += len(batch)
                batch = []
        if batch:
            _copy_csv(conn, "orders", columns, batch)
            total_written += len(batch)
    print(f"  -> {total_written:,} orders loaded.")


def generate_engagements(avg_engagements_per_customer: int, batch_size: int) -> None:
    lo, hi = _customer_id_range()
    n_customers = hi - lo + 1
    print(f"[3/3] Generating campaign engagements for {n_customers:,} customers "
          f"(avg {avg_engagements_per_customer}/customer)...")

    columns = ["customer_id", "channel", "campaign_name", "sent_at",
               "opened_at", "clicked_at", "converted"]

    with get_psycopg2_conn() as conn:
        batch = []
        total_written = 0
        for cust_id in tqdm(range(lo, hi + 1), desc="engagements (by customer)"):
            n_eng = np.random.poisson(avg_engagements_per_customer)
            for _ in range(n_eng):
                sent_at = _random_date(SIGNUP_START, datetime(2026, 9, 1))
                opened = None
                clicked = None
                converted = False
                if random.random() < 0.45:  # open rate
                    opened = sent_at + timedelta(hours=random.uniform(0.1, 48))
                    if random.random() < 0.35:  # click-through rate given open
                        clicked = opened + timedelta(minutes=random.uniform(1, 600))
                        if random.random() < 0.20:  # conversion rate given click
                            converted = True
                batch.append((
                    cust_id,
                    random.choice(ENGAGEMENT_CHANNELS),
                    random.choice(CAMPAIGN_NAMES),
                    sent_at, opened, clicked, converted,
                ))
            if len(batch) >= batch_size:
                _copy_csv(conn, "campaign_engagements", columns, batch)
                total_written += len(batch)
                batch = []
        if batch:
            _copy_csv(conn, "campaign_engagements", columns, batch)
            total_written += len(batch)
    print(f"  -> {total_written:,} campaign engagements loaded.")


def main():
    parser = argparse.ArgumentParser(description="Generate synthetic retail data")
    parser.add_argument("--customers", type=int, default=200_000)
    parser.add_argument("--orders-per-customer", type=int, default=3)
    parser.add_argument("--engagements-per-customer", type=int, default=4)
    parser.add_argument("--batch-size", type=int, default=20_000)
    parser.add_argument("--skip-customers", action="store_true",
                         help="Skip customer generation (append orders/engagements to existing customers)")
    args = parser.parse_args()

    start = time.perf_counter()

    if not args.skip_customers:
        generate_customers(args.customers, args.batch_size)

    generate_orders(args.orders_per_customer, args.batch_size)
    generate_engagements(args.engagements_per_customer, args.batch_size)

    elapsed = time.perf_counter() - start
    print(f"\nDone in {elapsed:.1f}s.")
    print("Row counts:")
    with get_psycopg2_conn() as conn:
        with conn.cursor() as cur:
            for tbl in ("customers", "orders", "campaign_engagements"):
                cur.execute(f"SELECT COUNT(*) FROM {tbl};")
                print(f"  {tbl:<25} {cur.fetchone()[0]:,}")


if __name__ == "__main__":
    main()
