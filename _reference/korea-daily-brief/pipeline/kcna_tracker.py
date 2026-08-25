"""
KCNA Rhetoric Tracker
Persistent tracking of KCNA rhetoric data across digest runs.
Stores daily phrase counts, propaganda focus, output volume, and tone shifts
so the digest prompt can include real historical data instead of hallucinated baselines.
"""
import json
from pathlib import Path
from datetime import datetime, timezone, timedelta

TRACKER_FILE = Path(__file__).parent / "kcna_tracker.json"
MAX_DAYS = 14  # keep 2 weeks of history


def _load() -> dict:
    """Load tracker data from disk."""
    if TRACKER_FILE.exists():
        try:
            return json.loads(TRACKER_FILE.read_text())
        except (json.JSONDecodeError, IOError):
            pass
    return {"days": [], "last_updated": None}


def _save(data: dict):
    """Save tracker data to disk."""
    data["last_updated"] = datetime.now(timezone.utc).isoformat()
    TRACKER_FILE.write_text(json.dumps(data, indent=2, ensure_ascii=False))


def _normalize_date(date_str: str) -> str:
    """Normalize various date formats to YYYY-MM-DD."""
    for fmt in ("%Y-%m-%d", "%A, %B %d, %Y", "%A, %d %B %Y", "%B %d, %Y",
                "%d %B %Y", "%m/%d/%Y"):
        try:
            return datetime.strptime(date_str.strip(), fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    return date_str  # return as-is if we can't parse


def update_from_digest(digest: dict):
    """Extract KCNA delta from a completed digest and persist it."""
    kcna = digest.get("kcna_delta") or {}
    if not kcna or not any(kcna.values()):
        return

    date_str = _normalize_date(digest.get("digest_date", ""))
    if not date_str:
        date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    # Build the day record
    day_record = {
        "date": date_str,
        "silence_today": kcna.get("silence_today", False),
        "watch_flag": kcna.get("watch_flag", False),
        "bottom_line": kcna.get("bottom_line"),
    }

    # Extract official quotes
    quotes = kcna.get("key_quotes") or []
    if quotes:
        day_record["quotes"] = [
            {"speaker": q.get("speaker", ""), "quote": q.get("quote", ""), "source": q.get("source_article", "")}
            for q in quotes[:4] if q.get("quote")
        ]

    data = _load()

    # Replace if same date exists, otherwise append
    data["days"] = [d for d in data["days"] if d.get("date") != date_str]
    data["days"].append(day_record)

    # Sort by date descending and trim to MAX_DAYS
    data["days"].sort(key=lambda d: d.get("date", ""), reverse=True)
    data["days"] = data["days"][:MAX_DAYS]

    _save(data)


def get_recent_days(limit: int = 7) -> list:
    """Return the most recent day records (newest first)."""
    data = _load()
    return sorted(data["days"], key=lambda d: d.get("date", ""), reverse=True)[:limit]


def build_context_block() -> str:
    """Build a context string for the digest prompt with recent DPRK official statement history."""
    recent = get_recent_days(7)
    if not recent:
        return ""

    lines = ["DPRK OFFICIAL STATEMENTS HISTORY (last 7 days):"]
    lines.append("Use this for context on recent official activity.\n")

    for day in recent:
        date = day.get("date", "unknown")
        silence = day.get("silence_today", False)
        watch = day.get("watch_flag", False)
        bottom = day.get("bottom_line", "")
        day_quotes = day.get("quotes") or []

        flags = []
        if silence:
            flags.append("SILENCE")
        if watch:
            flags.append("WATCH")
        flag_str = f" [{', '.join(flags)}]" if flags else ""

        lines.append(f"--- {date}{flag_str} ---")
        for dq in day_quotes:
            speaker = dq.get("speaker", "Unknown")
            quote = dq.get("quote", "")
            if quote:
                lines.append(f'  {speaker}: "{quote}"')
        if bottom:
            lines.append(f"  Bottom line: {bottom}")
        lines.append("")

    return "\n".join(lines)
