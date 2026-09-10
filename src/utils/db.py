"""
Database access helpers.

Provides:
- get_engine(): pooled SQLAlchemy engine
- get_psycopg2_conn(): raw psycopg2 connection
- run_sql(): execute SQL and return a pandas DataFrame
- execute_ddl(): execute SQL statements from .sql files
- explain_analyze(): run EXPLAIN ANALYZE and return the plan + timing
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
    """Return a pooled SQLAlchemy engine."""
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
    """Return a raw psycopg2 database connection."""
    conn = psycopg2.connect(settings.database_url)

    try:
        yield conn
    finally:
        conn.close()


def run_sql(
    query: str,
    params: dict[str, Any] | None = None,
) -> pd.DataFrame:
    """Execute SQL and return the result as a DataFrame."""
    engine = get_engine()

    with engine.connect() as conn:
        result = conn.execute(
            text(query),
            params or {},
        )

        if result.returns_rows:
            rows = result.fetchall()
            columns = result.keys()

            return pd.DataFrame(
                rows,
                columns=columns,
            )

        return pd.DataFrame()


def execute_ddl(sql_text: str) -> None:
    """
    Execute SQL statements from a SQL file.

    Comments are removed before splitting statements so that
    semicolons inside comments do not break the SQL.
    """

    engine = get_engine()

    statements = _split_statements(sql_text)

    with engine.begin() as conn:
        for stmt in statements:
            if stmt.strip():
                conn.execute(text(stmt))


def _split_statements(sql_text: str) -> list[str]:
    """
    Split SQL into individual statements.

    SQL single-line comments beginning with '--' are removed
    before splitting on semicolons.
    """

    cleaned_lines = []

    for line in sql_text.splitlines():

        # Remove SQL comments.
        if "--" in line:
            line = line.split("--", 1)[0]

        cleaned_lines.append(line)

    cleaned_sql = "\n".join(cleaned_lines)

    return [
        stmt.strip()
        for stmt in cleaned_sql.split(";")
        if stmt.strip()
    ]


def explain_analyze(query: str) -> tuple[dict, float]:
    """
    Run EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON).

    Returns:
        plan_json, latency_seconds
    """

    with get_psycopg2_conn() as conn:
        with conn.cursor(
            cursor_factory=psycopg2.extras.RealDictCursor
        ) as cur:

            start = time.perf_counter()

            cur.execute(
                f"EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON) {query}"
            )

            plan = cur.fetchone()

            elapsed = time.perf_counter() - start

            # EXPLAIN ANALYZE executed the query.
            # Roll back any accidental transactional changes.
            conn.rollback()

    plan_json = plan["QUERY PLAN"][0] if plan else {}

    execution_time_ms = plan_json.get("Execution Time")

    if execution_time_ms is not None:
        latency = execution_time_ms / 1000.0
    else:
        latency = elapsed

    return plan_json, latency