"""
Australia Chair Daily Brief: Digest generator
CSIS Australia Chair

Takes the collected payload, injects the trackers and baselines, and asks Claude
for a structured JSON briefing package.

Forked from the Korea Daily Brief. The editorial guardrails are carried over
intact; what changes is the region, the section schema, the regional floors, and
the ALREADY COVERED block that gives this brief the cross-day memory the Korea
and Japan pipelines lack.
"""
import json
import os
import re
import time
from datetime import datetime

# anthropic and httpx are imported lazily inside the call helpers. Collection,
# validation, and rendering all import this module for _count_digest_words, and
# none of them should need the API client installed to run.

# ─────────────────────────────────────────────────────────────────────────────
# SYSTEM PROMPT
# ─────────────────────────────────────────────────────────────────────────────

SYSTEM_PROMPT = """You are the senior analyst for the CSIS Australia Chair. You produce the Australia Chair Daily Brief, a daily intelligence-style product covering Australia, New Zealand, and the Pacific Islands, read by CSIS staff, US government Indo-Pacific officials, Australian and New Zealand officials, and academic specialists on the region.

YOUR AUDIENCE IS EXPERT. They do not need your opinion. They need facts, figures, and connective context so they can form their own. Your job is to save them time, surface what they might otherwise miss, and connect data points across sources. Do NOT editorialize. Do NOT tell the reader what to think.

COVERAGE MANDATE, the twelve topics this brief exists to cover:
US-Australia relations; AUKUS; Australian foreign policy; Australian defence policy; New Zealand defence policy; New Zealand foreign policy; Pacific Islands diplomacy; Australian politics; New Zealand politics; Pacific Islands politics; China in the Pacific Islands; US-China competition in the Pacific Islands.

REGIONAL BALANCE, READ THIS TWICE:
Australian news volume will outrun New Zealand and Pacific Islands volume by an order of magnitude every single day. That is a property of the feeds, not of what matters. Four of the twelve topics above are Pacific Islands topics and two are New Zealand topics. A brief that is 90 percent Canberra has failed its mandate even if every Canberra item is good.
- pacific_wire and new_zealand have MINIMUM item counts. Fill them from genuine reporting.
- If, and only if, the day's articles contain nothing that qualifies, return the single stand-in string for that section (specified in the schema below). Do NOT pad the section with an Australian story that mentions the Pacific in passing, a sport or human-interest item, or a rewritten version of something already in another section. A short honest section beats a padded one. Padding is a worse failure than an empty section.

GROUNDING, THE ZERO HALLUCINATION RULE (CRITICAL):
Getting a name, title, date, or figure wrong destroys credibility. One wrong name and the reader stops trusting every fact in the brief.
SOURCE-OR-SKIP PRINCIPLE: for EVERY factual claim you write you must be able to point to either (a) a source article in this batch, or (b) a reference baseline provided in this prompt. If a fact comes from neither, DO NOT INCLUDE IT. An omission is always better than an invention.
- ONLY use names, titles, figures, and claims that appear explicitly in the source articles. If an article says "the defence minister" without naming them, write "the defence minister". Do NOT fill in a name from training data. Ministries reshuffle, governments change, portfolios move.
- PROPER NOUNS: COPY, DO NOT RECALL. Ship names, unit designations, company names, place names, party names, island and province names: use exactly the form in the source article.
- If two sources conflict, note both. If a source is vague, stay vague.
- HISTORICAL CLAIMS: pattern_note may reference a precedent ONLY if it appears in today's articles or in the trackers injected below. Otherwise set it to null. A wrong date is worse than no date.
- DATES: calendar_watch and on_this_day may use dates from today's articles or from the VERIFIED DIPLOMATIC CALENDAR below. Nowhere else. For a standing fixture with no confirmed date, write the window ("expected in August"), never a specific day.
- AUKUS STATUS: submarine milestones, boat counts, payment figures, and yard progress come from the AUKUS MILESTONE TRACKER below or from today's articles. Never from memory. A milestone the tracker marks UNVERIFIED SEED must not be stated as fact, omit it.
- PACIFIC HISTORY: "first since", "last time", and agreement histories come from the CHINA IN THE PACIFIC TRACKER below or from today's articles. If the tracker is silent on a state, say nothing about its history.
- ARITHMETIC: when this prompt supplies a pre-calculated total or percentage, use it exactly. Do NOT recalculate.
- EVERY ITEM MUST EXIST IN THE INPUT. Every entry in every section must correspond to an actual article in the data below, with that article's real URL. Do NOT present old events as today's news. If a section has fewer qualifying articles than its target, return fewer items.

THINK TANK FABRICATION, HARD BLOCK:
You have a strong tendency to invent generic-sounding think tank pieces when the feed is thin. For this region the tempting inventions are Lowy Institute, the Lowy Interpreter, ASPI, the ASPI Strategist, the United States Studies Centre, Devpolicy, CSIS, Brookings, Carnegie, and RAND. The fabrications follow a telltale pattern: a vague title ("examines Australia's evolving strategic environment", "argues for deeper Pacific engagement"), no specific data, and no real URL. STOP. If a think tank piece is not in the input with a real URL, it does not exist. Do not create it.

SPORT AND ENTERTAINMENT, HARD BLOCK:
NEVER include AFL, NRL, rugby, cricket, the Ashes, netball, the Melbourne Cup, the Australian Open, Olympic or Commonwealth Games coverage, motorsport, horse racing, celebrity, reality television, film, music, or royal-visit colour in ANY section. Not even when a sport story carries a diplomatic angle (a Pacific rugby tour, a stadium financed by a foreign government) unless the diplomatic substance is the story and the sport is incidental. This brief covers security, foreign policy, defence, politics, trade, and development only.

PRESTIGE OUTLET RULE, MANDATORY INCLUSION:
If a qualifying story appears from any of The Australian, Sydney Morning Herald, Australian Financial Review, ABC News, Wall Street Journal, New York Times, Politico, Radio New Zealand Pacific, Islands Business, or Pacific Island Times, it MUST appear in the brief, in top_stories if major, otherwise in the appropriate section. These are the outlets the Australia Chair reads. Never drop a qualifying story from them.
Also mandatory: Reuters, AP, AFP, Financial Times, The Economist, Bloomberg, and the Washington Post assign this region selectively, so when they publish on it, it is inherently worth carrying.

SPECIALIST RULE, MANDATORY INCLUSION:
A same-day piece from the Lowy Interpreter, the ASPI Strategist, or the Devpolicy Blog ALWAYS appears in also_today, even if the section is at capacity. These three carry a large share of the serious analysis on this region and publish selectively.

JOURNALIST FLAGGING: when a byline from the watch list appears in the input as flagged_journalist, treat the story as higher priority and name the correspondent in the source line.

VOICE, ECONOMIST-STYLE AND FACTS FIRST:
Write like a senior Economist correspondent. Crisp, declarative, no throat-clearing. Every sentence earns its place. Lead with the verb, not the setup.
- Never open with "In a move that", "According to", "This comes as", "The move comes amid", or "This is significant because". State what happened.
- Do NOT use: notably, importantly, significantly, crucially, interestingly, it is worth noting. If it were not notable you would not be running it.
- Do NOT interpret. No "this suggests" or "this could mean". State the facts and the precedent; the expert reader draws the inference.
- Active voice. "Canberra recalled its high commissioner", not "the high commissioner was recalled".
- so_what: one sentence naming the specific decision, meeting, or deadline this affects, and only when that decision appears in today's articles or the calendar. No editorializing.
- pattern_note: one sentence citing a dated precedent, only when the precedent is sourced. Otherwise null.
- morning_memo: the factual connections across the day. State them as fact; the reader sees the implication.
- The RE: line is a crisp factual one-liner readable on a phone in five seconds.

HOUSE STYLE, BINDING ON EVERY SENTENCE:
- ZERO em-dashes. Use commas for parentheticals, a colon for an emphatic setup, or two sentences. En-dashes only in numeric ranges (2020-2024). Name pairs take a hyphen: Australia-China, US-Australia, China-Pacific.
- Numbers: spell out zero to one hundred and round multiples; numerals otherwise and for all percentages ("42 percent" in text). Money as "$5 million". Never open a sentence with a numeral.
- Dates in text: "August 24, 2026".
- U.S. and U.K. keep their periods, as noun and as adjective. EU, UN, NATO, AUKUS, ANZUS take none.
- Serial comma always. American quotation style.
- "said" and "wrote" are the neutral attribution verbs. Never "claimed" to imply doubt.
- Spell Australian and New Zealand institutions the way they spell themselves: Department of Defence, Ministry of Foreign Affairs and Trade, Labor (the party) but labour (the noun).
- No emojis anywhere, in any field.

BREVITY: top_stories body 2-3 sentences (60-80 words). overnight_items 2-3 sentences (50-70 words). All other item sections 1-2 sentences (40-60 words). Academic summaries may run to 3 sentences. Cut filler.

DEDUPLICATION, ZERO TOLERANCE, WITHIN THE DAY:
- ONE TOPIC = ONE ENTRY across the entire brief. Before placing any item ask: is this the same underlying event, decision, or announcement as something already placed? If yes, do not include it, regardless of source or angle.
- Classic collisions for this region, each of which is ONE entry: an RBA cash rate decision written up by four outlets; an AUKUS submarine milestone covered by both Defence News and the ABC; a prime ministerial press conference reported by SMH, The Australian, and the AFR; a Pacific Islands Forum statement carried by RNZ Pacific and Islands Business; any wire story repicked by an Australian outlet.
- Pick the BEST source for each topic and place it in the HIGHEST appropriate section.
- FINAL DEDUP PASS, MANDATORY: after drafting, walk the sections in placement-priority order and delete any item whose topic already appeared in a higher section.

PLACEMENT PRIORITY (highest wins): top_stories > overnight_items > aukus_watch > pacific_wire > new_zealand > china_in_the_pacific > canberra_politics > business_economy > primary_documents > also_today. Each article appears in exactly ONE section.

CATEGORIES: closed list. Valid values: US-Australia, AUKUS, AU-Foreign-Policy, AU-Defense, NZ-Foreign-Policy, NZ-Defense, Pacific-Diplomacy, AU-Politics, NZ-Politics, Pacific-Politics, China-Pacific, US-China-Pacific, Trade-Economy. Do NOT invent other labels.
SIGNAL TYPES: closed list: ESCALATION, ANOMALY, DEVELOPMENT, CONFIRMATION, CONTEXT.

Return ONLY valid JSON. No markdown, no preamble, no commentary outside the JSON structure."""


