"""
Australia Chair Daily Brief: Main Runner
CSIS Australia Chair

Orchestrates: collect -> digest -> validate -> render -> archive -> send

Usage:
  python run.py                # full pipeline
  python run.py --no-send      # render to file only, no email
  python run.py --from-cache   # skip collection, reuse collected.json
  python run.py --dry-run      # collect only, no Claude call
  python run.py --no-track     # skip tracker and archive write-back
"""
import argparse
import json
import os
import re
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo
from concurrent.futures import ThreadPoolExecutor, as_completed

from digest import _count_digest_words

# ─────────────────────────────────────────────────────────────────────────────
# SECTION CAPS
# ─────────────────────────────────────────────────────────────────────────────
# (min, max). The two regional floors are the one real departure from the Korea
# design: Australian volume will crowd out New Zealand and the Pacific every day
# unless the schema reserves space. A floor may be satisfied by a stand-in line
# (see _has_stand_in): that is honest. Padding is not, and is caught separately.
# Raised for the Pacific and New Zealand after the first live issue, which hit
# the ceiling in four sections at once: pacific_wire 5/5, new_zealand 4/4,
# also_today 6/6, canberra_politics 5/5. The supply was not the constraint. That
# day collected 94 Pacific items and published 10, leaving 84 unused, while the
# section that carries them sat at its cap.
#
# The Pacific ceilings are raised and the Australian ones are not, deliberately.
# Seventeen Pacific states and territories compete for pacific_wire; Canberra
# has one capital and already takes the largest share of the brief. Raising both
# would have widened the gap the REGIONAL BALANCE rule exists to close.
SECTION_CAPS = {
    "morning_memo":         (3, 3),
    "top_stories":          (2, 4),
    "overnight_items":      (3, 7),
    "aukus_watch":          (0, 5),
    "pacific_wire":         (2, 12),  # FLOOR, raised from 5 then 8
    "new_zealand":          (1, 5),   # FLOOR, raised from 4
    "china_in_the_pacific": (0, 5),   # raised from 4
    "canberra_politics":    (0, 5),
    "business_economy":     (0, 5),
    "primary_documents":    (0, 4),
    "calendar_watch":       (4, 5),
    "also_today":           (0, 8),   # raised from 6
    "opeds_today":          (0, 6),
    "academic_today":       (0, 6),
    "on_this_day":          (0, 1),
}

# Sections that may satisfy their floor with a stand-in line instead of items.
_FLOOR_SECTIONS = ("pacific_wire", "new_zealand")

_PRESTIGE_OUTLETS = {
    "The Australian", "SMH", "Sydney Morning Herald", "AFR",
    "Australian Financial Review", "ABC News", "WSJ", "Wall Street Journal",
    "NYT", "New York Times", "Politico", "RNZ Pacific", "Islands Business",
    "Pacific Island Times", "Reuters", "AP", "AFP", "Financial Times",
    "The Economist", "Bloomberg", "Washington Post",
}

_ALL_ITEM_SECTIONS = (
    "top_stories", "overnight_items", "aukus_watch", "pacific_wire",
    "new_zealand", "china_in_the_pacific", "canberra_politics",
    "business_economy", "primary_documents", "also_today",
    "opeds_today", "academic_today",
)

# Dedup priority order: first section wins a collision.
_DEDUP_SECTIONS = (
    "top_stories", "overnight_items", "aukus_watch", "pacific_wire",
    "new_zealand", "china_in_the_pacific", "canberra_politics",
    "business_economy", "primary_documents", "also_today",
    "opeds_today", "academic_today",
)

_STOP_WORDS = frozenset({
    "the", "a", "an", "in", "on", "of", "to", "for", "and", "is", "at", "by",
    "as", "with", "from", "its", "new", "over", "after", "says", "said", "amid",
    "that", "has", "will", "may", "could", "been", "are", "was", "were", "this",
    "but", "not", "all", "more", "than", "also",
})

# Entity match alone triggers dedup: institutions, people, recurring events.
_TOPIC_ENTITIES = {
    "rba": {"rba", "reserve bank of australia", "cash rate", "rate decision",
            "interest rate", "monetary policy"},
    "rbnz": {"rbnz", "reserve bank of new zealand", "official cash rate"},
    "aukus": {"aukus", "virginia-class", "virginia class", "ssn-aukus",
              "nuclear-powered submarine", "submarine rotational force", "srf-west"},
    "ausmin": {"ausmin", "australia-us ministerial", "australia-united states ministerial"},
    "quad": {"quad", "quadrilateral security dialogue"},
    "pif": {"pacific islands forum", "pif leaders", "forum leaders meeting"},
    "talisman sabre": {"talisman sabre", "exercise talisman"},
    "budget": {"federal budget", "budget night", "mid-year economic"},
}

