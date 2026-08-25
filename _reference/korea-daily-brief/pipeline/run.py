"""
Korea Daily Brief — Main Runner
CSIS Korea Chair
Orchestrates: collect → databases → digest → validate → render → push → send
Usage:
  python run.py              # Full pipeline (collect + digest + render + send)
  python run.py --no-send    # Render to file only, no email
  python run.py --from-cache # Skip collection, use existing collected.json
  python run.py --no-push    # Skip pushing new entries to databases
"""
import json
import argparse
import os
import re
from pathlib import Path
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
from concurrent.futures import ThreadPoolExecutor, as_completed
from digest import _count_digest_words


_PRESTIGE_OUTLETS = {"WSJ", "Wall Street Journal", "Washington Post", "WaPo", "NYT",
                      "New York Times", "Bloomberg", "Financial Times", "FT", "Economist", "The Economist"}


def _check_url(url: str, timeout: float = 5.0) -> tuple[str, bool, str]:
    """HEAD-check a URL; returns (url, ok, reason).
    Only flags 404/410 (definitively dead). Treats 403/405/429 as OK
    since paywalled sites and bot-protected servers commonly return these."""
    import requests
    try:
        resp = requests.head(url, timeout=timeout, allow_redirects=True,
                             headers={"User-Agent": "Mozilla/5.0 Korea-Digest-Validator/1.0"})
        # Only flag definitively dead URLs — 403/405/429/451 are normal for
        # paywalled sites (WSJ, FT, Bloomberg) and bot-protected servers
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
    """Check URLs in parallel; returns list of (url, reason) for broken ones."""
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
        pass  # some URLs still pending — report what we have so far
    return broken

_STOP_WORDS = frozenset({"the", "a", "an", "in", "on", "of", "to", "for", "and", "is", "at", "by", "as", "with", "from",
                         "its", "new", "over", "after", "says", "said", "amid", "that", "has", "will", "may", "could",
                         "been", "are", "was", "were", "this", "but", "not", "all", "more", "than", "also"})

# Topic entities — entity match alone triggers dedup (institution/person/event)
_TOPIC_ENTITIES = {
    "bok": {"bok", "bank of korea", "central bank", "monetary policy", "rate decision", "interest rate", "base rate", "benchmark rate"},
    "kim jong un": {"kim jong un", "kim jongun", "kju", "north korean leader", "dprk leader", "supreme leader"},
    "yoon": {"yoon", "yoon suk yeol", "yoon suk-yeol", "president yoon"},
    "lee jae myung": {"lee jae myung", "lee jae-myung", "lee jaemyung"},
    "usfk": {"usfk", "us forces korea", "united states forces korea"},
    "freedom shield": {"freedom shield", "joint military exercise", "joint drill", "combined exercise"},
}

# Company entities — need keyword overlap too (big conglomerates have many unrelated stories)
_COMPANY_ENTITIES = {
    "samsung": {"samsung", "samsung electronics"},
    "hyundai": {"hyundai", "hyundai motor", "hyundai motors"},
    "sk": {"sk hynix", "sk group", "sk innovation", "sk telecom"},
    "posco": {"posco"},
    "hanwha": {"hanwha"},
    "lg": {"lg energy", "lg electronics", "lg chem"},
}

# All sections that contain items with URLs/headlines
_ALL_ITEM_SECTIONS = ("top_stories", "overnight_items", "also_today",
                       "business_economy", "opeds_today", "academic_today",
                       "social_statements", "northeast_asia")

# Sections where duplicates get auto-stripped (priority order — first wins)
_DEDUP_SECTIONS = ("top_stories", "overnight_items", "business_economy",
                    "northeast_asia", "also_today", "opeds_today",
                    "academic_today", "social_statements")


def _extract_entities(text: str) -> tuple[set[str], set[str]]:
    """Extract topic and company entity tags from a headline.
    Returns (topic_entities, company_entities)."""
    text_lower = text.lower()
    topics = set()
    companies = set()
    for tag, aliases in _TOPIC_ENTITIES.items():
        if any(alias in text_lower for alias in aliases):
            topics.add(tag)
    for tag, aliases in _COMPANY_ENTITIES.items():
        if any(alias in text_lower for alias in aliases):
            companies.add(tag)
    return topics, companies


def _headline_key(item: dict) -> tuple[str, set, set, set]:
    """Return (headline, keywords, topic_entities, company_entities) for an item."""
    headline = (item.get("headline", "") or "").lower().strip()
    words = {w for w in re.split(r'\W+', headline) if len(w) > 2 and w not in _STOP_WORDS}
    topics, companies = _extract_entities(headline)
    return headline, words, topics, companies


