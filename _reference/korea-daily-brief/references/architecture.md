# Architecture Reference

Full detail on how the Korea Daily Brief pipeline is built and why. The SKILL.md body is the quick map; this is the territory.

## Stage-by-stage

### 1. COLLECT — `collect.py` (~1,661 lines)

25 parallel threads (`_fetch_feeds_parallel`) pull RSS/Atom across four tiers. Tier sets the recency window:

| Tier | Window | Count | Examples |
|------|--------|-------|----------|
| 1 — News | 24h | 90+ | Korea Herald, Reuters, WSJ, NYT, Bloomberg, Yonhap, JTBC, Global Times, Xinhua, TASS (incl. Korean-language feeds; Claude translates downstream) |
| 2 — Analysis | 36h | 25 | CSIS, Brookings, 38 North, Foreign Affairs, The Diplomat, RAND + DPRK-specialist direct feeds |
| 3 — Academic | 72h | 18 | International Security, Asian Survey, Pacific Affairs (Google Scholar RSS + site searches to cut noise) |
| 4 — DPRK | 24h | 4–12 | KCNA Watch, Rodong Sinmun (via relay), Daily NK, NK News |

Feed sets are the `TIER1_FEEDS` … `TIER4_FEEDS` dicts plus `KIM_TRACKER_FEEDS`. `MAJOR_FEEDS` marks prestige wires.

Alongside news the collector pulls:
- **Market data** — KOSPI, Brent crude, USD/KRW, BOK rate, CDS spreads, GDP estimates (BOK ECOS API among sources).
- **Gallup Korea polling** — presidential approval, ruling-party support (Democratic Party / 더불어민주당), opposition (People Power Party / 국민의힘), independents (무당층), plus the weekly special-topic finding. Realmeter daily as secondary. Written into the `sentiment_baseline` payload field with scrape-validation guardrails.
- **Satellite imagery reports** — status of 11 monitored DPRK facilities.

Output: one structured JSON payload, every article scored and sorted by source prestige.

### 2. DIGEST — `digest.py` (~889 lines)

`generate_digest(payload, db_context=...)` sends the payload to Claude with an ~80-line system prompt engineered for zero hallucination. Reference baselines (trade/tariff entries, the confirmed Gallup baseline, verified Korea dates) are injected into the **user** prompt so they can be updated independently of the system prompt.

Model constants:
- `FAST_MODEL` — Sonnet, first attempt.
- `PRIMARY_MODEL` — Opus, retry escalation.
- Retry logic: `retry_model = FAST_MODEL if attempt == 0 else PRIMARY_MODEL`.

Output: structured JSON, ~15 sections (see `prompt-rules.md` for the schema).

### 3. VALIDATE — `run.py` (~1,122 lines)

`validate_digest(digest, payload=...)` runs quality gates and returns warnings; critical warnings drive `regenerate_digest()` (up to 2 retries, escalating the model):
- **Dedup** — keyword overlap + entity matching (same company + same topic = duplicate).
- **URL repair** — `_validate_urls()` fuzzy-matches headlines to fix hallucinated Google News URLs; unfixable URLs are dropped.
- **Source diversity** — `_enforce_source_diversity()` caps any single outlet to `_SOURCE_CAP` (=3) appearances per section; cross-section caps flagged separately.
- **Section caps** — `SECTION_CAPS` enforces per-section min/max item counts.
- **Word count** — hard minimum 850 (too short → critical), target 1200–1400 for a 5-minute read.
- **Sentiment completeness** — all polling metrics present and from one survey date.

### 4. RENDER — `render.py` (~1,408 lines)

Validated JSON → deterministic HTML email. Only the data varies; the template is fixed. Constraints (email clients are hostile to modern CSS):
- Table-based layout, no CSS grid/flex.
- Inline styles only, no external stylesheets.
- Color-coded section banners with category accent colors; signal badges (ESCALATION, ANOMALY, DEVELOPMENT, CONFIRMATION); market strip with directional arrows; 2×2 satellite facility grid with status badges.
- Media-query mobile layout, dark-mode support, plain-text fallback for legacy clients.

### 5. SEND — `send_email.py` (~210 lines)

