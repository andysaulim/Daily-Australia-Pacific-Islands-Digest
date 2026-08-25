---
name: korea-daily-brief
description: >-
  Operate, maintain, debug, and extend the Korea Daily Brief — Andy's automated
  Korean Peninsula intelligence newsletter that runs on GitHub Actions and emails a
  structured digest at 6 AM ET. Use whenever the work touches that pipeline or its
  files (run.py, collect.py, digest.py, render.py, send_email.py, databases.py, the
  kim/kcna/bp trackers, weekly.py), or involves: updating Gallup Korea baselines,
  adding or fixing an RSS feed, editing the HTML email template, updating the
  NK-Russia / provocations / facility / Kim-appearance trackers, running the digest
  locally, debugging a failed Actions run, the Friday "Week in Review," or the
  editorial guardrails (SOURCE-OR-SKIP, same-poll-date, prestige enforcement).
  Trigger even on casual phrasing — "the brief didn't send," "add NK News to the
  feeds," "the poll numbers are stale," "regenerate today's digest" — and even
  without the words "Korea Daily Brief." This is the ops skill for the live pipeline;
  korea-digest-edit handles editorial copy edits instead.
---

# Korea Daily Brief — Pipeline Operations

Andy's automated Korean Peninsula intelligence briefing. Collects 140+ feeds overnight, synthesizes through Claude into structured JSON, validates, renders an HTML email, sends via Gmail SMTP at 6 AM ET, and archives to GitHub Pages. Runs unattended on GitHub Actions. Recipients are analysts at CSIS, State, the Pentagon, and academic Korea-watchers.

`pipeline/` here is a **reference snapshot** of the live code, bundled so any session can read or run it. The canonical source is Andy's GitHub repo (`andysaulim.github.io/Daily-Korea-Digest` deploys the archive). When making real edits, work in the repo; treat the snapshot as the map, not the territory, and flag if the two have drifted.

## Pipeline flow

Orchestrated by `run.py` → `main()`. Strict order:

```
COLLECT → DATABASES → DIGEST → VALIDATE → RENDER → PUSH (archive) → SEND
```

| Stage | File | What happens |
|-------|------|--------------|
| COLLECT | `collect.py` | 25 parallel threads pull 4 feed tiers + market data + Gallup polling + satellite reports → scored JSON payload (~15s) |
| DIGEST | `digest.py` | Payload + injected reference databases → Claude → structured JSON (~15 sections). `generate_digest()` |
| VALIDATE | `run.py` | `validate_digest()`: dedup, URL repair, source caps, section caps, word-count floor. Critical fails → `regenerate_digest()`, up to 2 retries |
| RENDER | `render.py` | Validated JSON → 1,400-line table-based HTML email (inline CSS, dark mode, mobile, plain-text fallback) |
| SEND | `send_email.py` | Gmail SMTP SSL :465. Subject = the digest's RE: line |
| ARCHIVE | workflow | `public/latest.html`, `public/digest_YYYY-MM-DD.html`, `public/archive.json` → GitHub Pages |

Model handling lives in `digest.py`: `FAST_MODEL` (Sonnet) runs the first attempt; `PRIMARY_MODEL` (Opus) is the retry escalation. Both are pinned string constants — see `references/maintenance.md` before touching them.

## Critical rules — never break these in an edit

These are the guardrails the whole system is built around. Any change to `digest.py`, `collect.py`, or `run.py` must preserve them. Full detail in `references/prompt-rules.md`.

- **SOURCE-OR-SKIP.** Every claim traces to a collected article or an injected baseline. No memory-based assertions. An omission always beats an invention.
- **Same-poll-date.** All polling numbers come from one Gallup Korea survey. Never mix weeks.
- **Prestige enforcement.** WSJ, NYT, FT, and specialists (38 North, ArmsControlWonk) appear if they published.
- **Pre-calculated totals are authoritative.** When the prompt supplies a sum/percentage, the model uses it verbatim — no recomputation.
- **Dates from sources only.** `calendar_watch` / `on_this_day` use dates from today's articles, the verified-dates list, or injected baselines — never from model memory.
- **Trackers over recall.** Kim's last appearance, the KCNA baseline, and facility statuses come from cached JSON injected into the prompt, never from the model guessing.

