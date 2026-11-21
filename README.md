# Automation Demand Finder

A personal dashboard that finds people online asking for small automations
("copy my orders into a sheet", "email me when X happens"), has Claude score
each request, and shows a ranked list you can tick, track and act on.

**What it does every day**

1. **Collect** new public posts from Ask HN, the n8n and Make community forums
   (official Discourse feeds), Stack Exchange (official API) and subreddit feeds
   (Reddit needs API approval), plus **jobs** from freelance marketplaces and remote
   job boards with official feeds: Freelancer.com, Remote OK, Remotive, Himalayas,
   We Work Remotely and Working Nomads (searches: config/sources.json → job_boards).
   Services looked for: automation, Etsy, SEO and local SEO (config/services.json).
   You can also **import** posts you found anywhere else (Facebook, Instagram,
   Quora...): see section 5.
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
5. **Act faster (version 1.1):** for any post, Claude can **draft a reply** (a free tip
   first, you edit and post it yourself); for posts you tick, a **Decision Report**
   (do it / think first / skip, an A-to-Z plan, delivery days, how to contact them
   and a ready draft). Similar requests are grouped into **Top problems**, and for
   any problem Claude can write a **product spec** for a sellable Google Sheets template.

Nothing is ever posted or sent automatically, and emails and phone numbers are
removed before anything is saved.

---

## 1. Install (once)

You need **Python 3.11+** (tick "Add Python to PATH" on Windows) and **git**.

```powershell
git clone https://github.com/sebtiskills533-del/automation-demand-finder.git
cd automation-demand-finder
python -m venv .venv
.venv\Scripts\python -m pip install -r requirements-dev.txt
copy .env.example .env
```

Open `.env` in VS Code and paste your Anthropic API key after `ANTHROPIC_API_KEY=`.
Also set a spending limit in the Anthropic Console.

Then choose a dashboard password (it's stored only as a secure hash). Typing is hidden: nothing appears while you type, which is normal. If your keyboard seems to do nothing, add `--visible` to the command.

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

**Desktop shortcut:** run this once and you get two desktop icons: *Automation Demand Finder*
(starts the dashboard and opens your browser) and *Automation Demand Finder -
Daily job* (collects and rates new posts right now):

```powershell
powershell -ExecutionPolicy Bypass -File scripts\create_desktop_shortcut.ps1
```

Keep the small black window open while you use the app; close it to stop.

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

## Using the dashboard

| Page | What you do there |
| --- | --- |
| 🎯 **Opportunities** | Pick a view (⭐ Best leads, 🛠️ In progress, 📋 All…), click a post, then use the tabs: **Overview**, **💡 Samjhao** (click the button: Claude explains the job and how to do it in simple Roman Urdu, about Rs 2–3 each, saved after that), **✍️ Reply** (draft with Claude, edit, copy), **📋 Decision report** (after you tick it), **🗂️ Track** (status, path, notes) |
| 🧩 **Top problems** | See requests that repeat, open one to see its posts, and click **📝 Write product spec** (download it for Claude Code) |
| 💼 **Jobs** (a view on Opportunities) | Job-board listings rated for a beginner: small gigs, part-time and junior roles rank high, senior full-time roles low |
| 🙋 **Non-tech people** (a view on Opportunities) | Posts from people with no IT background who don't know a tool could help: your best customers. The **Who** column shows 🙋 Non-tech / 🙂 Some tech / 🧑‍💻 Techie, with the reason in the post's Overview |
| 🏆 **Portfolio** | Click **🏆 Pick my portfolio projects**: Claude picks 4–6 projects from your real posts (non-tech people first) that build trust and stay useful for years, each with why, skills, tools, hours, how to show it, a Roman Urdu note and the real posts it answers (about Rs 3–6 a click) |
| 🧭 **Platforms** | All the freelancing platforms you listed, grouped: connected automatically, good fit (use by hand, e.g. Fiverr, Upwork), later, not your skills. Edit config/platforms.json |
| 📥 **Import posts** | Add posts you found yourself, rate them, or start a Muse collection |
| ⚙️ **Settings & costs** | This month's AI spending and which Claude model does each job |

Colours: 🟢 *Work on this* (score 70+), 🟠 *Worth a look* (45+), ⚪ *Skip*.
Nothing is ever posted for you: copy a draft and post it yourself.

## Replies to your posts (🔔 Replies)

When people answer your comments on Facebook, Instagram or Quora:

1. **🔔 Replies → 🔍 Check for replies with Muse** (Muse reads your notifications,
   read-only), or add a reply by hand / paste Muse's `REPLIES START … REPLIES END` list.
2. New replies show as **Replies (N new)** in the menu, a pop-up when you log in,
   and in the **💬 Replies** tab of the post they answer.
3. **Emails:** kept **only** when the person wrote their email in that reply to you.
   The app never looks for emails anywhere else, and phone numbers are always removed.
4. For a reply with an email: **✨ Draft an email with Claude**, edit it, then
   **📧 Open in my email app** — you press Send yourself; the app never sends email.
5. **Tick what you've read:** every reply has a **✅ Seen** tick (in the table and on the
   reply). Opening a reply doesn't tick it; **🆕 Not seen yet** shows the unticked ones,
   and **✅ Mark all as seen** ticks them in one go.
