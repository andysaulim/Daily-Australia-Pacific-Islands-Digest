# Australia Chair Daily Brief

An automated weekday intelligence brief covering **Australia, New Zealand, and the
Pacific Islands**, produced for the CSIS Australia Chair. It collects roughly 120
feeds overnight, synthesises them through Claude into structured JSON, validates
the result, renders an HTML email, and sends at 6:00 AM ET.

Sent at 6:00 AM ET, which is 8:00 PM in Sydney and 10:00 PM in Wellington the
same day. The brief therefore closes a completed Australian business day rather
than catching one mid-morning.

<!-- STATS:START -->
**Last run:** 2026-08-31 &middot; 1,690 words &middot; sent

| Metric | Last 10 issues |
| --- | --- |
| Average length | 1,788 words |
| Pacific Wire items per issue | 6.3 |
| New Zealand items per issue | 2.3 |
| Issues where Pacific fell back to the stand-in | 0 of 7 |
| Issues where New Zealand fell back to the stand-in | 0 of 7 |
| Validation retries | 1 |
| Articles in the archive | 1,661 |
| Issues published | 5 |

_Updated 2026-08-31 10:03 AM ET._
<!-- STATS:END -->

## What it covers

The twelve topics the Australia Chair asked for:

US-Australia relations &middot; AUKUS &middot; Australian foreign policy &middot;
Australian defence policy &middot; New Zealand defence policy &middot; New Zealand
foreign policy &middot; Pacific Islands diplomacy &middot; Australian politics
&middot; New Zealand politics &middot; Pacific Islands politics &middot; China in
the Pacific Islands &middot; US-China competition in the Pacific Islands

The eleven named outlets are all in Tier 1 and carry a mandatory-inclusion rule:
The Australian, Sydney Morning Herald, Australian Financial Review, Australian
Foreign Affairs, ABC, Wall Street Journal, Politico, New York Times, RNZ Pacific,
Islands Business, and Pacific Island Times.

## Architecture

Forked from the CSIS Korea Daily Brief, which runs the same design. Two things
differ, both deliberate.

