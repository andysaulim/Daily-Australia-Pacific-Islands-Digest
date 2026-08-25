"""
Peninsula Tension Index Scorer
Computes a daily 0-10 tension score from digest output and tracker data.
Persists 30 days of history for trend analysis.
"""
import json
import re
from pathlib import Path
from datetime import datetime, timezone, timedelta

TRACKER_FILE = Path(__file__).parent / "tension_tracker.json"

# Level thresholds
LEVELS = [
    (0, 2, "LOW"),
    (3, 4, "GUARDED"),
    (5, 6, "ELEVATED"),
    (7, 8, "HIGH"),
    (9, 10, "CRITICAL"),
]


def _load() -> dict:
    """Load tension history from disk."""
    if TRACKER_FILE.exists():
        try:
            return json.loads(TRACKER_FILE.read_text())
        except (json.JSONDecodeError, IOError):
            pass
    return {"history": [], "last_updated": None}


def _save(data: dict):
    """Save tension history to disk, keeping last 30 days."""
    data["last_updated"] = datetime.now(timezone.utc).isoformat()
    # Trim to 30 entries
    data["history"] = data["history"][-30:]
    TRACKER_FILE.write_text(json.dumps(data, indent=2, ensure_ascii=False))


def _get_level(score: float) -> str:
    """Map numeric score to threat level string."""
    rounded = round(score)
    for low, high, label in LEVELS:
        if low <= rounded <= high:
            return label
    return "CRITICAL"


def _get_trend(history: list) -> str:
    """Determine trend from recent history (last 3 entries)."""
    if len(history) < 2:
        return "STABLE"
    recent_scores = [entry["score"] for entry in history[-3:]]
    if len(recent_scores) < 2:
        return "STABLE"
    diff = recent_scores[-1] - recent_scores[0]
    if diff >= 1.5:
        return "RISING"
    elif diff <= -1.5:
        return "FALLING"
    return "STABLE"


def _score_kcna_rhetoric(digest: dict) -> tuple:
    """Score KCNA rhetoric intensity (0-2 pts)."""
    kcna = digest.get("kcna_delta") or {}
    score = 0.0
    details = []

    if kcna.get("watch_flag"):
        score += 1.0
        details.append("watch_flag=true (+1)")
    if kcna.get("silence_today"):
        score += 1.0
        details.append("silence_today=true (+1)")
    output_volume = str(kcna.get("output_volume", "")).lower()
    if "heavy" in output_volume:
        score += 0.5
        details.append("output_volume=Heavy (+0.5)")

    return min(score, 2.0), details


def _score_kim_absence(digest: dict) -> tuple:
    """Score Kim Jong Un absence duration (0-2 pts)."""
    kcna = digest.get("kcna_delta") or {}
    days = kcna.get("days_since_last_appearance")
    score = 0.0
    details = []

    if days is None:
        return 0.0, ["days_since_last_appearance not available"]

    if isinstance(days, str):
        try:
            days = int(days)
        except (ValueError, TypeError):
            return 0.0, [f"days_since_last_appearance unparseable: {days}"]

    if days >= 10:
        score = 2.0
        details.append(f"{days} days absent (+2)")
    elif days >= 7:
        score = 1.0
        details.append(f"{days} days absent (+1)")
    elif days >= 4:
        score = 0.5
        details.append(f"{days} days absent (+0.5)")
    else:
        details.append(f"{days} days absent (normal)")

    return score, details


def _score_bp_facilities(digest: dict) -> tuple:
    """Score Beyond Parallel facility status (0-2 pts)."""
    locations = digest.get("bp_locations") or []
    elevated_count = sum(
        1 for loc in locations
        if str(loc.get("status", "")).lower() == "elevated"
    )
    score = 0.0
    details = []

    if elevated_count >= 3:
        score = 2.0
    elif elevated_count == 2:
        score = 1.0
    elif elevated_count == 1:
        score = 0.5

    details.append(f"{elevated_count} facilities at elevated status (+{score})")
    return score, details


