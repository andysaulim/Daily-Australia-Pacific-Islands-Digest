"""
Korea Daily Brief — Week in Review
Synthesizes Saturday-through-Friday daily digests into a "Top 10" weekly edition.
Run: python weekly.py [--no-send]
Triggered Fridays at 5:00 PM ET via GitHub Actions, or manually.
"""
import json
import os
import time
from pathlib import Path
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import anthropic
import httpx


WEEKLY_SYSTEM_PROMPT = """You are the senior intelligence analyst for the CSIS Korea Chair. You produce the Korea Daily Brief — Week in Review edition, a concise synthesis of the week's Korea-related developments for senior policymakers.

Your readers are experts who received the daily briefs but want a consolidated weekend read highlighting what mattered most this week. Be ruthlessly concise — they've already seen the details. Your job is synthesis, pattern recognition, and forward-looking assessment.

RULES:
- Never fabricate. Every claim must trace to the daily digest data provided.
- No editorializing. Present patterns and let readers draw conclusions.
- Highlight what CHANGED this week, not what remained stable.
- Return ONLY valid JSON. No markdown fences, no preamble."""


WEEKLY_USER_PROMPT_TEMPLATE = """Today is {date_str} (Friday). Synthesize this week's daily digests (Saturday through Friday) into a Week in Review.

DAILY DIGESTS THIS WEEK:
{digests_json}

Return a JSON object with:
- week_label: string (e.g. "May 17-23, 2026")
- re_line: 1-sentence summary of the week's most important development (under 80 chars)
- top_10: array of the 10 most consequential stories this week, ranked by significance. Each: rank (1-10), headline (concise, factual), body (2-3 sentences synthesizing the week's coverage of this story), first_reported (date string), category (e.g. "Security", "Diplomacy", "Economy", "DPRK", "US-ROK", "Trade"), sources (array of outlet names that covered this story). If fewer than 10 consequential stories occurred, return as many as the data supports — do not pad with trivial items.
- dprk_statements: object summarizing the week's DPRK official statements — kim_appearances (count of days Kim appeared), notable_quotes (up to 3 most significant official quotes from the week, each with speaker and quote text), watch_flags (count of days with watch flag), silence_days (count of days with KCNA silence), summary (2-3 sentences on the week's official posture)
- bp_changes: array of facility status changes this week (only facilities whose status or note changed). Each: name, status_start, status_end, change_summary (1 sentence)
- market_weekly: object with kospi_open (Monday value), kospi_close (Friday value), kospi_change_pct (string e.g. "+1.2%"), krw_open (Monday), krw_close (Friday), krw_change_pct (string), bok_action (null or description of any rate decision)
- sentiment_weekly: object with approval_start (Monday presidential approval %), approval_end (Friday), party_ruling (latest %), party_opposition (latest %), source (e.g. "Gallup Korea")
- calendar_next_week: array of 3-5 key events in the coming 7 days. Each: date, headline, detail (1 sentence)
- bottom_line: 2-3 sentences. The single most important takeaway from this week and what to watch next week. Be ruthlessly concise.
- story_count_total: total Tier 1 articles processed across all daily digests this week
"""


def _load_week_digests() -> list[dict]:
    """Load daily digest JSON files from Saturday through Friday (today)."""
    tz = ZoneInfo("America/New_York")
    today = datetime.now(tz).date()
    # Friday = weekday 4. Find the preceding Saturday (weekday 5).
    # If today is Friday, last Saturday is 6 days ago.
    days_since_saturday = (today.weekday() - 5) % 7
    if days_since_saturday == 0:
        days_since_saturday = 7
    start_date = today - timedelta(days=days_since_saturday)

    digests = []
    d = start_date
    while d <= today:
        date_slug = d.strftime("%Y-%m-%d")
        for pattern in [f"digest_{date_slug}.json", f"public/digest_{date_slug}.json"]:
            path = Path(pattern)
            if path.exists():
                try:
                    data = json.loads(path.read_text(encoding="utf-8"))
                    data["_date"] = date_slug
                    digests.append(data)
                except (json.JSONDecodeError, IOError):
                    continue
                break
        d += timedelta(days=1)
    return digests