Gmail SMTP over SSL, port 465, app-password auth. Subject line is the digest's RE: line — a one-sentence summary of the day's themes. Env: `GMAIL_USER`, `GMAIL_APP_PASS`, `DIGEST_TO` (comma-separated), optional `GMAIL_FROM`.

### 6. ARCHIVE

The workflow writes `public/latest.html`, `public/digest_YYYY-MM-DD.html`, and `public/archive.json` (manifest for search + weekly synthesis), then deploys `./public` to GitHub Pages with `keep_files: true`.

## Persistent trackers

State cached across Actions runs (restored/saved via `actions/cache`) so the model never guesses slow-moving facts:

| Tracker JSON | Producer | Purpose |
|--------------|----------|---------|
| `kim_tracker.json` | `kim_tracker.py` | Confirmed Kim Jong Un appearances → "days since last seen" |
| `kcna_tracker.json` | `kcna_tracker.py` | 14-day KCNA rhetoric baseline — phrase counts, tone, volume |
| `bp_tracker.json` | `bp_tracker.py` | 11 DPRK facility statuses (Yongbyon, Punggye-ri, Sohae, Sinpo, …) |
| `metrics.jsonl` | `run.py` | Per-run metrics — word count, article counts, validation retries, send status |

Reference databases (compiled into the prompt, not cached state) live in `databases.py`: `NK_RUSSIA_DB` (NK-Russia bilateral timeline, 270+ events) and `PROVOCATIONS_DB` (NK provocations, 540+ since 1958). `tension_scorer.py` computes a 0–10 Peninsula tension index.

## Scheduling

- **Primary** — an external cron (cron-job.org) fires a GitHub Actions `workflow_dispatch` at 6:00 AM ET, sidestepping GitHub's cron queue delays.
- **Fallback** — Actions `schedule` crons at 7:30 AM (`30 11 * * *` UTC) and 9:00 AM ET (`0 13 * * *` UTC). A guard step queries `gh run list` for today's successful/in-progress runs and skips if the dispatch already fired — preventing duplicate emails.
- **Weekly** — Fridays 9 AM ET, `weekly.py` fetches the week's 7 archived digests and synthesizes a "Week in Review."
- **Failure alerts** — on `failure()`, a small inline Python step emails the operator a link to the failed run. Post-email steps (`update_readme.py`, Pages deploy, README commit) use `continue-on-error` so a non-fatal post-step never marks the run failed and re-triggers a fallback.

Workflow file: `pipeline/workflows/daily-digest.yml` (lives at `.github/workflows/daily-digest.yml` in the repo).

## Design decisions

- **Structured JSON, not prose** — lets every field be validated, section minimums enforced, cross-section dedup run, and rendering stay pixel-deterministic.
- **Sonnet first, Opus on retry** — Sonnet handles the ~90% of days that pass validation cheaply and fast; Opus is reserved for complex news days where Sonnet under-generates.
- **External cron over Actions cron** — GitHub's scheduler has no SLA (30 min–4 hr drift); an API dispatch fires instantly.
- **Persistent trackers over recall** — slow-changing facts stored as JSON and injected beat asking the model to remember across sessions.

## Stack

Python 3.12 · Anthropic API (Sonnet primary / Opus retry) · Gmail SMTP · GitHub Actions (compute) + GitHub Pages (archive) · cron-job.org (primary scheduler) · RSS/Atom + Google News relay + BOK ECOS API.

## File map

| File | Lines | Role |
|------|-------|------|
| `run.py` | 1,122 | Orchestrator: collect → databases → digest → validate → render → push → send |
| `collect.py` | 1,661 | Parallel RSS scraper + market data + Gallup polling |
| `digest.py` | 889 | Claude integration + system prompt + baselines |
| `render.py` | 1,408 | HTML email renderer |
| `send_email.py` | 210 | Gmail SMTP sender |
| `databases.py` | 746 | NK-Russia + provocations reference databases |
| `kim_tracker.py` | 247 | Kim Jong Un appearance tracker |
| `kcna_tracker.py` | 120 | KCNA rhetoric baseline |
| `bp_tracker.py` | 103 | Satellite facility tracker |
| `tension_scorer.py` | 404 | Peninsula tension index (0–10) |
| `weekly.py` | 392 | Friday "Week in Review" synthesis |
| `update_readme.py` | 124 | README stats updater |