**Regional floors.** Australian news volume outruns New Zealand and Pacific
volume by an order of magnitude every day. `pacific_wire` has a floor of two
items and `new_zealand` a floor of one. A floor may be met by real reporting or
by an explicit stand-in line ("No significant Pacific Islands developments in the
past 24 hours"). It may never be met by padding, and the validator treats a
padded floor as a critical failure. A run of stand-ins means the feed set needs
sources, not a lower floor.

**Cross-day memory.** The Korea and Japan briefs have no state between runs,
which is why the same story can appear two days running. This one keeps a SQLite
ledger of what it has published and defends against repetition in three layers:
the collector marks anything seen in the last seven days, the prompt receives an
ALREADY COVERED block listing the last three issues, and the validator drops any
exact-URL repeat whose body carries no new date or figure.

```
COLLECT -> DIGEST -> VALIDATE -> TRACK -> RENDER -> ARCHIVE -> SEND
```

| Stage | File | What happens |
| --- | --- | --- |
| Collect | `collect.py` | 25 threads over four feed tiers, region tagging, cross-day marking |
| Digest | `digest.py` | Payload plus trackers and baselines to Claude, structured JSON out |
| Validate | `run.py` | Section caps, regional floors, padding check, dedup, source caps, URL repair, house-style gate |
| Track | `aukus_tracker.py`, `pacific_tracker.py`, `archive.py` | Write-back, only after validation passes |
| Render | `render.py` | Table-based HTML email, navy ground with a Pacific teal accent |
| Send | `send_email.py` | Gmail SMTP over SSL, recipients as BCC |
| Archive | workflow | `public/` to GitHub Pages, `data/archive.db` committed back |

### Feed tiers

| Tier | Window | Content |
| --- | --- | --- |
| 1 | 24h | News. Australian national, NZ, Pacific national press, wires, government, China reaction layer |
| 2 | 36h | Analysis and newsletters. Lowy Interpreter, ASPI Strategist and Fault Lines, USSC, Devpolicy, CSIS, Brookings, Futura Doctrina, Democracy Project NZ |
| 3 | 72h | Academic journals |
| 4 | 48h | Primary documents. Communiques, joint statements, ministerial transcripts |

Paywalled and bot-protected publishers route through Google News search rather
than direct fetch. **A publisher that returns 403 in testing gets added to the
Google News routing list, not removed from the feed set.**

### The trackers

Each exposes `build_context_block()` for prompt injection and
`update_from_digest()` for write-back. Write-back runs only after validation
passes, so a failed run cannot corrupt state.

- **`aukus_tracker.py`**: Pillar 1 milestones (submarine rotations, the
  Virginia-class sale, industrial-base payments, Osborne and Henderson, export
  controls) and the eight Pillar 2 workstreams.
- **`pacific_tracker.py`**: a per-country ledger across seventeen Pacific states
  and territories: PRC security and policing arrangements, port and
  infrastructure deals, loans, senior visits, recognition questions.
- **`calendar_tracker.py`**: the verified date whitelist behind
  `calendar_watch`. `add_event()` refuses a date with fewer than two independent
  sources. Named `calendar_tracker` rather than `calendar` because a
  `calendar.py` in the repo root shadows the standard library module feedparser
  imports.

## Running it

```bash
pip install -r requirements.txt
export ANTHROPIC_API_KEY=...
export GMAIL_USER=...           # Gmail address
export GMAIL_APP_PASS=...       # 16-character app password, not the account password
export DIGEST_TO=...            # comma-separated recipient list
python run.py
```

| Flag | Effect |
| --- | --- |
| `--dry-run` | Collect only. No Claude call. Inspect `collected.json`. |
| `--no-send` | Full generation and render, no email. Open `latest.html`. |
| `--from-cache` | Skip collection, reuse `collected.json`. Fast prompt iteration. |
| `--no-track` | Skip tracker and archive write-back. |

Optional environment: `GMAIL_FROM` (sending alias), `WEB_URL` (archive base for
the read-online link), `USE_OS_TRUSTSTORE=1` (see below).

### Tests

```bash
python smoke_test.py
```

Two hundred and fourteen offline checks covering the relevance filters, region tagging, the
trackers, the archive's cross-day memory, the validator (including the regional
floors, the padding check, and the em-dash and emoji gates), dedup, source caps,
URL repair, and the renderer. No network, no API call. It writes
`smoke_output.html` so a layout change can be eyeballed for free. Run it before
any commit touching the validator or the renderer.

### A note on corporate networks

On a network that inspects TLS, most feeds die with an `SSLError` and the source
health line collapses. `_parse_feed` does not retry a TLS failure, since it will
not fix itself and each attempt costs 30 seconds. Setting `USE_OS_TRUSTSTORE=1`
(with `pip install truststore`) makes Python trust the OS certificate store
instead; it did not help on the CSIS network when tested, so compare the source
health line before keeping it. GitHub Actions has no middlebox and is unaffected.

### GitHub Actions secrets

| Secret | Purpose |
| --- | --- |
| `ANTHROPIC_API_KEY` | Claude API access |
| `GMAIL_USER` | Sender address |
| `GMAIL_APP_PASS` | Gmail app password |
| `DIGEST_TO` | Recipient list, delivered as BCC |
| `ALERT_TO` | Where failure alerts go. Set this to the operator, not the list. |

`GH_PAT` is not a repository secret. Nothing in either workflow reads it; the
Pages deploy and the double-send guard both use the built-in `GITHUB_TOKEN`.
The external cron's token lives at cron-job.org, scoped to this repository
with Actions write and nothing else. SETUP step 8a.

