"""
Korea Daily Brief — Database Integration
Fetches NK-Russia timeline and NK provocations databases from GitHub,
provides historical context for the digest, and pushes new entries back.
"""
import base64
import json
import os
import re
import requests
from datetime import datetime, timezone, timedelta
from typing import Optional

# ─────────────────────────────────────────────────────────────────────────────
# CONFIGURATION
# ─────────────────────────────────────────────────────────────────────────────

GITHUB_TOKEN_ENV = "GITHUB_TOKEN"  # PAT with repo write access

NKR_REPO = "andysaulim/Timeline-of-North-Korea-Russia-Cooperation-since-2022"
NKR_FILE = "index.html"
NKR_RAW_URL = f"https://raw.githubusercontent.com/{NKR_REPO}/main/{NKR_FILE}"

PROV_REPO = "andysaulim/North-Korean-Provocations-since-1958"
PROV_FILE = "index.html"
PROV_RAW_URL = f"https://raw.githubusercontent.com/{PROV_REPO}/main/{PROV_FILE}"

HEADERS = {"User-Agent": "CSISKoreaDigest/1.0"}
TIMEOUT = 15


# ─────────────────────────────────────────────────────────────────────────────
# PARSER: Extract JS arrays from HTML
# ─────────────────────────────────────────────────────────────────────────────

def _strip_js_comments(text: str) -> str:
    """Remove JS block and line comments, but not inside strings."""
    out = []
    i = 0
    n = len(text)
    while i < n:
        # Single-quoted string
        if text[i] == "'":
            j = i + 1
            while j < n:
                if text[j] == "\\" and j + 1 < n:
                    j += 2
                    continue
                if text[j] == "'":
                    j += 1
                    break
                j += 1
            out.append(text[i:j])
            i = j
        # Double-quoted string
        elif text[i] == '"':
            j = i + 1
            while j < n:
                if text[j] == "\\" and j + 1 < n:
                    j += 2
                    continue
                if text[j] == '"':
                    j += 1
                    break
                j += 1
            out.append(text[i:j])
            i = j
        # Block comment
        elif text[i:i+2] == "/*":
            end = text.find("*/", i + 2)
            i = end + 2 if end != -1 else n
        # Line comment
        elif text[i:i+2] == "//":
            end = text.find("\n", i)
            i = end if end != -1 else n
        else:
            out.append(text[i])
            i += 1
    return "".join(out)


def _extract_bracket(html: str, start: int) -> str:
    """Extract a balanced [...] from html starting at position start."""
    depth = 0
    for i in range(start, len(html)):
        if html[i] == "[":
            depth += 1
        elif html[i] == "]":
            depth -= 1
            if depth == 0:
                return html[start:i + 1]
    return ""


def _js_to_json(raw: str) -> str:
    """Convert JS object/array notation to valid JSON.
    Handles: unquoted keys, single-quoted values with escaped quotes,
    trailing commas, JS booleans/null.
    """
    out = []
    i = 0
    n = len(raw)
    while i < n:
        c = raw[i]

        # Skip whitespace
        if c in " \t\n\r":
            out.append(c)
            i += 1
            continue

        # Single-quoted string → double-quoted
        if c == "'":
            i += 1
            s = []
            while i < n and raw[i] != "'":
                if raw[i] == "\\" and i + 1 < n:
                    nxt = raw[i + 1]
                    if nxt == "'":
                        s.append("'")
                        i += 2
                        continue
                    elif nxt == '"':
                        s.append('\\"')
                        i += 2
                        continue
                    else:
                        s.append(raw[i])
                        i += 1
                        continue
                if raw[i] == '"':
                    s.append('\\"')
                else:
                    s.append(raw[i])
                i += 1
            i += 1  # skip closing '
            out.append('"')
            out.append("".join(s))
            out.append('"')
            continue

        # Double-quoted string — pass through as-is
        if c == '"':
            out.append(c)
            i += 1
            while i < n and raw[i] != '"':
                if raw[i] == "\\" and i + 1 < n:
                    out.append(raw[i])
                    out.append(raw[i + 1])
                    i += 2
                    continue
                out.append(raw[i])
                i += 1
            if i < n:
                out.append(raw[i])  # closing "
                i += 1
            continue

        # Unquoted key (word followed by colon)
        if c.isalpha() or c == "_":
            j = i
            while j < n and (raw[j].isalnum() or raw[j] == "_"):
                j += 1
            word = raw[i:j]
            # Check if followed by colon (it's a key)
            k = j
            while k < n and raw[k] in " \t":
                k += 1
            if k < n and raw[k] == ":":
                out.append('"')
                out.append(word)
                out.append('"')
                i = j
            else:
                # It's a bare value: true, false, null
                if word in ("true", "false", "null"):
                    out.append(word)
                else:
                    out.append('"')
                    out.append(word)
                    out.append('"')
                i = j
            continue

        # Trailing comma: skip comma before } or ]
        if c == ",":
            j = i + 1
            while j < n and raw[j] in " \t\n\r":
                j += 1
            if j < n and raw[j] in "}]":
                i = j  # skip the comma
                continue
            out.append(c)
            i += 1
            continue

        # Everything else (digits, braces, brackets, colons, etc.)
        out.append(c)
        i += 1

    return "".join(out)


