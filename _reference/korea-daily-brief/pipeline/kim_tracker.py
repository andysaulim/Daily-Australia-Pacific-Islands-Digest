"""
Kim Jong Un Appearance Tracker
Persistent tracking of confirmed Kim Jong Un public appearances across digest runs.
"""
import json
from pathlib import Path
from datetime import datetime, timezone, timedelta

TRACKER_FILE = Path(__file__).parent / "kim_tracker.json"


def _load() -> dict:
    """Load tracker data from disk."""
    if TRACKER_FILE.exists():
        try:
            return json.loads(TRACKER_FILE.read_text())
        except (json.JSONDecodeError, IOError):
            pass
    return {"appearances": [], "last_updated": None}


def _save(data: dict):
    """Save tracker data to disk."""
    data["last_updated"] = datetime.now(timezone.utc).isoformat()
    TRACKER_FILE.write_text(json.dumps(data, indent=2, ensure_ascii=False))


def get_recent_appearances(limit: int = 10) -> list:
    """Return the most recent confirmed appearances (newest first)."""
    data = _load()
    return sorted(data["appearances"], key=lambda x: x["date"], reverse=True)[:limit]


def get_last_appearance() -> dict | None:
    """Return the most recent confirmed appearance, or None."""
    recent = get_recent_appearances(1)
    return recent[0] if recent else None


def days_since_last_appearance() -> int | None:
    """Calculate days since last confirmed appearance."""
    last = get_last_appearance()
    if not last:
        return None
    try:
        last_date = datetime.strptime(last["date"], "%Y-%m-%d").replace(tzinfo=timezone.utc)
        delta = datetime.now(timezone.utc) - last_date
        return delta.days
    except (ValueError, KeyError):
        return None


def record_appearance(date_str: str, activity: str, source: str):
    """Record a confirmed Kim Jong Un appearance. Deduplicates by date."""
    data = _load()
    existing_dates = {a["date"] for a in data["appearances"]}
    if date_str not in existing_dates:
        data["appearances"].append({
            "date": date_str,
            "activity": activity,
            "source": source,
        })
        _save(data)
        return True
    return False


def update_from_digest(digest: dict):
    """Extract Kim appearance data from a completed digest and persist it."""
    kcna = digest.get("kcna_delta") or {}
    if kcna.get("kim_appearance_today"):
        date_str = digest.get("digest_date", datetime.now(timezone.utc).strftime("%Y-%m-%d"))
        # Normalize date format (handle "Friday, April 4, 2026" etc.)
        parsed_ok = False
        for fmt in ("%Y-%m-%d", "%A, %B %d, %Y", "%A, %d %B %Y", "%d %B %Y", "%B %d, %Y"):
            try:
                parsed = datetime.strptime(date_str, fmt)
                date_str = parsed.strftime("%Y-%m-%d")
                parsed_ok = True
                break
            except ValueError:
                continue
        if not parsed_ok:
            # Last resort: use today's date
            date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        activity = kcna.get("kim_activity", "Public appearance reported")
        record_appearance(date_str, activity, "KCNA digest")


