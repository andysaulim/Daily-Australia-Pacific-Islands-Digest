"""Keep the brief inside its length, deterministically.

The pipeline had a floor and no ceiling. Asking the model to be shorter is
not a ceiling — it is a preference that competes with every other instruction
in the prompt, and on a heavy news day it loses. This module is the ceiling:
if the brief is over budget after the model has written it, sections are
trimmed from the tail in a fixed order until it fits.

The order is an editorial judgement, stated once here rather than improvised.
Top stories and the morning memo are never trimmed — they are the brief. What
goes first is breadth that repeats value found elsewhere: the tail of the
wire, then quoted statements, then the second and third op-ed. Trimming takes
from the end of each section because the model is asked to order by importance
within a section, so the tail is the least important item it chose.

Nothing here rewrites text. It only drops whole items, so anything that
survives is exactly what the model wrote and every remaining claim keeps the
source it was checked against.
"""
from __future__ import annotations

# Sections in the order they give up items, least costly first. A section not
# named here is never trimmed: top_stories, morning_memo, key_stat,
# calendar_watch, on_this_day.
TRIM_ORDER: list[tuple[str, int]] = [
    # (section, floor — never trim below this many items)
    ("also_today", 3),
    ("primary_documents", 1),
    ("academic_today", 1),
    ("opeds_today", 2),
    ("china_in_the_pacific", 2),
    ("canberra_politics", 2),
    ("business_economy", 2),
    ("new_zealand", 1),
    ("aukus_watch", 2),
    ("pacific_wire", 2),
    ("overnight_items", 3),
]


def plan(digest: dict, count_fn, ceiling: int) -> list[tuple[str, int, int]]:
    """Work out what to drop without changing anything.

    Returns (section, from_count, to_count) for each section that would be
    trimmed. Separated from `apply` so a run can log the decision, and so the
    logic is testable without mutating a digest.
    """
    words = count_fn(digest)
    if words <= ceiling:
        return []

    working = {k: list(v) for k, v in digest.items() if isinstance(v, list)}
    shadow = dict(digest)
    cuts: dict[str, tuple[int, int]] = {}

    for section, floor in TRIM_ORDER:
        items = working.get(section) or []
        start = len(items)
        while len(items) > floor:
            items.pop()
            shadow[section] = items
            if section not in cuts:
                cuts[section] = (start, len(items))
            cuts[section] = (cuts[section][0], len(items))
            if count_fn(shadow) <= ceiling:
                return [(s, a, b) for s, (a, b) in cuts.items()]
        working[section] = items

    return [(s, a, b) for s, (a, b) in cuts.items()]


def apply(digest: dict, count_fn, ceiling: int) -> list[str]:
    """Trim the digest in place. Returns human-readable lines for the run log."""
    cuts = plan(digest, count_fn, ceiling)
    if not cuts:
        return []
    before = count_fn(digest)
    for section, _start, keep in cuts:
        digest[section] = (digest.get(section) or [])[:keep]
    after = count_fn(digest)
    lines = [f"over length: {before} words, trimmed to {after} (ceiling {ceiling})"]
    lines += [f"    {section}: {start} items -> {keep}" for section, start, keep in cuts]
    if after > ceiling:
        lines.append("    still over after trimming every section that may be "
                     "trimmed; the fixed sections are carrying the excess")
    return lines