def _extract_js_array(html: str, var_name: str) -> list:
    """Extract a JavaScript array variable from HTML and parse it."""
    # Flexible match: const/let/var or bare assignment, flexible whitespace
    pattern = rf"(?:const|let|var)?\s*{var_name}\s*=\s*\["
    match = re.search(pattern, html)
    if not match:
        print(f"    ⚠  Could not find {var_name} array in HTML")
        return []

    # Find the opening bracket
    bracket_pos = html.index("[", match.start())
    raw = _extract_bracket(html, bracket_pos)
    if not raw:
        print(f"    ⚠  Could not extract balanced array for {var_name}")
        return []

    # Strip JS comments outside of strings
    raw = _strip_js_comments(raw)

    # Try direct JSON parse first (EVENTS uses valid JSON)
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        pass

    # Convert JS notation to JSON (RECORDS uses single quotes / unquoted keys)
    try:
        converted = _js_to_json(raw)
        return json.loads(converted)
    except json.JSONDecodeError as e:
        print(f"    ⚠  JSON parse error for {var_name}: {e}")
        return []


# ─────────────────────────────────────────────────────────────────────────────
# FETCH DATABASES
# ─────────────────────────────────────────────────────────────────────────────

def fetch_nk_russia_timeline() -> list:
    """Fetch the NK-Russia cooperation timeline from GitHub."""
    try:
        resp = requests.get(NKR_RAW_URL, timeout=TIMEOUT, headers=HEADERS)
        resp.raise_for_status()
        events = _extract_js_array(resp.text, "EVENTS")
        print(f"    NK-Russia timeline: {len(events)} events loaded")
        return events
    except Exception as e:
        print(f"    ⚠  NK-Russia timeline fetch error: {e}")
        return []


def fetch_nk_provocations() -> list:
    """Fetch the NK provocations database from GitHub."""
    try:
        resp = requests.get(PROV_RAW_URL, timeout=TIMEOUT, headers=HEADERS)
        resp.raise_for_status()
        records = _extract_js_array(resp.text, "RECORDS")
        print(f"    NK provocations: {len(records)} records loaded")
        return records
    except Exception as e:
        print(f"    ⚠  NK provocations fetch error: {e}")
        return []


def fetch_all() -> dict:
    """Fetch both databases in parallel. Returns dict with both datasets."""
    from concurrent.futures import ThreadPoolExecutor
    print("  ── Databases")
    with ThreadPoolExecutor(max_workers=2) as pool:
        nkr_f = pool.submit(fetch_nk_russia_timeline)
        prov_f = pool.submit(fetch_nk_provocations)
    return {
        "nk_russia_timeline": nkr_f.result(),
        "nk_provocations": prov_f.result(),
    }


# ─────────────────────────────────────────────────────────────────────────────
# CONTEXT BUILDERS — feed into digest prompt
# ─────────────────────────────────────────────────────────────────────────────