def _score_story_escalation(digest: dict) -> tuple:
    """Score story escalation signals (0-2 pts)."""
    escalation_count = 0

    # Check top_stories
    for story in (digest.get("top_stories") or []):
        signal = str(story.get("signal_type", "")).upper()
        categories = [c.upper() for c in (story.get("categories") or [])]
        if "ESCALATION" in signal or "DPRK" in categories:
            escalation_count += 1

    # Check overnight_items
    for item in (digest.get("overnight_items") or []):
        signal = str(item.get("signal_type", "")).upper()
        categories = [c.upper() for c in (item.get("categories") or [])]
        if "ESCALATION" in signal or "DPRK" in categories:
            escalation_count += 1

    score = 0.0
    if escalation_count >= 3:
        score = 2.0
    elif escalation_count == 2:
        score = 1.0
    elif escalation_count == 1:
        score = 0.5

    details = [f"{escalation_count} escalation/DPRK stories (+{score})"]
    return score, details


def _score_military_exercise(digest: dict) -> tuple:
    """Score military exercise proximity (0-1 pt)."""
    calendar = digest.get("calendar_watch") or []
    score = 0.0
    details = []

    # calendar_watch may be a list of events or a dict
    if isinstance(calendar, dict):
        calendar = [calendar]

    exercise_keywords = ["exercise", "drill", "maneuver", "wargame", "ulchi",
                         "freedom shield", "vigilant", "foal eagle", "max thunder"]

    today = datetime.now(timezone.utc).date()

    for event in calendar:
        event_text = str(event.get("event", "") or event.get("title", "")).lower()
        date_str = str(event.get("date", "") or event.get("start_date", ""))

        is_exercise = any(kw in event_text for kw in exercise_keywords)
        if not is_exercise:
            continue

        # Try to parse date and check if within 7 days
        event_date = None
        for fmt in ("%Y-%m-%d", "%B %d, %Y", "%d %B %Y"):
            try:
                event_date = datetime.strptime(date_str, fmt).date()
                break
            except ValueError:
                continue

        if event_date:
            delta = abs((event_date - today).days)
            if delta <= 7:
                score = 1.0
                details.append(f"exercise within 7 days: {event_text} ({date_str}) (+1)")
                break
        else:
            # If we can't parse the date but it's in calendar_watch, assume proximity
            score = 1.0
            details.append(f"exercise noted in calendar: {event_text} (+1)")
            break

    if score == 0.0:
        details.append("no exercises within 7 days")

    return score, details


def _score_maritime_nll(digest: dict) -> tuple:
    """Score maritime/NLL incidents (0-1 pt)."""
    nll_pattern = re.compile(r"NLL|Northern Limit Line|maritime incident", re.IGNORECASE)
    score = 0.0
    details = []

    # Search through all stories and items
    all_items = (digest.get("top_stories") or []) + (digest.get("overnight_items") or [])

    for item in all_items:
        text_fields = [
            str(item.get("headline", "")),
            str(item.get("title", "")),
            str(item.get("summary", "")),
            str(item.get("description", "")),
        ]
        combined = " ".join(text_fields)
        if nll_pattern.search(combined):
            score = 1.0
            details.append(f"NLL/maritime reference found: {item.get('headline', item.get('title', 'unknown'))} (+1)")
            break

    if score == 0.0:
        details.append("no NLL/maritime incidents detected")

    return score, details


def score_tension(digest: dict) -> dict:
    """
    Compute the Peninsula Tension Index from a digest.

    Returns:
        dict with keys: score (float), components (dict), level (str), trend (str)
    """
    components = {}

    kcna_score, kcna_details = _score_kcna_rhetoric(digest)
    components["kcna_rhetoric"] = {"score": kcna_score, "max": 2.0, "details": kcna_details}

    kim_score, kim_details = _score_kim_absence(digest)
    components["kim_absence"] = {"score": kim_score, "max": 2.0, "details": kim_details}

    bp_score, bp_details = _score_bp_facilities(digest)
    components["bp_facilities"] = {"score": bp_score, "max": 2.0, "details": bp_details}

    escalation_score, escalation_details = _score_story_escalation(digest)
    components["story_escalation"] = {"score": escalation_score, "max": 2.0, "details": escalation_details}

    exercise_score, exercise_details = _score_military_exercise(digest)
    components["military_exercise"] = {"score": exercise_score, "max": 1.0, "details": exercise_details}

    nll_score, nll_details = _score_maritime_nll(digest)
    components["maritime_nll"] = {"score": nll_score, "max": 1.0, "details": nll_details}

    total = kcna_score + kim_score + bp_score + escalation_score + exercise_score + nll_score
    total = min(total, 10.0)

    # Get trend from history
    data = _load()
    trend = _get_trend(data["history"])

    return {
        "score": round(total, 1),
        "components": components,
        "level": _get_level(total),
        "trend": trend,
    }


