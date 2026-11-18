# Automation Demand Finder — notes for Claude Code

A personal tool for one user (Safi, a beginner). It collects public posts where
people ask for small automations, has Claude score them, and shows a ranked,
password-protected Streamlit dashboard on the user's own computer.

## Hard rules (never break these)

- Only public posts, via RSS feeds or official APIs. Respect rate limits
  (2 s minimum between requests) and send a clear User-Agent.
- Never store emails or phone numbers: run text through
  `app.privacy.strip_contact_details` before saving.
- Nothing is ever posted or sent automatically. Every AI output is a draft.
- Secrets live in `.env` only (never in code, never committed).
- All SQL uses `?` placeholders. Never format user input into SQL.
- Never render post text as HTML or Markdown in the dashboard (XSS):
  use `st.text`, dataframes or `st.code`.

## Layout

```
app/
  config.py           settings from .env (validated)
  errors.py           the app's exception types
  logging_setup.py    console + rotating file logs in logs/
  db.py               SQLite connection + schema
  store.py            every database query (repository layer)
  domain.py           shared types and constants (Post, statuses...)
  privacy.py          strips emails / phone numbers
  html_text.py        HTML -> plain text
  http_client.py      polite HTTP client (rate limit, retries, UA)
  sources/            Reddit RSS + Hacker News fetchers
  collect.py          step 1: collect posts
  prefilter.py        step 2: keyword filter
  models_registry.py  Claude models, levels, prices, per-task choice
  llm.py              the single wrapper around the Claude API
  analyze.py          step 3: AI analysis
  scoring.py          opportunity score + label
  timeline.py         delivery-time rules
  contact.py          allowed contact method rules
  pipeline.py         collect -> filter -> analyze
  auth.py             password hashing + login lockout
  set_password.py     CLI to set the dashboard password
  dashboard.py        Streamlit entry point
  ui/                 dashboard pages and widgets
config/               sources.json, phrases.txt, models.json (user-editable)
run_daily.py          the daily run
tests/                pytest suite (no network, no real API calls)
```

## Commands

- Tests: `.venv\Scripts\python -m pytest`
- Daily run: `.venv\Scripts\python run_daily.py`
- Dashboard: `.venv\Scripts\python -m streamlit run app/dashboard.py`
- Set password: `.venv\Scripts\python -m app.set_password`

## Conventions

- Reuse `store.py` for queries and `llm.py` for Claude calls; don't duplicate.
- Every new feature gets tests, including failure cases.
- Commit after each working change; never commit `.env` or `data/`.