def build_dot_calendar(today_appeared: bool | None = None) -> str:
    """Return an HTML snippet showing a 30-day GitHub-style dot calendar.

    Colors:
      - Green (#22c55e) = confirmed appearance that day
      - Red (#ef4444) = day was tracked but Kim was absent
      - Gray (#d1d5db) = no data for that day

    The calendar reads left-to-right (oldest to newest).
    Designed for email: inline styles, table-based layout, no external CSS/JS.

    Args:
        today_appeared: If provided, overrides today's status
                        (True=appeared, False=absent, None=use stored data only).
    """
    data = _load()
    appearances = data.get("appearances", [])

    # Build a set of dates with confirmed appearances
    appearance_dates: set[str] = {a["date"] for a in appearances}

    # Determine all dates we have ANY data for (tracked days).
    # We consider a day "tracked" if it has an appearance record.
    # Days without records are gray (no data).
    # For "red" (absent) we need to know which days were tracked but had no appearance.
    # Since the tracker only stores appearances, we infer tracked days from
    # the range of tracking: any day between the first appearance and today
    # is considered "tracked" (the digest runs daily).
    today = datetime.now(timezone.utc).date()

    # Find earliest appearance to determine tracking start
    all_dates_parsed = []
    for a in appearances:
        try:
            all_dates_parsed.append(datetime.strptime(a["date"], "%Y-%m-%d").date())
        except (ValueError, KeyError):
            continue

    tracking_start = min(all_dates_parsed) if all_dates_parsed else None

    # Generate last 30 days
    days_list = []
    for i in range(29, -1, -1):
        d = today - timedelta(days=i)
        date_str = d.strftime("%Y-%m-%d")
        date_label = d.strftime("%b %d")

        if date_str in appearance_dates:
            color = "#22c55e"  # green — confirmed appearance
            status = "Appeared"
        elif tracking_start and d >= tracking_start:
            color = "#ef4444"  # red — tracked but absent
            status = "No appearance"
        else:
            color = "#d1d5db"  # gray — no data
            status = "No data"

        days_list.append((date_str, date_label, color, status))

    # Override today if explicitly provided
    if today_appeared is not None:
        today_str = today.strftime("%Y-%m-%d")
        for idx, (ds, dl, c, s) in enumerate(days_list):
            if ds == today_str:
                if today_appeared:
                    days_list[idx] = (ds, dl, "#22c55e", "Appeared")
                else:
                    days_list[idx] = (ds, dl, "#ef4444", "No appearance")
                break

    # Stats: appearances in last 30 days
    thirty_days_ago = today - timedelta(days=30)
    appearances_count = sum(
        1 for a in appearances
        if _safe_parse_date(a.get("date", "")) and _safe_parse_date(a["date"]) >= thirty_days_ago
    )

    # Longest gap in last 30 days
    longest_gap = _calc_longest_gap(appearances, today)

    # Build HTML dots using a table row for Outlook compatibility
    dots_html = ""
    for date_str, date_label, color, status in days_list:
        dots_html += (
            f'<td style="padding:0 1px;" title="{date_label}: {status}">'
            f'<div style="width:9px;height:9px;border-radius:50%;background:{color};'
            f'display:block;mso-line-height-rule:exactly;font-size:0;line-height:0;">'
            f'&nbsp;</div></td>'
        )

    # Summary line
    gap_text = f"Longest gap: {longest_gap}d" if longest_gap is not None else "Longest gap: —"
    summary = f"{appearances_count} appearance{'s' if appearances_count != 1 else ''} in last 30 days &nbsp;·&nbsp; {gap_text}"

    html = f"""<div style="margin-top:8px;padding:8px 12px;background:rgba(255,255,255,0.04);border-radius:4px;">
      <div style="font-size:10px;text-transform:uppercase;letter-spacing:0.5px;color:rgba(255,255,255,0.5);margin-bottom:6px;">30-Day Appearance Calendar</div>
      <table cellpadding="0" cellspacing="0" border="0" style="border-collapse:collapse;">
        <tr>{dots_html}</tr>
      </table>
      <div style="margin-top:6px;font-size:10px;color:#AAA;line-height:1.4;">{summary}</div>
      <div style="margin-top:4px;font-size:9px;color:#777;">
        <span style="display:inline-block;width:7px;height:7px;border-radius:50%;background:#22c55e;vertical-align:middle;margin-right:3px;"></span>Appeared
        <span style="display:inline-block;width:7px;height:7px;border-radius:50%;background:#ef4444;vertical-align:middle;margin-left:8px;margin-right:3px;"></span>Absent
        <span style="display:inline-block;width:7px;height:7px;border-radius:50%;background:#d1d5db;vertical-align:middle;margin-left:8px;margin-right:3px;"></span>No data
      </div>
    </div>"""

    return html


def _safe_parse_date(date_str: str):
    """Parse a YYYY-MM-DD string to a date object, or return None."""
    try:
        return datetime.strptime(date_str, "%Y-%m-%d").date()
    except (ValueError, TypeError):
        return None


def _calc_longest_gap(appearances: list, today) -> int | None:
    """Calculate the longest gap (in days) between appearances in the last 30 days."""
    thirty_days_ago = today - timedelta(days=30)

    # Get appearance dates within the last 30 days, sorted
    recent_dates = sorted(
        d for a in appearances
        if (d := _safe_parse_date(a.get("date", ""))) and d >= thirty_days_ago
    )

    if not recent_dates:
        return None

    # Include boundaries: start of window and today
    boundaries = [thirty_days_ago] + recent_dates + [today]
    max_gap = 0
    for i in range(1, len(boundaries)):
        gap = (boundaries[i] - boundaries[i - 1]).days
        if gap > max_gap:
            max_gap = gap

    return max_gap if max_gap > 0 else None


def build_context_block() -> str:
    """Build a context string for the digest prompt with Kim appearance history."""
    recent = get_recent_appearances(10)
    days = days_since_last_appearance()

    if not recent:
        return "No Kim Jong Un appearance records in tracker. Determine from articles."

    lines = ["CONFIRMED KIM JONG UN APPEARANCES (from persistent tracker):"]
    for a in recent:
        lines.append(f"  - {a['date']}: {a['activity']} (source: {a['source']})")

    if days is not None:
        lines.append(f"\nDAYS SINCE LAST CONFIRMED APPEARANCE: {days}")
        lines.append("Use this as ground truth for days_since_last_appearance. Only override if today's articles confirm a MORE RECENT appearance.")
    return "\n".join(lines)
