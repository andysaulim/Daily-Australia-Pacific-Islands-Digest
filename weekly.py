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
from urllib.parse import urlparse
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
- top_stories: 5 to 8 of the week's most consequential developments, ranked. Each: rank, headline, body (2 to 3 sentences synthesizing the week's coverage of THAT ONE story), category (one of "AU-Foreign-Policy", "AU-Defense", "AUKUS", "US-Australia", "NZ-Foreign-Policy", "NZ-Defense", "Pacific-Diplomacy", "Pacific-Politics", "China-Pacific", "AU-Politics", "NZ-Politics", "Trade-Economy"), sources (array of outlet names that carried it), url (the single best URL from the items, copied exactly), source_urls (1 to 4 URLs from the items that carried this story, best first, each copied exactly). If fewer than 5 consequential developments occurred, return fewer. Do not pad.
- pacific_thread: an object {{"text": 2 to 3 sentences on what moved in the Pacific Islands this week, "source_urls": the URLs behind those sentences}}, or null if genuinely nothing did.
- nz_thread: the same object shape, for New Zealand, or null.
- aukus_thread: the same object shape, for AUKUS movement, or null.
- patterns: 2 to 3 objects, each {{"pattern": a theme that recurred across multiple days, "days": the days it appeared, "source_urls": 2 to 4 URLs, ideally one from each day the theme appeared}}.
- bottom_line: an object {{"text": 2 to 3 sentences, the single most important takeaway and what to watch next week, "source_urls": the URLs behind it}}.

SOURCE URLS: every entry in every source_urls array must be a URL copied character for character from an item above. Never shorten one, never rebuild one from a headline, and never supply a URL for a claim the items do not carry. A URL that does not appear above is dropped before the brief is sent, so an invented one costs a link rather than gaining one. A theme you cannot point at with at least one real URL is a theme to leave out.
"""


def load_week(days: int = 7) -> list[dict]:
    """The week's published items, joined back to their collected articles.

    The join runs twice on purpose. `items` stores the URL as collected, which
    for four fifths of this corpus is a Google News redirect, while `published`
    stores the canonical URL resolve.py turned it into. Matching on url alone
    therefore missed every resolved item: measured over the 1,204 rows
    published since 1 September, 541 joined and 663 did not, so 55% of the
    week arrived at the synthesis with no outlet and, worse, no body. The
    second join walks url_cache back from the canonical URL to the Google News
    one and picks the row up there, which takes coverage to 100%. The subquery
    returns at most one gnews_url and items.url is the primary key, so the
    extra join cannot multiply a published row.
    """
    cutoff = (datetime.now(ZoneInfo("America/New_York")).date()
              - timedelta(days=days)).strftime("%Y-%m-%d")
    try:
        with sqlite3.connect(archive.DB_PATH) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                """SELECT p.digest_date, p.section, p.headline, p.url,
                          COALESCE(NULLIF(i.source, ''),  ig.source)  AS source,
                          COALESCE(NULLIF(i.region, ''),  ig.region)  AS region,
                          COALESCE(NULLIF(i.summary, ''), ig.summary) AS summary
                     FROM published p
                     LEFT JOIN items i  ON i.url = p.url
                     LEFT JOIN items ig ON ig.url = (
                              SELECT u.gnews_url FROM url_cache u
                               WHERE u.resolved_url = p.url LIMIT 1)
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


# ─────────────────────────────────────────────────────────────────────────────
# SOURCE TRAILS
# ─────────────────────────────────────────────────────────────────────────────
# Only The Week carried a link. Patterns, the three threads and the bottom line
# are where this edition does its own work rather than restating the dailies,
# and all five shipped as bare prose: a reader who wanted the reporting behind
# "Chinese coast guard presence recurred Tuesday, Wednesday and Friday" had to
# go back through five daily issues to find it. These functions put a trail of
# linked outlets under each one.
#
# The model supplies URLs and never the label. A name typed next to a URL can
# disagree with it, and a wrong outlet on a right link is a factual error in a
# CSIS product, so the label is read back out of the archive row that URL
# belongs to. Only the trailing feed qualifier is tidied away: the brief says
# "RNZ Pacific", not "RNZ Pacific (wire)".
#
# A URL the week did not publish is dropped. The daily pipeline has
# _repair_digest_urls guarding exactly this; the weekly had nothing, so a
# fabricated link would have gone out unchecked. The allowlist is the week's
# own published rows, which is also precisely the set of URLs the prompt
# showed the model, so a URL copied correctly always survives and only an
# invented one falls.