## Running locally

```bash
cd pipeline
pip install -r requirements.txt
export ANTHROPIC_API_KEY=...
export GMAIL_USER=...        # Gmail address
export GMAIL_APP_PASS=...    # 16-char app password, not the account password
export DIGEST_TO=...         # comma-separated recipients
python run.py
```

Optional: `GMAIL_FROM` (sending alias, defaults to `GMAIL_USER`), `WEB_URL` (archive base for links). To regenerate without emailing, run the collect/digest stages and inspect output before the send step rather than firing `run.py` end to end.

## Common tasks — router

Read `references/maintenance.md` for the full playbook on any of these. Quick map:

| Task | Where it lives |
|------|----------------|
| Update Gallup Korea baselines (weekly, Fridays) | `digest.py` confirmed-baseline block + `collect.py` sentiment fallbacks. Use explicit date ranges, never week numbers |
| Add / remove an RSS feed | `collect.py` → `TIER1_FEEDS`…`TIER4_FEEDS` (tier sets the recency window: 24h/36h/72h/24h) |
| Edit the email design | `render.py` — inline styles only, tables not grid/flex, test in Gmail + Outlook + Apple Mail |
| Update NK-Russia / provocations data | `databases.py` → `NK_RUSSIA_DB`, `PROVOCATIONS_DB` |
| Update facility statuses | `bp_tracker.py` (11 DPRK sites) |
| Adjust validation thresholds | `run.py` — `_SOURCE_CAP` (=3), `SECTION_CAPS`, word floor (hard min 850, target 1200–1400) |
| Debug a failed run | `references/maintenance.md` → Debugging. Check Actions log; common: API rate limit, feed timeouts (non-fatal), validation retry exhaustion, SMTP auth |
| Friday "Week in Review" | `weekly.py` — synthesizes the week's 7 archived digests |

## Where to read more

- `references/architecture.md` — full stage-by-stage detail, file map with line counts, scheduling (external cron + Actions fallback + guard logic), persistent trackers, design rationale.
- `references/maintenance.md` — step-by-step playbooks for every recurring task above, plus debugging and the required Actions secrets.
- `references/prompt-rules.md` — the digest's editorial guardrails in full, the section schema, and the baseline-injection mechanism.
- `commands/newsletter.md` — the `/newsletter` slash-command reference (architecture + maintenance quick-reference), preserved as-is.

## Editorial register

Digest copy is PUBLICATIONS-register, intelligence-analyst voice: precise, sourced, numbers over adjectives, every claim attributable. Email-medium overrides apply (dark section banners permitted for scanning, table-safe inline CSS) — this is the same house style as the broader Korea Digest work. For pure content/wording edits to digest prose, korea-digest-edit is the better-fit skill; use this one for the pipeline itself.


## House style (mandatory for all prose)

Every sentence this skill produces is governed by the consolidated house writing
standard: the `house-style` skill, backed by the full guide at
`C:\Users\ALim\Dropbox\Writing\House Style Guide.md` (Zinsser + Strunk + Pinker +
The Economist). Before delivering, run its Anti-Claude Protocol as a pass: no
throat-clearing ("it is important to note"); no value-stamping adverbs
(importantly/notably/crucially/interestingly); hedge as a choice, not a tic; no
emphasis inflation (key/crucial/critical/robust/comprehensive); no zombie
abstractions (landscape/framework/ecosystem/stakeholders) — people and institutions
doing things; no reflexive "not only X but also Y" symmetry; zero em-dashes in shipped
text (en-dash ranges fine); no signpost or recap endings; the same name for the same
thing throughout; varied sentence rhythm; a position taken rather than both sides
split. Prefer short old words, active voice by default, concrete detail, and
sentences that end on the load-bearing word. Where this skill's voice or format
profile explicitly requires something the standard discourages, this skill wins for
that piece.


**Sourcing gate (hard rule).** This is research-brief output: every load-bearing fact — figures, dates, attributions, counts, causal claims, stated positions — carries two working, independent sources before it ships. "Working" = resolves today; "independent" = the second does not derive from the first (a dataset and the report built on it count as one source). A single-source claim must be attributed in-line, flagged as single-source, or cut. See the house-style guide, "Facts and sourcing."