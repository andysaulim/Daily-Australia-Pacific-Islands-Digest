# Maintenance Playbooks

Step-by-step for every recurring task. Anchor on function/constant names, not line numbers — line numbers drift as the code changes. Save a backup before any destructive edit, per Andy's working rule.

## Update Gallup Korea baselines (weekly, typically Fridays)

When a new Gallup Korea poll releases. Two places must move together, or the digest will mix weeks and violate the same-poll-date rule.

1. **`digest.py` — confirmed-baseline block.** Inside the user-prompt assembly there is a CONFIRMED baseline line (approval %, DP %, PPP %, independents %, Gallup Korea survey label + field dates) plus a plausibility range used to reject bad scrapes. Update the numbers and the survey date label. Use an explicit date range — `"May 19-21, 2026"` — never a week number. Also bump the staleness note so the model actively looks for newer polling in today's articles.
2. **`collect.py` — sentiment fallbacks.** Update the fallback values the scraper writes into `sentiment_baseline`: `presidential_approval`, `party_ruling` / `ruling_party_support`, `opposition_support`, `political_independents`, `gallup_spotlight`. These are the safety net when the live scrape fails.

Sanity check after editing: approval should sit in the plausibility band the prompt enforces; if a party rating could be misread as presidential approval, the prompt is built to ignore the scrape and fall back to the confirmed block — keep that block correct.

## Add or remove an RSS feed

1. Open `collect.py`. Add the feed URL to the right tier dict — `TIER1_FEEDS` (24h news), `TIER2_FEEDS` (36h analysis), `TIER3_FEEDS` (72h academic), `TIER4_FEEDS` (24h DPRK). Tier choice sets the recency window, so put slow-moving analysis in Tier 2/3 and breaking wires in Tier 1.
2. Korean-language feeds are fine in Tier 1 — Claude translates during analysis. Mark prestige wires by adding to `MAJOR_FEEDS` if relevant.
3. To track a new Kim-appearance source, add to `KIM_TRACKER_FEEDS`.
4. Test the collector alone before a full run; a single dead feed is non-fatal (the pipeline continues with what it got) but a malformed entry can throw.

## Edit the email design

All in `render.py`. Email clients are the constraint, not modern browsers.

- Inline styles only. Tables for layout — no CSS grid, no flexbox.
- Preserve: section banners with category accent colors, signal badges (ESCALATION / ANOMALY / DEVELOPMENT / CONFIRMATION), the market strip with directional arrows, the 2×2 satellite facility grid, dark-mode rules, and the plain-text fallback.
- Test the rendered HTML in **Gmail, Outlook, and Apple Mail** — Outlook strips the most. Keep the email-medium overrides from Andy's house style: dark section banners are allowed here (scanning), unlike hairline-only HTML briefs.

## Update reference databases and trackers

| Data | File | Symbol / shape |
|------|------|----------------|
| NK-Russia bilateral events | `databases.py` | `NK_RUSSIA_DB` (270+ events) |
| NK provocations history | `databases.py` | `PROVOCATIONS_DB` (540+ since 1958) |
| DPRK facility statuses | `bp_tracker.py` | 11 sites (Yongbyon, Punggye-ri, Sohae, Sinpo, …) |
| Kim appearance log | `kim_tracker.py` → `kim_tracker.json` | confirmed appearances; do not hand-edit the JSON if the .py recomputes it |
| KCNA rhetoric baseline | `kcna_tracker.py` → `kcna_tracker.json` | 14-day rolling baseline |

The `*.json` tracker files are cached state restored each Actions run. Edit the producing `.py` logic; only hand-edit a JSON to correct a confirmed factual error (e.g. a wrong "last seen" date), and note that the cache key (`tracker-data-v1`) governs persistence.

## Adjust validation thresholds

In `run.py`:
- `_SOURCE_CAP` (=3) — max appearances of one outlet per section. It was 2; that was too aggressive and crashed word count. Don't drop it back to 2 without re-checking word count.
- `SECTION_CAPS` — per-section min/max item counts.
- Word floor — hard minimum **850** (critical, blocks send), target **1200–1400**. Raising the floor risks more retries; lowering it ships thin digests.
- Retry budget — up to 2 regenerations, escalating `FAST_MODEL` → `PRIMARY_MODEL`.

## Model IDs

`FAST_MODEL` and `PRIMARY_MODEL` in `digest.py` are pinned string constants. If swapping models, change both constants and re-test a full run — newer models can shift output length enough to trip the word-count gate or the section caps. Confirm current model strings against Anthropic's docs before pinning.

## Debug a failed run

Start at the GitHub Actions log for the failed run (the failure-alert email links straight to it). Common causes:

| Symptom | Cause | Handling |
|---------|-------|----------|
| Retry/backoff messages | Anthropic API rate limit | Automatic retry with backoff; usually self-heals |
| Missing feeds, run continues | RSS feed timeouts | Non-fatal by design — pipeline proceeds with available feeds |
| Repeated regeneration then stop | Validation failure (word count < 850, missing section, dedup) | Auto-retries up to 2×, escalates to Opus; if still failing, inspect the payload — likely a thin news day or a broken feed starving collection |
| Auth error at send | `GMAIL_APP_PASS` expired/rotated | Regenerate the Gmail app password, update the secret |
| Pages/README step red but email sent | Post-send step failure | Expected to be non-fatal (`continue-on-error`); the digest already went out |
| Duplicate emails | Guard logic bypassed | Check the "Check if already sent today" step and the `gh run list` query in `daily-digest.yml` |

To reproduce locally, run with the same env vars (below) and inspect the digest JSON between DIGEST and SEND rather than firing end to end.

## Friday "Week in Review"

`weekly.py` fetches the week's 7 archived digests (from `public/` / `archive.json`) and synthesizes a single "Week in Review." Runs on its own Friday-9-AM-ET schedule. Same prompt guardrails apply.

## Required GitHub Actions secrets

| Secret | Purpose |
|--------|---------|
| `ANTHROPIC_API_KEY` | Claude API access |
| `GMAIL_USER` | Sender Gmail address |
| `GMAIL_APP_PASS` | Gmail app password (16-char, not the account password) |
| `DIGEST_TO` | Comma-separated recipient list |
| `GH_PAT` | Token with `repo` + `workflow` scopes — Pages deploy, README commit, and the cron-fallback guard's `gh run list` |

## Run locally

```bash
cd pipeline
pip install -r requirements.txt
export ANTHROPIC_API_KEY=...
export GMAIL_USER=...
export GMAIL_APP_PASS=...
export DIGEST_TO=...
python run.py
```
