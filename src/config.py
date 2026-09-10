"""
Central configuration for the AI Customer Cohort & Query Optimization Engine.

Reads from environment variables (.env via python-dotenv) first, and falls
back to Streamlit secrets (st.secrets) when running inside Streamlit Cloud,
so the same code works locally and when deployed.
"""
from __future__ import annotations

import os
from dataclasses import dataclass

from dotenv import load_dotenv

load_dotenv()


def _get(key: str, default: str | None = None) -> str | None:
    """Fetch a config value from env first, then st.secrets if available."""
    val = os.getenv(key)
    if val:
        return val
    try:
        import streamlit as st  # imported lazily; not required for CLI usage

        if key in st.secrets:
            return st.secrets[key]
    except Exception:
        pass
    return default


@dataclass(frozen=True)
class Settings:
    # --- Database ---
    database_url: str
    db_host: str
    db_port: str
    db_name: str
    db_user: str
    db_password: str

    # --- LLM ---
    llm_provider: str
    openai_api_key: str | None
    openai_model: str
    groq_api_key: str | None
    groq_model: str

    # --- App ---
    app_env: str
    log_level: str


def load_settings() -> Settings:
    db_host = _get("DB_HOST", "localhost")
    db_port = _get("DB_PORT", "5432")
    db_name = _get("DB_NAME", "cohort_engine")
    db_user = _get("DB_USER", "postgres")
    db_password = _get("DB_PASSWORD", "postgres")

    default_url = f"postgresql+psycopg2://{db_user}:{db_password}@{db_host}:{db_port}/{db_name}"
    database_url = _get("DATABASE_URL", default_url)

    return Settings(
        database_url=database_url,
        db_host=db_host,
        db_port=db_port,
        db_name=db_name,
        db_user=db_user,
        db_password=db_password,
        llm_provider=_get("LLM_PROVIDER", "groq"),
        openai_api_key=_get("OPENAI_API_KEY"),
        openai_model=_get("OPENAI_MODEL", "gpt-4o-mini"),
        groq_api_key=_get("GROQ_API_KEY"),
        groq_model=_get("GROQ_MODEL", "llama-3.3-70b-versatile"),
        app_env=_get("APP_ENV", "development"),
        log_level=_get("LOG_LEVEL", "INFO"),
    )


settings = load_settings()