def get_on_this_day(provocations: list, timeline: list,
                    today: Optional[datetime] = None, window_days: int = 3) -> list:
    """
    Find historical events from both databases that occurred on or near
    today's date (same month/day, any year) within a ±window_days range.
    Returns list of dicts for the digest prompt.
    """
    if today is None:
        today = datetime.now(timezone.utc)

    results = []

    # Check provocations
    for rec in provocations:
        date_str = rec.get("date", "")
        if not date_str:
            continue
        try:
            dt = datetime.strptime(date_str, "%Y-%m-%d")
        except (ValueError, TypeError):
            continue
        # Same month/day, within window
        this_year_dt = dt.replace(year=today.year)
        delta = abs((this_year_dt - today.replace(tzinfo=None)).days)
        if delta <= window_days:
            years_ago = today.year - dt.year
            results.append({
                "type": "provocation",
                "date": date_str,
                "years_ago": years_ago,
                "days_until": (this_year_dt - today.replace(tzinfo=None)).days,
                "event": rec.get("event", ""),
                "description": rec.get("desc", "")[:200],
                "category": rec.get("cat", ""),
                "severity": rec.get("sev", 0),
            })

    # Check NK-Russia timeline
    for evt in timeline:
        try:
            y = int(evt.get("year", 0))
            m = int(evt.get("month", 0))
            d = int(evt.get("day", 1))
            if not (y and m):
                continue
            dt = datetime(y, m, d)
        except (ValueError, TypeError):
            continue
        this_year_dt = dt.replace(year=today.year)
        delta = abs((this_year_dt - today.replace(tzinfo=None)).days)
        if delta <= window_days:
            years_ago = today.year - dt.year
            results.append({
                "type": "nk_russia",
                "date": f"{y}-{m:02d}-{d:02d}",
                "years_ago": years_ago,
                "days_until": (this_year_dt - today.replace(tzinfo=None)).days,
                "event": evt.get("headline", ""),
                "description": evt.get("text", "")[:200],
                "category": evt.get("tag", ""),
                "is_landmark": evt.get("is_landmark", False),
            })

    # Sort by proximity to today
    results.sort(key=lambda x: abs(x["days_until"]))
    return results[:10]


def get_recent_timeline_entries(timeline: list, days: int = 30) -> list:
    """Get the most recent NK-Russia timeline entries for context."""
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    recent = []
    for evt in timeline:
        try:
            y = int(evt.get("year", 0))
            m = int(evt.get("month", 0))
            d = int(evt.get("day", 1))
            dt = datetime(y, m, d, tzinfo=timezone.utc)
        except (ValueError, TypeError):
            continue
        if dt >= cutoff:
            recent.append({
                "date": f"{y}-{m:02d}-{d:02d}",
                "headline": evt.get("headline", ""),
                "tag": evt.get("tag", ""),
                "is_landmark": evt.get("is_landmark", False),
            })
    recent.sort(key=lambda x: x["date"], reverse=True)
    return recent[:15]


def get_provocation_stats(provocations: list) -> dict:
    """Get summary stats for provocation database context."""
    total = len(provocations)
    if not total:
        return {}

    # Count by category
    cats = {}
    for rec in provocations:
        cat = rec.get("cat", "other")
        cats[cat] = cats.get(cat, 0) + 1

    # Most recent
    dates = []
    for rec in provocations:
        d = rec.get("date", "")
        if d:
            dates.append(d)
    dates.sort(reverse=True)
    last_provocation = dates[0] if dates else None

    # This year
    current_year = str(datetime.now(timezone.utc).year)
    this_year = sum(1 for rec in provocations if rec.get("date", "").startswith(current_year))

    return {
        "total_provocations": total,
        "by_category": cats,
        "last_provocation_date": last_provocation,
        "this_year_count": this_year,
    }