# ─────────────────────────────────────────────────────────────────────────────
# REFERENCE BASELINES
# ─────────────────────────────────────────────────────────────────────────────
# These live in the USER prompt, not the system prompt, so they can be updated
# without touching the cached system block.
#
# MAINTENANCE: this block is deliberately thin and every line is marked
# unverified. Populate it with confirmed office-holders and figures, two working
# sources each, before the first supervised send. An out-of-date minister here is
# worse than no baseline at all, because the model will trust it.

_REGIONAL_BASELINES = """\
UNVERIFIED: this baseline block has not been populated yet. Until it is, treat it
as empty: take every name, title, portfolio, and figure from today's source
articles only, and write around anything the articles do not supply.

To populate (two independent working sources per line):
  Australia: Prime Minister, Deputy PM, Foreign Minister, Defence Minister,
                Minister for International Development and the Pacific, Treasurer,
                Opposition Leader and shadow foreign/defence; governing party and
                seat margin; next federal election window.
  New Zealand: Prime Minister, Foreign Minister, Defence Minister, coalition
                composition, next general election window.
  Defence: Australian defence spending as a share of GDP and its announced
                trajectory; headline force-structure decisions; NZ defence
                capability plan status.
  Economy: RBA cash rate and last decision date; RBNZ official cash rate;
                Australia's top export markets and China's share.
  Pacific: Pacific Islands Forum membership count and current chair; states
                holding Compacts of Free Association with the US; which states
                recognise Taiwan.
"""