_TRAIL_MAX = 4


def _host_label(url: str) -> str:
    """Last-resort label: the publisher's own domain, as _clean_src does."""
    try:
        host = (urlparse(url).hostname or "").lower()
    except Exception:
        return ""
    return host[4:] if host.startswith("www.") else host


def _outlet_label(source: str, url: str) -> str:
    source = (source or "").strip()
    for qualifier in (" (wire)", " (region)"):
        if source.endswith(qualifier):
            source = source[: -len(qualifier)].strip()
    return source or _host_label(url)


def _week_source_index(items: list[dict]) -> dict:
    """URL to outlet label, over every item the week actually published."""
    index = {}
    for i in items:
        url = (i.get("url") or "").strip()
        if not url:
            continue
        label = _outlet_label(i.get("source") or "", url)
        if not index.get(url):
            index[url] = label
    return index


def _resolve_sources(raw, index: dict, dropped: list) -> list[dict]:
    """Model URLs to [{outlet, url}], dropping any the week did not publish.

    Deduplicated by outlet, not only by URL. A trail answers who carried the
    story, so an outlet that filed twice belongs in it once: three SMH URLs
    behind one pattern printed as "SMH Federal Politics, SMH Federal Politics,
    SMH Federal Politics" before this, which reads as a mistake rather than as
    corroboration. The first URL an outlet appears under is the one kept, and
    the cap counts distinct outlets.
    """
    out, seen_urls, seen_outlets = [], set(), set()
    for entry in (raw or []):
        url = entry.get("url") if isinstance(entry, dict) else entry
        url = url.strip() if isinstance(url, str) else ""
        if not url or url in seen_urls:
            continue
        seen_urls.add(url)
        if url not in index:
            dropped.append(url)
            continue
        label = index[url] or _host_label(url)
        key = label.casefold()
        if not label or key in seen_outlets:
            continue
        seen_outlets.add(key)
        out.append({"outlet": label, "url": url})
        if len(out) >= _TRAIL_MAX:
            break
    return out


def _prose_parts(value, text_key: str = "text") -> tuple[str, list]:
    """(text, resolved sources) from a prose section in any of its shapes.

    Three shapes reach the renderer. A bare string is what the model returned
    before this change and what an archived weekly.json still holds. The
    object with source_urls is what the model returns now. The object with
    sources is that one after _attach_sources has checked it. Only the last
    carries links, so a weekly rendered without the checking step degrades to
    the prose it always was instead of printing an unverified URL.
    """
    if isinstance(value, dict):
        text = ""
        for key in (text_key, "text", "pattern"):
            candidate = value.get(key)
            if isinstance(candidate, str) and candidate.strip():
                text = candidate
                break
        sources = value.get("sources")
        return text, (sources if isinstance(sources, list) else [])
    if isinstance(value, list):
        return (value[0] if value and isinstance(value[0], str) else ""), []
    return ("" if value is None else str(value)), []


_PROSE_KEYS = ("pacific_thread", "nz_thread", "aukus_thread", "bottom_line")


