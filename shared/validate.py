"""Deterministic pre-send checks, shared by every CSIS daily brief.

These run after the model has written the brief and before anything is sent.
No model is involved: each function is ordinary code a reviewer can read, which
is what makes the brief defensible under a model-risk review.

Every check here was in Korea's `run.py` and nowhere else, so the other three
briefs have been sending without them. Thresholds are arguments rather than
constants because a leaner edition legitimately runs shorter than Korea does.

Each function returns a list of human-readable problem strings. An empty list
means the check passed. Nothing here raises, and nothing mutates the digest —
callers decide what is fatal and what is a warning.
"""
from __future__ import annotations

import re
from datetime import date

__all__ = [
    "check_urls_in_input", "check_section_caps", "check_source_cap",
    "check_word_floor", "check_digest_date", "check_placeholder_names",
    "check_offdate_items", "run_all",
]

# Values that mean the model had no real name and filled the gap anyway.
# Two shapes: a phrase that starts a non-name ("Not named in source" — the
# form that actually shipped), and a short token that is the whole value.
_PLACEHOLDER_PREFIX_RE = re.compile(
    r"^\s*(not\s+(named|specified|disclosed|given)|unnamed|undisclosed|"
    r"name\s+withheld|to\s+be\s+(announced|determined|confirmed)|no\s+name)\b", re.I)
_PLACEHOLDER_EXACT_RE = re.compile(
    r"^\s*(unknown|n/?a|tbd|tba|none|null|\[.*\]|[—–-]+)\s*$", re.I)


def _is_placeholder(value: str) -> bool:
    return bool(_PLACEHOLDER_PREFIX_RE.match(value)
                or _PLACEHOLDER_EXACT_RE.match(value))


def _items(digest: dict, sections) -> list[tuple[str, dict]]:
    """(section_name, item) for every dict item in the named sections."""
    out = []
    for name in sections:
        for item in (digest.get(name) or []):
            if isinstance(item, dict):
                out.append((name, item))
    return out


def check_urls_in_input(digest: dict, sections, input_urls: set) -> list[str]:
    """Every URL in the brief must be one the collector actually fetched.

    This is the single most important check in the file: it is what makes a
    fabricated article impossible to ship, because the model cannot invent a
    URL that was already in the input set.
    """
    if not input_urls:
        return []
    problems = []
    for section, item in _items(digest, sections):
        url = (item.get("url") or "").strip()
        if url and url not in input_urls:
            head = (item.get("headline") or "")[:60]
            problems.append(f"{section}: URL not in collected input — {head!r} ({url[:70]})")
    return problems


def check_section_caps(digest: dict, caps: dict) -> list[str]:
    """caps maps a section name to (minimum, maximum)."""
    problems = []
    for section, (lo, hi) in caps.items():
        n = len(digest.get(section) or [])
        if lo is not None and n < lo:
            problems.append(f"{section}: {n} items (minimum {lo})")
        if hi is not None and n > hi:
            problems.append(f"{section}: {n} items (maximum {hi})")
    return problems


def check_source_cap(digest: dict, sections, max_per_source: int = 3) -> list[str]:
    """No single outlet may dominate the sections a reader actually reads."""
    counts: dict[str, int] = {}
    for _, item in _items(digest, sections):
        src = (item.get("source") or "").strip()
        if src:
            counts[src] = counts.get(src, 0) + 1
    return [f"source over-represented: {s} appears {n} times (max {max_per_source})"
            for s, n in sorted(counts.items()) if n > max_per_source]


def check_word_floor(word_count: int, floor: int) -> list[str]:
    """A brief far under target usually means truncated output, not a quiet day."""
    return ([f"word count {word_count} is below the floor of {floor}"]
            if word_count < floor else [])


def check_digest_date(digest: dict, today: date | None = None) -> list[str]:
    """The brief must be dated today. A stale date means a cached run shipped."""
    today = today or date.today()
    stated = str(digest.get("digest_date") or "")
    if not stated:
        return ["digest_date is missing"]
    if str(today.year) not in stated:
        return [f"digest_date {stated!r} does not carry the current year"]
    return []


def check_placeholder_names(digest: dict, sections) -> list[str]:
    """Catch entries where the model had no name and wrote one anyway."""
    problems = []
    for section, item in _items(digest, sections):
        for field in ("name", "person", "official", "appointee"):
            value = item.get(field)
            if isinstance(value, str) and _is_placeholder(value):
                problems.append(f"{section}: placeholder in {field} — {value!r}")
    return problems


def check_offdate_items(digest: dict, section: str, today: date | None = None) -> list[str]:
    """An 'on this day' item whose date is not today's date.

    Shipped once as a 4 July entry in a 24 July issue.
    """
    today = today or date.today()
    problems = []
    for item in (digest.get(section) or []):
        if not isinstance(item, dict):
            continue
        raw = str(item.get("date") or item.get("month_day") or "")
        m = re.search(r"(\d{1,2})\s*[/-]\s*(\d{1,2})", raw)
        if m:
            month, day = int(m.group(1)), int(m.group(2))
            if (month, day) != (today.month, today.day):
                problems.append(f"{section}: dated {month}/{day}, today is "
                                f"{today.month}/{today.day}")
    return problems


def run_all(digest: dict, *, sections, input_urls: set | None = None,
            caps: dict | None = None, word_count: int = 0,
            word_floor: int = 0, max_per_source: int = 3,
            today: date | None = None) -> list[str]:
    """Run every applicable check and return one combined problem list.

    Callers pass only what they have; a check with no data to work on is
    skipped rather than failing. Returning problems rather than raising keeps
    the decision about what blocks a send with the caller.
    """
    problems: list[str] = []
    if input_urls:
        problems += check_urls_in_input(digest, sections, input_urls)
    if caps:
        problems += check_section_caps(digest, caps)
    problems += check_source_cap(digest, sections, max_per_source)
    if word_floor:
        problems += check_word_floor(word_count, word_floor)
    problems += check_digest_date(digest, today)
    problems += check_placeholder_names(digest, sections)
    problems += check_offdate_items(digest, "on_this_day", today)
    return problems