# ─────────────────────────────────────────────────────────────────────────────
# USER PROMPT
# ─────────────────────────────────────────────────────────────────────────────

def _tier_json(articles: list, max_items: int = 60) -> str:
    trimmed = articles[:max_items]
    result = []
    for a in trimmed:
        item = {
            "title":   a.get("title", ""),
            "url":     a.get("url", ""),
            "summary": a.get("summary", "")[:800],
            "source":  a.get("source", ""),
            "region":  a.get("region", ""),
        }
        for optional in ("prestige", "journal_tier", "flagged_journalist",
                         "primary_document", "seen_before", "tags"):
            if a.get(optional):
                item[optional] = a[optional]
        result.append(item)
    return json.dumps(result, ensure_ascii=False, indent=1)


def build_user_prompt(payload: dict, date_str: str) -> str:
    from aukus_tracker import build_context_block as aukus_context
    from pacific_tracker import build_context_block as pacific_context
    from calendar_tracker import build_context_block as calendar_context
    from archive import build_context_block as archive_context

    bar = "=" * 60
    counts = payload.get("region_counts", {})

    covered = archive_context(days=3)
    covered_block = f"\n{bar}\nCROSS-DAY MEMORY\n{bar}\n{covered}\n" if covered else ""

    return f"""Produce the Australia Chair Daily Brief for {date_str}.

CRITICAL: SOURCE GROUNDING: every name, title, number, and fact you write must come from the source articles below or the reference blocks in this prompt. Do NOT fill in names from memory.
CRITICAL: SOURCE URLs: every item must carry the exact URL from the input data. Never use "#" or a placeholder. If an item has no URL, omit the url field rather than inventing one.

{bar}
REGIONAL BASELINES
{bar}
{_REGIONAL_BASELINES}
{bar}
{aukus_context()}
{bar}
{pacific_context()}
{bar}
{calendar_context()}
{covered_block}{bar}
TODAY'S COLLECTION BALANCE
{bar}
Tier 1 news by region: {counts.get('AU', 0)} Australia, {counts.get('NZ', 0)} New Zealand, {counts.get('Pacific', 0)} Pacific Islands.
Use this to calibrate: if the Pacific count is low, the pacific_wire section will be thin, and a thin honest section is the correct output. Do not compensate by stretching an Australian story into a Pacific one.

{bar}
TIER 1: NEWS ARTICLES (last 24h)
{bar}
{_tier_json(payload.get("tier1", []), max_items=90)}

{bar}
TIER 2: ANALYSIS AND COMMENTARY (last 36h)
{bar}
{_tier_json(payload.get("tier2", []), max_items=40)}

{bar}
TIER 3: ACADEMIC (last 72h)
{bar}
{_tier_json(payload.get("tier3", []), max_items=15)}

{bar}
TIER 4: PRIMARY DOCUMENTS (last 48h)
{bar}
{_tier_json(payload.get("tier4", []), max_items=20)}
These are official statements, communiques, and ministerial transcripts. Unlike a state-media tier they are authoritative and MAY drive framing. Quote them precisely.

{bar}
OUTPUT SCHEMA
{bar}
Return a single JSON object with these keys.

- digest_date: "{date_str}"
- re_line: the day's headline facts as 3 to 5 clipped fragments joined by " · " (space, middle dot, space), scannable on a phone in five seconds. Not a sentence. No "RE:" inside the string, no trailing full stop. This is also the email subject line, so put the most consequential fragment first. Example shape: "AUKUS Osborne milestone slips · Marles in Honiara · RBA holds · PIF communique lands · NZ frigate decision due"
- morning_memo: EXACTLY 3 strings. The three things a desk officer would tell their boss in a lift. One sentence each, lead with the verb, sourced from today's articles. All three must be distinct, and at least one should come from outside Australia when the day's reporting allows it.
- top_stories: 2-4 items, aim for 3. The biggest HARD NEWS of the day, original reporting from wires, correspondents, national dailies, or government sources. Not op-eds, not think tank commentary. Each item MUST cover a different topic; span domains where the day allows (one alliance or defence story, one politics story, one Pacific or regional story). Each: url, source, category_tag (from the closed list), headline, body (2-3 sentences, facts first, specific numbers, one beat of context, no interpretation), so_what (1 sentence or null), pattern_note (1 sentence with a sourced dated precedent, or null), src_line.
  src_line FORMAT, follow exactly: `per <Outlet>: "<the article's exact published headline>"`. Copy the headline verbatim from the input data, do not paraphrase it. When a second outlet carried the same story and you drew on it, append ` · also <Outlet>`. When the byline is on the watch list, write `per <Outlet> (<Correspondent>): "<headline>"`.
- overnight_items: 3-6 items. Source diversity is mandatory: no outlet more than 3 times. Topic diversity is mandatory. Each: url, source, category, headline (under 100 chars), body_text (2-3 sentences), signal_type.
- aukus_watch: 0-5 items. AUKUS Pillar 1 and Pillar 2 developments: submarine milestones, yard and workforce news, congressional and parliamentary action, export-control and licensing changes, Pillar 2 workstreams. Cross-check every status claim against the AUKUS MILESTONE TRACKER. Each: url, source, headline, body_text, pillar ("1", "2", or "both"), signal_type.
- pacific_wire: MINIMUM 2, maximum 5. Pacific Islands diplomacy, politics, security, and development, from Pacific and regional reporting. Prefer items sourced from RNZ Pacific, Islands Business, Pacific Island Times, Benar News, and the national Pacific press over Australian coverage of the Pacific, when both exist. Each: url, source, country (the Pacific state or territory, or "Regional"), headline, body_text, category, signal_type.
  IF AND ONLY IF nothing qualifies: return exactly [{{"stand_in": "No significant Pacific Islands developments in the past 24 hours."}}], a one-element array containing only that object. Never pad.
- new_zealand: MINIMUM 1, maximum 4. New Zealand foreign policy, defence policy, and politics. Each: url, source, headline, body_text, category, signal_type.
  IF AND ONLY IF nothing qualifies: return exactly [{{"stand_in": "No significant New Zealand developments in the past 24 hours."}}].
- china_in_the_pacific: 0-4 items. PRC activity in the Pacific Islands and US-China competition there: security and policing arrangements, port and infrastructure deals, loans, senior visits, recognition questions, fisheries and maritime presence. Cross-check history claims against the CHINA IN THE PACIFIC TRACKER. Each: url, source, country, headline, body_text, activity_type, signal_type, is_reaction_source (true for Global Times, Xinhua, China Daily, People's Daily).
- canberra_politics: 0-5 items. Australian domestic politics where it bears on foreign or defence policy: parliamentary action, committee inquiries, portfolio changes, party positioning, budget and procurement decisions. Each: url, source, headline, body_text, category.
- business_economy: 0-5 items. Trade, critical minerals, energy, investment screening, and economic coercion. Each: url, source, headline, body_text, category.
- primary_documents: 0-4 items drawn from Tier 4. Each: url, source, document_type (communique, joint statement, ministerial transcript, readout, testimony), headline, body_text, key_line (the single most consequential sentence, quoted exactly from the source, or null).
- calendar_watch: 4-5 entries. Upcoming events with dates or windows, drawn ONLY from today's articles or the VERIFIED DIPLOMATIC CALENDAR. Each: date (ISO if confirmed, else null), window (a phrase like "expected in August", or null), event, why_it_matters (1 sentence), confirmed (boolean).
- also_today: 0-6 items. The wire. Secondary news worth a line. Mandatory placement for same-day Lowy Interpreter, ASPI Strategist, and Devpolicy pieces. Each: url, source, headline, body_text.
- opeds_today: 0-6 items from Tier 2. Each: url, source, headline (the EXACT published title, not a paraphrase), authors, prestige_tier, central_argument, summary, policy_so_what.
- academic_today: 0-6 items from Tier 3. Each: url, source, headline, authors, journal_tier, summary (3 sentences), policy_so_what.
- on_this_day: 0-1 items, ONLY from the calendar's confirmed anniversaries. Each: date, event, relevance. Empty array if none.
- story_count: integer, total items across all sections.

TARGET LENGTH: HARD MINIMUM 1,000 WORDS. Aim for 1,400-1,600 words: post-processing strips duplicate URLs and over-represented sources, which typically removes 200-400 words. Reach the target by covering MORE stories, not by inflating individual bodies. If your draft runs short, add items to overnight_items or also_today.

FINAL CHECKS before you return:
1. Walk placement priority and delete every cross-section duplicate.
2. Confirm pacific_wire has at least 2 real items or exactly the stand-in object.
3. Confirm new_zealand has at least 1 real item or exactly the stand-in object.
4. Confirm morning_memo has exactly 3 distinct strings.
5. Confirm nothing from the ALREADY COVERED list is repeated without a material new development.
6. Confirm zero em-dashes and zero emojis anywhere in the output.
7. Confirm every url is a real URL from the input data above.

Return ONLY valid JSON. Begin your response with {{ and end it with }}. No code fences, no preamble, no commentary before or after the JSON."""


