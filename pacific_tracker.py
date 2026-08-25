"""
Australia Chair Daily Brief: China in the Pacific tracker
CSIS Australia Chair

A per-country ledger of PRC activity across the Pacific Islands: security and
policing arrangements, port and infrastructure deals, senior visits, and
recognition switches. Feeds both the china_in_the_pacific section and the
Pacific Wire, and covers two of the requester's named topics directly.

Same contract as the AUKUS tracker:
    build_context_block()      -> str
    update_from_digest(digest) -> int

The country list is the ledger's spine. It stays fixed so a state that goes
quiet still shows as quiet rather than disappearing from the brief entirely.
"""
import json
import re
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

TRACKER_PATH = Path(__file__).parent / "data" / "pacific_tracker.json"

# The ledger's spine. Aliases are what the matcher looks for in a headline.
PACIFIC_STATES = {
    "Papua New Guinea":  ["papua new guinea", "png", "port moresby", "marape", "bougainville"],
    "Fiji":              ["fiji", "fijian", "suva", "rabuka"],
    "Solomon Islands":   ["solomon islands", "honiara", "solomons"],
    "Vanuatu":           ["vanuatu", "port vila"],
    "Samoa":             ["samoa", "apia"],
    "Tonga":             ["tonga", "nuku alofa"],
    "Kiribati":          ["kiribati", "tarawa"],
    "Tuvalu":            ["tuvalu", "funafuti"],
    "Nauru":             ["nauru"],
    "Palau":             ["palau", "koror"],
    "Marshall Islands":  ["marshall islands", "majuro"],
    "Micronesia (FSM)":  ["micronesia", "pohnpei", "\\bfsm\\b"],
    "Cook Islands":      ["cook islands", "rarotonga"],
    "Niue":              ["niue"],
    "New Caledonia":     ["new caledonia", "noumea", "kanak"],
    "French Polynesia":  ["french polynesia", "tahiti", "papeete"],
    "Timor-Leste":       ["timor-leste", "east timor", "dili"],
}

# What kind of PRC activity an item describes. Order matters: first match wins,
# and the more consequential categories are listed first.
ACTIVITY_PATTERNS = [
    ("security agreement",  r"security (?:pact|agreement|deal|arrangement|treaty)"
                            r"|policing (?:agreement|deal|mou|cooperation)"
                            r"|police (?:training|advisers|deployment)"
                            r"|military (?:base|access|agreement)"),
    ("recognition switch",  r"(?:switch|switched|recognition|recognise|recognize)"
                            r".{0,40}(?:taiwan|beijing|prc|one.china)"
                            r"|diplomatic (?:ties|relations).{0,30}(?:taiwan|beijing)"),
    ("port / infrastructure", r"\bport\b|wharf|airstrip|airport|runway|undersea cable"
                            r"|submarine cable|telecom|5g|highway|infrastructure (?:deal|project|loan)"),
    ("loan / finance",      r"\bloan\b|debt|financing|grant aid|budget support|belt and road"),
    ("senior visit",        r"\bvisit(?:s|ed|ing)?\b|delegation|foreign minister|premier"
                            r"|state visit|signed .{0,30}(?:agreement|memorandum)"),
    ("fisheries / maritime", r"fisheries|fishing fleet|maritime surveillance|coast ?guard"
                            r"|research vessel|survey ship"),
]

_CHINA_PATTERN = re.compile(
    r"\bchina\b|chinese|beijing|\bprc\b|xi jinping|belt and road|huawei"
    r"|wang yi|state councillor|taiwan",
    re.IGNORECASE,
)


def _today() -> str:
    return datetime.now(ZoneInfo("America/New_York")).strftime("%Y-%m-%d")


def _blank() -> dict:
    return {
        "last_updated": None,
        "countries": {
            name: {"last_event": None, "last_event_date": None,
                   "last_activity_type": None, "url": None, "event_count": 0}
            for name in PACIFIC_STATES
        },
        "recent_events": [],
    }


