"""
Australia Chair Daily Brief: verified diplomatic calendar
CSIS Australia Chair

The whitelist behind calendar_watch. The guardrail this exists to enforce is
"dates from sources only": the model may use a date that appears in today's
articles or in this file, and nowhere else. Wrong dates are the fastest way to
lose an expert reader.

Every entry carries a `confidence` field:
    confirmed: two independent working sources; safe to print
    expected: recurring event, date not yet announced; print as a window, not a date
    seed: placeholder written at build time; NEVER printed

Only `confirmed` entries reach the prompt as dates. Everything else is either
described as a window or withheld.

Maintenance: review weekly. When an event passes, either delete it or move it to
ANNIVERSARIES if it is worth an "on this day" line.

Named calendar_tracker rather than calendar on purpose: a module called
calendar.py in the repo root shadows the standard library's calendar, which
feedparser imports, and the collector dies at import time.
"""
import json
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

CALENDAR_PATH = Path(__file__).parent / "data" / "calendar.json"

# Recurring fixtures. These are the events the brief should always be looking
# ahead to, whether or not a date has been announced. Dates go in calendar.json,
# not here: this list is just the standing watch.
RECURRING_EVENTS = [
    ("Pacific Islands Forum Leaders Meeting", "annual, usually August or September"),
    ("AUSMIN (Australia-US Ministerial Consultations)", "annual, venue alternates"),
    ("ANZMIN (Australia-New Zealand Foreign and Defence Ministers)", "annual"),
    ("Quad Leaders Summit", "annual, host rotates"),
    ("Quad Foreign Ministers Meeting", "roughly twice yearly"),
    ("Talisman Sabre", "biennial, odd-numbered years, Australian winter"),
    ("Australian federal budget", "second Tuesday in May, ordinarily"),
    ("New Zealand budget", "May, ordinarily"),
    ("Shangri-La Dialogue", "annual, Singapore, late May or early June"),
    ("Pacific Islands Forum Foreign Ministers Meeting", "annual"),
    ("Melanesian Spearhead Group Leaders Summit", "periodic"),
    ("Australian parliamentary sitting weeks", "published sitting calendar"),
    ("New Zealand parliamentary sitting weeks", "published sitting calendar"),
]

_SEED = {
    "last_reviewed": None,
    "events": [
        # Seed entries are deliberately empty of dates. Populate from announced
        # schedules with two sources each before the first supervised send.
        {"event": name, "date": None, "window": window,
         "confidence": "seed", "sources": [], "note": None}
        for name, window in RECURRING_EVENTS
    ],
    "anniversaries": [
        # on_this_day candidates. Same rule: nothing prints without confirmation.
        # Example shape:
        # {"month": 9, "day": 1, "year": 1951,
        #  "event": "ANZUS Treaty signed in San Francisco",
        #  "confidence": "confirmed", "sources": ["url1", "url2"]}
    ],
}


def _today():
    return datetime.now(ZoneInfo("America/New_York")).date()


def _load() -> dict:
    try:
        return json.loads(CALENDAR_PATH.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return json.loads(json.dumps(_SEED))


def _save(data: dict) -> None:
    CALENDAR_PATH.parent.mkdir(parents=True, exist_ok=True)
    CALENDAR_PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2),
                             encoding="utf-8")


def upcoming(days: int = 90) -> list[dict]:
    """Confirmed events falling inside the window, soonest first."""
    data = _load()
    today = _today()
    horizon = today + timedelta(days=days)
    out = []
    for e in data.get("events", []):
        if e.get("confidence") != "confirmed" or not e.get("date"):
            continue
        try:
            when = datetime.strptime(e["date"], "%Y-%m-%d").date()
        except ValueError:
            continue
        if today <= when <= horizon:
            out.append({**e, "days_out": (when - today).days})
    return sorted(out, key=lambda e: e["days_out"])


def on_this_day() -> list[dict]:
    """Confirmed anniversaries matching today's month and day."""
    data = _load()
    today = _today()
    return [a for a in data.get("anniversaries", [])
            if a.get("confidence") == "confirmed"
            and a.get("month") == today.month and a.get("day") == today.day]


def build_context_block() -> str:
    """Render the calendar for prompt injection."""
    data = _load()
    lines = ["VERIFIED DIPLOMATIC CALENDAR (the ONLY dates you may use besides today's articles)"]
    lines.append(f"  Calendar last reviewed: {data.get('last_reviewed') or 'never'}")

    confirmed = upcoming(days=90)
    if confirmed:
        lines.append("  Confirmed, next 90 days:")
        for e in confirmed:
            note = f": {e['note']}" if e.get("note") else ""
            lines.append(f"    - {e['date']} ({e['days_out']} days out): {e['event']}{note}")
    else:
        lines.append("  No confirmed dates in the next 90 days.")

    pending = [e for e in data.get("events", [])
               if e.get("confidence") in ("expected", "seed") and not e.get("date")]
    if pending:
        lines.append("  Standing fixtures with NO confirmed date, refer to these as "
                     "windows, never as dates:")
        for e in pending:
            lines.append(f"    - {e['event']} ({e.get('window', 'date not announced')})")

    otd = on_this_day()
    if otd:
        lines.append("  On this day (confirmed):")
        for a in otd:
            lines.append(f"    - {a['year']}: {a['event']}")
    else:
        lines.append("  On this day: no confirmed anniversary. Return an empty "
                     "on_this_day array rather than recalling one.")

    lines.append("  RULE: calendar_watch entries must come from today's articles or from "
                 "the confirmed list above. Do NOT generate a date from memory. For a "
                 "standing fixture with no confirmed date, write the window "
                 "(\"expected in August\"), never a specific day.")
    return "\n".join(lines)


def mark_reviewed() -> None:
    data = _load()
    data["last_reviewed"] = _today().strftime("%Y-%m-%d")
    _save(data)


def add_event(event: str, date: str, sources: list[str], note: str | None = None) -> None:
    """Add or replace a confirmed calendar entry.

    Requires two sources. This mirrors the house sourcing gate and is enforced
    here rather than left to the operator's memory.
    """
    if len(sources) < 2:
        raise ValueError(
            f"'{event}' needs two independent working sources, got {len(sources)}. "
            "A single-source date does not go in the calendar.")
    datetime.strptime(date, "%Y-%m-%d")  # raises if malformed

    data = _load()
    data["events"] = [e for e in data.get("events", []) if e.get("event") != event]
    data["events"].append({
        "event": event, "date": date, "window": None,
        "confidence": "confirmed", "sources": sources, "note": note,
    })
    data["last_reviewed"] = _today().strftime("%Y-%m-%d")
    _save(data)


if __name__ == "__main__":
    print(build_context_block())