# ─────────────────────────────────────────────────────────────────────────────
# WORD COUNT AND CONTENT MINIMUMS
# ─────────────────────────────────────────────────────────────────────────────

_TEXT_FIELDS = ("body", "body_text", "summary", "detail", "so_what",
                "pattern_note", "central_argument", "policy_so_what",
                "key_line", "why_it_matters")

_COUNTED_SECTIONS = (
    "top_stories", "overnight_items", "aukus_watch", "pacific_wire",
    "new_zealand", "china_in_the_pacific", "canberra_politics",
    "business_economy", "primary_documents", "also_today",
    "opeds_today", "academic_today", "calendar_watch",
)


def _count_digest_words(digest: dict) -> int:
    """Rough word count across all readable text fields."""
    words = 0
    for mi in (digest.get("morning_memo") or []):
        words += len(str(mi).split())
    words += len(str(digest.get("re_line", "")).split())
    for section_key in _COUNTED_SECTIONS:
        for item in (digest.get(section_key) or []):
            if not isinstance(item, dict):
                continue
            if item.get("stand_in"):
                continue  # stand-in lines are not content
            for field in _TEXT_FIELDS:
                words += len(str(item.get(field, "")).split())
    return words


def _check_content_minimums(digest: dict) -> list[str]:
    """Fast pre-validation used to decide whether to re-prompt for length."""
    failures = []
    wc = _count_digest_words(digest)
    if wc < 1000:
        failures.append(f"word count {wc} is below the 1000-word minimum")
    if len(digest.get("top_stories") or []) < 2:
        failures.append("top_stories has fewer than 2 items")
    if len(digest.get("overnight_items") or []) < 3:
        failures.append("overnight_items has fewer than 3 items")
    if len(digest.get("morning_memo") or []) != 3:
        failures.append("morning_memo must have exactly 3 items")
    if not (digest.get("re_line") or "").strip():
        failures.append("re_line is missing")
    return failures


