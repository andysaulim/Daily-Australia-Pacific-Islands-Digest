"""
Australia Chair Daily Brief: HTML Renderer
CSIS Australia Chair

Takes the validated digest JSON and renders the email. Table-based layout,
inline styles only, for maximum client compatibility.

Forked from the Korea Daily Brief renderer. The institutional navy ground is
kept so the three CSIS briefs read as one family; the accent rule changes from
Korea's gold to a Pacific teal so this one is distinguishable at a glance in a
crowded inbox.

Email constraints that are not negotiable:
  - inline styles only, nested tables, never grid or flex
  - no CSS custom properties
  - text-underline-offset is stripped by Outlook; use border-bottom instead
  - test in Gmail, Outlook, and Apple Mail before shipping a layout change
"""
import re as _re
from datetime import datetime
from urllib.parse import urlparse as _urlparse

# ── Palette ──────────────────────────────────────────────────────────────
NAVY_DEEP = "#0D1B2A"
NAVY = "#1B2A4A"
TEAL = "#17798C"   # the Australia Chair accent
TEAL_LT = "#2E9CB0"
ALERT = "#C0392B"
INK = "#2C3E50"
NZ_GREEN = "#1B6A4A"  # the third geography, and the only other accent used


def _clean_src(raw: str) -> str:
    """Strip raw URLs out of source lines, keeping human-readable text."""
    if not raw:
        return raw
    stripped = raw.strip()
    if _re.match(r"^https?://", stripped) and " " not in stripped:
        try:
            host = _urlparse(stripped).hostname or ""
            if host.startswith("www."):
                host = host[4:]
            return host or raw
        except Exception:
            return raw
    cleaned = _re.sub(r"https?://\S+", "", raw).strip()
    cleaned = _re.sub(r"  +", " ", cleaned)
    return cleaned or raw


def _str(val) -> str:
    """Coerce to str, the API occasionally returns a single-element list."""
    if isinstance(val, list):
        return val[0] if val else ""
    return val if isinstance(val, str) else str(val) if val is not None else ""


def _esc(text) -> str:
    if text is None or text == "":
        return ""
    text = str(text)
    if text == "None":
        return ""
    return (text.replace("&", "&amp;").replace("<", "&lt;")
                .replace(">", "&gt;").replace('"', "&quot;"))


def _signal_badge(signal_type: str) -> str:
    """Retired. Returns nothing.

    DEVELOPMENT / CONTEXT / ESCALATION badges labelled almost every item and
    told the reader little the headline did not: a brief of this kind is
    developments, so the modal badge carried no information while adding five
    more colours to the page. Kept as a no-op so an older digest.json still
    renders, and so the field can be dropped from the schema separately.
    """
    return ""


def _link_or_text(text: str, url: str,
                  style: str = f"color:{NAVY};border-bottom:1px solid {TEAL};"
                               "padding-bottom:1px;text-decoration:none;") -> str:
    """Render as a link only when the URL is real. `text` must already be escaped."""
    if url and url != "#" and str(url).startswith("http"):
        return f'<a href="{_esc(url)}" style="{style}">{text}</a>'
    return text


_SEC = 'style="padding:20px 32px;border-bottom:1px solid #EBEBEB;" class="sec"'
_SEC_ALERT = (f'style="padding:20px 32px;border-top:3px solid {ALERT};'
              'border-bottom:1px solid #EBEBEB;" class="sec"')


def _sec_label(label: str, color: str = NAVY) -> str:
    return (f'<div style="font-size:10px;font-weight:700;text-transform:uppercase;'
            f'letter-spacing:2px;color:{color};font-family:Arial,sans-serif;'
            f'margin-bottom:14px;padding-bottom:8px;border-bottom:2px solid {color};">'
            f'{label}</div>')


