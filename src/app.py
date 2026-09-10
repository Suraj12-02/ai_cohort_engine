"""
Interactive Dashboard — AI Customer Cohort & Query Optimization Engine.

Tab 1: Retail Business Intelligence
    - Customer retention (cohort heatmap)
    - Churn-risk cohorts by spend tier
    - Campaign ROI by channel

Tab 2: Query Optimizer & Gen AI Copilot
    - Natural language -> SQL (LangChain copilot)
    - Rule-based + LLM query auditor
    - SQL sandbox with EXPLAIN ANALYZE
    - Before/after latency comparison (from benchmark_results.json)

Run: streamlit run src/app.py
"""
from __future__ import annotations

import json
import pathlib
import sys

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

# Allow `streamlit run src/app.py` to resolve `from src...` imports
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from src.ai_copilot import QueryAuditor, TextToSQLCopilot
from src.config import settings
from src.utils.db import explain_analyze, run_sql
from src.utils.metrics import (
    CAMPAIGN_ROI_SQL,
    CHURN_RISK_COHORT_SQL,
    CLV_BY_CHANNEL_SQL,
    MONTHLY_RETENTION_SQL,
    REPEAT_PURCHASE_90D_SQL,
    TABLE_ROW_COUNTS_SQL,
)

REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
BENCHMARK_RESULTS_PATH = REPO_ROOT / "benchmark_results.json"

st.set_page_config(
    page_title="AI Cohort & Query Optimization Engine",
    page_icon="📊",
    layout="wide",
)


@st.cache_data(ttl=300, show_spinner=False)
def cached_sql(query: str) -> pd.DataFrame:
    return run_sql(query)


def db_connection_ok() -> bool:
    try:
        cached_sql("SELECT 1;")
        return True
    except Exception as e:
        st.error(
            "Could not connect to the database. Check `.env` / secrets "
            f"(DATABASE_URL) and that Postgres is reachable.\n\nDetail: {e}"
        )
        return False


st.title("📊 AI Customer Cohort & Query Optimization Engine")
st.caption(
    "Synthetic retail data at scale · SQL performance benchmarking · "
    "LangChain Text-to-SQL & Query Auditor"
)

if not db_connection_ok():
    st.stop()

row_counts = cached_sql(TABLE_ROW_COUNTS_SQL)
cols = st.columns(len(row_counts) + 1)
total_rows = int(row_counts["row_count"].sum())
cols[0].metric("Total rows", f"{total_rows:,}")
for i, row in row_counts.iterrows():
    cols[i + 1].metric(row["table_name"], f"{int(row['row_count']):,}")

tab_bi, tab_copilot = st.tabs(
    ["📈 Retail Business Intelligence", "🧠 Query Optimizer & Gen AI Copilot"]
)

# =====================================================================
# TAB 1 — Retail Business Intelligence
# =====================================================================
with tab_bi:
    st.subheader("Customer Lifetime Value by Acquisition Channel")
    clv_df = cached_sql(CLV_BY_CHANNEL_SQL)
    c1, c2 = st.columns([2, 1])
    with c1:
        fig = px.bar(
            clv_df, x="acquisition_channel", y="clv_per_customer",
            color="acquisition_channel", text_auto=".2s",
            labels={"clv_per_customer": "CLV per customer ($)",
                    "acquisition_channel": "Acquisition channel"},
        )
        fig.update_layout(showlegend=False)
        st.plotly_chart(fig, use_container_width=True)
    with c2:
        st.dataframe(clv_df, use_container_width=True, hide_index=True)

    st.divider()

    st.subheader("90-Day Repeat Purchase Rate")
    repeat_df = cached_sql(REPEAT_PURCHASE_90D_SQL)
    repeat_pct = repeat_df.iloc[0, 0] if not repeat_df.empty and repeat_df.iloc[0, 0] is not None else 0
    st.metric("Repeat purchase within 90 days", f"{repeat_pct:.1f}%")

    st.divider()

    st.subheader("Churn-Risk Cohorts by Spend Tier")
    churn_df = cached_sql(CHURN_RISK_COHORT_SQL)
    c1, c2 = st.columns([2, 1])
    with c1:
        fig = px.bar(
            churn_df, x="spend_tier", y="churn_risk_pct",
            color="spend_tier", text_auto=".1f",
            labels={"churn_risk_pct": "% at churn risk (no order in 90d)",
                    "spend_tier": "Spend tier"},
            category_orders={"spend_tier": ["high_value", "mid_value", "low_value"]},
        )
        fig.update_layout(showlegend=False)
        st.plotly_chart(fig, use_container_width=True)
    with c2:
        st.dataframe(churn_df, use_container_width=True, hide_index=True)

    st.divider()

    st.subheader("Campaign ROI by Channel")
    roi_df = cached_sql(CAMPAIGN_ROI_SQL)
    c1, c2 = st.columns([2, 1])
    with c1:
        fig = px.bar(
            roi_df, x="channel", y="attributed_revenue",
            color="channel", text_auto=".2s",
            labels={"attributed_revenue": "Attributed revenue ($, 7-day window)",
                    "channel": "Campaign channel"},
        )
        fig.update_layout(showlegend=False)
        st.plotly_chart(fig, use_container_width=True)
    with c2:
        st.dataframe(roi_df, use_container_width=True, hide_index=True)

    st.divider()

    st.subheader("Monthly Retention Cohort Heatmap")
    with st.spinner("Loading retention cohort (can take a moment on large datasets)..."):
        retention_df = cached_sql(MONTHLY_RETENTION_SQL)
    if not retention_df.empty:
        pivot = retention_df.pivot_table(
            index="cohort_month", columns="months_since_signup",
            values="active_customers", aggfunc="sum",
        ).fillna(0)
        pivot.index = pivot.index.astype(str)
        fig = go.Figure(data=go.Heatmap(
            z=pivot.values, x=[str(c) for c in pivot.columns], y=pivot.index,
            colorscale="Blues",
        ))
        fig.update_layout(
            xaxis_title="Months since signup",
            yaxis_title="Signup cohort month",
            height=500,
        )
        st.plotly_chart(fig, use_container_width=True)
    else:
        st.info("Not enough order history yet to build a retention cohort.")

