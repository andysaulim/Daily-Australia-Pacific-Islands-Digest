# Prompt Rules & Output Schema

The editorial guardrails that make the digest trustworthy, plus the section schema and how baselines get injected. These rules live in `digest.py`'s system + user prompts. Any edit to the prompt, the collector payload, or the validator must keep them intact — they are the reason recipients can rely on the brief.

## The non-negotiable rules

**SOURCE-OR-SKIP.** For every factual claim, the model must be able to point to either (a) a source article in today's batch, or (b) a reference baseline injected into the prompt. A fact from neither is dropped. Omission always beats invention. This is the spine of the whole system.

**Same-poll-date.** All polling numbers in the Public Sentiment Tracker come from one Gallup Korea survey. Never mix weeks. The collector and the confirmed-baseline block both carry a survey date; if today's articles contain a newer poll, the model updates every metric together and bumps `last_updated`, never piecemeal.

**Prestige enforcement.** If WSJ, NYT, FT, or specialist outlets (38 North, ArmsControlWonk) published relevant work, they appear — they are not dropped in favor of wire aggregators. The validator's prestige check backstops this.

**Pre-calculated totals are authoritative.** When the prompt supplies a total, percentage, or sum, the model uses it exactly — no recomputation (LLMs make arithmetic errors). It only adjusts a pre-calculated value if today's articles introduce a genuinely new data point not already in the baseline.

**Dates from sources only.** `calendar_watch` and `on_this_day` use dates from (a) today's articles, (b) the verified-dates reference, or (c) injected trade/tariff baselines. No dates from model memory — a wrong date destroys credibility. Calendar items persist across digests until their date passes.

**Trackers over recall.** Kim Jong Un's last appearance, the 14-day KCNA rhetoric baseline, and facility statuses are injected from cached JSON, not recalled. The model reports the change-vs-baseline, it does not invent the baseline.

## Analyst voice

Write like an intelligence analyst producing raw summaries: precise, factual, sourced. Add value three ways — (1) connect data points across sources the reader hasn't seen together, (2) supply specific historical precedents with dates, (3) flag what changed vs. yesterday's baseline. Numbers over adjectives. No hedging, no filler.

## Output schema — the ~15 sections

The model returns structured JSON (not prose) so every field can be validated and rendered deterministically. Sections:

- Top Stories
- Overnight Flash
- Key Stat of the Day
- Pyongyang Watch (KCNA analysis)
- Satellite & Location Watch (facility grid)
- ROK Government (ministry cards, personnel, calendar)
- Election Tracker (key races table)
- Trade & Economy (tariff dashboard)
- Northeast Asia Watch
- Public Sentiment Tracker (polling, full party names)
- The Wire (brief items)
- Statements & Analysis
- On This Day
- (plus Russia-Korea watch and calendar_watch threads woven through)

Section item counts are bounded by `SECTION_CAPS` in `run.py`; `calendar_watch` requires 4–5 forward-looking events.

## Baseline injection mechanism

Baselines go into the **user** prompt (not the system prompt) so they can be updated independently:

- **Confirmed Gallup baseline** — current approval/party numbers + survey date + a plausibility band the validator uses to reject bad scrapes. Updated weekly (see `maintenance.md`).
- **Trade & tariff baselines** — `_TRADE_BASELINES` in `digest.py`: standing trade-policy entries, sector rates, the Section 122 surcharge line, and the next-trigger deadlines. Carried forward unless today's articles report a change.
- **Reference databases** — NK-Russia timeline and provocations history from `databases.py`, plus the KCNA and Kim trackers, supplied as `db_context`.
- **Verified Korea dates** — a whitelist of confirmed dates the model may use for calendar/anniversary content.

The validator (`validate_digest` in `run.py`) is the enforcement layer behind these rules: dedup, URL repair, source/section caps, word floor, and sentiment completeness. If a rule isn't holding in output, check both the prompt text and the matching validator gate.