The primary trigger is an external cron firing `workflow_dispatch` at 6:00 AM ET
on weekdays. The six Actions cron entries are fallbacks, and a guard step skips
them if the dispatch already succeeded, so a slow run cannot produce a second
issue.

## Editorial guardrails

- **SOURCE-OR-SKIP.** Every claim traces to a collected article or an injected
  baseline. Omission beats invention.
- **Think tank fabrication, hard block.** Lowy, ASPI, USSC, Devpolicy, CSIS,
  Brookings, Carnegie, and RAND are the tempting inventions on this beat.
- **Sport and entertainment, hard block.** The analogue of the Korea brief's
  K-pop block, and more necessary: AU and NZ feeds are dominated by sport.
- **Prestige outlet rule.** The requester's eleven outlets appear when they
  publish something qualifying.
- **Specialist rule.** Same-day Lowy Interpreter, ASPI Strategist, and Devpolicy
  pieces always make Also Today, even at capacity.
- **Dates from sources only.** `calendar_watch` and `on_this_day` draw from
  today's articles or the confirmed calendar, never from model recall.
- **House style, enforced in the validator.** Zero em-dashes, no emojis, Chicago
  numbers, U.S. and U.K. keep their periods, serial comma. An em-dash is a
  critical failure that blocks the send.

Word count: the prompt targets 2,200 to 2,600 words; the validator's hard floor
is 1,400 with a 2,000 to 2,400 target. The gap is deliberate, since
post-processing strips 200 to 400 words. These are not the Korea brief's
numbers, which were 850 and 1,200: twelve topics across Australia, New Zealand
and seventeen Pacific states do not fit in 1,500 words, and the second live
issue proved it by clearing every gate at 1,516 words while running a
supermarket promotion and dropping six wire services.

## Repo layout

```
run.py                  orchestrator and validator
collect.py              feed tiers, relevance filter, region tagging
digest.py               prompts, schema, model calls
render.py               HTML email
send_email.py           Gmail SMTP
archive.py              SQLite corpus and cross-day memory
aukus_tracker.py        AUKUS milestone ledger
pacific_tracker.py      China-in-the-Pacific ledger
calendar_tracker.py     verified diplomatic calendar
update_readme.py        refreshes the stats block above
smoke_test.py           offline test suite, no network and no API spend
pipeline_health.py      post-run health monitor, feeds into metrics.jsonl
resolve.py              canonicalizes Google News redirects, cached in archive.db
fulltext.py             fetches real article bodies, cached in archive.db
newsletters.py          subscriber-only newsletters over IMAP, off by default
markets.py              ASX 200, AUD/USD, NZX 50, NZD/USD, Brent, with fallbacks
list_sources.py         Regenerates SOURCES.md from the feed dicts
SOURCES.md              Every feed, tier, region and routing decision
weekly.py               Friday Week in Review, synthesized from the archive
cost_report.py          spend per issue and projected monthly, from metrics.jsonl
BASELINES_WORKSHEET.md  staging for _REGIONAL_BASELINES, not imported anywhere
data/                   archive.db and tracker JSON, committed each run
public/                 GitHub Pages archive, rebuilt each run
.github/workflows/      daily-brief.yml and weekly-review.yml
_reference/             read-only snapshot of the Korea pipeline
```

## Setting it up

See **[SETUP.md](SETUP.md)** for the full runbook: credentials, the seeded files
that must be populated first, creating the repo, the six Actions secrets, Pages,
the external cron, and the two-week supervised pilot.

## Before the first supervised send

Three seeded files still carry placeholder content and are marked as such in
their own output, so the model refuses to state them as fact. Populate each with
two independent working sources per line:

1. `digest.py` → `_REGIONAL_BASELINES`: current ministries, portfolios, cash
   rates, PIF membership and chair.
2. `data/aukus_tracker.json`: every Pillar 1 milestone currently marked
   `"confidence": "seed"`.
3. `data/calendar_tracker` entries, use `calendar_tracker.add_event()`, which
   enforces the two-source rule.