def _attach_sources(weekly: dict, items: list[dict]) -> dict:
    """Check every model URL against the week, and resolve it to an outlet.

    Runs between the model and the renderer. Returns counts for the run log.
    """
    index = _week_source_index(items)
    dropped: list[str] = []

    for story in (weekly.get("top_stories") or []):
        if not isinstance(story, dict):
            continue
        story["sources_linked"] = _resolve_sources(
            story.get("source_urls"), index, dropped)
        url = (story.get("url") or "").strip()
        if url and url not in index:
            dropped.append(url)
            story.pop("url", None)

    for key in _PROSE_KEYS:
        value = weekly.get(key)
        if value is None:
            continue
        text, _ = _prose_parts(value)
        raw = value.get("source_urls") if isinstance(value, dict) else None
        weekly[key] = {"text": text,
                       "sources": _resolve_sources(raw, index, dropped)}

    patterns = []
    for entry in (weekly.get("patterns") or []):
        text, _ = _prose_parts(entry, text_key="pattern")
        if not text.strip():
            continue
        raw = entry.get("source_urls") if isinstance(entry, dict) else None
        days = entry.get("days") if isinstance(entry, dict) else ""
        patterns.append({
            "pattern": text,
            "days": days if isinstance(days, str) else "",
            "sources": _resolve_sources(raw, index, dropped),
        })
    weekly["patterns"] = patterns

    linked = sum(len(s.get("sources_linked") or [])
                 for s in (weekly.get("top_stories") or [])
                 if isinstance(s, dict))
    linked += sum(len((weekly.get(k) or {}).get("sources") or [])
                  for k in _PROSE_KEYS if isinstance(weekly.get(k), dict))
    linked += sum(len(p.get("sources") or []) for p in patterns)
    return {"linked": linked, "dropped": dropped}


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
        blocks = []
        for s in stories:
            # The trail supersedes the meta line's outlet names: both say who
            # carried the story, but the trail is checked against the archive
            # and clickable, so printing the model's plain list beside it only
            # repeats it. Without a trail the plain list still stands.
            trail = R._source_trail(s.get("sources_linked"))
            src = "" if trail else R._esc(", ".join(s.get("sources") or []))
            blocks.append(R._item_block(
                R._esc(s.get("category", "")), src,
                R._esc(s.get("headline", "")), R._esc(s.get("body", "")),
                s.get("url", ""), bar_color=R._cat_color(s.get("category", "")),
                extra_html=trail))
        sections.append(
            f'<div {R._SEC}>{R._sec_label("The Week")}{"".join(blocks)}</div>')

    for key, title, colour in (("pacific_thread", "Pacific Thread", R.TEAL),
                               ("nz_thread", "New Zealand Thread", R.NZ_GREEN),
                               ("aukus_thread", "AUKUS Thread", R.NAVY)):
        text, sources = _prose_parts(weekly.get(key))
        if text.strip():
            sections.append(
                f'<div {R._SEC}>{R._sec_label(title, color=colour)}'
                f'<div style="font-size:13px;line-height:1.6;color:#555;">{R._esc(text)}</div>'
                f'{R._source_trail(sources)}</div>')

    rows = []
    for entry in (weekly.get("patterns") or []):
        text, sources = _prose_parts(entry, text_key="pattern")
        if not text.strip():
            continue
        days = entry.get("days", "") if isinstance(entry, dict) else ""
        rows.append(
            f'<div style="margin-bottom:12px;padding-left:12px;border-left:3px solid {R.TEAL};'
            f'font-size:13px;line-height:1.55;color:#555;">{R._esc(text)}'
            f'{R._source_trail(sources, prefix=R._str(days))}</div>')
    if rows:
        sections.append(f'<div {R._SEC}>{R._sec_label("Patterns")}{"".join(rows)}</div>')

    bottom, bottom_sources = _prose_parts(weekly.get("bottom_line"))
    if bottom.strip():
        sections.append(
            f'<div {R._SEC}>{R._sec_label("Bottom Line", color=R.TEAL)}'
            f'<div style="font-size:14px;line-height:1.65;color:{R.INK};">{R._esc(bottom)}</div>'
            f'{R._source_trail(bottom_sources)}</div>')

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

    stats = _attach_sources(weekly, items)
    print(f"  Source trails: {stats['linked']} link(s) checked against the week.")
    if stats["dropped"]:
        print(f"  !  Dropped {len(stats['dropped'])} URL(s) the week never published:")
        for url in stats["dropped"][:5]:
            print(f"       {url[:96]}")

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
