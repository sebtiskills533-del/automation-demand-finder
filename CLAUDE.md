# Automation Demand Finder — notes for Claude Code

A personal tool for one user (a beginner). It collects public posts where
people ask for small automations, has Claude score them, and shows a ranked,
password-protected Streamlit dashboard on the user's own computer.

## Hard rules (never break these)

- Only public posts, via RSS feeds or official APIs. Respect rate limits
  (2 s minimum between requests) and send a clear User-Agent.
- Never store emails or phone numbers: run text through
  `app.privacy.strip_contact_details` before saving. The ONE exception:
  `replies.email`, an address a person wrote in their own reply to the user
  (`app.inbox`, via `privacy.extract_email` on that reply only). Never look up,
  scrape or guess emails anywhere else; phone numbers are never kept.
- Nothing is ever posted or sent automatically. Every AI output is a draft.
- Muse (app/muse/) is the one exception to "official APIs only": the user chose it,
  knowing the terms-of-service risk. Keep it opt-in (MUSE_ENABLED), started only
  by the user, one job at a time (BrowserLock), small batches, read-only
  instructions to Muse, and its own Chrome on port 9223 (never 9222).
- Secrets live in `.env` only (never in code, never committed).
- All SQL uses `?` placeholders. Never format user input into SQL.
- Never render post text as HTML or Markdown in the dashboard (XSS):
  use `st.text`, dataframes or `st.code`, or `components.md_escape` when it
  must sit inside Markdown (titles, bullet lists, captions).

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
  sources/            Reddit RSS, Hacker News (Ask HN), Stack Exchange, Discourse, rss_forum and
                      jobs.py (Freelancer.com, Remote OK, Remotive, Himalayas, WWR, Working Nomads;
                      budgets via money_range so the phone filter never eats them)
  services.py         the user's services (config/services.json): automation, etsy, seo, local_seo
  importer.py         import user-supplied posts (CSV/JSON/form); skips the keyword filter
  muse/               optional Muse collection: browser.py (Chrome/CDP/lock), client.py
                      (chat driving), parse.py (reply formats + JSON answers), job.py (run; PLACES: default,
                      reddit, linkedin, linkedin_trends), analysis.py (Muse as a thinking engine), selectors.py
  collect.py          step 1: collect posts
  prefilter.py        step 2: keyword filter
  models_registry.py  Claude models, levels, prices, per-task choice
  llm.py              the single wrapper around the Claude API
  analyze.py          step 3: AI analysis
  scoring.py          opportunity score + label
  timeline.py         delivery-time rules
  contact.py          allowed contact method rules
  pipeline.py         collect -> filter -> analyze -> group (clusters, at most once a day) -> fresh gigs;
                      scheduled every 3 h by scripts/schedule_every_3_hours.ps1 (pythonw, no window)
  budget.py           monthly AI budget check shared by every Claude feature
  replies.py          v1.1 draft replies (saved to actions.reply_draft)
  reports.py          v1.1 Decision Reports (ticked posts only; app rules win)
  clusters.py         v1.1 top problems (numbered lines in, numbers out)
  specs.py            v1.2 product specs per problem group
  inbox.py            replies people sent the user (linking, email-if-shared rule)
  schedule.py         on/off switch + hours for scheduled runs (run_daily.py --scheduled obeys it)
  results.py          📊 Results logic (reply rate by platform/service/style/speed/bids); pure functions
  notify.py           Windows toast for fresh low-bid gigs (text via env vars, never in the command)
  techlevel.py        judge who is non-tech for posts rated before analyses.tech_level existed (on click)
  painpoints.py       💡 pain points + discussion + AdSense/subscription; Claude OR Muse engine, same validator
                      (numbers in, numbers out; app rules: evidence strength by count, no 'Build it' if thin/60+ h)
  portfolio.py        🏆 portfolio picks from real posts (numbers in, numbers out; <= 30 h each)
  explain.py          'Samjhao' tab: Roman Urdu job explanation, only on click (explanations table)
  emails.py           email drafts for replies with a shared email (mailto, never sends)
  auth.py             password hashing + login lockout
  set_password.py     CLI to set the dashboard password
  dashboard.py        Streamlit entry point
  ui/                 dashboard pages (opportunities, replies_page, problems_page,
                      import_page, painpoints_page, platforms_page, portfolio_page, results_page, settings_page), reply_widgets.py, background.py, components.py (badges, md_escape), assets/
                      Theme: .streamlit/config.toml (teal + amber).
config/               sources.json, phrases.txt, models.json, services.json, platforms.json (user-editable)
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