# Company match needs keyword overlap too: big firms generate many unrelated stories.
_COMPANY_ENTITIES = {
    "bhp": {"bhp"},
    "rio tinto": {"rio tinto"},
    "fortescue": {"fortescue"},
    "woodside": {"woodside"},
    "telstra": {"telstra"},
    "qantas": {"qantas"},
    "fonterra": {"fonterra"},
    "asc": {"asc pty", "australian submarine corporation"},
    "bae": {"bae systems"},
}


# ─────────────────────────────────────────────────────────────────────────────
# URL CHECKING
# ─────────────────────────────────────────────────────────────────────────────

def _check_url(url: str, timeout: float = 5.0) -> tuple[str, bool, str]:
    """HEAD-check a URL. Only 404 and 410 count as dead: 403, 405, 429, and 451
    are normal for paywalled publishers and bot-protected servers, which is most
    of this brief's source list."""
    import requests
    try:
        resp = requests.head(url, timeout=timeout, allow_redirects=True,
                             headers={"User-Agent": "Mozilla/5.0 AU-Brief-Validator/1.0"})
        if resp.status_code in (404, 410):
            return (url, False, f"HTTP {resp.status_code}")
        return (url, True, "")
    except requests.exceptions.Timeout:
        return (url, False, "timeout")
    except requests.exceptions.ConnectionError:
        return (url, False, "connection error")
    except Exception as e:
        return (url, False, str(e)[:50])


def _validate_urls(urls: list[str]) -> list[tuple[str, str]]:
    broken = []
    try:
        with ThreadPoolExecutor(max_workers=10) as pool:
            futures = {pool.submit(_check_url, u): u for u in urls}
            for f in as_completed(futures, timeout=30):
                try:
                    url, ok, reason = f.result()
                    if not ok:
                        broken.append((url, reason))
                except Exception:
                    broken.append((futures[f], "check failed"))
    except TimeoutError:
        pass
    return broken


# ─────────────────────────────────────────────────────────────────────────────
# DEDUP
# ─────────────────────────────────────────────────────────────────────────────

def _is_stand_in(item) -> bool:
    return isinstance(item, dict) and bool(item.get("stand_in"))


def _has_stand_in(digest: dict, section: str) -> bool:
    items = digest.get(section) or []
    return len(items) == 1 and _is_stand_in(items[0])


def _real_items(digest: dict, section: str) -> list:
    return [i for i in (digest.get(section) or [])
            if isinstance(i, dict) and not i.get("stand_in")]


def _extract_entities(text: str) -> tuple[set[str], set[str]]:
    lowered = text.lower()
    topics = {tag for tag, aliases in _TOPIC_ENTITIES.items()
              if any(a in lowered for a in aliases)}
    companies = {tag for tag, aliases in _COMPANY_ENTITIES.items()
                 if any(a in lowered for a in aliases)}
    return topics, companies


def _headline_key(item: dict) -> tuple[str, set, set, set]:
    headline = (item.get("headline", "") or "").lower().strip()
    words = {w for w in re.split(r"\W+", headline)
             if len(w) > 2 and w not in _STOP_WORDS}
    topics, companies = _extract_entities(headline)
    return headline, words, topics, companies


def _is_dup(words_a, topics_a, companies_a, words_b, topics_b, companies_b) -> str | None:
    """Return a reason string when two headlines describe the same thing."""
    shared_topics = topics_a & topics_b
    if shared_topics:
        return f"same topic entity: {', '.join(sorted(shared_topics))}"

    if not words_a or not words_b:
        return None
    overlap = words_a & words_b
    ratio = len(overlap) / min(len(words_a), len(words_b))

    shared_companies = companies_a & companies_b
    if shared_companies and ratio >= 0.35:
        return f"same company ({', '.join(sorted(shared_companies))}) and {ratio:.0%} keyword overlap"
    if ratio >= 0.6 and len(overlap) >= 3:
        return f"{ratio:.0%} keyword overlap"
    return None


def _dedup_digest(digest: dict) -> tuple[dict, list[str]]:
    """Strip cross-section duplicates, keeping the higher-priority placement."""
    log = []
    seen_urls: dict[str, str] = {}
    seen_headlines: list[tuple[str, str, set, set, set]] = []

    for section in _DEDUP_SECTIONS:
        items = digest.get(section)
        if not items:
            continue
        kept = []
        for item in items:
            if _is_stand_in(item):
                kept.append(item)
                continue
            if not isinstance(item, dict):
                continue

            url = (item.get("url") or "").strip()
            if url and url in seen_urls:
                log.append(f"    - {section}: duplicate URL, already in {seen_urls[url]} "
                           f"({(item.get('headline') or '')[:60]})")
                continue

            headline, words, topics, companies = _headline_key(item)
            dup_reason = None
            if len(headline) > 15:
                for prev_sec, prev_head, prev_words, prev_topics, prev_comp in seen_headlines:
                    dup_reason = _is_dup(words, topics, companies,
                                         prev_words, prev_topics, prev_comp)
                    if dup_reason:
                        log.append(f"    - {section}: same topic as {prev_sec} "
                                   f"({dup_reason}): '{headline[:55]}'")
                        break
            if dup_reason:
                continue

            if url:
                seen_urls[url] = section
            if len(headline) > 15:
                seen_headlines.append((section, headline, words, topics, companies))
            kept.append(item)

        digest[section] = kept
    return digest, log