def _is_dup(words_a, topics_a, companies_a, words_b, topics_b, companies_b) -> str | None:
    """Check if two items are duplicates. Returns reason string or None."""
    if not words_a or not words_b:
        return None
    # Keyword overlap
    overlap = words_a & words_b
    min_len = min(len(words_a), len(words_b))
    keyword_dup = min_len > 1 and len(overlap) >= 2 and len(overlap) / min_len >= 0.5
    if keyword_dup:
        return "keyword overlap"
    # Topic entity match — require keyword overlap too (just like companies).
    # On heavy news days a single figure (e.g. KJU) dominates many *distinct*
    # stories; entity-only matching was wiping them all out.
    shared_topics = topics_a & topics_b
    if shared_topics:
        # Strip entity alias words from overlap to avoid self-matching
        entity_words = set()
        for tag in shared_topics:
            for alias in _TOPIC_ENTITIES.get(tag, set()):
                entity_words.update(w for w in alias.split() if len(w) > 2)
        non_entity_overlap = overlap - entity_words
        if len(non_entity_overlap) >= 2:
            return f"shared topic + keywords: {shared_topics}, {non_entity_overlap}"
    # Company entity match — need 2+ non-entity keyword overlaps
    # (company names alone aren't enough — Samsung has many unrelated stories)
    shared_companies = companies_a & companies_b
    if shared_companies:
        # Strip out company name words from overlap to avoid self-matching
        company_words = set()
        for tag in shared_companies:
            for alias in _COMPANY_ENTITIES.get(tag, set()):
                company_words.update(w for w in alias.split() if len(w) > 2)
        non_entity_overlap = overlap - company_words
        if len(non_entity_overlap) >= 2:
            return f"shared company + keywords: {shared_companies}, {non_entity_overlap}"
    return None


def _dedup_digest(digest: dict) -> tuple[dict, list[str]]:
    """Auto-strip duplicate topics from digest. Returns (cleaned_digest, log_messages).

    Walks sections in priority order. For each item, checks against all items
    already seen. If duplicate, removes it from the lower-priority section.
    Respects section minimums — won't strip a section below its floor.
    """
    _SECTION_FLOORS = {"top_stories": 3, "overnight_items": 3}
    log = []
    seen = []  # (section, headline, words, topics, companies)
    seen_urls_global = {}  # url -> section_key (track across ALL sections)

    for section_key in _DEDUP_SECTIONS:
        items = digest.get(section_key)
        if not items or not isinstance(items, list):
            continue

        floor = _SECTION_FLOORS.get(section_key, 0)
        kept = []
        deferred_removals = []  # (index, log_msg) — removals that may be restored

        for item in items:
            url = (item.get("url") or "").strip()

            # URL dedup — across all sections
            if url and url.startswith("http"):
                if url in seen_urls_global:
                    msg = f"  Removed duplicate URL in {section_key} (already in {seen_urls_global[url]}): {url[:80]}"
                    deferred_removals.append((item, msg))
                    continue
                seen_urls_global[url] = section_key

            headline, words, topics, companies = _headline_key(item)
            if len(headline) < 15:
                kept.append(item)
                continue

            is_duplicate = False
            for prev_section, prev_headline, prev_words, prev_topics, prev_companies in seen:
                reason = _is_dup(words, topics, companies, prev_words, prev_topics, prev_companies)
                if reason:
                    msg = (
                        f"  Removed from {section_key} ({reason}): "
                        f"'{headline[:60]}' — kept in {prev_section}: '{prev_headline[:60]}'")
                    deferred_removals.append((item, msg))
                    is_duplicate = True
                    break

            if not is_duplicate:
                kept.append(item)
                seen.append((section_key, headline, words, topics, companies))

        # Restore items if removals would breach section floor
        if len(kept) < floor and deferred_removals:
            need = floor - len(kept)
            restored = deferred_removals[:need]
            deferred_removals = deferred_removals[need:]
            for item, _msg in restored:
                kept.append(item)
                headline, words, topics, companies = _headline_key(item)
                seen.append((section_key, headline, words, topics, companies))
                url = (item.get("url") or "").strip()
                if url and url.startswith("http"):
                    seen_urls_global[url] = section_key

        for _item, msg in deferred_removals:
            log.append(msg)

        digest[section_key] = kept

    return digest, log


_US_KEYWORDS = frozenset({
    "us", "u.s.", "united states", "america", "american", "washington",
    "ustr", "commerce department", "white house", "pentagon", "congress",
    "treasury", "biden", "trump", "section 232", "section 301", "section 122",
    "ieepa", "cfius", "ita", "itc",
})


def _filter_non_us_deals(digest: dict) -> list[str]:
    """Remove deals from us_korea_deals.deals that don't involve the US.

    Returns log messages for any removed items.
    """
    log = []
    us_korea = digest.get("us_korea_deals")
    if not us_korea or not isinstance(us_korea, dict):
        return log

    deals = us_korea.get("deals")
    if not deals or not isinstance(deals, list):
        return log

    kept = []
    for deal in deals:
        # Check headline, detail, and parties for US connection
        text = " ".join(str(deal.get(f, "")) for f in
                        ("headline", "detail", "parties", "sector")).lower()
        has_us = any(kw in text for kw in _US_KEYWORDS)
        if has_us:
            kept.append(deal)
        else:
            headline = deal.get("headline", "")[:80]
            log.append(f"  Removed non-US deal from us_korea_deals: '{headline}'")

    us_korea["deals"] = kept
    return log


# Max times a single source can appear in overnight_items / also_today.
# Yonhap (연합뉴스) is Korea's dominant wire service — like AP for the US.
# Cap of 2 was too aggressive, removing 5-7 items per run and crashing word count.
_SOURCE_CAP = 3