# ─────────────────────────────────────────────────────────────────────────────
# JSON PARSING
# ─────────────────────────────────────────────────────────────────────────────

def _strip_fences(raw: str) -> str:
    text = raw.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    return text.strip()


def _robust_json_parse(raw: str) -> dict:
    """Parse Claude's JSON, repairing the usual truncation and trailing-comma cases."""
    text = _strip_fences(raw)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    # Trailing commas
    repaired = re.sub(r",\s*([}\]])", r"\1", text)
    try:
        return json.loads(repaired)
    except json.JSONDecodeError:
        pass

    # Truncated output: close the open structures and retry
    opens = repaired.count("{") - repaired.count("}")
    bracket = repaired.count("[") - repaired.count("]")
    if opens > 0 or bracket > 0:
        candidate = repaired.rstrip().rstrip(",")
        candidate += "]" * max(0, bracket) + "}" * max(0, opens)
        try:
            parsed = json.loads(candidate)
            print("  !  JSON was truncated; recovered by closing open structures")
            return parsed
        except json.JSONDecodeError:
            pass

    # Last resort: the largest parseable prefix ending on a closing brace
    for end in range(len(repaired), 0, -1):
        if repaired[end - 1] != "}":
            continue
        try:
            return json.loads(repaired[:end])
        except json.JSONDecodeError:
            continue

    raise ValueError(f"Could not parse Claude response as JSON (len {len(raw)}): {raw[:400]}")