# Category accent colours, cut to three.
#
# Thirteen accent colours plus five badge colours plus nine section-label
# colours meant nothing on the page was un-coloured, so no colour meant
# anything. The left bar now carries one distinction the reader actually reads
# the brief for: which of the three geographies an item belongs to. Everything
# else is navy.
_CAT_COLORS = {
    "US-Australia":      NAVY,
    "AUKUS":             NAVY,
    "AU-Foreign-Policy": NAVY,
    "AU-Defense":        NAVY,
    "AU-Politics":       NAVY,
    "Trade-Economy":     NAVY,
    "NZ-Foreign-Policy": NZ_GREEN,
    "NZ-Defense":        NZ_GREEN,
    "NZ-Politics":       NZ_GREEN,
    "Pacific-Diplomacy": TEAL,
    "Pacific-Politics":  TEAL,
    "China-Pacific":     TEAL,
    "US-China-Pacific":  TEAL,
}


def _cat_color(cat: str, default: str = NAVY) -> str:
    return _CAT_COLORS.get(_str(cat).strip(), default)


def _cal_stamp(date_val: str, window: str, confirmed: bool) -> str:
    """Calendar date or window, always carrying a year.

    A confirmed ISO date rendered raw as "2026-08-30"; a window rendered as
    "expected in August" with no year at all. In a brief whose calendar reaches
    90 days ahead and which is read next to a nine-week-out election, "August"
    alone is ambiguous. Confirmed dates become "30 Aug 2026"; a window without a
    four-digit year gets the current one appended.
    """
    if date_val and confirmed:
        try:
            return datetime.strptime(date_val[:10], "%Y-%m-%d").strftime(
                "%d %b %Y").lstrip("0")
        except ValueError:
            return date_val                      # unparseable, show it as given
    text = (window or "").strip()
    if not text:
        return "date not set"
    if _re.search(r"\b20\d{2}\b", text):
        return text
    from zoneinfo import ZoneInfo
    return f"{text} {datetime.now(ZoneInfo('America/New_York')).year}"


def _stand_in(items: list) -> str | None:
    """Return the stand-in text when a section is honestly empty."""
    if len(items) == 1 and isinstance(items[0], dict) and items[0].get("stand_in"):
        return _esc(items[0]["stand_in"])
    return None


def _stand_in_block(text: str) -> str:
    return (f'<div style="font-size:13px;color:#8A8A8A;font-style:italic;'
            f'padding:6px 0 2px 12px;border-left:3px solid #DDD;">{text}</div>')


def _item_block(cat: str, src: str, headline: str, body: str, url: str,
                bar_color: str = NAVY, extra_html: str = "") -> str:
    """The standard border-left news item."""
    meta = " &middot; ".join(p for p in (cat, src) if p)
    return f"""
            <div style="margin-bottom:12px;padding-left:12px;border-left:3px solid {bar_color};">
              <div style="font-size:11px;color:#888;text-transform:uppercase;letter-spacing:0.5px;">{meta}</div>
              <div style="font-size:13px;font-weight:600;color:{NAVY};margin:2px 0 3px;">
                {_link_or_text(headline, url)}
              </div>
              <div style="font-size:12px;line-height:1.5;color:#555;">{body}</div>
              {extra_html}
            </div>"""


def _real_items(digest: dict, key: str) -> list:
    """Section items with any stand-in placeholder removed."""
    return [i for i in (digest.get(key) or [])
            if isinstance(i, dict) and not i.get("stand_in")]


def _estimate_word_count(digest: dict) -> int:
    from digest import _count_digest_words
    return _count_digest_words(digest)


# ─────────────────────────────────────────────────────────────────────────────
# RENDER
# ─────────────────────────────────────────────────────────────────────────────