6. **📝 Posts I answered** lists every post you answered with its number of replies.

**Answered posts:** after you answer a post, tick **✅ Maine jawab bhej diya** on it
(Opportunities). It then shows ✅ in the table and in the **✅ Answered** view. Posts
that get a reply are ticked automatically.

## 4. Settings you can change

| File | What it controls |
| --- | --- |
| `.env` | API key, password hash, `MAX_POSTS_PER_RUN`, `MONTHLY_BUDGET_USD`, `USD_TO_INR`, `REDDIT_USERNAME`, `LOG_LEVEL` |
| `config/sources.json` | Which subreddits, Hacker News searches (`ask_hn` or `story`) and Discourse forums to read; `skip_categories` drops showcase/news/"offer help" topics |
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

## 5. Importing posts from other platforms

Facebook, Instagram and Quora have no official way for apps to read posts, so
the app doesn't collect from them. Instead, when you find a good request there,
bring it in yourself and the app rates it like everything else:

- **Dashboard → Import posts → Add one post:** paste the link, title and text.
- **Upload a file / Paste CSV or JSON** with the columns
  `url,title,text,source,author,posted_at` (only `url` and `title` are required).
  A template is downloadable on that page.
- **Command line:** `.venv\Scripts\python -m app.importer my_posts.csv`

Then click **Rate new posts now** (or wait for the daily run). Emails and phone
numbers are removed, and the same link is never imported twice.

The Import page also has a ready-made instruction you can give an AI browser
assistant (such as Muse) to turn posts you have open into this CSV format.
Those platforms forbid automated collection, and an agent using your logged-in
accounts can get them banned, so keep it small and manual.

### Collect with Muse (optional, automated)

The app can drive **Muse** (Meta's AI agent at muse.ai) the same way the
SavorFable Autopilot app does: in its **own Chrome** (own profile, port 9223,
listening on this computer only), it types a job into a Muse side chat, waits
for the reply (it is finished after 60 s without new text, at most 30 min),
imports the posts and rates them. Muse has no API, so this is browser
automation: muse.ai, Facebook, Instagram and Quora may consider it against
their terms. It only runs when you click, one job at a time, 20 posts at most.

One-time setup:

1. In `.env` set `MUSE_ENABLED=true` (and `CHROME_PATH` if Chrome is installed elsewhere).
2. Dashboard → **Import posts** → **Open the Muse Chrome**, and log in to muse.ai
   in that window. It remembers you.
3. In Muse, create a **side chat** named exactly `AUTOMATION DEMAND FINDER`
   and paste the whole of [`docs/muse-finder-procedure.md`](docs/muse-finder-procedure.md) into it once.
4. Muse must be able to see Facebook, Instagram and Quora (log in to them in
   that Chrome if Muse asks).

Then: **Import posts → Start Muse collection** (optionally type a focus such as
"Etsy sellers and Google Sheets"). Watch the first run in the Muse Chrome.
Results and any problem appear in the table under the button. Command line:
`.venv\Scripts\python -m app.muse run --focus "Etsy sellers"`.

If muse.ai changes its page and runs start failing, the page selectors are
all in `app/muse/selectors.py`.

## 6. How the code is organised

```
app/
  config.py           settings from .env, validated
  db.py / store.py    SQLite schema + every query (safe placeholders only)
  privacy.py          removes emails and phone numbers
  http_client.py      polite HTTP: 2 s between requests, retries, clear User-Agent
  sources/            Reddit RSS, Hacker News, Stack Exchange, Discourse forum and job-board readers (jobs.py)
  importer.py         import posts from CSV / JSON / the dashboard form
  muse/               optional Muse collection (Chrome over CDP, chat driving, reply parsing)
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

## 7. Updating

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
| Dashboard says no password is set | Run `.venv\Scripts\python -m app.set_password`, then reload the page |
| `Too many wrong passwords` | Wait 15 minutes |