# ─────────────────────────────────────────────────────────────────────────────
# MODEL CALLS
# ─────────────────────────────────────────────────────────────────────────────
# Pinned constants. FAST_MODEL runs the first attempt; PRIMARY_MODEL is the
# retry escalation.
#
# This is the Korea brief's cost design, carried over deliberately: Sonnet
# handles the ~90 percent of days that pass validation first time, and Opus is
# reserved for the days Sonnet under-generates. Set FAST_MODEL to PRIMARY_MODEL
# to run every day on Opus.
#
# These IDs are complete as written. Do NOT append a date suffix: the Korea
# pipeline pins dated Claude 4 snapshots, and that convention does not carry to
# the current generation.
FAST_MODEL = "claude-sonnet-5"
PRIMARY_MODEL = "claude-opus-5"

# Thinking is adaptive on the current models; budget_tokens was removed and is
# rejected with a 400. Effort controls depth and spend.
_THINKING = {"type": "adaptive"}
_EFFORT = "high"

# Streaming, so a large max_tokens does not hit the HTTP timeout.
MAX_OUTPUT_TOKENS = 32000


def _stream_claude(client, messages: list, max_tokens: int = MAX_OUTPUT_TOKENS,
                   _retries: int = 3, model: str | None = None) -> dict:
    """Stream a Claude call and return the parsed digest.

    No assistant prefill. The Korea pipeline opens the response with an
    assistant turn containing '{"' to force JSON, which works on the Claude 4
    models it pins but is rejected with a 400 on the current generation. The
    prompt asks for bare JSON instead, and _robust_json_parse cleans up fences
    or a stray preamble if one appears.

    Retries on transient stream errors.
    """
    import httpx

    use_model = model or PRIMARY_MODEL
    model_label = use_model.split("-")[1] if "-" in use_model else use_model

    for attempt in range(_retries):
        try:
            t0 = time.time()
            collected = []
            with client.messages.stream(
                model=use_model,
                max_tokens=max_tokens,
                thinking=_THINKING,
                output_config={"effort": _EFFORT},
                system=[{
                    "type": "text",
                    "text": SYSTEM_PROMPT,
                    # The system prompt is frozen, so it caches cleanly. Every
                    # volatile thing (today's date, the articles, the trackers)
                    # lives in the user prompt, after this breakpoint.
                    "cache_control": {"type": "ephemeral"},
                }],
                messages=messages,
            ) as stream:
                for text in stream.text_stream:
                    collected.append(text)
            response = stream.get_final_message()
            if response.stop_reason == "max_tokens":
                print(f"  !  Response truncated at {response.usage.output_tokens} tokens")
            if response.stop_reason == "refusal":
                detail = getattr(response, "stop_details", None)
                raise RuntimeError(
                    f"Claude declined the request "
                    f"(category: {getattr(detail, 'category', 'unknown')})")
            elapsed = time.time() - t0
            raw_text = "".join(collected)
            if not raw_text.strip():
                raise ValueError("Empty response from the Claude API")
            cache_read = getattr(response.usage, "cache_read_input_tokens", 0) or 0
            cache_create = getattr(response.usage, "cache_creation_input_tokens", 0) or 0
            cache_info = (f" / {cache_read} cache-hit" if cache_read
                          else f" / {cache_create} cache-write" if cache_create else "")
            print(f"    {model_label} call: {elapsed:.0f}s "
                  f"({response.usage.input_tokens} in / "
                  f"{response.usage.output_tokens} out{cache_info})")
            return _robust_json_parse(raw_text)
        except (httpx.RemoteProtocolError, httpx.ReadError, httpx.StreamError) as e:
            if attempt < _retries - 1:
                wait = 5 * (attempt + 1)
                print(f"  !  Stream interrupted ({e.__class__.__name__}), retrying in {wait}s...")
                time.sleep(wait)
            else:
                raise
    raise RuntimeError("stream retries exhausted")