# ─────────────────────────────────────────────────────────────────────────────
# SOURCE DIVERSITY
# ─────────────────────────────────────────────────────────────────────────────
# Cap of 3 per outlet per section. The Korea pipeline's comment is worth
# heeding: a cap of 2 stripped 5 to 7 items a run and crashed the word count.
_SOURCE_CAP = 3

_SOURCE_PREFIX_MAP = {
    "smh": "smh", "sydney morning herald": "smh",
    "the age": "nine", "nine": "nine",
    "afr": "afr", "australian financial review": "afr",
    "the australian": "the australian", "theaustralian": "the australian",
    "abc": "abc", "australian broadcasting": "abc",
    "rnz": "rnz", "radio new zealand": "rnz",
    "guardian": "guardian",
    "reuters": "reuters", "ap": "ap", "associated press": "ap", "afp": "afp",
    "nyt": "nyt", "new york times": "nyt",
    "wsj": "wsj", "wall street journal": "wsj",
    "politico": "politico",
    "islands business": "islands business",
    "pacific island times": "pacific island times",
    "lowy": "lowy", "aspi": "aspi", "devpolicy": "devpolicy",
}

# Sections exempt from the cap: top_stories is editorially chosen, and the two
# floored sections are thin enough already.
_DIVERSITY_EXEMPT = ("top_stories", "pacific_wire", "new_zealand")


def _normalize_source(src: str) -> str:
    lowered = (src or "").strip().lower()
    for prefix, canonical in _SOURCE_PREFIX_MAP.items():
        if lowered.startswith(prefix):
            return canonical
    return lowered


# No single Pacific state may take more than this many slots in a section.
#
# pacific_wire is the widest section in the brief and covers seventeen states
# and territories. Raising its ceiling buys spread only if the extra room
# cannot be spent on more Fiji: without this, twelve slots could be eight Fiji
# items and the section would be bigger without being more regional. Fiji and
# Papua New Guinea generate the most English-language copy by a wide margin,
# which is a property of the media landscape, not of what matters.
#
# Three, not one: a genuine Fiji election week should be able to run three
# items. It is a cap on dominance, not a quota of one per country.
_COUNTRY_CAP = 3
_COUNTRY_CAPPED_SECTIONS = ("pacific_wire", "china_in_the_pacific")


def _normalize_country(raw: str) -> str:
    """Fold the spellings the model reasonably varies between."""
    c = (raw or "").strip().lower()
    if not c or c in ("regional", "pacific", "pacific islands"):
        return ""            # a regional item belongs to no single state
    aliases = {
        "png": "papua new guinea",
        "fsm": "micronesia (fsm)",
        "micronesia": "micronesia (fsm)",
        "federated states of micronesia": "micronesia (fsm)",
        "rmi": "marshall islands",
        "solomons": "solomon islands",
        "east timor": "timor-leste",
        "timor leste": "timor-leste",
    }
    return aliases.get(c, c)


def _enforce_country_diversity(digest: dict) -> list[str]:
    """Stop one state monopolising a Pacific section.

    Regional items are exempt: they belong to no single state, and capping them
    would penalise exactly the Forum-wide coverage the brief wants most.
    """
    log = []
    for section in _COUNTRY_CAPPED_SECTIONS:
        items = digest.get(section)
        if not items:
            continue
        counts: dict[str, int] = {}
        kept = []
        for item in items:
            if not isinstance(item, dict) or _is_stand_in(item):
                kept.append(item)
                continue
            country = _normalize_country(item.get("country", ""))
            if country and counts.get(country, 0) >= _COUNTRY_CAP:
                log.append(f"    - {section}: over country cap for {country} "
                           f"({(item.get('headline') or '')[:50]})")
                continue
            if country:
                counts[country] = counts.get(country, 0) + 1
            kept.append(item)
        digest[section] = kept
    return log


def _enforce_source_diversity(digest: dict) -> list[str]:
    log = []
    for section in _ALL_ITEM_SECTIONS:
        if section in _DIVERSITY_EXEMPT:
            continue
        items = digest.get(section)
        if not items:
            continue
        counts: dict[str, int] = {}
        kept = []
        for item in items:
            if not isinstance(item, dict) or _is_stand_in(item):
                kept.append(item)
                continue
            src = _normalize_source(item.get("source", ""))
            if src and counts.get(src, 0) >= _SOURCE_CAP:
                log.append(f"    - {section}: over cap for {src} "
                           f"({(item.get('headline') or '')[:55]})")
                continue
            if src:
                counts[src] = counts.get(src, 0) + 1
            kept.append(item)
        digest[section] = kept
    return log


# ─────────────────────────────────────────────────────────────────────────────
# CROSS-DAY REPEAT FILTER
# ─────────────────────────────────────────────────────────────────────────────