_SOURCE_PREFIX_MAP = {
    "yonhap": "yonhap", "연합뉴스": "yonhap",
    "reuters": "reuters",
    "nikkei": "nikkei",
    "kyodo": "kyodo",
    "associated press": "ap", "ap news": "ap",
    "agence france": "afp", "afp": "afp",
    "korea herald": "korea herald",
    "korea times": "korea times",
    "joongang": "joongang", "중앙일보": "joongang",
    "chosun": "chosun", "조선일보": "chosun",
    "hankyoreh": "hankyoreh", "한겨레": "hankyoreh",
    "dong-a": "dong-a", "동아일보": "dong-a",
    "maeil": "maeil", "매일경제": "maeil",
    "hankook": "hankook", "한국경제": "hankook",
    "jtbc": "jtbc", "kbs": "kbs", "mbc": "mbc", "sbs": "sbs", "ytn": "ytn",
    "nk news": "nk news", "nk pro": "nk news",
    "daily nk": "daily nk",
    "scmp": "scmp", "south china morning": "scmp",
    "global times": "global times",
    "tass": "tass", "xinhua": "xinhua",
    "japan times": "japan times",
}

def _normalize_source(src: str) -> str:
    """Collapse source name variants to a canonical key."""
    s = src.lower().strip()
    for prefix, canonical in _SOURCE_PREFIX_MAP.items():
        if s.startswith(prefix):
            return canonical
    return s

def _enforce_source_diversity(digest: dict) -> list[str]:
    """Cap any single source to _SOURCE_CAP appearances per section.

    Skips top_stories (curated 3-4 item section where story importance
    outweighs source diversity). Excess items from over-represented sources
    are dropped, but the section is never reduced below its floor.
    Returns log messages for removed items.
    """
    _SECTION_MINIMUMS = {"overnight_items": 3}
    log = []
    for section_key in ("overnight_items", "also_today"):
        items = digest.get(section_key)
        if not items or not isinstance(items, list):
            continue

        floor = _SECTION_MINIMUMS.get(section_key, 0)

        source_counts: dict[str, int] = {}
        kept = []
        dropped = []
        for item in items:
            src = _normalize_source(item.get("source", "Unknown"))
            source_counts[src] = source_counts.get(src, 0) + 1
            if source_counts[src] <= _SOURCE_CAP:
                kept.append(item)
            else:
                headline = item.get("headline", "")[:60]
                dropped.append((item, src, headline))

        # If removing all excess items would breach the section minimum,
        # keep enough excess items (from the end) to stay at the floor.
        if len(kept) < floor and dropped:
            need = floor - len(kept)
            # Re-add the last N dropped items (least egregious duplicates)
            restored = dropped[-need:]
            dropped = dropped[:-need]
            for item, src, headline in restored:
                kept.append(item)

        if dropped:
            digest[section_key] = kept
            for _item, src, headline in dropped:
                log.append(f"  Removed excess {src} from {section_key}: '{headline}'")

    return log