def _summarize_digest(d: dict) -> dict:
    """Extract key fields from a daily digest for the weekly prompt."""
    return {
        "date": d.get("_date", "unknown"),
        "re_line": d.get("re_line", ""),
        "top_stories": [
            {"headline": s.get("headline", ""), "category": s.get("category_tag", ""),
             "source": s.get("source", ""), "body": s.get("body", s.get("body_text", ""))[:200]}
            for s in (d.get("top_stories") or [])
        ],
        "overnight_headlines": [
            {"headline": s.get("headline", ""), "source": s.get("source", "")}
            for s in (d.get("overnight_items") or [])
        ],
        "also_today_headlines": [s.get("headline", "") for s in (d.get("also_today") or [])],
        "kcna_delta": {
            "silence_today": (d.get("kcna_delta") or {}).get("silence_today"),
            "watch_flag": (d.get("kcna_delta") or {}).get("watch_flag"),
            "bottom_line": (d.get("kcna_delta") or {}).get("bottom_line"),
            "kim_appearance_today": (d.get("kcna_delta") or {}).get("kim_appearance_today"),
            "key_quotes": (d.get("kcna_delta") or {}).get("key_quotes") or [],
        },
        "bp_locations": [
            {"name": loc.get("name"), "status": loc.get("status"), "note": loc.get("note", "")[:100]}
            for loc in (d.get("bp_locations") or [])
            if loc.get("status") in ("elevated", "alert")
        ],
        "market_indicators": {
            "kospi": (d.get("market_indicators") or {}).get("kospi"),
            "krw_usd": (d.get("market_indicators") or {}).get("krw_usd"),
        },
        "sentiment": {
            "presidential_approval": (d.get("sentiment") or {}).get("presidential_approval"),
            "party_ruling": (d.get("sentiment") or {}).get("party_ruling"),
            "party_opposition": (d.get("sentiment") or {}).get("party_opposition"),
        },
        "deals": [
            {"company": deal.get("company"), "value": deal.get("value"), "sector": deal.get("sector")}
            for deal in ((d.get("us_korea_deals") or {}).get("investment_package") or {}).get("known_deals") or []
        ],
        "northeast_asia": [
            {"headline": s.get("headline", ""), "source": s.get("source", "")}
            for s in (d.get("northeast_asia") or [])[:3]
        ],
        "business_economy": [
            {"headline": s.get("headline", "")}
            for s in (d.get("business_economy") or [])[:3]
        ],
        "calendar_watch": d.get("calendar_watch") or [],
        "story_count": d.get("story_count", 0),
    }


def generate_weekly(digests: list[dict]) -> dict:
    """Generate weekly summary via Claude API."""
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        raise RuntimeError("Missing ANTHROPIC_API_KEY")
    client = anthropic.Anthropic(api_key=api_key)

    summaries = [_summarize_digest(d) for d in digests]
    tz = ZoneInfo("America/New_York")
    date_str = datetime.now(tz).strftime("%A, %B %-d, %Y")

    user_prompt = WEEKLY_USER_PROMPT_TEMPLATE.format(
        date_str=date_str,
        digests_json=json.dumps(summaries, ensure_ascii=False, indent=1),
    )

    print(f"\n🤖  Generating weekly summary ({len(digests)} daily digests)...")
    t0 = time.time()
    collected = []
    prefill = '{"'
    with client.messages.stream(
        model="claude-sonnet-4-20250514",
        max_tokens=6000,
        system=[{
            "type": "text",
            "text": WEEKLY_SYSTEM_PROMPT,
            "cache_control": {"type": "ephemeral"},
        }],
        messages=[
            {"role": "user", "content": user_prompt},
            {"role": "assistant", "content": prefill},
        ],
    ) as stream:
        for text in stream.text_stream:
            collected.append(text)
    elapsed = time.time() - t0
    raw_text = prefill + "".join(collected)
    print(f"    ⏱  Weekly generation: {elapsed:.0f}s")

    text = raw_text.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[1] if "\n" in text else text[3:]
    if text.endswith("```"):
        text = text[:-3]
    text = text.strip()

    return json.loads(text)