def _load() -> dict:
    try:
        data = json.loads(TRACKER_PATH.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return _blank()
    # Keep the spine complete even if the country list grows later.
    for name in PACIFIC_STATES:
        data.setdefault("countries", {}).setdefault(
            name, {"last_event": None, "last_event_date": None,
                   "last_activity_type": None, "url": None, "event_count": 0})
    return data


def _save(data: dict) -> None:
    TRACKER_PATH.parent.mkdir(parents=True, exist_ok=True)
    TRACKER_PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2),
                            encoding="utf-8")


def _match_country(text: str) -> str | None:
    lowered = text.lower()
    for name, aliases in PACIFIC_STATES.items():
        for alias in aliases:
            if alias.startswith("\\b"):
                if re.search(alias, lowered):
                    return name
            elif alias in lowered:
                return name
    return None


def _match_activity(text: str) -> str | None:
    for label, pattern in ACTIVITY_PATTERNS:
        if re.search(pattern, text, re.IGNORECASE):
            return label
    return None


def build_context_block() -> str:
    """Render the ledger for prompt injection."""
    data = _load()
    countries = data.get("countries", {})

    with_history = [(n, c) for n, c in countries.items() if c.get("last_event")]
    without = [n for n, c in countries.items() if not c.get("last_event")]

    lines = ["CHINA IN THE PACIFIC TRACKER (persistent state, use these, do not recall from memory)"]
    lines.append(f"  Tracker last updated: {data.get('last_updated') or 'never'}")

    if with_history:
        lines.append("  Last recorded PRC-related development, by state:")
        for name, c in sorted(with_history,
                              key=lambda kv: kv[1].get("last_event_date") or "",
                              reverse=True):
            lines.append(
                f"    - {name} ({c['last_event_date']}, {c['last_activity_type']}): "
                f"{c['last_event']}")
    else:
        lines.append("  No PRC-related developments recorded yet, the ledger is empty.")

    if without:
        lines.append(f"  No record yet for: {', '.join(sorted(without))}")

    lines.append("  RULE: use this ledger for 'first since' and 'last time' claims. Do NOT "
                 "assert a Pacific agreement, visit, or recognition switch that is neither "
                 "in today's articles nor in this ledger. If the ledger is silent on a "
                 "state, say nothing about its history rather than recalling one.")
    return "\n".join(lines)


def update_from_digest(digest: dict) -> int:
    """Write today's China-in-the-Pacific items back to the ledger."""
    data = _load()
    today = _today()
    recorded = 0

    sections = ("top_stories", "china_in_the_pacific", "pacific_wire",
                "overnight_items", "primary_documents", "also_today")
    for section in sections:
        for item in (digest.get(section) or []):
            if not isinstance(item, dict):
                continue
            headline = item.get("headline", "") or ""
            body = " ".join(str(item.get(f, "")) for f in
                            ("body", "body_text", "summary", "detail"))
            text = f"{headline} {body}"

            if not _CHINA_PATTERN.search(text):
                continue
            country = _match_country(text)
            if not country:
                continue
            activity = _match_activity(text) or "other"

            entry = data["countries"][country]
            entry["last_event"] = headline[:200]
            entry["last_event_date"] = today
            entry["last_activity_type"] = activity
            entry["url"] = item.get("url")
            entry["event_count"] = entry.get("event_count", 0) + 1

            data.setdefault("recent_events", []).insert(0, {
                "date": today, "country": country, "activity": activity,
                "headline": headline, "url": item.get("url"), "section": section,
            })
            recorded += 1

    data["recent_events"] = data.get("recent_events", [])[:120]
    if recorded:
        data["last_updated"] = today
        _save(data)
    return recorded


if __name__ == "__main__":
    if not TRACKER_PATH.exists():
        _save(_load())
        print(f"Wrote seed ledger to {TRACKER_PATH}\n")
    print(build_context_block())