def _filter_stale_repeats(digest: dict) -> list[str]:
    """Layer three of the cross-day defence.

    Drops items whose exact URL already shipped inside the last 7 days and whose
    body carries no fresh date or figure. Leaves top_stories alone: if a story
    is genuinely the biggest of the day for a second day running, that is an
    editorial judgment the model is allowed to make.
    """
    try:
        from archive import is_stale_repeat
    except Exception as e:
        return [f"    ! cross-day filter unavailable: {e}"]

    log = []
    for section in _ALL_ITEM_SECTIONS:
        if section == "top_stories":
            continue
        items = digest.get(section)
        if not items:
            continue
        kept = []
        for item in items:
            if not isinstance(item, dict) or _is_stand_in(item):
                kept.append(item)
                continue
            prior = is_stale_repeat(item, days=7)
            if prior:
                log.append(f"    - {section}: ran {prior['date']} in {prior['section']} "
                           f"with nothing new ({(item.get('headline') or '')[:55]})")
                continue
            kept.append(item)
        digest[section] = kept
    return log


# ─────────────────────────────────────────────────────────────────────────────
# URL REPAIR
# ─────────────────────────────────────────────────────────────────────────────

def _headline_tokens(text: str) -> set[str]:
    return {w for w in re.split(r"\W+", (text or "").lower())
            if len(w) > 3 and w not in _STOP_WORDS}


def _repair_digest_urls(digest: dict, payload: dict) -> list[str]:
    """Fix mangled URLs by matching the headline back to the collected article."""
    log = []
    collected = []
    for tier in ("tier1", "tier2", "tier3", "tier4"):
        collected.extend(payload.get(tier) or [])
    if not collected:
        return log

    index = [(a.get("url", ""), _headline_tokens(a.get("title", ""))) for a in collected]
    valid_urls = {a.get("url", "") for a in collected}

    for section in _ALL_ITEM_SECTIONS:
        for item in (digest.get(section) or []):
            if not isinstance(item, dict) or _is_stand_in(item):
                continue
            url = (item.get("url") or "").strip()
            if url and url in valid_urls:
                continue
            tokens = _headline_tokens(item.get("headline", ""))
            if len(tokens) < 3:
                continue
            best_url, best_score = None, 0.0
            for cand_url, cand_tokens in index:
                if not cand_tokens or not cand_url:
                    continue
                score = len(tokens & cand_tokens) / min(len(tokens), len(cand_tokens))
                if score > best_score:
                    best_url, best_score = cand_url, score
            if best_url and best_score >= 0.6:
                log.append(f"    - {section}: '{(item.get('headline') or '')[:50]}' "
                           f"-> matched source URL ({best_score:.0%})")
                item["url"] = best_url
            elif url and not url.startswith("http"):
                log.append(f"    - {section}: dropped placeholder URL "
                           f"'{(item.get('headline') or '')[:50]}'")
                item.pop("url", None)
    return log


# ─────────────────────────────────────────────────────────────────────────────
# VALIDATION
# ─────────────────────────────────────────────────────────────────────────────

_EM_DASH = re.compile(r"[—―]")
_EMOJI = re.compile(
    "["
    "🀀-🫿"   # emoji blocks
    "☀-➿"           # misc symbols and dingbats
    "⬀-⯿"           # arrows and geometric shapes
    "←-⇿"           # arrows
    "️"                   # variation selector, the emoji presentation form
    "]"
)


def _all_text(digest: dict) -> list[tuple[str, str]]:
    """Every rendered string in the digest, as (location, text)."""
    out = [("re_line", str(digest.get("re_line", "")))]
    for i, m in enumerate(digest.get("morning_memo") or []):
        out.append((f"morning_memo[{i}]", str(m)))
    for section in _ALL_ITEM_SECTIONS + ("calendar_watch", "on_this_day"):
        for i, item in enumerate(digest.get(section) or []):
            if not isinstance(item, dict):
                continue
            for field, value in item.items():
                if isinstance(value, str):
                    out.append((f"{section}[{i}].{field}", value))
    return out


