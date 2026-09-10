"""
Gen AI Query Copilot & Auditor.

Two components:

1. TextToSQLCopilot — converts a natural-language business question into a
   syntactically correct, schema-grounded PostgreSQL query using LangChain
   + an LLM (OpenAI or Groq, switchable via config). The prompt is
   constrained to the known schema and read-only SELECT statements.

2. QueryAuditor — scans a SQL query for common anti-patterns
   (SELECT *, missing WHERE/LIMIT, unindexed GROUP BY/JOIN columns,
   leading wildcard LIKE, implicit cross joins, etc.) using fast
   rule-based checks, then asks the LLM for a short natural-language
   performance review layered on top of the rule findings.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from langchain_core.messages import HumanMessage, SystemMessage

from src.config import settings

SCHEMA_DESCRIPTION = """
You are working with a PostgreSQL retail analytics database with these tables:

customers (
    customer_id BIGINT PRIMARY KEY,
    first_name VARCHAR, last_name VARCHAR, email VARCHAR,
    signup_date DATE, city VARCHAR, state VARCHAR, country VARCHAR,
    age SMALLINT, gender VARCHAR,
    acquisition_channel VARCHAR  -- one of: organic, paid_search, social, referral, email
)

campaign_engagements (
    engagement_id BIGINT PRIMARY KEY,
    customer_id BIGINT REFERENCES customers(customer_id),
    channel VARCHAR,        -- one of: email, sms, whatsapp
    campaign_name VARCHAR,
    sent_at TIMESTAMP, opened_at TIMESTAMP, clicked_at TIMESTAMP,
    converted BOOLEAN
)

orders (
    order_id BIGINT PRIMARY KEY,
    customer_id BIGINT REFERENCES customers(customer_id),
    order_date TIMESTAMP,
    order_amount NUMERIC(10,2),
    product_category VARCHAR,
    is_repeat_purchase BOOLEAN,
    channel VARCHAR          -- one of: web, app, store
)

Indexed columns (prefer filtering/joining on these when possible):
  orders(customer_id), orders(customer_id, order_date DESC) INCLUDE (order_amount),
  orders(order_date) [partial, last ~400 days], orders(order_date) [BRIN],
  customers(acquisition_channel, signup_date),
  campaign_engagements(customer_id), campaign_engagements(channel, campaign_name) [partial],
  campaign_engagements(clicked_at) [partial]
"""


def _get_llm(temperature: float = 0.0):
    """Return a configured LangChain chat model based on settings.llm_provider."""
    provider = settings.llm_provider.lower()
    if provider == "openai":
        from langchain_openai import ChatOpenAI
        if not settings.openai_api_key:
            raise RuntimeError("OPENAI_API_KEY is not set. Add it to your .env file.")
        return ChatOpenAI(
            model=settings.openai_model,
            temperature=temperature,
            api_key=settings.openai_api_key,
        )
    elif provider == "groq":
        from langchain_groq import ChatGroq
        if not settings.groq_api_key:
            raise RuntimeError("GROQ_API_KEY is not set. Add it to your .env file.")
        return ChatGroq(
            model=settings.groq_model,
            temperature=temperature,
            api_key=settings.groq_api_key,
        )
    else:
        raise ValueError(f"Unknown LLM_PROVIDER: {provider!r} (expected 'openai' or 'groq')")


# ---------------------------------------------------------------------------
# 1. Text-to-SQL Copilot
# ---------------------------------------------------------------------------

TEXT_TO_SQL_SYSTEM_PROMPT = f"""You are a senior PostgreSQL data engineer.
Convert the user's natural-language business question into a single,
syntactically correct, READ-ONLY PostgreSQL SELECT query.

{SCHEMA_DESCRIPTION}

Rules:
- Output ONLY the SQL query. No markdown fences, no commentary, no explanation.
- Only ever generate SELECT statements. Never generate INSERT/UPDATE/DELETE/DROP/ALTER/TRUNCATE.
- Prefer explicit column lists over SELECT *.
- Prefer indexed columns in WHERE/JOIN/GROUP BY where the question allows it.
- Use appropriate date filters (e.g. CURRENT_DATE - INTERVAL 'N days') for
  relative time phrases like "last 30 days".