def render_weekly(weekly: dict) -> str:
    """Render weekly summary as HTML email matching daily digest design."""
    from html import escape as _esc
    week_label = _esc(weekly.get("week_label", "This Week"))
    re_line = _esc(weekly.get("re_line", ""))
    bottom_line = _esc(weekly.get("bottom_line", ""))

    # Top 10 stories
    top_html = ""
    for story in (weekly.get("top_10") or []):
        rank = story.get("rank", "")
        headline = _esc(story.get("headline", ""))
        body = _esc(story.get("body", ""))
        category = _esc(story.get("category", ""))
        sources = ", ".join(_esc(s) for s in (story.get("sources") or []))
        top_html += f"""
        <tr><td style="padding:14px 0;border-bottom:1px solid #EBEBEB;">
            <table cellpadding="0" cellspacing="0" border="0"><tr>
                <td style="vertical-align:top;padding-right:12px;">
                    <div style="background:#1B2A4A;color:#fff;border-radius:50%;width:26px;height:26px;text-align:center;line-height:26px;font-size:12px;font-weight:700;">{rank}</div>
                </td>
                <td>
                    <div style="font-size:14px;font-weight:700;color:#1B2A4A;line-height:1.3;">{headline}</div>
                    <div style="font-size:10px;color:#D4AC0D;text-transform:uppercase;letter-spacing:1px;margin-top:2px;">{category}</div>
                    <div style="font-size:13px;color:#555;line-height:1.5;margin-top:6px;">{body}</div>
                    <div style="font-size:10px;color:#999;margin-top:4px;">{sources}</div>
                </td>
            </tr></table>
        </td></tr>"""

    # DPRK statements summary
    dprk = weekly.get("dprk_statements") or {}
    dprk_html = ""
    if dprk:
        kim_ct = dprk.get("kim_appearances", 0)
        watch_ct = dprk.get("watch_flags", 0)
        silence_ct = dprk.get("silence_days", 0)
        summary = _esc(dprk.get("summary", ""))
        quotes_html = ""
        for q in (dprk.get("notable_quotes") or [])[:3]:
            speaker = _esc(q.get("speaker", ""))
            quote = _esc(q.get("quote", ""))
            if quote:
                quotes_html += f"""
                <div style="padding:8px 12px;background:rgba(255,255,255,0.04);border-radius:4px;border-left:3px solid #C9A96E;margin-top:8px;">
                    <div style="font-size:12px;color:#E8E8E8;font-style:italic;">&ldquo;{quote}&rdquo;</div>
                    <div style="font-size:10px;color:#C9A96E;margin-top:3px;">{speaker}</div>
                </div>"""
        dprk_html = f"""
        <table width="100%" cellpadding="0" cellspacing="0" border="0" style="background:#0F1A12;border-radius:4px;margin-top:16px;">
            <tr><td style="padding:14px 20px;">
                <div style="font-size:10px;font-weight:700;text-transform:uppercase;letter-spacing:2px;color:#C9A96E;margin-bottom:10px;">DPRK Official Statements — Week Summary</div>
                <div style="font-size:11px;color:#AAA;margin-bottom:8px;">Kim appearances: {kim_ct} &nbsp;·&nbsp; Watch flags: {watch_ct} &nbsp;·&nbsp; Silence days: {silence_ct}</div>
                <div style="font-size:13px;color:#E0E0E0;line-height:1.5;">{summary}</div>
                {quotes_html}
            </td></tr>
        </table>"""

    # Calendar next week
    cal_html = ""
    for event in (weekly.get("calendar_next_week") or []):
        date = _esc(event.get("date", ""))
        headline = _esc(event.get("headline", ""))
        detail = _esc(event.get("detail", ""))
        cal_html += f"""
        <tr><td style="padding:8px 0;border-bottom:1px solid #EBEBEB;font-size:13px;">
            <strong style="color:#1B2A4A;">{date}</strong> — {headline}
            <div style="font-size:11px;color:#888;margin-top:2px;">{detail}</div>
        </td></tr>"""

    # Market weekly
    mkt = weekly.get("market_weekly") or {}
    mkt_html = ""
    if mkt:
        kospi_chg = _esc(str(mkt.get("kospi_change_pct", "—")))
        krw_chg = _esc(str(mkt.get("krw_change_pct", "—")))
        bok = _esc(mkt.get("bok_action") or "No change")
        mkt_html = f"""
        <table width="100%" cellpadding="0" cellspacing="0" border="0" style="background:#F8F9FA;border-radius:4px;margin-top:16px;">
            <tr>
                <td style="padding:12px 16px;text-align:center;width:33%;">
                    <div style="font-size:10px;text-transform:uppercase;letter-spacing:1px;color:#888;">KOSPI</div>
                    <div style="font-size:16px;font-weight:700;color:#1B2A4A;">{kospi_chg}</div>
                </td>
                <td style="padding:12px 16px;text-align:center;width:33%;border-left:1px solid #EBEBEB;border-right:1px solid #EBEBEB;">
                    <div style="font-size:10px;text-transform:uppercase;letter-spacing:1px;color:#888;">KRW/USD</div>
                    <div style="font-size:16px;font-weight:700;color:#1B2A4A;">{krw_chg}</div>
                </td>
                <td style="padding:12px 16px;text-align:center;width:33%;">
                    <div style="font-size:10px;text-transform:uppercase;letter-spacing:1px;color:#888;">BOK</div>
                    <div style="font-size:13px;color:#1B2A4A;">{bok}</div>
                </td>
            </tr>
        </table>"""

    tz = ZoneInfo("America/New_York")
    gen_time = datetime.now(tz).strftime("%-I:%M %p ET")
    story_count = weekly.get("story_count_total", 0)

    return f"""<!DOCTYPE html>
<html lang="en" xmlns="http://www.w3.org/1999/xhtml">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Korea Daily Brief — Week in Review · {week_label}</title>
<style type="text/css">
@media screen and (max-width: 600px) {{
    .wrapper {{ width: 100% !important; }}
    .sec {{ padding: 16px 14px !important; }}
}}
</style>
</head>
<body style="margin:0;padding:0;background:#F0F0F0;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Helvetica,Arial,sans-serif;">
<table width="100%" cellpadding="0" cellspacing="0" border="0" style="background:#F0F0F0;">
<tr><td align="center" style="padding:20px 0;">
<table class="wrapper" width="640" cellpadding="0" cellspacing="0" border="0" style="background:#FFFFFF;border-radius:4px;overflow:hidden;">

<!-- Header -->
<tr><td bgcolor="#0D1B2A" style="background-color:#0D1B2A;padding:28px 32px;text-align:center;">
    <div style="font-size:9px;text-transform:uppercase;letter-spacing:3px;color:rgba(255,255,255,0.5);font-family:Arial,sans-serif;">CSIS Korea Chair</div>
    <div style="font-size:24px;font-weight:700;color:#FFFFFF;margin:8px 0 4px;font-family:Georgia,serif;">Week in Review</div>
    <div style="font-size:14px;color:rgba(255,255,255,0.6);">{week_label}</div>
    <div style="height:2px;background:#C9A96E;width:60px;margin:14px auto 0;"></div>
</td></tr>

<!-- RE: line -->
<tr><td style="padding:20px 32px;border-bottom:1px solid #EBEBEB;">
    <div style="font-size:11px;text-transform:uppercase;letter-spacing:1px;color:#C9A96E;font-weight:700;margin-bottom:6px;">RE:</div>
    <div style="font-size:15px;color:#1B2A4A;font-weight:600;line-height:1.4;">{re_line}</div>
    <div style="font-size:10px;color:#999;margin-top:6px;">{story_count} articles processed this week</div>
</td></tr>

<!-- Top 10 -->
<tr><td style="padding:20px 32px;border-bottom:1px solid #EBEBEB;">
    <div style="font-size:10px;font-weight:700;text-transform:uppercase;letter-spacing:2px;color:#1B2A4A;font-family:Arial,sans-serif;margin-bottom:14px;padding-bottom:8px;border-bottom:2px solid #1B2A4A;">Top 10 Stories</div>
    <table width="100%" cellpadding="0" cellspacing="0" border="0">{top_html}</table>
</td></tr>

<!-- DPRK + Markets -->
<tr><td style="padding:20px 32px;border-bottom:1px solid #EBEBEB;">
    {dprk_html}
    {mkt_html}
</td></tr>

<!-- Next Week -->
<tr><td style="padding:20px 32px;border-bottom:1px solid #EBEBEB;">
    <div style="font-size:10px;font-weight:700;text-transform:uppercase;letter-spacing:2px;color:#1B2A4A;font-family:Arial,sans-serif;margin-bottom:14px;padding-bottom:8px;border-bottom:2px solid #1B2A4A;">Next Week</div>
    <table width="100%" cellpadding="0" cellspacing="0" border="0">{cal_html}</table>
</td></tr>

<!-- Bottom Line -->
<tr><td style="padding:20px 32px;border-bottom:1px solid #EBEBEB;">
    <div style="padding:16px;background:#F8F9FA;border-radius:4px;border-left:3px solid #1B2A4A;">
        <div style="font-size:10px;font-weight:700;text-transform:uppercase;letter-spacing:2px;color:#1B2A4A;margin-bottom:8px;">Bottom Line</div>
        <div style="font-size:14px;color:#333;line-height:1.6;">{bottom_line}</div>
    </div>
</td></tr>

<!-- Footer -->
<tr><td style="padding:20px 32px;background:#1B2A4A;text-align:center;">
    <div style="font-size:9px;text-transform:uppercase;letter-spacing:2px;color:rgba(255,255,255,0.45);font-family:Arial,sans-serif;line-height:2;">
        CSIS Korea Chair &nbsp;&middot;&nbsp; Week in Review &nbsp;&middot;&nbsp; Generated {gen_time}
    </div>
</td></tr>

</table>
</td></tr></table>
</body></html>"""