def build_sparkline(history: list) -> str:
    """
    Build an inline SVG sparkline from up to 14 data points.

    Args:
        history: list of float scores (0-10 scale)

    Returns:
        Inline SVG string (120px wide, 24px tall)
    """
    # Take last 14 points
    points = history[-14:] if len(history) > 14 else history

    if not points:
        return '<svg width="120" height="24" xmlns="http://www.w3.org/2000/svg"></svg>'

    width = 120
    height = 24
    padding = 2
    usable_width = width - 2 * padding
    usable_height = height - 2 * padding

    # Calculate x positions
    if len(points) == 1:
        x_positions = [width / 2]
    else:
        x_step = usable_width / (len(points) - 1)
        x_positions = [padding + i * x_step for i in range(len(points))]

    # Calculate y positions (inverted: high score = top of chart)
    max_val = 10.0
    y_positions = [padding + usable_height - (p / max_val * usable_height) for p in points]

    # Build polyline points string
    coords = " ".join(f"{x:.1f},{y:.1f}" for x, y in zip(x_positions, y_positions))

    # Color based on last value
    last_val = points[-1]
    if last_val >= 7:
        color = "#dc2626"  # red
    elif last_val >= 5:
        color = "#f59e0b"  # amber
    elif last_val >= 3:
        color = "#eab308"  # yellow
    else:
        color = "#22c55e"  # green

    svg = (
        f'<svg width="{width}" height="{height}" xmlns="http://www.w3.org/2000/svg">'
        f'<polyline points="{coords}" fill="none" stroke="{color}" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"/>'
        f'<circle cx="{x_positions[-1]:.1f}" cy="{y_positions[-1]:.1f}" r="2" fill="{color}"/>'
        f'</svg>'
    )

    return svg


def update_from_digest(digest: dict):
    """Score the digest and persist the result to tension_tracker.json."""
    result = score_tension(digest)
    data = _load()

    # Determine date for this entry
    date_str = digest.get("digest_date", datetime.now(timezone.utc).strftime("%Y-%m-%d"))
    # Normalize date format
    for fmt in ("%Y-%m-%d", "%A, %B %d, %Y", "%A, %d %B %Y", "%d %B %Y", "%B %d, %Y"):
        try:
            parsed = datetime.strptime(date_str, fmt)
            date_str = parsed.strftime("%Y-%m-%d")
            break
        except ValueError:
            continue

    # Avoid duplicate entries for the same date
    existing_dates = {entry["date"] for entry in data["history"]}
    if date_str in existing_dates:
        # Update existing entry
        data["history"] = [
            entry if entry["date"] != date_str
            else {"date": date_str, "score": result["score"], "level": result["level"]}
            for entry in data["history"]
        ]
    else:
        data["history"].append({
            "date": date_str,
            "score": result["score"],
            "level": result["level"],
        })

    _save(data)
    return result


def build_context_block() -> str:
    """
    Build a context string for injection into the digest prompt.
    Shows last 7 days of tension scores with sparkline.
    """
    data = _load()
    history = data.get("history", [])

    if not history:
        return "PENINSULA TENSION INDEX: No history available yet."

    recent = history[-7:]
    scores = [entry["score"] for entry in history]
    sparkline = build_sparkline(scores)

    lines = ["PENINSULA TENSION INDEX (last 7 days):"]
    for entry in recent:
        lines.append(f"  - {entry['date']}: {entry['score']}/10 [{entry['level']}]")

    # Current trend
    trend = _get_trend(history)
    lines.append(f"\nTrend: {trend}")
    lines.append(f"Sparkline (last 14 days): {sparkline}")

    return "\n".join(lines)
