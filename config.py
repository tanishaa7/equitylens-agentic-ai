"""Central settings: API keys, model names, paths."""

import os
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).parent
load_dotenv(ROOT / ".env")   # local: keys in .env (git-ignored)


def _cloud_secrets_to_env():
    """Hosted (Streamlit Community Cloud): keys live in the app's Secrets settings.
    Root-level secrets usually arrive as env vars already; this covers the rest."""
    if os.getenv("GEMINI_API_KEY"):
        return
    on_cloud = os.path.exists("/mount/src")              # where Streamlit Cloud puts the repo
    has_local_secrets = (ROOT / ".streamlit" / "secrets.toml").exists()
    if not (on_cloud or has_local_secrets):
        return
    try:
        import streamlit as st
        for k, v in st.secrets.items():
            if isinstance(v, (str, int, float)) and not os.getenv(k):
                os.environ[k] = str(v)
    except Exception:   # no secrets configured — fine
        pass


_cloud_secrets_to_env()

# --- AI provider ---
# Agents call agents/llm.py, never a provider SDK directly, so switching
# provider later is a change in one file plus this setting.
LLM_PROVIDER = os.getenv("LLM_PROVIDER", "gemini").lower()

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
# Free-tier models. Specialists use the lighter model (higher free limits);
# the judge uses the strongest free model.
GEMINI_SPECIALIST_MODEL = os.getenv("GEMINI_SPECIALIST_MODEL", "gemini-3.5-flash-lite")
GEMINI_JUDGE_MODEL = os.getenv("GEMINI_JUDGE_MODEL", "gemini-3.8-flash")
# How many AI calls may run at once — keeps bursts under the per-minute limit.
GEMINI_MAX_CONCURRENT = int(os.getenv("GEMINI_MAX_CONCURRENT", "2"))
# Bull vs bear: 1 = opening cases only (2 AI calls), 2 = + rebuttals (4 AI calls)
DEBATE_ROUNDS = int(os.getenv("DEBATE_ROUNDS", "2"))

# --- Other keys ---
REDDIT_CLIENT_ID = os.getenv("REDDIT_CLIENT_ID", "")
REDDIT_CLIENT_SECRET = os.getenv("REDDIT_CLIENT_SECRET", "")
REDDIT_USER_AGENT = os.getenv("REDDIT_USER_AGENT", "equitylens-prototype/0.1")

# --- Paths ---
CACHE_DIR = ROOT / "cache"
CACHE_DIR.mkdir(exist_ok=True)

# --- Market defaults ---
# Indian stocks on Yahoo Finance: NSE uses ".NS", BSE uses ".BO".
DEFAULT_EXCHANGE_SUFFIX = ".NS"