- Always alias tables and qualify columns.
- End the statement with a semicolon.
"""


@dataclass
class TextToSQLResult:
    question: str
    sql: str
    raw_response: str


class TextToSQLCopilot:
    """Natural language -> PostgreSQL query generator."""

    def __init__(self, temperature: float = 0.0):
        self.llm = _get_llm(temperature=temperature)

    def generate_sql(self, question: str) -> TextToSQLResult:
        messages = [
            SystemMessage(content=TEXT_TO_SQL_SYSTEM_PROMPT),
            HumanMessage(content=question),
        ]
        response = self.llm.invoke(messages)
        raw = response.content.strip()
        sql = _extract_sql(raw)
        _guard_read_only(sql)
        return TextToSQLResult(question=question, sql=sql, raw_response=raw)


def _extract_sql(raw: str) -> str:
    """Strip markdown code fences if the LLM added them despite instructions."""
    fence_match = re.search(r"```(?:sql)?\s*(.*?)```", raw, re.DOTALL | re.IGNORECASE)
    sql = fence_match.group(1).strip() if fence_match else raw.strip()
    return sql


def _guard_read_only(sql: str) -> None:
    """Defense in depth: reject anything that isn't a SELECT."""
    forbidden = re.compile(
        r"\b(INSERT|UPDATE|DELETE|DROP|ALTER|TRUNCATE|GRANT|REVOKE|CREATE|COPY)\b",
        re.IGNORECASE,
    )
    if forbidden.search(sql):
        raise ValueError(
            "Generated SQL contains a non-read-only statement and was blocked. "
            "Only SELECT queries are permitted."
        )
    if not re.match(r"^\s*(WITH|SELECT)\b", sql, re.IGNORECASE):
        raise ValueError("Generated SQL does not start with SELECT/WITH and was blocked.")


# ---------------------------------------------------------------------------
# 2. Query Performance Auditor
# ---------------------------------------------------------------------------

INDEXED_COLUMNS = {
    "orders": {"customer_id", "order_date"},
    "customers": {"acquisition_channel", "signup_date"},
    "campaign_engagements": {"customer_id", "channel", "campaign_name", "clicked_at"},
}


@dataclass
class AuditFinding:
    severity: str      # "high" | "medium" | "low"
    rule: str
    message: str


@dataclass
class AuditReport:
    sql: str
    findings: list[AuditFinding] = field(default_factory=list)
    llm_summary: str = ""

    @property
    def score(self) -> int:
        """0-100, 100 = no issues found."""
        penalty = {"high": 25, "medium": 12, "low": 5}
        total = sum(penalty[f.severity] for f in self.findings)
        return max(0, 100 - total)


