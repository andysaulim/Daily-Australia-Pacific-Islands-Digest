"""
Australia Chair Daily Brief: Week in Review

A Friday synthesis of the week's issues. Run with `python weekly.py`, or on the
Friday schedule in the workflow.

    python weekly.py            # generate and send
    python weekly.py --no-send  # render to weekly.html only
    python weekly.py --days 7   # widen or narrow the window

Deliberately built differently from the Korea brief's weekly.py. That one reads
`digest_<date>.json` files off disk, which works on a laptop and not in Actions,
where every run is a fresh checkout and `public/` is gitignored. This one reads
the archive database, which is committed back after every run and is therefore
the only thing in this pipeline that actually remembers last Tuesday.

Reading the archive also gives the synthesis something Korea's cannot get: each
published item joins back to its collected article, so the model sees the
enriched body text fulltext.py fetched, not just the headline it shipped.

SOURCE-OR-SKIP applies exactly as it does daily. The model is given the week's
published items and nothing else, and may not add a fact from memory.
"""
import argparse
import json
import os
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import archive
from digest import FAST_MODEL, PRIMARY_MODEL, _robust_json_parse, _stream_claude

WEEKLY_SYSTEM_PROMPT = """You are the senior analyst for the CSIS Australia Chair. You produce the Week in Review edition of the Australia Chair Daily Brief: a consolidated Friday read for people who already received the daily briefs.

They have seen the detail. Your job is synthesis, pattern recognition, and pointing at what changed. Be ruthlessly concise.

RULES:
- SOURCE-OR-SKIP: every claim, every date, name, number and event must appear explicitly in the week's data below. You have NO other knowledge. An omission is always better than an invention.
- NO COMPOSITE FACTS: do not merge separate developments into one combined claim. If a Pacific visit appears in one item and a security agreement in another, you may NOT construct a single trip that delivered an agreement. State what each item actually reported.
- NO EXTRAPOLATION: do not project, predict, or assume a plan unless an item explicitly reported it.
- Synthesis means condensing what was reported, not generating connective facts. Pattern recognition is about recurring themes, not invented links.
- REGIONAL BALANCE: the week's Australian volume will dwarf New Zealand and the Pacific. A Week in Review that is all Canberra has failed, exactly as the daily brief would have.
- No editorialising. Present the pattern and let the reader draw the conclusion.
- House style: zero em-dashes, no emojis, serial comma, U.S. and U.K. keep their periods.
- Return ONLY valid JSON. No markdown fences, no preamble."""

WEEKLY_USER_TEMPLATE = """Today is {date_str}. Synthesize the week's published items into a Week in Review.

THE WEEK'S PUBLISHED ITEMS ({count} items across {days} issues):
{items_json}

Return a JSON object with:
- week_label: string, e.g. "25 to 29 August 2026"
- re_line: one sentence on the week's single most important development, under 100 characters
- top_stories: 5 to 8 of the week's most consequential developments, ranked. Each: rank, headline, body (2 to 3 sentences synthesizing the week's coverage of THAT ONE story), category (one of "AU-Foreign-Policy", "AU-Defense", "AUKUS", "US-Australia", "NZ-Foreign-Policy", "NZ-Defense", "Pacific-Diplomacy", "Pacific-Politics", "China-Pacific", "AU-Politics", "NZ-Politics", "Trade-Economy"), sources (array of outlet names that carried it), url (the single best URL from the items, copied exactly). If fewer than 5 consequential developments occurred, return fewer. Do not pad.
- pacific_thread: 2 to 3 sentences on what moved in the Pacific Islands this week, or null if genuinely nothing did.
- nz_thread: 2 to 3 sentences on what moved in New Zealand this week, or null.
- aukus_thread: 2 to 3 sentences on AUKUS movement this week, or null.
- patterns: 2 to 3 strings, each naming a theme that recurred across multiple days, with the days it appeared.
- bottom_line: 2 to 3 sentences. The single most important takeaway and what to watch next week.
"""


def load_week(days: int = 7) -> list[dict]:
    """The week's published items, joined back to their collected articles."""
    cutoff = (datetime.now(ZoneInfo("America/New_York")).date()
              - timedelta(days=days)).strftime("%Y-%m-%d")
    try:
        with sqlite3.connect(archive.DB_PATH) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                """SELECT p.digest_date, p.section, p.headline, p.url,
                          i.source, i.region, i.summary
                     FROM published p
                     LEFT JOIN items i ON i.url = p.url
                    WHERE p.digest_date >= ? AND p.headline != ''
                    ORDER BY p.digest_date, p.section""",
                (cutoff,)).fetchall()
    except sqlite3.Error as e:
        print(f"  !  Could not read the archive: {e}")
        return []
    out = []
    for r in rows:
        out.append({
            "date": r["digest_date"], "section": r["section"],
            "headline": r["headline"], "url": r["url"],
            "source": r["source"] or "", "region": r["region"] or "",
            # Trim hard: a week of enriched bodies at full length would dwarf
            # the daily prompt, and synthesis needs the gist, not the article.
            "summary": (r["summary"] or "")[:600],
        })
    return out