def build_context_block(databases: dict) -> str:
    """Build a text block for the digest prompt with database context."""
    timeline = databases.get("nk_russia_timeline", [])
    provocations = databases.get("nk_provocations", [])

    parts = []

    # On this day
    otd = get_on_this_day(provocations, timeline)
    if otd:
        otd_json = json.dumps(otd[:6], ensure_ascii=False, indent=1)
        parts.append(f"""ON THIS DAY / UPCOMING ANNIVERSARIES (from CSIS databases):
{otd_json}
Use these for the on_this_day and watch_today fields. Prioritize anniversaries within 3 days. Connect to current events where possible.""")

    # Recent NK-Russia entries
    recent = get_recent_timeline_entries(timeline)
    if recent:
        recent_json = json.dumps(recent, ensure_ascii=False, indent=1)
        parts.append(f"""RECENT NK-RUSSIA TIMELINE ENTRIES (last 30 days from CSIS database):
{recent_json}
Reference these when analyzing new NK-Russia stories. Flag if today's news extends or contradicts these entries.""")

    # Provocation stats
    stats = get_provocation_stats(provocations)
    if stats:
        stats_json = json.dumps(stats, ensure_ascii=False, indent=1)
        parts.append(f"""PROVOCATION DATABASE SUMMARY:
{stats_json}
Use for context. The database tracks {stats.get('total_provocations', 0)} provocations since 1958.""")

    if not parts:
        return ""

    return "\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n".join(parts)


# ─────────────────────────────────────────────────────────────────────────────
# PUSH NEW ENTRIES — append to source repos via GitHub API
# ─────────────────────────────────────────────────────────────────────────────

def _github_api_headers() -> dict:
    token = os.environ.get(GITHUB_TOKEN_ENV)
    if not token:
        return {}
    return {
        "Authorization": f"token {token}",
        "Accept": "application/vnd.github.v3+json",
    }


def _get_file_content(repo: str, path: str) -> tuple[str, str]:
    """Fetch a file from GitHub API. Returns (content, sha)."""
    headers = _github_api_headers()
    if not headers:
        raise RuntimeError("GITHUB_TOKEN not set")
    url = f"https://api.github.com/repos/{repo}/contents/{path}"
    resp = requests.get(url, headers=headers, timeout=TIMEOUT)
    resp.raise_for_status()
    data = resp.json()
    content = base64.b64decode(data["content"]).decode("utf-8")
    return content, data["sha"]


def _put_file_content(repo: str, path: str, content: str, sha: str, message: str):
    """Update a file on GitHub via the API."""
    headers = _github_api_headers()
    if not headers:
        raise RuntimeError("GITHUB_TOKEN not set")
    url = f"https://api.github.com/repos/{repo}/contents/{path}"
    encoded = base64.b64encode(content.encode("utf-8")).decode("utf-8")
    payload = {
        "message": message,
        "content": encoded,
        "sha": sha,
    }
    resp = requests.put(url, headers=headers, json=payload, timeout=TIMEOUT)
    resp.raise_for_status()
    return resp.json()


def push_nk_russia_entry(entry: dict) -> bool:
    """
    Append a new event to the NK-Russia timeline.
    entry should have: headline, text, tag, source (url), year, month, day
    Returns True on success.
    """
    try:
        html, sha = _get_file_content(NKR_REPO, NKR_FILE)

        # Find the EVENTS array and parse existing to get next ID
        events = _extract_js_array(html, "EVENTS")
        next_id = max((e.get("id", 0) for e in events), default=0) + 1

        # Build the new event JS object
        new_event = {
            "id": next_id,
            "year": entry.get("year", datetime.now().year),
            "month": entry.get("month", datetime.now().month),
            "day": entry.get("day", datetime.now().day),
            "headline": entry.get("headline", ""),
            "text": entry.get("text", ""),
            "media": entry.get("media", ""),
            "thumb": entry.get("thumb", ""),
            "caption": entry.get("caption", ""),
            "credit": entry.get("credit", "Auto-flagged by Korea Daily Brief"),
            "source": entry.get("source", ""),
            "tag": entry.get("tag", "military"),
            "src_type": entry.get("src_type", "press"),
            "phase": entry.get("phase", 3),
            "is_landmark": entry.get("is_landmark", False),
            "landmark_why": entry.get("landmark_why", ""),
            "figures": entry.get("figures", []),
        }

        # Convert to JS-style string
        js_obj = json.dumps(new_event, ensure_ascii=False, indent=4)

        # Insert before the closing ]; of the EVENTS array
        # Find the last ] that closes the array
        pattern = r"(const\s+EVENTS\s*=\s*\[.*?)(];)"
        match = re.search(pattern, html, re.DOTALL)
        if not match:
            print("    ⚠  Could not locate EVENTS array for insertion")
            return False

        insert_pos = match.end(1)
        # Add comma if the array isn't empty
        if events:
            new_html = html[:insert_pos] + ",\n    " + js_obj + "\n" + html[match.start(2):]
        else:
            new_html = html[:insert_pos] + "\n    " + js_obj + "\n" + html[match.start(2):]

        _put_file_content(
            NKR_REPO, NKR_FILE, new_html, sha,
            f"Auto-add: {entry.get('headline', 'New event')[:60]} [Korea Daily Brief]"
        )
        print(f"    ✅  NK-Russia timeline: added event #{next_id}")
        return True

    except Exception as e:
        print(f"    ⚠  NK-Russia push error: {e}")
        return False