def _call_claude(client, user_prompt: str, max_tokens: int = MAX_OUTPUT_TOKENS,
                 model: str | None = None) -> dict:
    return _stream_claude(client, [{"role": "user", "content": user_prompt}],
                          max_tokens, model=model)


def generate_digest(payload: dict) -> dict:
    """Call Claude and return the structured brief. Retries to reach content minimums."""
    import anthropic

    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        raise RuntimeError("Missing ANTHROPIC_API_KEY. Set it before running.")
    client = anthropic.Anthropic(api_key=api_key)

    from zoneinfo import ZoneInfo
    date_str = datetime.now(ZoneInfo("America/New_York")).strftime("%A, %B %d, %Y")
    user_prompt = build_user_prompt(payload, date_str)
    total = sum(len(v) for k, v in payload.items() if isinstance(v, list))
    print(f"\n  Generating brief ({total} articles -> Claude)...")

    MAX_ATTEMPTS = 4
    digest = None
    best_digest = None
    best_words = 0
    failures = []

    for attempt in range(MAX_ATTEMPTS):
        try:
            retry_model = FAST_MODEL if attempt == 0 else PRIMARY_MODEL
            if attempt == 0 or digest is None:
                digest = _call_claude(client, user_prompt, model=retry_model)
            else:
                deficit = max(0, 1000 - _count_digest_words(digest))
                expansion = (
                    "Your previous output failed content minimums:\n"
                    + "\n".join(f"  - {f}" for f in failures)
                    + f"\n\nYou are roughly {deficit} words short of the 1,000-word minimum.\n"
                    "\nHere is your previous output:\n"
                    + json.dumps(digest, ensure_ascii=False)[:8000]
                    + "\n\nReturn a COMPLETE updated brief JSON that fixes every failure above.\n"
                    "- WORD COUNT: reach 1,000 words minimum. top_stories bodies 60-80 words, "
                    "overnight_items 50-70, other sections 40-60. Add MORE items from the "
                    "available articles. Do not inflate existing bodies with filler.\n"
                    "- Keep the regional floors: pacific_wire needs 2 real items or the "
                    "stand-in object; new_zealand needs 1 real item or the stand-in object. "
                    "Do NOT invent Pacific or New Zealand items to satisfy them.\n"
                    "- morning_memo must have exactly 3 distinct strings.\n"
                    "Return ONLY valid JSON."
                )
                messages = [
                    {"role": "user", "content": user_prompt},
                    {"role": "assistant",
                     "content": json.dumps(digest, ensure_ascii=False)[:4000]},
                    {"role": "user", "content": expansion},
                ]
                digest = _stream_claude(client, messages, model=retry_model)

            words = _count_digest_words(digest)
            if words > best_words:
                best_digest, best_words = digest, words

            failures = _check_content_minimums(digest)
            if not failures:
                print(f"  Content minimums met on attempt {attempt + 1} ({words} words)")
                return digest

            print(f"  !  Attempt {attempt + 1} short of minimums ({words} words):")
            for f in failures:
                print(f"       - {f}")

        except Exception as e:
            print(f"  !  Attempt {attempt + 1} failed: {e}")
            if attempt == MAX_ATTEMPTS - 1 and best_digest is None:
                raise

    print(f"  !  Returning the best of {MAX_ATTEMPTS} attempts ({best_words} words)")
    return best_digest or digest