# =====================================================================
# TAB 2 — Query Optimizer & Gen AI Copilot
# =====================================================================
with tab_copilot:
    st.subheader("🗣️ Natural Language → SQL Copilot")
    st.caption(f"LLM provider: **{settings.llm_provider}**  ·  "
               f"model: **{settings.openai_model if settings.llm_provider=='openai' else settings.groq_model}**")

    question = st.text_area(
        "Ask a business question in plain English",
        value="Find high-value retail shoppers who haven't made a purchase in the last 30 days",
        height=80,
    )

    gen_col, run_col = st.columns([1, 1])
    generated_sql = st.session_state.get("generated_sql", "")

    if gen_col.button("✨ Generate SQL", type="primary", use_container_width=True):
        try:
            with st.spinner("Asking the LLM to write SQL..."):
                copilot = TextToSQLCopilot()
                result = copilot.generate_sql(question)
            st.session_state["generated_sql"] = result.sql
            generated_sql = result.sql
        except Exception as e:
            st.error(f"Could not generate SQL: {e}")

    st.markdown("#### SQL Sandbox")
    sql_input = st.text_area(
        "Editable SQL (generated queries land here — edit freely before running)",
        value=generated_sql or "SELECT * FROM customers LIMIT 25;",
        height=180,
        key="sql_sandbox",
    )

    audit_col, exec_col = st.columns(2)

    if audit_col.button("🔍 Audit Query", use_container_width=True):
        with st.spinner("Running static analysis + LLM review..."):
            auditor = QueryAuditor(use_llm=True)
            report = auditor.audit(sql_input)
        score_color = "green" if report.score >= 80 else ("orange" if report.score >= 50 else "red")
        st.markdown(f"**Performance score:** :{score_color}[{report.score}/100]")
        if report.findings:
            for f in report.findings:
                icon = {"high": "🔴", "medium": "🟠", "low": "🟡"}[f.severity]
                st.markdown(f"{icon} **[{f.severity.upper()}] {f.rule}** — {f.message}")
        else:
            st.success("No rule-based anti-patterns detected.")
        if report.llm_summary:
            st.markdown("**LLM performance review:**")
            st.info(report.llm_summary)

    if exec_col.button("▶️ Run EXPLAIN ANALYZE", use_container_width=True):
        try:
            with st.spinner("Executing query and capturing plan..."):
                plan_json, latency = explain_analyze(sql_input)
            st.metric("Execution latency", f"{latency*1000:.1f} ms")
            st.markdown("**Query plan (top node):**")
            top = plan_json.get("Plan", {})
            plan_summary = {
                "Node Type": top.get("Node Type"),
                "Total Cost": top.get("Total Cost"),
                "Actual Rows": top.get("Actual Rows"),
                "Actual Total Time (ms)": top.get("Actual Total Time"),
            }
            st.json(plan_summary)
            with st.expander("Full EXPLAIN (ANALYZE, BUFFERS, JSON) plan"):
                st.json(plan_json)
        except Exception as e:
            st.error(f"Query failed: {e}")

    st.divider()

    st.subheader("⚡ Before/After Indexing — Latency Comparison")
    if BENCHMARK_RESULTS_PATH.exists():
        data = json.loads(BENCHMARK_RESULTS_PATH.read_text())
        before = data.get("before", {})
        after = data.get("after", {})
        if before:
            rows = []
            for name in before:
                b = before.get(name, {}).get("latency_seconds")
                a = after.get(name, {}).get("latency_seconds") if after else None
                rows.append({
                    "query": name,
                    "before_ms": round(b * 1000, 1) if b is not None else None,
                    "after_ms": round(a * 1000, 1) if a is not None else None,
                })
            bench_df = pd.DataFrame(rows)
            melted = bench_df.melt(
                id_vars="query", value_vars=["before_ms", "after_ms"],
                var_name="stage", value_name="latency_ms",
            ).dropna()
            fig = px.bar(
                melted, x="query", y="latency_ms", color="stage",
                barmode="group", log_y=True,
                labels={"latency_ms": "Latency (ms, log scale)", "query": "Benchmark query"},
            )
            st.plotly_chart(fig, use_container_width=True)
            st.dataframe(bench_df, use_container_width=True, hide_index=True)
        else:
            st.info("Run `python -m src.query_benchmarker --run-all` to populate this chart.")
    else:
        st.info(
            "No benchmark_results.json found yet. Run "
            "`python -m src.query_benchmarker --run-all` from the project root, "
            "then refresh this page."
        )

st.divider()
st.caption(
    "Built with Streamlit · SQLAlchemy · PostgreSQL · LangChain · "
    f"{settings.llm_provider.title()}"
)