def push_provocation_entry(entry: dict) -> bool:
    """
    Append a new record to the NK provocations database.
    entry should have: date (YYYY-MM-DD), cat, event, desc, sev, source, url
    Returns True on success.
    """
    try:
        html, sha = _get_file_content(PROV_REPO, PROV_FILE)

        # Build new record JS object
        new_record = {
            "date": entry.get("date", datetime.now().strftime("%Y-%m-%d")),
            "cat": entry.get("cat", "other"),
            "event": entry.get("event", ""),
            "desc": entry.get("desc", ""),
            "sev": entry.get("sev", 3),
            "source": entry.get("source", "Korea Daily Brief"),
            "sourceType": entry.get("sourceType", "digest"),
            "url": entry.get("url", ""),
            "leader": "kim-jong-un",
        }

        js_obj = json.dumps(new_record, ensure_ascii=False, indent=4)

        # Insert before closing ]; of RECORDS array
        pattern = r"(const\s+RECORDS\s*=\s*\[.*?)(];)"
        match = re.search(pattern, html, re.DOTALL)
        if not match:
            print("    ⚠  Could not locate RECORDS array for insertion")
            return False

        insert_pos = match.end(1)
        new_html = html[:insert_pos] + ",\n    " + js_obj + "\n" + html[match.start(2):]

        _put_file_content(
            PROV_REPO, PROV_FILE, new_html, sha,
            f"Auto-add: {entry.get('event', 'New provocation')[:60]} [Korea Daily Brief]"
        )
        print(f"    ✅  Provocations DB: added '{entry.get('event', '')[:50]}'")
        return True

    except Exception as e:
        print(f"    ⚠  Provocations push error: {e}")
        return False


