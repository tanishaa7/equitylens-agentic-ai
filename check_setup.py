"""Step 0 sanity check: Python version, packages, API keys, live data access.

Run:  .venv/bin/python check_setup.py
"""

import importlib
import sys

OK, WARN, FAIL = "[ok]  ", "[warn]", "[FAIL]"
problems = 0


def report(status, msg):
    global problems
    if status == FAIL:
        problems += 1
    print(f"{status} {msg}")


# 1. Python version
v = sys.version_info
report(OK if v >= (3, 11) else FAIL, f"Python {v.major}.{v.minor}.{v.micro}")

# 2. Packages
for pkg in ["google.genai", "pydantic", "dotenv", "yfinance", "pandas",
            "feedparser", "requests", "praw", "streamlit"]:
    try:
        importlib.import_module(pkg)
        report(OK, f"package {pkg}")
    except ImportError:
        report(FAIL, f"package {pkg} missing — run: .venv/bin/pip install -r requirements.txt")

# 3. API keys (only says set / missing, never prints values)
import config  # noqa: E402

keys = {
    "GEMINI_API_KEY": (config.GEMINI_API_KEY, True),
    "REDDIT_CLIENT_ID": (config.REDDIT_CLIENT_ID, False),
    "REDDIT_CLIENT_SECRET": (config.REDDIT_CLIENT_SECRET, False),
}
for name, (value, required) in keys.items():
    if value:
        report(OK, f"{name} is set")
    elif required:
        report(FAIL, f"{name} missing — free key at https://aistudio.google.com/apikey, "
                     "paste it into .env")
    else:
        report(WARN, f"{name} not set (optional)")

# 4. Live market data
try:
    import yfinance as yf

    t = yf.Ticker("TCS.NS")
    hist = t.history(period="5d")
    if hist.empty:
        report(FAIL, "yfinance returned no data for TCS.NS")
    else:
        close = hist["Close"].iloc[-1]
        report(OK, f"yfinance works — TCS.NS last close ₹{close:,.2f}")
except Exception as e:  # network errors etc.
    report(FAIL, f"yfinance error: {e}")

# 5. Live AI call (only if a key is set) — one tiny request on each model
if config.GEMINI_API_KEY:
    from pydantic import BaseModel

    from agents.llm import AIUnavailable, generate_sync

    class Ping(BaseModel):
        reply: str

    for tier in ["specialist", "judge"]:
        model = config.GEMINI_JUDGE_MODEL if tier == "judge" else config.GEMINI_SPECIALIST_MODEL
        try:
            out = generate_sync("Reply in JSON.", "Say OK.", Ping, tier=tier, label="setup-check",
                                allow_fallback=False)
            report(OK, f"Gemini {model} works (replied: {out.reply[:20]!r})")
        except AIUnavailable as e:
            temporary = e.kind in ("busy", "rate_limit_minute")
            extra = (" — temporary; the judge falls back to the specialist model meanwhile"
                     if temporary and tier == "judge" else "")
            report(WARN if temporary else FAIL, f"Gemini {model}: {e.user_message}{extra}  "
                                                f"[detail: {e.detail[:120]}]")

print()
print("All good." if problems == 0
      else f"{problems} problem(s) to fix above.")