def generate_weekly(items: list[dict]) -> dict:
    import anthropic
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        raise RuntimeError("Missing ANTHROPIC_API_KEY.")
    client = anthropic.Anthropic(api_key=api_key)

    now = datetime.now(ZoneInfo("America/New_York"))
    days = len({i["date"] for i in items})
    prompt = WEEKLY_USER_TEMPLATE.format(
        date_str=now.strftime("%A, %d %B %Y"), count=len(items), days=days,
        items_json=json.dumps(items, ensure_ascii=False, indent=1))

    messages = [{"role": "user", "content": prompt}]
    print(f"  Synthesizing {len(items)} items across {days} issue(s)...")
    return _stream_claude(client, messages, model=PRIMARY_MODEL,
                          system_prompt=WEEKLY_SYSTEM_PROMPT)


def render_weekly(weekly: dict) -> str:
    """Reuses the daily renderer's shell, palette and item blocks."""
    import render as R
    now = datetime.now(ZoneInfo("America/New_York"))
    label = R._esc(weekly.get("week_label", now.strftime("%d %B %Y")))
    sections = []

    re_line = R._esc(weekly.get("re_line", ""))
    sections.append(f"""
        <div style="background:{R.NAVY_DEEP};padding:26px 32px;">
          <div style="font-size:22px;font-weight:700;color:#FFF;font-family:Georgia,serif;">Australia Chair Brief</div>
          <div style="margin-top:2px;font-size:13px;letter-spacing:3px;text-transform:uppercase;color:{R.TEAL_LT};font-family:Arial,sans-serif;">Week in Review</div>
          <div style="margin-top:8px;font-size:15px;color:rgba(255,255,255,0.9);font-family:Georgia,serif;">{label}</div>
          {f'<div style="margin-top:12px;padding-top:12px;border-top:1px solid rgba(46,156,176,0.35);font-size:13px;color:rgba(255,255,255,0.85);font-family:Georgia,serif;line-height:1.5;">{re_line}</div>' if re_line else ''}
        </div>
        <div style="height:3px;background:{R.TEAL};"></div>""")

    stories = weekly.get("top_stories") or []
    if stories:
        html = "".join(
            R._item_block(R._esc(s.get("category", "")),
                          R._esc(", ".join(s.get("sources") or [])),
                          R._esc(s.get("headline", "")), R._esc(s.get("body", "")),
                          s.get("url", ""), bar_color=R._cat_color(s.get("category", "")))
            for s in stories)
        sections.append(f'<div {R._SEC}>{R._sec_label("The Week")}{html}</div>')

    for key, title, colour in (("pacific_thread", "Pacific Thread", R.TEAL),
                               ("nz_thread", "New Zealand Thread", R.NZ_GREEN),
                               ("aukus_thread", "AUKUS Thread", R.NAVY)):
        text = R._str(weekly.get(key) or "")
        if text.strip():
            sections.append(
                f'<div {R._SEC}>{R._sec_label(title, color=colour)}'
                f'<div style="font-size:13px;line-height:1.6;color:#555;">{R._esc(text)}</div></div>')

    patterns = [p for p in (weekly.get("patterns") or []) if R._str(p).strip()]
    if patterns:
        rows = "".join(
            f'<div style="margin-bottom:8px;padding-left:12px;border-left:3px solid {R.TEAL};'
            f'font-size:13px;line-height:1.55;color:#555;">{R._esc(p)}</div>' for p in patterns)
        sections.append(f'<div {R._SEC}>{R._sec_label("Patterns")}{rows}</div>')

    bottom = R._esc(R._str(weekly.get("bottom_line", "")))
    if bottom:
        sections.append(
            f'<div {R._SEC}>{R._sec_label("Bottom Line", color=R.TEAL)}'
            f'<div style="font-size:14px;line-height:1.65;color:{R.INK};">{bottom}</div></div>')

    return R._shell("".join(sections), label)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-send", action="store_true")
    ap.add_argument("--days", type=int, default=7)
    args = ap.parse_args()

    print("=" * 60)
    print("  Australia Chair Brief: Week in Review")
    print("=" * 60)

    items = load_week(args.days)
    if len(items) < 10:
        print(f"\n  Only {len(items)} published item(s) in the last {args.days} days. "
              f"Not enough to synthesize a week; skipping.")
        return

    weekly = generate_weekly(items)
    Path("weekly.json").write_text(json.dumps(weekly, ensure_ascii=False, indent=2),
                                   encoding="utf-8")
    html = render_weekly(weekly)
    Path("weekly.html").write_text(html, encoding="utf-8")
    print(f"\n  Rendered weekly.html ({len(html):,} bytes)")

    if args.no_send:
        print("  --no-send: stopping before email.")
        return
    from send_email import send
    send(html, re_line=weekly.get("re_line"),
         subject=f"Australia Chair Brief, Week in Review, {weekly.get('week_label', '')}")


if __name__ == "__main__":
    main()