def process_digest_entries(digest: dict) -> dict:
    """
    After digest generation, process flagged entries:
    - timeline_candidates → push to NK-Russia timeline
    - ESCALATION + DPRK → push to provocations database
    Returns summary of what was pushed.
    """
    token = os.environ.get(GITHUB_TOKEN_ENV)
    if not token:
        print("  ── Database push: skipped (no GITHUB_TOKEN)")
        return {"nk_russia_added": 0, "provocations_added": 0}

    print("  ── Database push")
    nkr_added = 0
    prov_added = 0

    # Collect all articles from the digest for reference
    all_stories = []
    for key in ("top_stories", "also_today", "overnight_items"):
        all_stories.extend(digest.get(key) or [])

    story_by_url = {s.get("url", ""): s for s in all_stories if s.get("url")}

    # NK-Russia timeline candidates
    candidates = digest.get("timeline_candidates") or []
    for url in candidates:
        story = story_by_url.get(url, {})
        if not story:
            continue

        # Parse date from digest_date
        now = datetime.now(timezone.utc)
        entry = {
            "year": now.year,
            "month": now.month,
            "day": now.day,
            "headline": story.get("headline", story.get("translated_title", "")),
            "text": story.get("body", story.get("body_text", story.get("summary", ""))),
            "source": url,
            "tag": _infer_nkr_tag(story),
            "src_type": "press",
            "phase": 3,
            "is_landmark": False,
            "figures": [],
        }
        if push_nk_russia_entry(entry):
            nkr_added += 1

    # DPRK Escalations → provocations
    for story in all_stories:
        signal = story.get("signal_type", "")
        cats = story.get("categories", story.get("category", ""))
        if isinstance(cats, list):
            cats = " ".join(cats)
        cats = str(cats).upper()

        if signal == "ESCALATION" and "DPRK" in cats:
            now = datetime.now(timezone.utc)
            entry = {
                "date": now.strftime("%Y-%m-%d"),
                "cat": _infer_prov_cat(story),
                "event": story.get("headline", story.get("translated_title", "")),
                "desc": story.get("body", story.get("body_text", story.get("summary", "")))[:300],
                "sev": _infer_severity(story),
                "source": story.get("source", ""),
                "url": story.get("url", ""),
            }
            if push_provocation_entry(entry):
                prov_added += 1

    summary = {"nk_russia_added": nkr_added, "provocations_added": prov_added}
    print(f"     Added: {nkr_added} NK-Russia, {prov_added} provocations")
    return summary


def _story_text(story: dict) -> str:
    """Extract searchable text from a story's key fields (avoids serializing the whole dict)."""
    return " ".join(
        str(story.get(f, ""))
        for f in ("headline", "translated_title", "body", "body_text", "summary", "categories", "category")
    ).lower()


def _infer_nkr_tag(story: dict) -> str:
    """Infer NK-Russia timeline tag from story content."""
    text = _story_text(story)
    if any(w in text for w in ("weapon", "arms", "ammunition", "missile", "artillery")):
        return "military"
    if any(w in text for w in ("diplomat", "summit", "visit", "meeting", "foreign minister")):
        return "diplomatic"
    if any(w in text for w in ("trade", "economic", "oil", "coal", "labor")):
        return "economic"
    if any(w in text for w in ("satellite", "cyber", "technology", "nuclear")):
        return "technology"
    if any(w in text for w in ("intelligence", "spy", "espionage")):
        return "intelligence"
    return "military"


def _infer_prov_cat(story: dict) -> str:
    """Infer provocation category from story content."""
    text = _story_text(story)
    if any(w in text for w in ("missile", "icbm", "ballistic", "hwasong", "launch")):
        return "missile"
    if any(w in text for w in ("nuclear test", "punggye", "underground")):
        return "nuclear-test"
    if any(w in text for w in ("nuclear", "yongbyon", "enrichment", "plutonium")):
        return "nuclear-program"
    if any(w in text for w in ("cyber", "hack", "ransomware")):
        return "cyber"
    if any(w in text for w in ("artillery", "shelling", "drone", "incursion")):
        return "conventional"
    return "other"


def _infer_severity(story: dict) -> int:
    """Infer provocation severity (1-5) from story context."""
    text = _story_text(story)
    if any(w in text for w in ("nuclear test", "icbm")):
        return 5
    if any(w in text for w in ("ballistic missile", "submarine launch", "slbm")):
        return 4
    if any(w in text for w in ("missile", "rocket", "satellite launch")):
        return 3
    if any(w in text for w in ("artillery", "drone", "cyber attack")):
        return 2
    return 2


# ─────────────────────────────────────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    dbs = fetch_all()
    otd = get_on_this_day(dbs["nk_provocations"], dbs["nk_russia_timeline"])
    print(f"\n  On this day ({len(otd)} events):")
    for e in otd:
        print(f"    {e['date']} ({e['years_ago']}y ago): {e['event'][:80]}")

    recent = get_recent_timeline_entries(dbs["nk_russia_timeline"])
    print(f"\n  Recent NK-Russia entries ({len(recent)}):")
    for e in recent:
        print(f"    {e['date']}: {e['headline'][:80]}")

    stats = get_provocation_stats(dbs["nk_provocations"])
    print(f"\n  Provocation stats: {json.dumps(stats, indent=2)}")