def validate_digest(digest: dict, payload: dict | None = None) -> list[str]:
    """Pre-send quality gate. Returns warnings; anything CRITICAL blocks the send."""
    warnings = []

    # ── Section caps ─────────────────────────────────────────────────────
    for section, (min_ct, max_ct) in SECTION_CAPS.items():
        items = digest.get(section) or []
        label = section.upper().replace("_", " ")

        if section in _FLOOR_SECTIONS and _has_stand_in(digest, section):
            # An honest empty section. Allowed, and worth surfacing in the log so
            # a run of them shows up as a feed problem rather than a quiet gap.
            warnings.append(f"{label}: empty, stand-in line used (non-blocking)")
            continue

        count = len([i for i in items if not _is_stand_in(i)])
        if min_ct and count < min_ct:
            warnings.append(f"{label} CRITICAL: only {count} (min {min_ct})")
        elif count > max_ct:
            warnings.append(f"{label} CRITICAL: {count} items (max {max_ct})")

    # ── Padding check on the floored sections ────────────────────────────
    # A floor met by items that do not actually belong to the region is worse
    # than a floor met by a stand-in.
    for section, pattern in (("pacific_wire", "PACIFIC_KEYWORDS"),
                             ("new_zealand", "NZ_KEYWORDS")):
        try:
            from collect import PACIFIC_KEYWORDS, NZ_KEYWORDS
            matcher = PACIFIC_KEYWORDS if section == "pacific_wire" else NZ_KEYWORDS
        except Exception:
            break
        for item in _real_items(digest, section):
            text = " ".join(str(item.get(f, "")) for f in
                            ("headline", "body_text", "country"))
            if not matcher.search(text):
                warnings.append(
                    f"{section.upper()} CRITICAL: item does not mention the region "
                    f"('{(item.get('headline') or '')[:50]}'), this looks like padding")

    # ── Morning memo ─────────────────────────────────────────────────────
    memo = digest.get("morning_memo") or []
    if len(memo) >= 2:
        texts = [str(m).strip() for m in memo]
        if len(set(texts)) < len(texts):
            warnings.append("MORNING MEMO CRITICAL: duplicate items, all 3 must be distinct")

    # ── RE: line ─────────────────────────────────────────────────────────
    re_line = digest.get("re_line")
    if not re_line or len(str(re_line).strip()) < 10:
        warnings.append("RE: LINE CRITICAL: missing or too short")

    # ── Word count ───────────────────────────────────────────────────────
    word_count = _count_digest_words(digest)
    # 1,400 hard, 2,000 target. The inherited Korea numbers were 850 and
    # 1,200, which this beat cannot live inside: twelve topics across three
    # geographies and seventeen Pacific states need the room. The second live
    # issue passed every gate at 1,516 words while running a supermarket
    # promotion and dropping six wire services, which is what a floor set too
    # low buys you.
    if word_count < 1400:
        warnings.append(f"WORD COUNT CRITICAL: ~{word_count} words (hard minimum 1400)")
    elif word_count < 2000:
        warnings.append(f"WORD COUNT: ~{word_count} words (target 2000-2400)")

    # ── House style: em-dashes and emojis ────────────────────────────────
    em_hits = [loc for loc, text in _all_text(digest) if _EM_DASH.search(text)]
    if em_hits:
        warnings.append(f"HOUSE STYLE CRITICAL: em-dash in {len(em_hits)} field(s): "
                        f"{', '.join(em_hits[:4])}")
    emoji_hits = [loc for loc, text in _all_text(digest) if _EMOJI.search(text)]
    if emoji_hits:
        warnings.append(f"HOUSE STYLE CRITICAL: emoji in {len(emoji_hits)} field(s): "
                        f"{', '.join(emoji_hits[:4])}")

    # ── Per-item checks ──────────────────────────────────────────────────
    seen_urls: dict[str, str] = {}
    bad_urls = 0
    dup_urls = 0
    thin_bodies = 0

    for section in _ALL_ITEM_SECTIONS:
        for item in (digest.get(section) or []):
            if not isinstance(item, dict) or _is_stand_in(item):
                continue
            url = item.get("url", "")
            if url and (url == "#" or not str(url).startswith("http")):
                bad_urls += 1
            if url and str(url).startswith("http"):
                if url in seen_urls:
                    dup_urls += 1
                    if dup_urls <= 3:
                        warnings.append(
                            f"DUPLICATE: URL in both {seen_urls[url]} and {section}")
                else:
                    seen_urls[url] = section
            body = (item.get("body") or item.get("body_text") or
                    item.get("summary") or item.get("detail") or "").strip()
            if not body or len(body) < 20:
                thin_bodies += 1

    if bad_urls:
        warnings.append(f"BAD URLS: {bad_urls} placeholder or invalid URL(s)")
    if thin_bodies:
        warnings.append(f"THIN BODIES: {thin_bodies} item(s) with under 20 characters")

    # ── Live URL check ───────────────────────────────────────────────────
    all_urls = [u for u in seen_urls if u.startswith("http")]
    if all_urls:
        broken = _validate_urls(all_urls)
        for url, reason in broken[:5]:
            warnings.append(f"BROKEN URL ({reason}): {url[:80]}")

    # ── Prestige coverage, non-blocking ──────────────────────────────────
    # Names the stories, not just the outlets. "collected but unused: AFP, AP,
    # Bloomberg, Financial Times, Reuters, WSJ" told the operator six wires had
    # been dropped and nothing whatever about whether that was a real miss or
    # six off-beat world stories, so it read as noise and got ignored. A
    # headline is diagnosable in one glance.
    if payload:
        used_urls = {i.get("url", "") for s in _ALL_ITEM_SECTIONS
                     for i in (digest.get(s) or []) if isinstance(i, dict)}
        dropped = [a for a in (payload.get("tier1") or [])
                   if a.get("source", "") in _PRESTIGE_OUTLETS
                   and a.get("url", "") not in used_urls]
        if dropped:
            shown = "; ".join(f"{a.get('source', '?')}: {(a.get('title') or '')[:60]}"
                              for a in dropped[:5])
            more = f" (+{len(dropped) - 5} more)" if len(dropped) > 5 else ""
            warnings.append(f"PRESTIGE: {len(dropped)} collected but unused: {shown}{more}")

    return warnings