def validate_digest(digest: dict, payload: dict | None = None) -> list[str]:
    """Pre-send quality gate. Returns list of warnings (empty = all clear)."""
    warnings = []

    # ── Section count checks (hard caps) ─────────────────────────────────
    SECTION_CAPS = {
        "top_stories":       (2, 4),
        "overnight_items":   (3, 6),
        "business_economy":  (0, 6),
        "calendar_watch":    (4, 5),
        "also_today":        (0, 6),
        "northeast_asia":    (0, 6),
        "social_statements": (0, 6),
        "rok_government":    (0, 6),
        "rok_assembly":      (0, 6),
        "opeds_today":       (0, 6),
        "academic_today":    (0, 6),
        "rok_personnel":     (0, 6),
        "morning_memo":      (3, 3),
        "on_this_day":       (0, 1),
    }
    for section_key, (min_ct, max_ct) in SECTION_CAPS.items():
        items = digest.get(section_key) or []
        label = section_key.upper().replace("_", " ")
        if min_ct and len(items) < min_ct:
            warnings.append(f"{label} CRITICAL: only {len(items)} (min {min_ct})")
        elif len(items) > max_ct:
            warnings.append(f"{label} CRITICAL: {len(items)} items (max {max_ct})")

    # ── Morning memo uniqueness check ───────────────────────────────────
    memo_items = digest.get("morning_memo") or []
    if len(memo_items) >= 2:
        memo_texts = [str(m).strip() for m in memo_items]
        if len(set(memo_texts)) < len(memo_texts):
            warnings.append("MORNING MEMO: duplicate memo items detected — all 3 must be distinct")

    # ── RE: line must be present and substantive ─────────────────────────
    re_line = digest.get("re_line")
    if not re_line or len(str(re_line).strip()) < 10:
        warnings.append("RE: LINE CRITICAL: missing or too short")

    # ── Word count check (hard minimum 850, target 1200-1400) ──────────
    # Hard floor lowered from 1000: legitimate slow-news-day digests were
    # landing at ~900-980 words and blocking sending. 850 catches genuinely
    # truncated outputs without false-positive on slim days.
    word_count = _count_digest_words(digest)
    if word_count < 850:
        warnings.append(f"WORD COUNT CRITICAL: ~{word_count} words (HARD MINIMUM 850 — newsletter is too short)")
    elif word_count < 1200:
        warnings.append(f"WORD COUNT: ~{word_count} words (target 1200-1400 for 5-min read)")

    # ── KCNA delta should exist but is non-blocking ────────────────────────
    kcna = digest.get("kcna_delta")
    if not kcna or not isinstance(kcna, dict):
        warnings.append("KCNA DELTA: missing kcna_delta section (non-blocking)")
    elif kcna.get("silence_today") and "scraper" in str(kcna.get("bottom_line", "")).lower():
        pass  # no-data stub is valid — scrapers returned 0 articles

    # ── Single pass over all items: URLs, headlines, sources, body checks ─
    seen_urls = {}
    seen_headlines = []  # (section, headline, keywords, topic_entities, company_entities)
    source_counts = {}
    bad_urls = 0
    dup_url_count = 0
    empty_body_count = 0

    for section_key in _ALL_ITEM_SECTIONS:
        for item in (digest.get(section_key) or []):
            url = item.get("url", "")

            # Bad URL check
            if url and (url == "#" or not url.startswith("http")):
                bad_urls += 1

            # Duplicate URL check
            if url and url.startswith("http"):
                if url in seen_urls:
                    dup_url_count += 1
                    if dup_url_count <= 3:
                        warnings.append(
                            f"DUPLICATE: URL appears in both {seen_urls[url]} and {section_key}")
                else:
                    seen_urls[url] = section_key

            # Empty body check — items should have substantive content
            body = (item.get("body") or item.get("body_text") or
                    item.get("summary") or item.get("detail") or "").strip()
            if not body or len(body) < 20:
                empty_body_count += 1

            # Source diversity (top_stories + overnight + also_today)
            if section_key in ("top_stories", "overnight_items", "also_today"):
                src = (item.get("source", "") or "").strip()
                if src:
                    source_counts[src] = source_counts.get(src, 0) + 1

            # Duplicate topic check (keyword overlap + entity matching)
            headline, words, topics, companies = _headline_key(item)
            if len(headline) > 15:
                for prev_section, prev_headline, prev_words, prev_topics, prev_companies in seen_headlines:
                    reason = _is_dup(words, topics, companies, prev_words, prev_topics, prev_companies)
                    if reason:
                        warnings.append(
                            f"DUPLICATE TOPIC CRITICAL: same topic in {prev_section} and {section_key} ({reason}): "
                            f"'{headline[:60]}...' vs '{prev_headline[:60]}...'")
                        break
                seen_headlines.append((section_key, headline, words, topics, companies))

    if bad_urls:
        warnings.append(f"BAD URLS: {bad_urls} placeholder or invalid URLs found")

    # Live URL accessibility check (HEAD requests in parallel)
    all_urls = [u for u in seen_urls if u.startswith("http")]
    if all_urls:
        broken = _validate_urls(all_urls)
        for url, reason in broken:
            section = seen_urls.get(url, "unknown")
            warnings.append(f"BROKEN LINK ({reason}): {url} in {section}")

    if empty_body_count:
        warnings.append(f"EMPTY BODIES: {empty_body_count} items have no substantive body text")

    # Source diversity (normalize source names to catch variants like "Yonhap" / "Yonhap News")
    normalized_counts: dict[str, int] = {}
    for src, count in source_counts.items():
        key = _normalize_source(src)
        normalized_counts[key] = normalized_counts.get(key, 0) + count
    for src, count in normalized_counts.items():
        if count > 10:
            warnings.append(
                f"SOURCE DIVERSITY CRITICAL: '{src}' appears {count} times across top sections — diversify sources")
        elif count > 7:
            warnings.append(
                f"SOURCE DIVERSITY: '{src}' appears {count} times across top sections — consider diversifying")

    # ── Check for "None" strings in critical fields ──────────────────────
    for field in ("re_line", "digest_date"):
        val = digest.get(field)
        if str(val).strip() == "None":
            warnings.append(f'NONE STRING: "{field}" field contains literal "None"')

    # ── Digest date matches today ────────────────────────────────────────
    digest_date = digest.get("digest_date", "")
    today_str = datetime.now(ZoneInfo("America/New_York")).strftime("%A, %B %-d, %Y")
    if digest_date and digest_date != today_str:
        warnings.append(f"DATE MISMATCH: digest says '{digest_date}', today is '{today_str}'")

    # ── Stale calendar_watch dates ──────────────────────────────────────
    today_date = datetime.now(ZoneInfo("America/New_York")).date()
    _MONTH_MAP = {"jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
                  "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12}
    for cal_item in (digest.get("calendar_watch") or []):
        month_str = str(cal_item.get("month", "")).strip().lower()[:3]
        day_num = cal_item.get("day")
        if month_str in _MONTH_MAP and day_num:
            try:
                from datetime import date as _date
                cal_date = _date(today_date.year, _MONTH_MAP[month_str], int(day_num))
                if cal_date < today_date:
                    headline = (cal_item.get("headline") or "")[:80]
                    warnings.append(f"STALE CALENDAR: '{headline}' ({month_str.upper()} {day_num}) is in the past")
            except (ValueError, TypeError):
                pass

    # ── Public sentiment must have all 4 metrics ─────────────────────────
    sentiment = digest.get("public_sentiment") or {}
    missing_sentiment = [k for k in ("presidential_approval", "party_ruling",
                                      "party_opposition", "party_independent")
                         if not (sentiment.get(k) or {}).get("value")]
    if missing_sentiment:
        warnings.append(f"SENTIMENT: missing values for {', '.join(missing_sentiment)}")

    # ── Prestige outlet cross-reference ───────────────────────────────
    # Use canonical name matching so "WSJ" matches "Wall Street Journal" etc.
    _PRESTIGE_CANONICAL = {
        "wsj": "wsj", "wall street journal": "wsj",
        "washington post": "wapo", "wapo": "wapo",
        "nyt": "nyt", "new york times": "nyt",
        "bloomberg": "bloomberg",
        "financial times": "ft", "ft": "ft",
        "economist": "economist", "the economist": "economist",
    }
    def _prestige_key(source_name: str) -> str | None:
        s = source_name.lower().strip()
        for prefix, key in _PRESTIGE_CANONICAL.items():
            if prefix in s:
                return key
        return None

    if payload:
        prestige_in_input: dict[str, str] = {}  # canonical_key -> original source name
        for tier_key in ("tier1", "tier2", "tier3", "tier4"):
            for a in (payload.get(tier_key) or []):
                src = (a.get("source") or "").strip()
                pk = _prestige_key(src)
                if pk and pk not in prestige_in_input:
                    prestige_in_input[pk] = src
        digest_prestige_keys: set[str] = set()
        for section_key in _ALL_ITEM_SECTIONS:
            for item in (digest.get(section_key) or []):
                pk = _prestige_key(item.get("source", ""))
                if pk:
                    digest_prestige_keys.add(pk)
        for pk, src in prestige_in_input.items():
            if pk not in digest_prestige_keys:
                warnings.append(
                    f"PRESTIGE OUTLET DROPPED: '{src}' had Korea articles in input but none appeared in digest")

    # ── Satellite imagery dual-placement check ──────────────────────────
    imagery_report = digest.get("imagery_report")
    if imagery_report and isinstance(imagery_report, dict):
        source_links = imagery_report.get("source_links") or []
        if source_links:
            story_urls: set[str] = set()
            for sk in ("top_stories", "overnight_items"):
                for item in (digest.get(sk) or []):
                    u = (item.get("url") or "").strip()
                    if u:
                        story_urls.add(u)
            # source_links is an array of {label, url} dicts (per digest schema).
            # Be defensive: accept either dicts or bare URL strings.
            link_urls = []
            for link in source_links:
                if isinstance(link, dict):
                    u = (link.get("url") or "").strip()
                    if u:
                        link_urls.append(u)
                elif isinstance(link, str):
                    link_urls.append(link.strip())
            if link_urls and not any(u in story_urls for u in link_urls):
                warnings.append(
                    "IMAGERY DUAL PLACEMENT: imagery_report exists but no matching story in top_stories/overnight_items")

    # ── Cross-reference: every digest URL must exist in input feed ────────
    if payload:
        input_urls: set[str] = set()
        for tier_key in ("tier1", "tier2", "tier3", "tier4"):
            for a in (payload.get(tier_key) or []):
                u = (a.get("url") or "").strip()
                if u and u.startswith("http"):
                    input_urls.add(u)
        fabricated_count = 0
        for section_key in _ALL_ITEM_SECTIONS:
            for item in (digest.get(section_key) or []):
                url = (item.get("url") or "").strip()
                if url and url.startswith("http") and url not in input_urls:
                    headline = (item.get("headline") or item.get("translated_title")
                                or item.get("title") or "")[:80]
                    fabricated_count += 1
                    if fabricated_count <= 5:
                        warnings.append(
                            f"FABRICATED ARTICLE CRITICAL: URL not in input feed — {section_key}: "
                            f"'{headline}' ({url})")
        if fabricated_count:
            warnings.append(
                f"FABRICATED ARTICLES CRITICAL: {fabricated_count} article(s) have URLs not found "
                f"in today's input feed — likely hallucinated")

    return warnings


def _headline_tokens(text: str) -> set[str]:
    """Tokenize a headline for fuzzy matching: drop stopwords and short words."""
    if not text:
        return set()
    return {w for w in re.split(r"\W+", text.lower())
            if len(w) > 2 and w not in _STOP_WORDS}


def _repair_digest_urls(digest: dict, payload: dict) -> list[str]:
    """Repair or drop digest items whose URLs don't match any input-feed URL.

    Claude sometimes outputs Google News RSS URLs with slightly altered query
    strings (repairable by headline match) or outright fabricates items that
    have no input backing (unrepairable — must be dropped so validation can
    pass).

    Two-pass behavior per digest item whose URL isn't in the input set:
      1. Fuzzy-match by headline token overlap (>=3 shared non-stopword tokens
         AND >=50% of the shorter headline). If found, rewrite URL to verbatim.
      2. Otherwise mark for drop. After processing a section, drop all marked
         items subject to section floors (top_stories >= 2, overnight_items >= 3).
         If a section would fall below its floor, restore the minimum number
         of items from the end of the drop list (least likely to be real news
         since they're last in priority order).

    Also drops items with empty headline AND empty body (garbage entries).

    Mutates digest in place. Returns a list of log messages.
    """
    log: list[str] = []
    if not payload:
        return log

    # Index every input article: (url, title, tokens)
    input_articles: list[tuple[str, str, set[str]]] = []
    input_urls: set[str] = set()
    for tier_key in ("tier1", "tier2", "tier3", "tier4"):
        for a in (payload.get(tier_key) or []):
            u = (a.get("url") or "").strip()
            if not u or not u.startswith("http"):
                continue
            input_urls.add(u)
            title = (a.get("title") or a.get("headline") or "").strip()
            input_articles.append((u, title, _headline_tokens(title)))

    if not input_urls:
        return log

    # Section floors — emergency minimums when dropping fabricated items.
    # These are deliberately 1 lower than the strict digest floors so we can
    # still drop obvious hallucinations without starving the section.
    _DROP_FLOORS = {"top_stories": 2, "overnight_items": 3}

    for section_key in _ALL_ITEM_SECTIONS:
        items = digest.get(section_key)
        if not items or not isinstance(items, list):
            continue

        kept: list = []
        # Split drop reasons so floor protection can restore fabricated items
        # (which have real content) but never restores empty garbage.
        always_drop: list[tuple[dict, str]] = []      # empty/broken — never restore
        restorable_drop: list[tuple[dict, str]] = []  # unmatched URL but has content

        for item in items:
            url = (item.get("url") or "").strip()
            headline = (item.get("headline") or item.get("translated_title")
                        or item.get("title") or "").strip()
            body = (item.get("body") or item.get("body_text")
                    or item.get("summary") or item.get("detail") or "").strip()

            # Drop garbage: no headline AND no body — never restore this
            if not headline and len(body) < 20:
                always_drop.append((item, "empty headline and body"))
                continue

            # URL missing/invalid or already matches input → keep as-is
            if not url or not url.startswith("http"):
                kept.append(item)
                continue
            if url in input_urls:
                kept.append(item)
                continue

            # Try to repair via headline token match
            tokens = _headline_tokens(headline)
            best_url = None
            best_score = 0
            if len(tokens) >= 3:
                for in_url, _in_title, in_tokens in input_articles:
                    if not in_tokens:
                        continue
                    overlap = tokens & in_tokens
                    min_len = min(len(tokens), len(in_tokens))
                    if (len(overlap) >= 3
                            and len(overlap) / min_len >= 0.5
                            and len(overlap) > best_score):
                        best_score = len(overlap)
                        best_url = in_url

            if best_url:
                log.append(
                    f"  Repaired URL in {section_key}: '{headline[:60]}' "
                    f"({best_score} token match)")
                item["url"] = best_url
                kept.append(item)
            else:
                reason = (f"headline too short ({len(tokens)} tokens)"
                          if len(tokens) < 3 else "no headline match in input")
                restorable_drop.append((item, reason))

        # Enforce floor: restore from restorable_drop only (never empty garbage)
        floor = _DROP_FLOORS.get(section_key, 0)
        if restorable_drop and len(kept) < floor:
            need = floor - len(kept)
            restored = restorable_drop[-need:]
            restorable_drop = restorable_drop[:-need]
            for item, _reason in restored:
                log.append(
                    f"  ⚠ Kept fabricated item in {section_key} to protect "
                    f"floor (min {floor}): '{(item.get('headline') or '')[:60]}'")
                kept.append(item)

        for item, reason in (always_drop + restorable_drop):
            hl = (item.get("headline") or item.get("translated_title")
                  or item.get("title") or "(no headline)")[:60]
            log.append(
                f"  Dropped unrepairable {section_key} item ({reason}): '{hl}'")

        digest[section_key] = kept

    return log


def _postprocess_digest(digest_data: dict, payload: dict | None = None) -> tuple[dict, list[str]]:
    """Run dedup, deal filter, source diversity, and URL repair. Returns (digest, all_log_messages)."""
    log = []

    # Repair first — so dedup sees the canonical URLs and can detect dupes
    # between sections that previously had mangled URLs
    if payload:
        repair_log = _repair_digest_urls(digest_data, payload)
        if repair_log:
            log.append(f"\n🔧  URL repair: fixed {len(repair_log)} mismatched URL(s) by headline match:")
            log.extend(repair_log)

    digest_data, dedup_log = _dedup_digest(digest_data)
    if dedup_log:
        log.append(f"\n🧹  Auto-dedup removed {len(dedup_log)} duplicate(s):")
        log.extend(dedup_log)

    deal_log = _filter_non_us_deals(digest_data)
    if deal_log:
        log.append(f"\n🧹  Filtered {len(deal_log)} non-US deal(s) from trade section:")
        log.extend(deal_log)

    diversity_log = _enforce_source_diversity(digest_data)
    if diversity_log:
        log.append(f"\n🧹  Source diversity: removed {len(diversity_log)} over-represented item(s):")
        log.extend(diversity_log)

    return digest_data, log


def _build_index_html() -> str:
    """Generate a landing page for GitHub Pages with links to latest digest and archive."""
    return """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Korea Daily Brief — CSIS Korea Chair</title>
<style>
  * { margin: 0; padding: 0; box-sizing: border-box; }
  body {
    font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif;
    background: #f5f6f8;
    color: #333;
    min-height: 100vh;
  }
  header {
    background: #1a1f36;
    color: #fff;
    padding: 40px 32px 36px;
    text-align: center;
  }
  header .brand {
    font-size: 11px;
    text-transform: uppercase;
    letter-spacing: 1.8px;
    color: #8a8fa8;
    margin-bottom: 8px;
  }
  header h1 {
    font-size: 30px;
    font-weight: 700;
    letter-spacing: -0.3px;
    margin-bottom: 8px;
  }
  header .subtitle {
    font-size: 15px;
    color: #9ca0b8;
    font-weight: 400;
    max-width: 520px;
    margin: 0 auto;
    line-height: 1.5;
  }
  .container {
    max-width: 640px;
    margin: 0 auto;
    padding: 40px 20px 60px;
  }
  .card-grid {
    display: grid;
    grid-template-columns: 1fr 1fr;
    gap: 16px;
  }
  .card {
    background: #fff;
    border: 1px solid #dce0e8;
    border-radius: 10px;
    padding: 28px 24px;
    text-decoration: none;
    color: inherit;
    box-shadow: 0 1px 3px rgba(0,0,0,0.04);
    transition: box-shadow 0.15s, border-color 0.15s, transform 0.15s;
    display: flex;
    flex-direction: column;
    align-items: center;
    text-align: center;
  }
  .card:hover {
    border-color: #4a7cf7;
    box-shadow: 0 4px 14px rgba(74,124,247,0.12);
    transform: translateY(-2px);
  }
  .card .icon {
    font-size: 32px;
    margin-bottom: 12px;
  }
  .card .card-title {
    font-size: 17px;
    font-weight: 600;
    color: #1a1f36;
    margin-bottom: 6px;
  }
  .card .card-desc {
    font-size: 13px;
    color: #777;
    line-height: 1.45;
  }
  @media (max-width: 500px) {
    .card-grid { grid-template-columns: 1fr; }
    header { padding: 28px 16px 24px; }
    header h1 { font-size: 24px; }
  }
</style>
</head>
<body>
<header>
  <div class="brand">CSIS Korea Chair</div>
  <h1>Korea Daily Brief</h1>
  <p class="subtitle">Daily intelligence digest covering security, diplomacy, trade, and technology on the Korean Peninsula.</p>
</header>
<div class="container">
  <div class="card-grid">
    <a class="card" href="latest.html">
      <div class="icon">&#128240;</div>
      <div class="card-title">Latest Digest</div>
      <div class="card-desc">Read today's Korea Daily Brief with the latest developments.</div>
    </a>
    <a class="card" href="archive.html">
      <div class="icon">&#128218;</div>
      <div class="card-title">Archive &amp; Search</div>
      <div class="card-desc">Browse and search all past digests by date or keyword.</div>
    </a>
  </div>
</div>
</body>
</html>"""


def main():
    parser = argparse.ArgumentParser(description="Korea Daily Brief pipeline")
    parser.add_argument("--no-send",    action="store_true", help="Render to file only, do not send email")
    parser.add_argument("--from-cache", action="store_true", help="Skip collection, use existing collected.json")
    parser.add_argument("--dry-run",    action="store_true", help="Collect only, don't call Claude")
    parser.add_argument("--no-push",    action="store_true", help="Skip pushing new entries to NK-Russia/provocations databases")
    args = parser.parse_args()

    print("=" * 60)
    print("  Korea Daily Brief")
    print("  CSIS Korea Chair")
    from zoneinfo import ZoneInfo
    print(f"  {datetime.now(ZoneInfo('America/New_York')).strftime('%Y-%m-%d %-I:%M %p ET')}")
    print("=" * 60)

    # ── Step 1: Collect ───────────────────────────────────────────────────────
    if args.from_cache and Path("collected.json").exists():
        print("\n📦  Using cached collection (collected.json)")
        payload = json.loads(Path("collected.json").read_text())
        total = sum(len(v) for v in payload.values() if isinstance(v, list))
        print(f"  {total} articles loaded from cache")
    else:
        from collect import collect
        payload = collect()
        Path("collected.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2))

    if args.dry_run:
        print("\n  --dry-run: stopping after collection. See collected.json")
        return

    # ── Step 1b: Fetch CSIS databases for context ─────────────────────────────
    from databases import fetch_all, build_context_block, process_digest_entries
    print("\n📚  Loading CSIS databases...")
    databases = fetch_all()
    db_context = build_context_block(databases)

    # ── Step 2: Generate digest via Claude (with validation retry) ──────────
    from digest import generate_digest, regenerate_digest
    MAX_VALIDATION_RETRIES = 2
    digest_data = generate_digest(payload, db_context=db_context)

    # Auto-strip duplicates, filter deals, enforce source diversity, repair URLs
    digest_data, pp_log = _postprocess_digest(digest_data, payload=payload)
    for msg in pp_log:
        print(msg)

    Path("digest.json").write_text(json.dumps(digest_data, ensure_ascii=False, indent=2))

    # ── Step 2+: Update trackers (Kim, KCNA, BP, Tension) ──────────────────
    from kim_tracker import update_from_digest
    from kcna_tracker import update_from_digest as kcna_update_from_digest
    from bp_tracker import update_from_digest as bp_update_from_digest

    validation_passed = False
    for validation_attempt in range(1 + MAX_VALIDATION_RETRIES):
        # ── Step 2a: Pre-send validation gate ─────────────────────────────────
        validation_warnings = validate_digest(digest_data, payload=payload)
        critical_warnings = [w for w in validation_warnings if "CRITICAL" in w]

        # Don't retry for DUPLICATE TOPIC issues — dedup already handled them,
        # and retrying just causes dedup to fight the retry loop
        retryable_warnings = [w for w in critical_warnings if "DUPLICATE TOPIC" not in w]

        if not critical_warnings:
            # Passed — print any non-critical warnings and move on
            if validation_warnings:
                print("\n⚠️  PRE-SEND VALIDATION WARNINGS (non-critical):")
                for w in validation_warnings:
                    print(f"    • {w}")
                print()
            else:
                print("\n✅  Validation passed — all checks OK")
            validation_passed = True
            break

        if not retryable_warnings:
            # Only duplicate topic warnings remain — dedup handled what it could
            print("\n⚠️  Remaining warnings are duplicate-topic only (auto-dedup applied):")
            for w in critical_warnings:
                print(f"    • {w}")
            # Downgrade these so they don't block sending
            validation_passed = True
            break

        # Critical failures found
        print(f"\n⚠️  VALIDATION ATTEMPT {validation_attempt + 1}/{1 + MAX_VALIDATION_RETRIES} — CRITICAL WARNINGS:")
        for w in validation_warnings:
            print(f"    • {w}")

        if validation_attempt < MAX_VALIDATION_RETRIES:
            # Retry only the digest generation, passing validation feedback
            print("\n🔄  Re-generating digest with validation feedback (reusing collected articles)...")
            digest_data = regenerate_digest(
                payload, digest_data, retryable_warnings, db_context=db_context,
                attempt=validation_attempt
            )
            # Post-process again after regeneration (incl. URL repair)
            digest_data, pp_log = _postprocess_digest(digest_data, payload=payload)
            for msg in pp_log:
                print(f"  {msg}")
            Path("digest.json").write_text(json.dumps(digest_data, ensure_ascii=False, indent=2))
        else:
            print("\n🚫  CRITICAL validation failures after all retries — newsletter will NOT be sent.")
            print("    Fix the issues above or re-run. HTML still rendered for review.")

    # Only update trackers if digest passed validation (avoid corrupting state)
    if validation_passed:
        update_from_digest(digest_data)
        kcna_update_from_digest(digest_data)
        bp_update_from_digest(digest_data)
    else:
        print("  ⚠  Skipping tracker updates due to critical validation failures")

    # ── Step 2b: Push flagged entries to databases ────────────────────────────
    if not args.no_push and validation_passed:
        push_summary = process_digest_entries(digest_data)
        if push_summary.get("nk_russia_added") or push_summary.get("provocations_added"):
            print(f"    NK-Russia: {push_summary.get('nk_russia_added', 0)} added, "
                  f"Provocations: {push_summary.get('provocations_added', 0)} added")
    else:
        print("\n  --no-push: skipping database updates")

    # ── Step 3: Render HTML ──────────────────────────────────────────────────
    from render import render

    # Inject web URL for "Read Online" link (set via env or GitHub Pages)
    web_base = os.environ.get("WEB_URL", "")
    if web_base:
        digest_data["web_url"] = web_base.rstrip("/") + "/latest.html"

    html = render(digest_data)

    date_slug = datetime.now(ZoneInfo("America/New_York")).strftime("%Y-%m-%d")
    out_path  = Path(f"digest_{date_slug}.html")
    out_path.write_text(html, encoding="utf-8")
    print(f"\n📄  HTML rendered: {out_path} ({len(html):,} bytes)")

    # Also write latest.html for convenience
    Path("latest.html").write_text(html, encoding="utf-8")

    # Archive copy for GitHub Pages
    archive_dir = Path("public")
    archive_dir.mkdir(exist_ok=True)
    (archive_dir / "latest.html").write_text(html, encoding="utf-8")
    (archive_dir / f"digest_{date_slug}.html").write_text(html, encoding="utf-8")

    # ── Save digest JSON for weekly summaries ──────────────────────────────
    (archive_dir / f"digest_{date_slug}.json").write_text(
        json.dumps(digest_data, ensure_ascii=False), encoding="utf-8"
    )

    # ── Maintain archive manifest (archive.json) ────────────────────────────
    archive_json_path = archive_dir / "archive.json"
    try:
        archive_entries = json.loads(archive_json_path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        archive_entries = []

    # Remove any existing entry for today (idempotent re-runs)
    archive_entries = [e for e in archive_entries if e.get("date") != date_slug]

    archive_entries.append({
        "date": date_slug,
        "headline_re": digest_data.get("re_line", ""),
        "top_stories_count": len(digest_data.get("top_stories") or []),
        "word_count": _count_digest_words(digest_data),
        "url": f"digest_{date_slug}.html",
    })
    archive_json_path.write_text(
        json.dumps(archive_entries, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    # ── Landing page and archive page for GitHub Pages ─────────────────────
    (archive_dir / "index.html").write_text(_build_index_html(), encoding="utf-8")
    archive_template = Path(__file__).parent / "templates" / "archive.html"
    if archive_template.exists():
        import shutil
        shutil.copy2(archive_template, archive_dir / "archive.html")

    # ── Step 4: Send email ───────────────────────────────────────────────────
    if not validation_passed:
        print("\n🚫  Skipping email due to critical validation failures. Review latest.html.")
        import sys
        sys.exit(1)
    elif args.no_send:
        print("\n  --no-send: skipping email. Open latest.html to review.")
    else:
        if not os.environ.get("DIGEST_TO"):
            print("\n⚠️  DIGEST_TO not set — email will only go to sender's own address")
        from send_email import send
        re_line = digest_data.get("re_line")
        send(html, re_line=re_line)

    # ── Step 5: Log quality metrics ────────────────────────────────────────
    try:
        metrics = {
            "date": date_slug,
            "word_count": _count_digest_words(digest_data),
            "top_stories": len(digest_data.get("top_stories") or []),
            "overnight_items": len(digest_data.get("overnight_items") or []),
            "business_economy": len(digest_data.get("business_economy") or []),
            "northeast_asia": len(digest_data.get("northeast_asia") or []),
            "opeds": len(digest_data.get("opeds_today") or []),
            "academic": len(digest_data.get("academic_today") or []),
            "statements": len(digest_data.get("social_statements") or []),
            "tier1_input": len(payload.get("tier1", [])),
            "tier4_input": len(payload.get("tier4", [])),
            "kcna_articles": (payload.get("kcna_summary") or {}).get("total_articles", 0),
            "validation_warnings": len(validation_warnings),
            "validation_retries": validation_attempt,
            "html_bytes": len(html),
            "sent": not args.no_send and validation_passed,
        }
        metrics_path = Path("metrics.jsonl")
        with open(metrics_path, "a") as f:
            f.write(json.dumps(metrics) + "\n")
    except Exception:
        pass

    print("\n✅  Done.\n")


if __name__ == "__main__":
    main()
