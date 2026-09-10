# AI Customer Cohort & Query Optimization Engine
[![Live Demo](https://img.shields.io/badge/🚀%20Live%20Demo-Streamlit-red?style=for-the-badge)](https://ai-cohort-engine.streamlit.app/)
A production-style, full-stack data engineering + Gen AI portfolio project:
synthetic retail data at scale (1M–10M rows), SQL performance benchmarking
(unindexed → indexed, ~12s → ~150ms), a LangChain-powered Text-to-SQL /
Query-Auditor copilot, and an interactive Streamlit dashboard.

---

## 1. Architecture

```
ai_cohort_engine/
├── README.md
├── requirements.txt
├── .env.example
├── Dockerfile
├── docker-compose.yml
├── .streamlit/
│   └── config.toml
├── db/
│   ├── schema.sql              # DDL for customers, campaign_engagements, orders
│   ├── indexes.sql             # B-Tree / composite / partial index DDL
│   ├── partitioning.sql        # Range partitioning strategy for orders
│   └── benchmark_queries.sql   # The 3 raw "unoptimized" cohort queries
├── src/
│   ├── config.py                # Central config / env loading
│   ├── data_generator.py        # Synthetic data pipeline (1M-10M rows)
│   ├── query_benchmarker.py     # EXPLAIN ANALYZE + before/after benchmarking
│   ├── ai_copilot.py            # LangChain Text-to-SQL + Query Auditor agent
│   ├── app.py                   # Streamlit dashboard (2 tabs)
│   └── utils/
│       ├── db.py                 # SQLAlchemy engine/session helpers
│       └── metrics.py            # CLV / churn / retention SQL helper queries
└── data/                        # (generated locally, gitignored)
```

### Data model

```
customers(customer_id PK, first_name, last_name, email, signup_date,
          city, state, country, age, gender, acquisition_channel)

campaign_engagements(engagement_id PK, customer_id FK, channel
                      ['email','sms','whatsapp'], campaign_name,
                      sent_at, opened_at, clicked_at, converted BOOLEAN)

orders(order_id PK, customer_id FK, order_date, order_amount,
       product_category, is_repeat_purchase BOOLEAN, channel)
```

### Why this project maps to top-tier Data/AI Engineering JDs
- **Scale**: multi-million row synthetic pipeline, bulk-loaded via
  `COPY`/`executemany` batching (not row-by-row ORM inserts).
- **SQL performance engineering**: real EXPLAIN ANALYZE plans, B-Tree +
  composite indexes, partial indexes, range partitioning, before/after
  latency benchmarking.
- **Gen AI orchestration**: LangChain SQL agent constrained to a read-only
  schema-aware prompt, plus a separate rule-based + LLM query auditor.
- **Product delivery**: a working Streamlit BI + Copilot dashboard,
  containerized for deployment (Streamlit Community Cloud / Docker / Render).

---

## 2. Setup (do this first)

### 2.1 Prerequisites
- Python 3.10+
- PostgreSQL 14+ (local, Docker, or a managed instance e.g. Neon/Supabase/RDS)
- An LLM API key: OpenAI **or** Groq (Groq is free-tier friendly & fast — recommended for a live demo)

### 2.2 Install

```bash
cd ai_cohort_engine
python -m venv venv
source venv/bin/activate        # Windows: venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env
# edit .env: DB creds + OPENAI_API_KEY or GROQ_API_KEY
```

### 2.3 Create the database

```bash
# Local Postgres example
createdb cohort_engine
psql cohort_engine -f db/schema.sql
```

### 2.4 Generate synthetic data

```bash
# Fast demo size (recommended before a deadline): ~1M rows total, ~2-4 min
python -m src.data_generator --customers 200000 --orders-per-customer 3 --engagements-per-customer 4

# Full "portfolio flex" size: ~10M rows, needs more time/RAM
python -m src.data_generator --customers 1500000 --orders-per-customer 4 --engagements-per-customer 4
```

This uses batched `COPY FROM STDIN` (via `psycopg2.copy_expert`) — the only
practical way to load millions of rows in minutes instead of hours.

### 2.5 Run the benchmark (unindexed → indexed)

```bash
python -m src.query_benchmarker --run-all
```

This will:
1. Run all 3 cohort queries cold (no indexes) → logs latency + `EXPLAIN ANALYZE`.
2. Apply `db/indexes.sql` (+ optional `db/partitioning.sql`).
3. Re-run the same 3 queries → logs new latency + plan.
4. Write a `benchmark_results.json` + prints a before/after summary table.

### 2.6 Launch the dashboard

```bash
streamlit run src/app.py
```

### 2.7 Deploy fast (Streamlit Community Cloud)
1. Push this repo to GitHub.
2. On https://share.streamlit.io → "New app" → point to `src/app.py`.
3. In app **Secrets**, paste the contents of `.env` in TOML form (see
   `.env.example` — same keys, `st.secrets` is read automatically as a
   fallback by `src/config.py`).
4. Point `DATABASE_URL` at a cloud Postgres (Neon/Supabase free tier both
   work and support the data sizes above).

### 2.8 Deploy via Docker (Render / Fly.io / any VM)
```bash
docker compose up --build
```
`docker-compose.yml` spins up Postgres + the Streamlit app together —
useful for a fully self-contained demo/recording.

---

## 3. Module-by-module notes

| File | Purpose |
|---|---|
| `src/data_generator.py` | Faker + numpy vectorized synthetic data, batched `COPY` loads, realistic skew (power-law purchase frequency, churn-prone segments). |
| `src/query_benchmarker.py` | Runs 3 unindexed cohort/CLV/churn queries, captures `EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON)`, applies indexes, re-benchmarks. |
| `src/ai_copilot.py` | LangChain `ChatOpenAI`/`ChatGroq` + schema-grounded prompt → SQL; separate rule-based+LLM `QueryAuditor` that flags anti-patterns. |
| `src/app.py` | Streamlit UI: Tab 1 BI (retention, churn cohorts, campaign ROI), Tab 2 SQL sandbox + copilot + latency charts. |
| `src/utils/db.py` | SQLAlchemy engine, connection pooling, `run_sql`, `explain_analyze` helpers. |
| `src/utils/metrics.py` | Reusable SQL for CLV, 90-day repeat purchase rate, churn-risk cohorts, campaign ROI. |

---

## 4. Time-boxed run order (if you're submitting soon)

1. `pip install -r requirements.txt` (2 min)
2. `psql cohort_engine -f db/schema.sql` (few sec)
3. `python -m src.data_generator --customers 200000 --orders-per-customer 3 --engagements-per-customer 4` (2-5 min)
4. `python -m src.query_benchmarker --run-all` (1-2 min) — this is your "before/after" proof
5. `streamlit run src/app.py` — screenshot/record Tab 1 and Tab 2
6. `git init && git add -A && git commit -m "AI Cohort & Query Optimization Engine" && git push`
7. Deploy on Streamlit Community Cloud (5 min, free)

Total: well under an hour on a laptop.