def regenerate_digest(payload: dict, previous_digest: dict, warnings: list[str],
                      attempt: int = 0) -> dict:
    """Re-generate with validator feedback. Escalates to the primary model."""
    import anthropic

    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        raise RuntimeError("Missing ANTHROPIC_API_KEY.")
    client = anthropic.Anthropic(api_key=api_key)

    from zoneinfo import ZoneInfo
    date_str = datetime.now(ZoneInfo("America/New_York")).strftime("%A, %B %d, %Y")
    user_prompt = build_user_prompt(payload, date_str)

    feedback = (
        "Your previous brief failed pre-send validation:\n"
        + "\n".join(f"  - {w}" for w in warnings)
        + "\n\nHere is your previous output:\n"
        + json.dumps(previous_digest, ensure_ascii=False)[:8000]
        + "\n\nReturn a COMPLETE corrected brief JSON that fixes every failure above.\n"
        "Reminders that are easy to break while fixing something else:\n"
        "- Section caps are hard limits, in both directions.\n"
        "- pacific_wire: 2 real items or exactly the stand-in object. Never padded.\n"
        "- new_zealand: 1 real item or exactly the stand-in object. Never padded.\n"
        "- Every URL must come from the input data.\n"
        "- Zero em-dashes, zero emojis.\n"
        "Return ONLY valid JSON."
    )
    messages = [
        {"role": "user", "content": user_prompt},
        {"role": "assistant", "content": json.dumps(previous_digest, ensure_ascii=False)[:4000]},
        {"role": "user", "content": feedback},
    ]
    model = FAST_MODEL if attempt == 0 else PRIMARY_MODEL
    return _stream_claude(client, messages, model=model)


if __name__ == "__main__":
    from pathlib import Path
    payload = json.loads(Path("collected.json").read_text(encoding="utf-8"))
    result = generate_digest(payload)
    Path("digest.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"  -> digest.json ({_count_digest_words(result)} words)")
