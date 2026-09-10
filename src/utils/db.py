"""
Database access helpers.

Provides:
- get_engine(): pooled SQLAlchemy engine (used by app.py / ai_copilot.py)
- get_psycopg2_conn(): raw psycopg2 connection (used for COPY and
  EXPLAIN ANALYZE where we want the raw plan/timing without SQLAlchemy
  overhead getting in the way)
- run_sql(): convenience helper returning a pandas DataFrame
- explain_analyze(): runs EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON) and
  returns both the parsed plan and wall-clock latency measured in Python
"""
from __future__ import annotations

import time
from contextlib import contextmanager
from typing import Any

import pandas as pd
import psycopg2
import psycopg2.extras
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine

from src.config import settings

_engine: Engine | None = None


def get_engine() -> Engine:
    global _engine
    if _engine is None:
        _engine = create_engine(
            settings.database_url,
            pool_size=5,
            max_overflow=10,
            pool_pre_ping=True,
            future=True,
        )
    return _engine


@contextmanager
def get_psycopg2_conn():
    """Raw psycopg2 connection — used for COPY loads and EXPLAIN ANALYZE."""
    conn = psycopg2.connect(settings.database_url)
    try:
        yield conn
    finally:
        conn.close()


def run_sql(query: str, params: dict[str, Any] | None = None) -> pd.DataFrame:
    """Execute a SQL statement and return the result as a DataFrame."""
    engine = get_engine()
    with engine.connect() as conn:
        result = conn.execute(text(query), params or {})
        if result.returns_rows:
            rows = result.fetchall()
            cols = result.keys()
            return pd.DataFrame(rows, columns=cols)
        conn.commit()
        return pd.DataFrame()


def execute_ddl(sql_text: str) -> None:
    """Execute one or more DDL/DML statements (e.g. contents of a .sql file)."""
    engine = get_engine()
    with engine.begin() as conn:
        for stmt in _split_statements(sql_text):
            if stmt.strip():
                conn.execute(text(stmt))


def _split_statements(sql_text: str) -> list[str]:
    """Naive splitter on ';' — fine for our DDL files (no stored procs)."""
    return [s.strip() for s in sql_text.split(";") if s.strip()]


def explain_analyze(query: str) -> tuple[dict, float]:
    """
    Runs EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON) for a query and also
    measures Python-side wall clock latency for a plain execution
    (EXPLAIN ANALYZE itself already executes the query once).

    Returns: (plan_json, latency_seconds)
    """
    with get_psycopg2_conn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            start = time.perf_counter()
            cur.execute(f"EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON) {query}")
            plan = cur.fetchone()
            elapsed = time.perf_counter() - start
            conn.rollback()  # EXPLAIN ANALYZE runs the query; no writes to commit
    plan_json = plan["QUERY PLAN"][0] if plan else {}
    # Prefer Postgres's own reported "Execution Time" (ms) when present —
    # more accurate than Python-side wall clock which includes driver overhead.
    exec_time_ms = plan_json.get("Execution Time")
    latency = (exec_time_ms / 1000.0) if exec_time_ms is not None else elapsed
    return plan_json, latency


def time_query(query: str) -> float:
    """Plain latency measurement (no EXPLAIN) — used for simple before/after bars."""
    with get_psycopg2_conn() as conn:
        with conn.cursor() as cur:
            start = time.perf_counter()
            cur.execute(query)
            cur.fetchall()
            conn.rollback()
    return time.perf_counter() - start