class QueryAuditor:
    """Rule-based anti-pattern scanner + optional LLM narrative summary."""

    def __init__(self, use_llm: bool = True, temperature: float = 0.0):
        self.use_llm = use_llm
        self._llm = None
        if use_llm:
            try:
                self._llm = _get_llm(temperature=temperature)
            except Exception:
                self._llm = None  # fall back to rule-only if no key configured

    # ---- rule-based checks -------------------------------------------------

    def _check_select_star(self, sql: str) -> AuditFinding | None:
        if re.search(r"SELECT\s+\*", sql, re.IGNORECASE):
            return AuditFinding(
                "medium", "select_star",
                "Uses SELECT * — fetches unneeded columns, increases I/O and "
                "prevents index-only scans. List only the columns you need.",
            )
        return None

    def _check_missing_where(self, sql: str) -> AuditFinding | None:
        has_from = re.search(r"\bFROM\b", sql, re.IGNORECASE)
        has_where = re.search(r"\bWHERE\b", sql, re.IGNORECASE)
        if has_from and not has_where:
            return AuditFinding(
                "high", "missing_where",
                "No WHERE clause found — the query will perform a full table "
                "scan. Add a filter on an indexed column if possible.",
            )
        return None

    def _check_missing_limit(self, sql: str) -> AuditFinding | None:
        is_aggregate = re.search(r"\bGROUP BY\b|\bCOUNT\(|\bSUM\(|\bAVG\(", sql, re.IGNORECASE)
        has_limit = re.search(r"\bLIMIT\b", sql, re.IGNORECASE)
        if not is_aggregate and not has_limit:
            return AuditFinding(
                "low", "missing_limit",
                "No LIMIT clause on a non-aggregate query — could return an "
                "unexpectedly large result set. Consider adding LIMIT for "
                "exploratory queries.",
            )
        return None

    def _check_leading_wildcard_like(self, sql: str) -> AuditFinding | None:
        if re.search(r"LIKE\s+'%\w", sql, re.IGNORECASE):
            return AuditFinding(
                "medium", "leading_wildcard_like",
                "LIKE pattern starts with '%' — this cannot use a standard "
                "B-Tree index (forces a full scan). Consider a trigram/GIN "
                "index (pg_trgm) or restructuring the filter.",
            )
        return None

    def _check_implicit_cross_join(self, sql: str) -> AuditFinding | None:
        from_clause = re.search(r"FROM\s+(.*?)(WHERE|GROUP BY|ORDER BY|$)", sql, re.IGNORECASE | re.DOTALL)
        if from_clause:
            clause = from_clause.group(1)
            comma_join = "," in clause and not re.search(r"\bJOIN\b", clause, re.IGNORECASE)
            if comma_join:
                return AuditFinding(
                    "high", "implicit_cross_join",
                    "Comma-separated tables in FROM without explicit JOIN/ON "
                    "conditions risk an accidental cross join. Use explicit "
                    "JOIN ... ON syntax.",
                )
        return None

    def _check_unindexed_group_by_or_join(self, sql: str) -> list[AuditFinding]:
        findings = []
        group_by_match = re.search(r"GROUP BY\s+(.*?)(ORDER BY|HAVING|LIMIT|$)", sql, re.IGNORECASE | re.DOTALL)
        join_matches = re.findall(r"JOIN\s+(\w+)\s+\w*\s*ON\s+(.*?)(?:JOIN|WHERE|GROUP BY|ORDER BY|$)",
                                   sql, re.IGNORECASE | re.DOTALL)

        known_tables = set(INDEXED_COLUMNS.keys())

        def col_is_indexed(col: str) -> bool:
            col = col.strip().lower()
            for table, cols in INDEXED_COLUMNS.items():
                if col in cols or col.split(".")[-1] in cols:
                    return True
            return False

        if group_by_match:
            cols = [c.strip() for c in group_by_match.group(1).split(",") if c.strip()]
            unindexed = [c for c in cols if not any(t in c.lower() for t in known_tables) and not col_is_indexed(c)]
            risky = [c for c in cols if "date_trunc" in c.lower() or "(" in c]
            if risky:
                findings.append(AuditFinding(
                    "medium", "grouped_on_expression",
                    f"GROUP BY uses a computed expression ({risky[0][:60]}...) "
                    "which cannot use a plain B-Tree index. Consider a "
                    "functional/expression index if this query runs frequently.",
                ))

        for table, on_clause in join_matches:
            if table.lower() not in known_tables:
                continue
            if "customer_id" not in on_clause.lower():
                findings.append(AuditFinding(
                    "medium", "join_on_unindexed_column",
                    f"JOIN on {table} does not appear to use the indexed "
                    "customer_id column — verify the join key is indexed to "
                    "avoid a sequential scan.",
                ))
        return findings

    def run_rule_checks(self, sql: str) -> list[AuditFinding]:
        checks = [
            self._check_select_star,
            self._check_missing_where,
            self._check_missing_limit,
            self._check_leading_wildcard_like,
            self._check_implicit_cross_join,
        ]
        findings = [f for f in (chk(sql) for chk in checks) if f]
        findings.extend(self._check_unindexed_group_by_or_join(sql))
        return findings

    # ---- LLM narrative layer ------------------------------------------------

    def _llm_summary(self, sql: str, findings: list[AuditFinding]) -> str:
        if not self._llm:
            return ("LLM review unavailable (no API key configured) — "
                    "showing rule-based findings only.")
        findings_text = "\n".join(f"- [{f.severity}] {f.rule}: {f.message}" for f in findings) or "None found."
        prompt = f"""You are a senior PostgreSQL performance engineer reviewing this query:

```sql
{sql}
```

Automated static-analysis findings:
{findings_text}

{SCHEMA_DESCRIPTION}

Write a concise (4-6 sentences) performance review for a data engineer.
Reference specific indexes from the schema above where relevant. Suggest
concrete rewrites or indexing strategies. Do not repeat the findings list
verbatim — synthesize them into actionable advice."""
        response = self._llm.invoke([HumanMessage(content=prompt)])
        return response.content.strip()

    def audit(self, sql: str) -> AuditReport:
        findings = self.run_rule_checks(sql)
        summary = self._llm_summary(sql, findings) if self.use_llm else ""
        return AuditReport(sql=sql, findings=findings, llm_summary=summary)
