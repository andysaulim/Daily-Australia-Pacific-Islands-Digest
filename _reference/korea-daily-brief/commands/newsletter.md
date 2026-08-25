# Korea Daily Brief — Architecture Reference

Use this command to understand how the newsletter pipeline works, debug issues, or make changes.

## Pipeline Flow

The pipeline runs in strict order via `run.py`:

1. **COLLECT** (`collect.py`) — 25 parallel threads scrape 140+ RSS feeds across 4 tiers (news/analysis/academic/DPRK). Also fetches KOSPI, USD/KRW, Brent crude, BOK rate, CDS spreads, Gallup Korea polling, and satellite imagery reports. Output: structured JSON payload. ~15 seconds.

2. **DIGEST** (`digest.py`) — Sends collected payload to Claude Sonnet with an 80-line system prompt. Injects reference databases: NK-Russia timeline (270+ events), NK provocations (540+), 14-day KCNA baseline, Kim Jong Un appearance log. Output: structured JSON with ~15 sections. If validation fails, retries with Claude Opus.

3. **VALIDATE** (`run.py`) — Deduplication (keyword overlap + entity matching), URL repair (fuzzy-match headlines to fix hallucinated URLs), source diversity caps (max 3 per outlet per section), prestige checks, sentiment completeness. Critical failures trigger regeneration (up to 2 retries).

4. **RENDER** (`render.py`) — Converts validated JSON to a 1,400-line HTML email. Table-based layout (no CSS grid), inline styles, color-coded section banners, signal badges (ESCALATION/ANOMALY/DEVELOPMENT/CONFIRMATION), market strip, satellite facility grid, dark mode, mobile responsive, plain-text fallback.

5. **SEND** (`send_email.py`) — Gmail SMTP (SSL, port 465) with app password. Subject line is the digest's RE: line.

6. **ARCHIVE** — Saved to `/public/` and deployed to GitHub Pages: `latest.html`, `digest_YYYY-MM-DD.html`, `archive.json`.

## Scheduling

- **Primary**: External cron (cron-job.org) fires `workflow_dispatch` at 6:00 AM ET via GitHub API
- **Fallback**: GitHub Actions crons at 7:30 AM and 9:00 AM ET, with guard logic that skips if dispatch already succeeded
- **Weekly**: Fridays at 9 AM ET, `weekly.py` synthesizes the week's 7 digests

## Common Maintenance Tasks

### Update Gallup Korea baselines
When a new Gallup Korea poll releases (typically Fridays):
1. Update the hard baseline in `digest.py` (~line 498): approval %, party support numbers, survey dates
2. Update fallback values in `collect.py` (~line 1514): `presidential_approval`, `ruling_party_support`, `opposition_support`, `political_independents`, `gallup_spotlight`
3. Use explicit date ranges (e.g., "May 19-21, 2026") not week numbers

### Add a new RSS feed
Add the feed URL to the appropriate tier list in `collect.py`. Tier determines the recency window (24h/36h/72h).

### Modify the email design
Edit `render.py`. All styles must be inline. Use tables, not CSS grid/flexbox. Test in Gmail, Outlook, and Apple Mail. A static preview mockup is in `preview_v2.html`.

### Update tracker databases
- NK-Russia events: `databases.py` → `NK_RUSSIA_DB`
- NK provocations: `databases.py` → `PROVOCATIONS_DB`
- Facility statuses: `bp_tracker.py`

### Debug a failed run
Check the GitHub Actions log. Common failures:
- Anthropic API rate limit → automatic retry with backoff
- RSS feed timeouts → non-fatal, pipeline continues with available feeds
- Validation failure → auto-retries up to 2x, escalates to Opus
- Gmail SMTP auth → check `GMAIL_APP_PASS` secret hasn't expired

## Secrets Required

| Secret | Purpose |
|--------|---------|
| `ANTHROPIC_API_KEY` | Claude API access |
| `GMAIL_USER` | Sender email address |
| `GMAIL_APP_PASS` | Gmail app password (not account password) |
| `DIGEST_TO` | Comma-separated recipient list |
| `GH_PAT` | GitHub token with `repo` + `workflow` scopes (for Pages deploy + cron trigger) |