def _postprocess_digest(digest_data: dict, payload: dict | None = None) -> tuple[dict, list[str]]:
    """URL repair, cross-day filter, dedup, source diversity."""
    log = []

    if payload:
        repair_log = _repair_digest_urls(digest_data, payload)
        if repair_log:
            log.append(f"\n  URL repair: fixed {len(repair_log)} URL(s) by headline match:")
            log.extend(repair_log)

    stale_log = _filter_stale_repeats(digest_data)
    if stale_log:
        log.append(f"\n  Cross-day: removed {len(stale_log)} stale repeat(s):")
        log.extend(stale_log)

    digest_data, dedup_log = _dedup_digest(digest_data)
    if dedup_log:
        log.append(f"\n  Dedup: removed {len(dedup_log)} duplicate(s):")
        log.extend(dedup_log)

    diversity_log = _enforce_source_diversity(digest_data)
    if diversity_log:
        log.append(f"\n  Source diversity: removed {len(diversity_log)} over-represented item(s):")
        log.extend(diversity_log)

    # After source diversity, so an item dropped for outlet concentration is not
    # also counted against its country's quota.
    country_log = _enforce_country_diversity(digest_data)
    if country_log:
        log.append(f"\n  Country diversity: removed {len(country_log)} item(s) "
                   f"from over-represented states:")
        log.extend(country_log)

    return digest_data, log


# ─────────────────────────────────────────────────────────────────────────────
# ARCHIVE LANDING PAGE
# ─────────────────────────────────────────────────────────────────────────────

def _build_index_html() -> str:
    return """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Australia Chair Daily Brief - CSIS</title>
<style>
  * { margin:0; padding:0; box-sizing:border-box; }
  body { font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Helvetica,Arial,sans-serif;
         background:#F2F3F5; color:#1B2A4A; line-height:1.6; }
  .wrap { max-width:680px; margin:0 auto; padding:48px 24px; }
  .eyebrow { font-size:11px; letter-spacing:3px; text-transform:uppercase; color:#17798C; }
  h1 { font-family:Georgia,serif; font-size:32px; margin:8px 0 4px; }
  .sub { font-size:12px; letter-spacing:1.5px; text-transform:uppercase; color:#7F8C8D; }
  .rule { height:3px; background:linear-gradient(90deg,#17798C 0%,#1B2A4A 100%); margin:24px 0; }
  a.cta { display:inline-block; background:#0D1B2A; color:#fff; padding:12px 22px;
          border-radius:3px; text-decoration:none; font-size:14px; }
  ul { list-style:none; margin-top:24px; }
  li { padding:10px 0; border-bottom:1px solid #E4E6E8; font-size:14px; }
  li a { color:#1B2A4A; text-decoration:none; border-bottom:1px solid #17798C; }
  .date { color:#7F8C8D; font-size:12px; }
</style>
</head>
<body>
<div class="wrap">
  <div class="eyebrow">CSIS Australia Chair</div>
  <h1>Australia Chair Daily Brief</h1>
  <div class="sub">Australia &middot; New Zealand &middot; the Pacific Islands</div>
  <div class="rule"></div>
  <p><a class="cta" href="latest.html">Read the latest issue</a></p>
  <ul id="archive"></ul>
</div>
<script>
fetch('archive.json').then(r => r.json()).then(entries => {
  const list = document.getElementById('archive');
  entries.sort((a, b) => b.date.localeCompare(a.date)).forEach(e => {
    const li = document.createElement('li');
    li.innerHTML = '<span class="date">' + e.date + '</span> &nbsp; ' +
                   '<a href="' + e.url + '">' + (e.headline_re || 'Issue') + '</a>';
    list.appendChild(li);
  });
}).catch(() => {});
</script>
</body>
</html>"""