def render(digest: dict) -> str:
    from zoneinfo import ZoneInfo
    now = datetime.now(ZoneInfo("America/New_York"))
    date_str = now.strftime("%A, %B %d, %Y").replace(" 0", " ")
    gen_time = now.strftime("%I:%M %p ET").lstrip("0")
    re_line = _esc(digest.get("re_line", ""))
    word_count = _estimate_word_count(digest)
    read_min = max(1, round(word_count / 250))
    web_url = digest.get("web_url", "")

    sections = []

    # ── 0. View in browser ───────────────────────────────────────────────
    if web_url:
        sections.append(f"""
        <div style="background:#F0F0F0;padding:6px 32px;text-align:center;font-size:11px;color:#888;" class="sec">
          Email not rendering? <a href="{_esc(web_url)}" style="color:{TEAL};text-decoration:none;">Read online &#8594;</a>
        </div>""")

    # ── 1. Header ────────────────────────────────────────────────────────
    re_block = ""
    if re_line:
        re_block = (f"<div style='margin-top:12px;padding-top:12px;"
                    f"border-top:1px solid rgba(46,156,176,0.35);font-size:13px;"
                    f"color:rgba(255,255,255,0.85);font-family:Georgia,serif;line-height:1.5;'>"
                    f"<strong style='color:{TEAL_LT};font-size:11px;letter-spacing:1px;'>RE:</strong>"
                    f"&nbsp; {re_line}</div>")

    sections.append(f"""
    <a name="top"></a>
    <div bgcolor="{NAVY_DEEP}" style="background-color:{NAVY_DEEP};background:linear-gradient(135deg, {NAVY_DEEP} 0%, {NAVY} 60%, #24485C 100%);color:#fff;padding:20px 32px 16px;" class="sec">
      <table width="100%" cellpadding="0" cellspacing="0" border="0"><tr>
        <td style="vertical-align:top;">
          <div style="font-size:10px;text-transform:uppercase;letter-spacing:3px;color:{TEAL_LT};font-family:Arial,sans-serif;margin-bottom:6px;">CSIS Australia Chair</div>
          <h1 style="margin:0;font-size:26px;font-weight:700;font-family:Georgia,'Times New Roman',serif;color:#fff;letter-spacing:0.3px;">
            Australia Chair Daily Brief
          </h1>
          <div style="margin-top:4px;font-size:11px;color:rgba(255,255,255,0.55);letter-spacing:1.5px;text-transform:uppercase;font-family:Arial,sans-serif;">Australia &nbsp;&middot;&nbsp; New Zealand &nbsp;&middot;&nbsp; the Pacific Islands</div>
          <div style="margin-top:8px;font-size:16px;color:rgba(255,255,255,0.9);font-family:Georgia,serif;">{_esc(date_str)}</div>
        </td>
        <td style="vertical-align:top;text-align:right;">
          <div style="font-size:10px;text-transform:uppercase;letter-spacing:1.5px;color:rgba(255,255,255,0.5);margin-bottom:4px;">{gen_time}</div>
          <div style="font-size:10px;color:rgba(255,255,255,0.4);">{word_count:,} words &middot; {read_min} min read</div>
        </td>
      </tr></table>
      {re_block}
    </div>
    <div style="height:3px;background-color:{TEAL};background:linear-gradient(90deg, {TEAL} 0%, {NAVY} 100%);"></div>""")

    # ── 2. Market strip: reserved, not built in v1 ──────────────────────
    # The slot and the market_indicators key are kept so ASX 200, AUD/USD,
    # NZX 50, NZD/USD, iron ore, the RBA cash rate and the RBNZ OCR can drop in
    # later without a re-layout.

    # ── 3. Today at a Glance ─────────────────────────────────────────────
    memo_items = digest.get("morning_memo") or []
    if memo_items:
        memo_html = ""
        for i, mi in enumerate(memo_items[:3]):
            text = _esc(mi if isinstance(mi, str) else
                        (mi.get("text", "") if isinstance(mi, dict) else str(mi or "")))
            memo_html += f"""
            <table width="100%" cellpadding="0" cellspacing="0" border="0" style="margin-bottom:10px;">
              <tr>
                <td width="28" style="vertical-align:top;padding-top:2px;">
                  <div style="width:24px;height:24px;border-radius:50%;background:{NAVY_DEEP};color:{TEAL_LT};text-align:center;line-height:24px;font-size:12px;font-weight:700;font-family:Georgia,serif;">{i + 1}</div>
                </td>
                <td style="padding-left:10px;vertical-align:top;">
                  <div style="font-size:14px;line-height:1.6;color:{INK};font-family:Georgia,serif;">{text}</div>
                </td>
              </tr>
            </table>"""
        sections.append(f"""
        <div style="padding:20px 32px;border-bottom:1px solid #EAEAEA;background:#FAFBFC;" class="sec">
          <a name="memo"></a>
          {_sec_label("Today at a Glance", color=TEAL)}
          {memo_html}
        </div>""")

    # ── 4. Top Stories ───────────────────────────────────────────────────
    top_stories = _real_items(digest, "top_stories")
    if top_stories:
        html = ""
        for story in top_stories:
            cat = _esc(_str(story.get("category_tag", story.get("category", ""))))
            headline = _esc(story.get("headline", ""))
            body = _esc(story.get("body", ""))
            so_what = _esc(story.get("so_what", ""))
            pattern = _esc(story.get("pattern_note", ""))
            src_line = _esc(_clean_src(_str(story.get("src_line", story.get("source", "")))))
            url = story.get("url", "")
            cat_badge = (f'<span style="display:inline-block;font-size:9px;'
                         f'text-transform:uppercase;letter-spacing:1px;color:{TEAL};'
                         f'font-weight:700;margin-bottom:6px;">{cat}</span>') if cat else ""
            so_what_html = (f"<p style='margin:0 0 6px 0;font-size:12px;line-height:1.5;"
                            f"color:{TEAL};'><strong>So what:</strong> {so_what}</p>") if so_what else ""
            pattern_html = ("<p style='margin:0 0 6px 0;font-size:12px;line-height:1.5;"
                            f"color:#7B5BA6;'><strong>Pattern:</strong> {pattern}</p>") if pattern else ""
            html += f"""
            <div class="story-card" style="margin-bottom:14px;padding:14px 16px;background:#fff;border-radius:3px;border-left:4px solid {_cat_color(_str(story.get("category_tag", story.get("category", ""))), NAVY_DEEP)};box-shadow:0 1px 3px rgba(0,0,0,0.06);">
              {cat_badge}
              <h3 style="margin:0 0 8px 0;font-size:16px;color:{NAVY_DEEP};font-family:Georgia,serif;line-height:1.4;">
                {_link_or_text(headline, url, style=f"color:{NAVY_DEEP};text-decoration:none;")}
              </h3>
              <p style="margin:0 0 8px 0;font-size:13px;line-height:1.6;color:#444;">{body}</p>
              {so_what_html}
              {pattern_html}
              <div style="font-size:10px;color:#AAA;margin-top:6px;">{src_line}</div>
            </div>"""
        sections.append(f"""
        <div {_SEC}>
          <a name="top-stories"></a>{_sec_label("Top Stories")}
          {html}
        </div>""")

    # ── 5. Overnight Flash ───────────────────────────────────────────────
    overnight = _real_items(digest, "overnight_items")
    if overnight:
        html = ""
        for item in overnight:
            cat = _str(item.get("category", ""))
            badge = _signal_badge(item.get("signal_type", ""))
            html += _item_block(
                _esc(cat),
                _esc(_clean_src(_str(item.get("source", "")))),
                _esc(item.get("headline", "")),
                _esc(item.get("body_text", "")),
                item.get("url", ""),
                bar_color=_cat_color(cat, ALERT),
                extra_html=(f'<div style="margin-top:5px;">{badge}</div>' if badge else ""),
            )
        sections.append(f"""
        <div {_SEC_ALERT}>
          <a name="overnight"></a>{_sec_label("Overnight")}
          {html}
        </div>""")

    # ── 6. AUKUS Watch ───────────────────────────────────────────────────
    aukus = _real_items(digest, "aukus_watch")
    if aukus:
        html = ""
        for item in aukus:
            pillar = _str(item.get("pillar", ""))
            label = {"1": "Pillar 1", "2": "Pillar 2",
                     "both": "Pillars 1 and 2"}.get(pillar, "AUKUS")
            badge = _signal_badge(item.get("signal_type", ""))
            html += _item_block(
                _esc(label),
                _esc(_clean_src(_str(item.get("source", "")))),
                _esc(item.get("headline", "")),
                _esc(item.get("body_text", "")),
                item.get("url", ""),
                bar_color=NAVY_DEEP,
                extra_html=(f'<div style="margin-top:5px;">{badge}</div>' if badge else ""),
            )
        sections.append(f"""
        <div {_SEC}>
          <a name="aukus"></a>{_sec_label("AUKUS Watch")}
          {html}
        </div>""")

    # ── 7. Pacific Wire ──────────────────────────────────────────────────
    pacific_raw = digest.get("pacific_wire") or []
    pacific = _real_items(digest, "pacific_wire")
    stand_in = _stand_in(pacific_raw)
    if pacific or stand_in:
        if stand_in:
            html = _stand_in_block(stand_in)
        else:
            html = ""
            for item in pacific:
                badge = _signal_badge(item.get("signal_type", ""))
                html += _item_block(
                    _esc(_str(item.get("country", "Regional"))),
                    _esc(_clean_src(_str(item.get("source", "")))),
                    _esc(item.get("headline", "")),
                    _esc(item.get("body_text", "")),
                    item.get("url", ""),
                    bar_color=TEAL,
                    extra_html=(f'<div style="margin-top:5px;">{badge}</div>' if badge else ""),
                )
        sections.append(f"""
        <div {_SEC}>
          <a name="pacific"></a>{_sec_label("Pacific Wire", color=TEAL)}
          {html}
        </div>""")

    # ── 8. New Zealand ───────────────────────────────────────────────────
    nz_raw = digest.get("new_zealand") or []
    nz = _real_items(digest, "new_zealand")
    stand_in = _stand_in(nz_raw)
    if nz or stand_in:
        if stand_in:
            html = _stand_in_block(stand_in)
        else:
            html = ""
            for item in nz:
                badge = _signal_badge(item.get("signal_type", ""))
                html += _item_block(
                    _esc(_str(item.get("category", ""))),
                    _esc(_clean_src(_str(item.get("source", "")))),
                    _esc(item.get("headline", "")),
                    _esc(item.get("body_text", "")),
                    item.get("url", ""),
                    bar_color="#1B6A4A",
                    extra_html=(f'<div style="margin-top:5px;">{badge}</div>' if badge else ""),
                )
        sections.append(f"""
        <div {_SEC}>
          <a name="nz"></a>{_sec_label("New Zealand", color=NZ_GREEN)}
          {html}
        </div>""")

    # ── 9. China in the Pacific: the one dark section ───────────────────
    china = _real_items(digest, "china_in_the_pacific")
    if china:
        html = ""
        for item in china:
            country = _esc(_str(item.get("country", "Regional")))
            activity = _esc(_str(item.get("activity_type", "")))
            src = _esc(_clean_src(_str(item.get("source", ""))))
            reaction = ('<span style="display:inline-block;font-size:9px;padding:1px 6px;'
                        'border:1px solid rgba(255,255,255,0.35);border-radius:2px;'
                        'color:rgba(255,255,255,0.6);margin-left:6px;">STATE MEDIA</span>'
                        if item.get("is_reaction_source") else "")
            meta = " &middot; ".join(p for p in (country, activity, src) if p)
            html += f"""
            <div style="margin-bottom:12px;padding-left:12px;border-left:3px solid {TEAL_LT};">
              <div style="font-size:11px;color:rgba(255,255,255,0.55);text-transform:uppercase;letter-spacing:0.5px;">{meta}{reaction}</div>
              <div style="font-size:13px;font-weight:600;color:#fff;margin:2px 0 3px;">
                {_link_or_text(_esc(item.get("headline", "")), item.get("url", ""),
                               style=f"color:#fff;border-bottom:1px solid {TEAL_LT};padding-bottom:1px;text-decoration:none;")}
              </div>
              <div style="font-size:12px;line-height:1.5;color:rgba(255,255,255,0.75);">{_esc(item.get("body_text", ""))}</div>
            </div>"""
        sections.append(f"""
        <div bgcolor="{NAVY_DEEP}" style="background-color:{NAVY_DEEP};padding:20px 32px;" class="sec china-dark">
          <a name="china-pacific"></a>
          <div style="font-size:10px;font-weight:700;text-transform:uppercase;letter-spacing:2px;color:{TEAL_LT};font-family:Arial,sans-serif;margin-bottom:14px;padding-bottom:8px;border-bottom:2px solid {TEAL_LT};">China in the Pacific</div>
          {html}
        </div>""")

    # ── 10. Canberra ─────────────────────────────────────────────────────
    canberra = _real_items(digest, "canberra_politics")
    if canberra:
        html = "".join(
            _item_block(_esc(_str(i.get("category", ""))),
                        _esc(_clean_src(_str(i.get("source", "")))),
                        _esc(i.get("headline", "")), _esc(i.get("body_text", "")),
                        i.get("url", ""), bar_color=_cat_color(_str(i.get("category", "")), NAVY))
            for i in canberra)
        sections.append(f"""
        <div {_SEC}>
          <a name="canberra"></a>{_sec_label("Canberra")}
          {html}
        </div>""")

    # ── 11. Business and Economy ─────────────────────────────────────────
    biz = _real_items(digest, "business_economy")
    if biz:
        html = "".join(
            _item_block(_esc(_str(i.get("category", ""))),
                        _esc(_clean_src(_str(i.get("source", "")))),
                        _esc(i.get("headline", "")), _esc(i.get("body_text", "")),
                        i.get("url", ""), bar_color="#B8860B")
            for i in biz)
        sections.append(f"""
        <div {_SEC}>
          <a name="business"></a>{_sec_label("Business and Economy")}
          {html}
        </div>""")

    # ── 12. Primary Documents ────────────────────────────────────────────
    docs = _real_items(digest, "primary_documents")
    if docs:
        html = ""
        for item in docs:
            key_line = _esc(item.get("key_line", ""))
            quote = (f"<div style='margin-top:6px;padding:8px 12px;background:#F5F8F9;"
                     f"border-left:2px solid {TEAL};font-size:12px;line-height:1.5;"
                     f"color:#444;font-family:Georgia,serif;font-style:italic;'>"
                     f"&ldquo;{key_line}&rdquo;</div>") if key_line else ""
            html += _item_block(
                _esc(_str(item.get("document_type", "Document"))),
                _esc(_clean_src(_str(item.get("source", "")))),
                _esc(item.get("headline", "")),
                _esc(item.get("body_text", "")),
                item.get("url", ""),
                bar_color="#5D6D7E",
                extra_html=quote,
            )
        sections.append(f"""
        <div {_SEC}>
          <a name="documents"></a>{_sec_label("Primary Documents")}
          {html}
        </div>""")

    # ── 13. Calendar Watch ───────────────────────────────────────────────
    calendar = _real_items(digest, "calendar_watch")
    if calendar:
        rows = ""
        for entry in calendar:
            date_val = _esc(_str(entry.get("date", "")))
            window = _esc(_str(entry.get("window", "")))
            confirmed = bool(entry.get("confirmed"))
            stamp = _esc(_cal_stamp(_str(entry.get("date", "")),
                                    _str(entry.get("window", "")), confirmed))
            stamp_color = NAVY_DEEP if (date_val and confirmed) else "#999"
            rows += f"""
              <tr>
                <td width="110" style="vertical-align:top;padding:8px 12px 8px 0;">
                  <div class="cal-date" style="font-size:11px;font-weight:700;color:{stamp_color};font-family:Arial,sans-serif;letter-spacing:0.5px;">{stamp}</div>
                </td>
                <td style="vertical-align:top;padding:8px 0;border-top:1px solid #EEE;">
                  <div style="font-size:13px;font-weight:600;color:{NAVY};">{_esc(entry.get("event", ""))}</div>
                  <div style="font-size:12px;line-height:1.5;color:#666;">{_esc(entry.get("why_it_matters", ""))}</div>
                </td>
              </tr>"""
        sections.append(f"""
        <div {_SEC}>
          <a name="calendar"></a>{_sec_label("Calendar Watch")}
          <table width="100%" cellpadding="0" cellspacing="0" border="0" class="cal-table">{rows}</table>
        </div>""")

    # ── 14. The Wire ─────────────────────────────────────────────────────
    wire = _real_items(digest, "also_today")
    if wire:
        html = "".join(
            _item_block("", _esc(_clean_src(_str(i.get("source", "")))),
                        _esc(i.get("headline", "")), _esc(i.get("body_text", "")),
                        i.get("url", ""), bar_color="#B0B7BF")
            for i in wire)
        sections.append(f"""
        <div {_SEC}>
          <a name="wire"></a>{_sec_label("The Wire")}
          {html}
        </div>""")

    # ── 15. Op-Eds ───────────────────────────────────────────────────────
    opeds = _real_items(digest, "opeds_today")
    if opeds:
        html = ""
        for item in opeds:
            authors = _esc(_str(item.get("authors", "")))
            arg = _esc(item.get("central_argument", ""))
            so_what = _esc(item.get("policy_so_what", ""))
            extra = ""
            if arg:
                extra += (f"<div style='font-size:12px;line-height:1.5;color:#555;"
                          f"margin-top:3px;'><strong>Argument:</strong> {arg}</div>")
            if so_what:
                extra += (f"<div style='font-size:12px;line-height:1.5;color:{TEAL};"
                          f"margin-top:3px;'><strong>So what:</strong> {so_what}</div>")
            html += _item_block(
                authors, _esc(_clean_src(_str(item.get("source", "")))),
                _esc(item.get("headline", "")), _esc(item.get("summary", "")),
                item.get("url", ""), bar_color="#8E44AD", extra_html=extra)
        sections.append(f"""
        <div {_SEC}>
          <a name="opeds"></a>{_sec_label("Analysis and Opinion")}
          {html}
        </div>""")

    # ── 16. Academic ─────────────────────────────────────────────────────
    academic = _real_items(digest, "academic_today")
    if academic:
        html = ""
        for item in academic:
            tier = _esc(_str(item.get("journal_tier", "")))
            authors = _esc(_str(item.get("authors", "")))
            meta = " &middot; ".join(p for p in (authors, f"Tier {tier}" if tier else "") if p)
            so_what = _esc(item.get("policy_so_what", ""))
            extra = (f"<div style='font-size:12px;line-height:1.5;color:{TEAL};"
                     f"margin-top:3px;'><strong>So what:</strong> {so_what}</div>") if so_what else ""
            html += _item_block(
                meta, _esc(_clean_src(_str(item.get("source", "")))),
                _esc(item.get("headline", "")), _esc(item.get("summary", "")),
                item.get("url", ""), bar_color="#16A085", extra_html=extra)
        sections.append(f"""
        <div {_SEC}>
          <a name="academic"></a>{_sec_label("From the Journals")}
          {html}
        </div>""")

    # ── 17. Footer ───────────────────────────────────────────────────────
    otd_footer = ""
    on_this_day = _real_items(digest, "on_this_day")
    if on_this_day:
        item = on_this_day[0]
        otd_footer = f"""
        <div style="text-align:left;margin-bottom:18px;padding:12px 16px;background:rgba(46,156,176,0.10);border-radius:3px;border-left:2px solid {TEAL_LT};">
          <div style="font-size:9px;text-transform:uppercase;letter-spacing:2px;color:{TEAL_LT};margin-bottom:6px;font-weight:600;">On This Day</div>
          <div style="font-size:12px;color:rgba(255,255,255,0.85);line-height:1.5;font-family:Georgia,serif;"><strong>{_esc(item.get("date", ""))}:</strong> {_esc(item.get("event", ""))}</div>
          <div style="font-size:11px;color:rgba(255,255,255,0.6);font-style:italic;margin-top:4px;line-height:1.4;">{_esc(item.get("relevance", ""))}</div>
        </div>"""

    sections.append(f"""
    <div style="padding:20px 32px;background:{NAVY};text-align:center;" class="sec footer">
      {otd_footer}
      <div style="font-size:9px;text-transform:uppercase;letter-spacing:2px;color:rgba(255,255,255,0.45);font-family:Arial,sans-serif;line-height:2;">
        CSIS Australia Chair &nbsp;&middot;&nbsp; Australia Chair Daily Brief &nbsp;&middot;&nbsp; Generated {gen_time}
      </div>
      <a href="#top" style="font-size:10px;color:rgba(255,255,255,0.4);text-decoration:none;letter-spacing:1px;">&#8593; Back to top</a>
    </div>""")

    body = "\n".join(sections)

    return f"""<!DOCTYPE html>
<html lang="en" xmlns="http://www.w3.org/1999/xhtml">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <meta http-equiv="X-UA-Compatible" content="IE=edge">
  <title>Australia Chair Daily Brief &ndash; {_esc(date_str)}</title>
  <style type="text/css">
    body, table, td, div, p {{ margin:0; padding:0; }}
    img {{ border:0; display:block; }}

    @media only screen and (max-width: 620px) {{
      .wrapper {{ width:100% !important; }}
      .sec, .footer {{ padding:16px 14px !important; }}
      .cal-table td[width="110"] {{ width:80px !important; padding:8px 8px 8px 0 !important; }}
      .cal-date {{ font-size:11px !important; }}
      .china-dark > div {{ padding:16px 14px !important; }}
      h1 {{ font-size:19px !important; }}
      h2 {{ font-size:12px !important; }}
      h3 {{ font-size:14px !important; }}
      .story-card {{ padding:12px 10px !important; }}
      p, div {{ word-wrap:break-word !important; overflow-wrap:break-word !important; }}
      img {{ max-width:100% !important; height:auto !important; }}
      body, td, div, p, span {{ font-size:14px !important; -webkit-text-size-adjust:100%; }}
      div[style*="font-size:9px"], div[style*="font-size:10px"],
      span[style*="font-size:9px"], span[style*="font-size:10px"] {{ font-size:11px !important; }}
      a {{ min-height:44px; min-width:44px; display:inline-block; line-height:44px; }}
      p a, div a, td a {{ min-height:auto; min-width:auto; display:inline; padding:6px 0; line-height:inherit; }}
    }}

    @media only screen and (min-width: 621px) and (max-width: 768px) {{
      .wrapper {{ width:100% !important; }}
      .sec, .footer {{ padding:14px 20px !important; }}
      h1 {{ font-size:21px !important; }}
    }}

    @media (prefers-color-scheme: dark) {{
      body {{ background:#121212 !important; }}
      .wrapper {{ background:#1a1a1a !important; }}
      .wrapper .sec {{ background:#222 !important; border-bottom-color:#333 !important; }}
      .wrapper h1, .wrapper h2, .wrapper h3 {{ color:#E0E0E0 !important; }}
      .wrapper p, .wrapper div, .wrapper td, .wrapper span {{ color:#CCC !important; }}
      .wrapper a {{ color:{TEAL_LT} !important; }}
      .wrapper .footer {{ background:#0F1A2E !important; }}
      .wrapper .story-card {{ background:#2a2a2a !important; border-color:#333 !important; }}
      .wrapper .china-dark {{ background:#101E2A !important; }}
    }}
  </style>
  <!--[if mso]>
  <style type="text/css">
    table {{ border-collapse:collapse; }}
  </style>
  <![endif]-->
</head>
<body style="margin:0;padding:0;background:#F2F3F5;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Helvetica,Arial,sans-serif;-webkit-text-size-adjust:100%;-ms-text-size-adjust:100%;">
  <!-- The frame is a TABLE whose width is an HTML ATTRIBUTE, not a div with a
       CSS max-width. Gmail and most clients drop every stylesheet block when a
       recipient forwards or replies, and many ignore max-width on a div, so the
       old wrapper lost its width on forward and the content sprawled. An
       attribute survives stylesheet stripping, so a forwarded copy keeps its
       shape. class="wrapper" stays so the mobile media query can still flex it
       to 100% while the stylesheet is intact. -->
  <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="margin:0;padding:0;background:#F2F3F5;">
    <tr>
      <td align="center" valign="top" style="padding:0;">
        <!--[if mso]><table width="680" cellpadding="0" cellspacing="0" border="0" align="center"><tr><td><![endif]-->
        <table role="presentation" class="wrapper" width="680" cellpadding="0" cellspacing="0" border="0" align="center" style="width:680px;max-width:680px;margin:0 auto;background:#FFFFFF;box-shadow:0 2px 20px rgba(0,0,0,0.08);">
          <tr>
            <td style="padding:0;">
              {body}
            </td>
          </tr>
        </table>
        <!--[if mso]></td></tr></table><![endif]-->
      </td>
    </tr>
  </table>
</body>
</html>"""


if __name__ == "__main__":
    import json
    from pathlib import Path
    digest = json.loads(Path("digest.json").read_text(encoding="utf-8"))
    html = render(digest)
    Path("latest.html").write_text(html, encoding="utf-8")
    print(f"Rendered {len(html):,} bytes -> latest.html")
