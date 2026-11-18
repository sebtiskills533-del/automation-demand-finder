# Automation Demand Finder

A personal dashboard that finds people online asking for small automations
("copy my orders into a sheet", "email me when X happens"), has Claude score
each request, and shows a ranked list you can tick, track and act on.

**What it does every day**

1. **Collect** new public posts from subreddit RSS feeds and Hacker News.
2. **Filter** out posts that clearly aren't requests (a free keyword check).
3. **Analyse** the rest with Claude. Each post gets:
   - a one-line summary, difficulty, best tool and estimated build hours
   - **seriousness 1–5**: how likely this is a real, committed person
   - **product potential 1–5**: how many others probably have the same problem
   - an **opportunity score 0–100** and a verdict: *Work on this / Worth a look / Skip*
   - a **delivery estimate** (for example "1–3 days") using fixed rules with a beginner safety margin
   - the **allowed contact method** (public reply, a DM only if they asked to hire, and so on)
   - the region, **only if the post states it**
4. **Review** everything in a password-protected dashboard, with a link to every original post.

Nothing is ever posted or sent automatically, and emails and phone numbers are
removed before anything is saved.

---

## 1. Install (once)

You need **Python 3.11+** (tick "Add Python to PATH" on Windows) and **git**.

```powershell
git clone https://github.com/<your-username>/automation-demand-finder.git
cd automation-demand-finder
python -m venv .venv
.venv\Scripts\python -m pip install -r requirements-dev.txt
copy .env.example .env
```

Open `.env` in VS Code and paste your Anthropic API key after `ANTHROPIC_API_KEY=`.
Also set a spending limit in the Anthropic Console.

Then choose a dashboard password (it's stored only as a secure hash):

```powershell
.venv\Scripts\python -m app.set_password
```

> On Mac/Linux use `.venv/bin/python` instead of `.venv\Scripts\python`.

## 2. Run

| What | Command |
| --- | --- |
| Daily job (collect → filter → analyse) | `.venv\Scripts\python run_daily.py` |
| Only collect | `.venv\Scripts\python -m app.collect` |
| Only filter | `.venv\Scripts\python -m app.prefilter` |
| Only analyse | `.venv\Scripts\python -m app.analyze` |
| Dashboard | `.venv\Scripts\python -m streamlit run app/dashboard.py` |
| Tests | `.venv\Scripts\python -m pytest` |

The dashboard opens at <http://localhost:8501>. It only listens on your own
computer, not your Wi-Fi network.

## 3. Run it automatically every morning (Windows)

Open PowerShell **in the project folder** and paste this once (it runs the job
daily at 7:00):

```powershell
$dir = (Get-Location).Path
$action = New-ScheduledTaskAction -Execute "$dir\.venv\Scripts\python.exe" -Argument "run_daily.py" -WorkingDirectory $dir
$trigger = New-ScheduledTaskTrigger -Daily -At 7am
Register-ScheduledTask -TaskName "Automation Demand Finder" -Action $action -Trigger $trigger -Description "Daily collect, filter and analyse"
```

To check it: open **Task Scheduler** → *Task Scheduler Library* → *Automation
Demand Finder* → right-click → **Run**. To remove it:
`Unregister-ScheduledTask -TaskName "Automation Demand Finder"`.

Logs from every run are in `logs/app.log`.

## 4. Settings you can change

| File | What it controls |
| --- | --- |
| `.env` | API key, password hash, `MAX_POSTS_PER_RUN`, `MONTHLY_BUDGET_USD`, `USD_TO_INR`, `REDDIT_USERNAME`, `LOG_LEVEL` |
| `config/sources.json` | Which subreddits and Hacker News searches to read |
| `config/phrases.txt` | Keywords that make a post count as a request |
| `config/models.json` | The Claude models (levels 1–4) and their prices |

In the dashboard's **Settings & costs** page you can pick which Claude model
does the analysis and see this month's spending. The default is Level 1
(Claude Haiku 5.5), the cheapest.

| Level | Model | Input / output price per 1M tokens |
| --- | --- | --- |
| 1 | Claude Haiku 5.5 | $0.10 / $0.50 |
| 2 | Claude Sonnet 5.5 | $2 / $10 |
| 3 | Claude Opus 5.5 | $4 / $20 |
| 4 | Claude Fable 5.2 | $8 / $40 |

**Cost guards:** at most `MAX_POSTS_PER_RUN` posts are analysed per run, the
run stops when `MONTHLY_BUDGET_USD` is reached, and every call's tokens and
cost are logged.

## 5. How the code is organised

```
app/
  config.py           settings from .env, validated
  db.py / store.py    SQLite schema + every query (safe placeholders only)
  privacy.py          removes emails and phone numbers
  http_client.py      polite HTTP: 2 s between requests, retries, clear User-Agent
  sources/            Reddit RSS + Hacker News readers
  collect.py          step 1    prefilter.py  step 2    analyze.py  step 3
  llm.py              the only code that calls Claude (structured JSON, cost logging)
  models_registry.py  model levels, prices, model per task
  scoring.py          opportunity score     timeline.py  delivery rules
  contact.py          allowed contact rules
  auth.py             password hashing + lockout
  dashboard.py, ui/   the Streamlit dashboard
config/               files you can edit without touching code
run_daily.py          the daily job
tests/                automated tests (no internet or real API calls needed)
```

Data lives in `data/finder.db` (one file). Delete it to start fresh.

## 6. Updating

```powershell
git pull
.venv\Scripts\python -m pip install -r requirements-dev.txt
.venv\Scripts\python -m pytest
```

## Rules this project follows

- Only public posts, through RSS feeds or official APIs, slowly, with a clear User-Agent.
- Never stores emails or phone numbers.
- Never posts or messages anyone; you review and send everything yourself.
- Secrets stay in `.env`, which git ignores.

## Troubleshooting

| Message | Fix |
| --- | --- |
| `ANTHROPIC_API_KEY is empty` | Paste your key into `.env` |
| `Claude rejected the API key` | Check the key and that your Anthropic account has credit |
| `r/...: FAILED (HTTP 429)` | Reddit is rate-limiting you; it retries next run. Setting `REDDIT_USERNAME` helps |
| `Monthly AI budget ... reached` | Raise `MONTHLY_BUDGET_USD` or wait for next month |
| Dashboard says no password is set | Run `.venv\Scripts\python -m app.set_password`, then restart the dashboard |
| `Too many wrong passwords` | Wait 15 minutes |