# ─────────────────────────────────────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Australia Chair Daily Brief pipeline")
    parser.add_argument("--no-send", action="store_true",
                        help="Render to file only, do not send email")
    parser.add_argument("--from-cache", action="store_true",
                        help="Skip collection, reuse collected.json")
    parser.add_argument("--dry-run", action="store_true",
                        help="Collect only, do not call Claude")
    parser.add_argument("--no-track", action="store_true",
                        help="Skip tracker and archive write-back")
    args = parser.parse_args()

    print("=" * 60)
    print("  Australia Chair Daily Brief")
    print("  CSIS Australia Chair")
    print(f"  {datetime.now(ZoneInfo('America/New_York')).strftime('%Y-%m-%d %I:%M %p ET')}")
    print("=" * 60)

    # ── Step 1: Collect ──────────────────────────────────────────────────
    if args.from_cache and Path("collected.json").exists():
        print("\n  Using cached collection (collected.json)")
        payload = json.loads(Path("collected.json").read_text(encoding="utf-8"))
        total = sum(len(v) for v in payload.values() if isinstance(v, list))
        print(f"  {total} articles loaded from cache")
    else:
        from collect import collect
        payload = collect()

        # Canonicalize Google News redirects FIRST. Order matters twice over:
        # the model can only copy a short URL accurately, and fulltext.py skips
        # Google News links because fetching one returns an interstitial. Doing
        # this before enrichment turns four fifths of the corpus from
        # unquotable-and-unfetchable into both.
        try:
            import resolve
            pool = [a for t in ("tier1", "tier2", "tier3", "tier4")
                    for a in (payload.get(t) or [])]
            resolve.resolve_items(pool)
        except Exception as e:                              # noqa: BLE001
            print(f"  !  URL resolution failed, keeping redirects: {e}")

        # Fetch real article bodies before anything is cached or sent to the
        # model. Runs here rather than inside collect() so --from-cache reuses
        # enriched summaries and --dry-run shows what the model will actually
        # see. Best-effort: a total failure leaves the RSS summaries intact.
        try:
            import fulltext
            payload = fulltext.enrich_payload(payload)
        except Exception as e:                              # noqa: BLE001
            print(f"  !  Full-text enrichment failed, using RSS summaries: {e}")

        Path("collected.json").write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    if args.dry_run:
        print("\n  --dry-run: stopping after collection. See collected.json")
        return

    # ── Step 2: Generate ─────────────────────────────────────────────────
    from digest import generate_digest, regenerate_digest
    MAX_VALIDATION_RETRIES = 2

    digest_data = generate_digest(payload)
    digest_data, pp_log = _postprocess_digest(digest_data, payload=payload)
    for msg in pp_log:
        print(msg)
    Path("digest.json").write_text(
        json.dumps(digest_data, ensure_ascii=False, indent=2), encoding="utf-8")

    # ── Step 3: Validate, with retry ─────────────────────────────────────
    validation_passed = False
    validation_warnings: list[str] = []
    validation_attempt = 0

    for validation_attempt in range(1 + MAX_VALIDATION_RETRIES):
        validation_warnings = validate_digest(digest_data, payload=payload)
        critical = [w for w in validation_warnings if "CRITICAL" in w]

        # Duplicate warnings are not retryable: dedup already did what it could,
        # and re-prompting just makes the two fight.
        retryable = [w for w in critical if "DUPLICATE" not in w]

        if not critical:
            if validation_warnings:
                print("\n  Pre-send warnings (non-critical):")
                for w in validation_warnings:
                    print(f"    - {w}")
            else:
                print("\n  Validation passed, all checks OK")
            validation_passed = True
            break

        if not retryable:
            print("\n  Remaining warnings are duplicates only (auto-dedup applied):")
            for w in critical:
                print(f"    - {w}")
            validation_passed = True
            break

        # Blocking and advisory warnings printed apart. They used to be one
        # undifferentiated list under a "critical warnings:" header, so a soft
        # word-count note and a prestige miss read as reasons the brief was
        # held. On the third run of 25 August that cost real time working out
        # which of three lines had actually blocked the send.
        print(f"\n  VALIDATION ATTEMPT {validation_attempt + 1}/"
              f"{1 + MAX_VALIDATION_RETRIES}, blocking:")
        for w in critical:
            print(f"    - {w}")
        advisory = [w for w in validation_warnings if w not in critical]
        if advisory:
            print("  also, not blocking:")
            for w in advisory:
                print(f"    - {w}")

        if validation_attempt < MAX_VALIDATION_RETRIES:
            print("\n  Re-generating with validation feedback (reusing collected articles)...")
            digest_data = regenerate_digest(payload, digest_data, retryable,
                                            attempt=validation_attempt)
            digest_data, pp_log = _postprocess_digest(digest_data, payload=payload)
            for msg in pp_log:
                print(f"  {msg}")
            Path("digest.json").write_text(
                json.dumps(digest_data, ensure_ascii=False, indent=2), encoding="utf-8")
        else:
            print("\n  CRITICAL failures after all retries, the brief will NOT be sent.")
            print("  HTML is still rendered for review.")

    # ── Step 4: Write back trackers and the published ledger ─────────────
    # Only after validation passes: a failed run must not poison state.
    if validation_passed and not args.no_track:
        try:
            from aukus_tracker import update_from_digest as aukus_update
            from pacific_tracker import update_from_digest as pacific_update
            from archive import record_published
            n_aukus = aukus_update(digest_data)
            n_pacific = pacific_update(digest_data)
            n_pub = record_published(digest_data)
            print(f"\n  Trackers: {n_aukus} AUKUS item(s), {n_pacific} Pacific item(s), "
                  f"{n_pub} headline(s) recorded for cross-day dedup")
        except Exception as e:
            print(f"  !  Tracker write-back failed: {e}")
    elif args.no_track:
        print("\n  --no-track: skipping tracker and archive write-back")
    else:
        print("\n  Skipping tracker write-back due to critical validation failures")

    # ── Step 5: Render ───────────────────────────────────────────────────
    from render import render

    web_base = os.environ.get("WEB_URL", "")
    if web_base:
        digest_data["web_url"] = web_base.rstrip("/") + "/latest.html"

    # Market figures come from the collector, never from the model, so they are
    # carried across here rather than trusted from the generated JSON. If the
    # model invents a market_indicators key, this overwrites it.
    digest_data["market_indicators"] = payload.get("market_indicators") or {}

    html = render(digest_data)
    date_slug = datetime.now(ZoneInfo("America/New_York")).strftime("%Y-%m-%d")

    Path(f"digest_{date_slug}.html").write_text(html, encoding="utf-8")
    Path("latest.html").write_text(html, encoding="utf-8")
    print(f"\n  HTML rendered: digest_{date_slug}.html ({len(html):,} bytes)")

    archive_dir = Path("public")
    archive_dir.mkdir(exist_ok=True)
    (archive_dir / "latest.html").write_text(html, encoding="utf-8")
    (archive_dir / f"digest_{date_slug}.html").write_text(html, encoding="utf-8")
    (archive_dir / f"digest_{date_slug}.json").write_text(
        json.dumps(digest_data, ensure_ascii=False), encoding="utf-8")

    # Archive manifest
    manifest_path = archive_dir / "archive.json"
    try:
        entries = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        entries = []
    entries = [e for e in entries if e.get("date") != date_slug]
    entries.append({
        "date": date_slug,
        "headline_re": digest_data.get("re_line", ""),
        "top_stories_count": len(_real_items(digest_data, "top_stories")),
        "pacific_count": len(_real_items(digest_data, "pacific_wire")),
        "nz_count": len(_real_items(digest_data, "new_zealand")),
        "word_count": _count_digest_words(digest_data),
        "url": f"digest_{date_slug}.html",
    })
    manifest_path.write_text(json.dumps(entries, ensure_ascii=False, indent=2),
                             encoding="utf-8")
    (archive_dir / "index.html").write_text(_build_index_html(), encoding="utf-8")

    # ── Step 6: Send ─────────────────────────────────────────────────────
    if not validation_passed:
        print("\n  Skipping email due to critical validation failures. Review latest.html.")
        import sys
        sys.exit(1)
    elif args.no_send:
        print("\n  --no-send: skipping email. Open latest.html to review.")
    else:
        if not os.environ.get("DIGEST_TO"):
            print("\n  !  DIGEST_TO not set, the brief will only go to the sender")
        from send_email import send
        send(html, re_line=digest_data.get("re_line"))

    # ── Step 7: Metrics ──────────────────────────────────────────────────
    try:
        metrics = {
            "date": date_slug,
            "word_count": _count_digest_words(digest_data),
            "top_stories": len(_real_items(digest_data, "top_stories")),
            "overnight_items": len(_real_items(digest_data, "overnight_items")),
            "aukus_watch": len(_real_items(digest_data, "aukus_watch")),
            "pacific_wire": len(_real_items(digest_data, "pacific_wire")),
            "pacific_stand_in": _has_stand_in(digest_data, "pacific_wire"),
            "new_zealand": len(_real_items(digest_data, "new_zealand")),
            "nz_stand_in": _has_stand_in(digest_data, "new_zealand"),
            "china_in_the_pacific": len(_real_items(digest_data, "china_in_the_pacific")),
            "tier1_input": len(payload.get("tier1", [])),
            "tier_counts": {t: len(payload.get(t) or [])
                            for t in ("tier1", "tier2", "tier3", "tier4")},
            "region_counts": payload.get("region_counts", {}),
            "validation_warnings": len(validation_warnings),
            "validation_retries": validation_attempt,
            "html_bytes": len(html),
            "tokens": list(getattr(__import__("digest"), "TOKEN_LEDGER", [])),
            "sent": not args.no_send and validation_passed,
        }

        # Health check last, so its findings ride along in the same metrics
        # row and a drift shows up as a trend rather than as one bad morning.
        # Runs after the send on purpose: nothing it reports should be able to
        # stop an otherwise good brief going out.
        try:
            import pipeline_health
            health = pipeline_health.check(payload=payload, digest=digest_data)
            metrics["health_warnings"] = len(health["warnings"])
            metrics["health_alerts"] = len(health["alerts"])
            metrics["baseline_age_days"] = health["baseline_age_days"]
        except Exception as e:                              # noqa: BLE001
            print(f"\n  !  Health check failed to run: {e}")

        with open("metrics.jsonl", "a", encoding="utf-8") as f:
            f.write(json.dumps(metrics) + "\n")
    except Exception:
        pass

    print("\n  Done.\n")


if __name__ == "__main__":
    main()