def main():
    import argparse
    parser = argparse.ArgumentParser(description="Korea Daily Brief — Week in Review")
    parser.add_argument("--no-send", action="store_true", help="Render only, no email")
    args = parser.parse_args()

    digests = _load_week_digests()
    if not digests:
        print("⚠  No daily digests found for this week. Run daily pipeline first.")
        return

    dates = [d.get("_date", "?") for d in digests]
    print(f"📅  Found {len(digests)} daily digests: {', '.join(dates)}")
    weekly = generate_weekly(digests)

    tz = ZoneInfo("America/New_York")
    date_slug = datetime.now(tz).strftime("%Y-%m-%d")
    json_path = Path(f"weekly_{date_slug}.json")
    json_path.write_text(json.dumps(weekly, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"📄  Weekly JSON: {json_path}")

    html = render_weekly(weekly)
    html_path = Path(f"weekly_{date_slug}.html")
    html_path.write_text(html, encoding="utf-8")
    print(f"📄  Weekly HTML: {html_path}")

    if not args.no_send:
        if os.environ.get("DIGEST_TO"):
            from send_email import send
            week_label = weekly.get("week_label", date_slug)
            re_short = weekly.get("re_line", "")[:80]
            subject = f"Korea Week in Review · {week_label} — {re_short}"
            send(html, subject=subject)
            print("📧  Weekly email sent")
        else:
            print("⚠  DIGEST_TO not set — skipping email")
    else:
        print("  --no-send: skipping email")

    print("\n✅  Week in Review done.\n")


if __name__ == "__main__":
    main()
