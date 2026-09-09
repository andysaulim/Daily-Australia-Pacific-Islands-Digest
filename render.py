"""
Australia Daily Brief: HTML Renderer
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



# Grounds already dark in both schemes, or white type on an accent fill:
# these need no dark variant and the coverage guard skips them.
_DARK_EXEMPT = {
    "#041a33", "#0a0f1e", "#0d1b2a", "#0e1c33", "#0f1b30", "#121212",
    "#162340", "#1a1a1a", "#1b2a4a", "#1e2126", "#262a30", "#2e3644",
    "#051f3d", "#6e0019", "#bc002d", "#de2910", "#17798c",
}


def _check_dark_coverage(html: str) -> list[str]:
    """Every inline colour in the output must have a dark counterpart.

    Hand-maintained dark rules drift silently: a colour added to the brief
    keeps its light value in dark mode, so a reader sees near-black type on a
    near-black ground and nothing catches it, because absent CSS is not an
    error. This is that catch, and the render test calls it.
    """
    import re as _re
    body = html.split("<body", 1)[-1]
    _m = _re.search(r"prefers-color-scheme:\s*dark", html)
    dark = html[_m.start():_m.start() + 20000] if _m else ""
    covered = {c.lower() for c in _re.findall(r"#[0-9A-Fa-f]{3,6}", dark)} | _DARK_EXEMPT
    missing = []
    for hexv in {c.lower() for c in
                 _re.findall(r"(?<!-)color:\s*(#[0-9A-Fa-f]{3,6})", body)}:
        if hexv not in covered:
            missing.append(f"text colour {hexv} has no dark rule")
    for hexv in {c.lower() for c in
                 _re.findall(r"background(?:-color)?:\s*(#[0-9A-Fa-f]{3,6})", body)}:
        if hexv not in covered:
            missing.append(f"background {hexv} has no dark rule")
    return sorted(missing)



def _emphasis(text: str) -> str:
    """Turn the model's **bold** and *italic* marks into tags, after escaping.

    Names and figures are what a reader scans a policy brief for, so the
    prompt asks for a person's name in **double asterisks** on first mention
    and a quantity in *single* ones. The model cannot emit HTML — every field
    goes through _esc() first — so this converts a narrow, fixed convention
    afterwards. Anything that is not one of these two exact shapes stays
    literal text, which is what keeps the escaping meaningful.
    """
    import re as _re
    text = _re.sub(r"\*\*(?!\s)([^*]{1,80}?)(?<!\s)\*\*",
                   r'<strong style="font-weight:700;">\1</strong>', text)
    text = _re.sub(r"(?<![*\w])\*(?!\s)([^*]{1,60}?)(?<!\s)\*(?![*\w])",
                   r"<em>\1</em>", text)
    return text

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


RING_ON_DARK = "#1B90A6"   # the accent, lightened to read on the black bar

def _sec_label(label: str, color: str = RING_ON_DARK) -> str:
    """A section bar: black field, an accent ring, a white letterspaced label.

    The label used to be small coloured type over a hairline rule. In a
    2,000-word brief with a dozen sections that gave the reader no stop
    between them: the sections blurred into one another and a scan found no
    purchase. This is a hard stop.

    Black rather than each edition's own colour. Four editions with four
    coloured bars would read as decoration; black reads as structure, and the
    accent lands as one deliberate mark instead of a whole field. It is also
    the only colour that leaves the masthead as the single place a reader
    meets the edition's identity.

    Solid background and a text glyph, so it survives clients that block
    images and clients that drop background images.
    """
    return (
        '<table width="100%" cellpadding="0" cellspacing="0" border="0" '
        'class="sec-bar" style="background:#14181F;margin-bottom:14px;">'
        '<tr><td style="padding:9px 14px;">'
        f'<span style="font-family:Arial,sans-serif;font-size:12px;color:{color};'
        'line-height:1;vertical-align:middle;margin-right:9px;">&#9675;</span>'
        '<span style="font-family:Arial,sans-serif;font-size:11px;font-weight:700;'
        'text-transform:uppercase;letter-spacing:2px;color:#FFFFFF;'
        f'vertical-align:middle;">{label}</span>'
        '</td></tr></table>')


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


MUTE = "#6B7280"



def _subhead(text: str) -> str:
    """A group label inside a section.

    Also Today ran every category together, so it read as one
    undifferentiated stream. One heading per subject beats a category
    repeated in grey on every row.
    """
    return (f'<div style="font-family:Arial,sans-serif;font-size:11px;font-weight:700;'
            f'text-transform:uppercase;letter-spacing:1.5px;color:#55607A;'
            f'margin:18px 0 9px;padding-bottom:5px;border-bottom:1px solid #E4E7EB;">'
            f'{text}</div>')


def _compact_row(cat: str, headline: str, url: str, src: str, body: str = "") -> str:
    """One wire item, in the same shape as every other news item in the brief.

    This was a two-column table: a category cell on the left, headline and a
    grey meta line on the right. It read as a different kind of object from
    the sections around it — the eye had to change mode to scan it, which is
    the opposite of what a wire is for.

    It is now the house item: a rule down the left, TAG · SOURCE in small grey
    caps, the headline, then the body. Same as the sections that read tightest,
    so The Wire scans like the rest of the brief instead of like a table.
    """
    tag_line = " &middot; ".join(x for x in (cat, src) if x)
    return (f'<div style="margin-bottom:11px;padding-left:12px;'
            f'border-left:3px solid {TEAL};">'
            + (f'<div style="font-family:Arial,sans-serif;font-size:10px;color:{MUTE};'
               f'text-transform:uppercase;letter-spacing:1px;font-weight:600;'
               f'margin-bottom:2px;">{tag_line}</div>' if tag_line else "")
            + f'<div style="font-family:Georgia,serif;font-size:14px;font-weight:600;'
              f'color:{INK};line-height:1.4;">{_link_or_text(headline, url)}</div>'
            + (f'<div style="font-family:Georgia,serif;font-size:13px;line-height:1.5;'
               f'color:#4A5260;margin-top:2px;">{body}</div>' if body else "")
            + '</div>')


def _cat_color(cat: str, default: str = NAVY) -> str:
    return _CAT_COLORS.get(_str(cat).strip(), default)


def _cal_block(date_val: str, confirmed: bool) -> tuple[str, str]:
    """("Sep", "12") for a confirmed ISO date, ("", "") otherwise.

    Only a confirmed, parseable date earns the solid block. A window such as
    "expected in August" has no day, and picking one would state a precision
    the source did not.
    """
    if not (date_val and confirmed):
        return "", ""
    try:
        when = datetime.strptime(date_val[:10], "%Y-%m-%d")
    except ValueError:
        return "", ""
    return when.strftime("%b"), str(when.day)


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
              <div style="font-size:13px;line-height:1.5;color:#555;">{body}</div>
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

    # ── 0. Utility row: internal-use notice left, links right ─────────────
    # The house treatment, matching the other editions. "Email not rendering?"
    # asked the reader to diagnose their own client; the links are buttons and
    # say what they do. Notice and links share one row rather than taking a
    # band each, which is ~90px of chrome above the nameplate.
    if web_url:
        _base = web_url[:-len("latest.html")] if web_url.endswith("latest.html") else ""
        _a = ('display:inline-block;padding:4px 12px;margin:0 2px;'
              'font-family:Arial,sans-serif;font-size:11px;font-weight:700;'
              'letter-spacing:0.5px;color:rgba(255,255,255,0.92);'
              'background:rgba(255,255,255,0.10);'
              'border:1px solid rgba(255,255,255,0.22);border-radius:3px;'
              'text-decoration:none;white-space:nowrap;')
        _links = [f'<a href="{_esc(web_url)}" style="{_a}">Read online</a>']
        # The dated PDF when the run published one, so the link names the issue
        # a reader is holding rather than whatever "latest" has since become.
        _pdf = digest.get("pdf_url") or ""
        if _pdf:
            _links.append(f'<a href="{_esc(_pdf)}" style="{_a}">Download PDF</a>')
        if _base:
            _links.append(f'<a href="{_esc(_base + "archive.html")}" style="{_a}">Past issues</a>')
        sections.append(f"""
        <table width="100%" cellpadding="0" cellspacing="0" border="0" style="background:#2E3644;" class="util-row no-print">
          <tr>
            <td class="util-cell" style="padding:7px 32px;font-family:Arial,sans-serif;font-size:10px;font-weight:700;letter-spacing:2px;text-transform:uppercase;color:rgba(255,255,255,0.72);white-space:nowrap;">For Internal Use Only</td>
            <td class="util-cell" align="right" style="padding:5px 32px 5px 0;text-align:right;">{''.join(_links)}</td>
          </tr>
        </table>
        """)

    # ── 1. Header ────────────────────────────────────────────────────────
    re_block = ""
    if re_line:
        re_block = (f"<div style='margin-top:12px;padding-top:12px;"
                    f"border-top:1px solid rgba(46,156,176,0.35);font-size:13px;"
                    f"color:rgba(255,255,255,0.85);font-family:Georgia,serif;line-height:1.5;'>"
                    f"<strong style='color:#FFFFFF;font-size:11px;letter-spacing:1px;'>RE:</strong>"
                    f"&nbsp; {re_line}</div>")

    # The house masthead, identical in all four briefs. Only the band colour,
    # the chair name and the title differ. Left column: chair, title, date.
    # Right column, bottom-aligned: the issue meta. Then a rule and the RE
    # line across the full width.
    #
    # It is written out rather than shared because these are four repositories
    # with no common package — so it is copied verbatim, and any change has to
    # be made in all four.
    sections.append(f"""
    <a name="top" id="top"></a>
    <div bgcolor="{TEAL}" style="background-color:{TEAL};color:#fff;padding:16px 32px 16px;border-bottom:1px solid rgba(255,255,255,0.18);" class="sec mast-band">
      <table width="100%" cellpadding="0" cellspacing="0" border="0"><tr>
        <td class="mast-main" style="vertical-align:top;">
          <div style="font-family:Arial,sans-serif;font-size:11px;font-weight:700;letter-spacing:2px;text-transform:uppercase;color:rgba(255,255,255,0.78);margin-bottom:7px;">CSIS Australia Chair</div>
          <h1 style="margin:0 0 4px 0;font-size:26px;font-weight:700;font-family:Georgia,'Times New Roman',serif;color:#fff;letter-spacing:0.5px;">
            Australia Daily Brief
          </h1>
          <div style="margin-top:2px;font-size:16px;font-weight:400;color:rgba(255,255,255,0.85);font-family:Georgia,serif;">{_esc(date_str)}</div>
        </td>
        <td class="mast-meta" style="vertical-align:bottom;text-align:right;">
          <div style="font-family:Arial,sans-serif;font-size:11px;letter-spacing:0.5px;color:rgba(255,255,255,0.72);white-space:nowrap;">{word_count:,} words &middot; {read_min} min read</div>
        </td>
      </tr></table>
      {re_block}
    </div>
    """)

    # Placeholder for the jump row, resolved at the end once every section is
    # known and its anchors can be checked.
    sections.append("%%NAV%%")

    # ── 2. Market strip ──────────────────────────────────────────────────
    # Renders whatever markets.py resolved, in its declared order, and nothing
    # when it resolved nothing. Green and red here are the one place in the
    # brief where colour is doing real work rather than decoration, so they are
    # the only two outside the three-geography palette.
    markets = digest.get("market_indicators") or {}
    if markets:
        # One row of four on the dark ground the other editions use. It was a
        # pale grey band of inline-blocks — as many indicators as resolved, in
        # navy on near-white — which read as a caption rather than as the
        # day's figures, and looked nothing like the other three briefs.
        #
        # Four tiles fit a phone without wrapping and without the horizontal
        # scrollbar the old nowrap table caused. Whatever resolved beyond the
        # first four is dropped rather than wrapped: the strip is a glance.
        _MONO = "'Courier New',Courier,monospace"
        _resolved = [m for m in markets.values()
                     if isinstance(m, dict) and m.get("value")][:4]
        if _resolved:
            _w = int(100 / len(_resolved))
            tiles = ""
            for idx, m in enumerate(_resolved):
                pct = m.get("change_pct", 0) or 0
                colour = "#5FD08A" if pct > 0 else "#FF8A8A" if pct < 0 else "#9DB2CE"
                sign = "+" if pct > 0 else ""
                edge = ("" if idx == 0 else
                        "border-left:1px solid rgba(255,255,255,0.10);")
                tiles += (f'<td width="{_w}%" align="center" '
                          f'style="padding:11px 6px 13px;{edge}">'
                          f'<div style="font-size:10px;text-transform:uppercase;'
                          f'letter-spacing:1px;color:#9DB2CE;">'
                          f'{_esc(m.get("label", ""))}</div>'
                          f'<div style="font-family:{_MONO};font-size:16px;'
                          f'font-weight:700;color:#fff;margin-top:3px;">'
                          f'{_esc(m["value"])}</div>'
                          f'<div style="font-family:{_MONO};font-size:11px;'
                          f'margin-top:2px;color:{colour};">{sign}{pct:.2f}%</div></td>')
            sections.append(
                f'<table class="mkt-table" width="100%" cellpadding="0" '
                f'cellspacing="0" border="0" style="background:{NAVY_DEEP};'
                f'color:#fff;border-bottom:1px solid rgba(255,255,255,0.10);">'
                f'<tr>{tiles}</tr></table>')

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
                  <div style="width:24px;height:24px;border-radius:50%;background:{TEAL};color:#FFFFFF;text-align:center;line-height:24px;font-size:13px;font-weight:700;font-family:Georgia,serif;">{i + 1}</div>
                </td>
                <td style="padding-left:10px;vertical-align:top;">
                  <div style="font-size:14px;line-height:1.6;color:{INK};font-family:Georgia,serif;">{text}</div>
                </td>
              </tr>
            </table>"""
        # A tinted panel with a rule down the left, as in Korea. The near-white
        # #FAFBFC ground was indistinguishable from the page, so the summary
        # read as the first news section rather than as the summary of all of
        # them.
        sections.append(f"""
        <div {_SEC}>
          <a name="memo" id="memo"></a>
          <table width="100%" cellpadding="0" cellspacing="0" border="0" class="glance-panel" style="background:#E9F2F4;border-left:3px solid {TEAL};">
            <tr><td style="padding:16px 20px 8px;">
              {_sec_label("Today at a Glance")}
              {memo_html}
            </td></tr>
          </table>
        </div>""")

    # ── 4. Top Stories ───────────────────────────────────────────────────
    top_stories = _real_items(digest, "top_stories")
    if top_stories:
        html = ""
        for story in top_stories:
            cat = _esc(_str(story.get("category_tag", story.get("category", ""))))
            headline = _esc(story.get("headline", ""))
            body = _esc(story.get("body", ""))
            src_line = _esc(_clean_src(_str(story.get("src_line", story.get("source", "")))))
            url = story.get("url", "")
            cat_badge = (f'<span style="display:inline-block;font-size:10px;'
                         f'text-transform:uppercase;letter-spacing:1px;color:{TEAL};'
                         f'font-weight:700;margin-bottom:6px;">{cat}</span>') if cat else ""
            html += f"""
            <div class="story-card" style="margin-bottom:14px;padding:14px 16px;background:#fff;border-radius:3px;border-left:4px solid {_cat_color(_str(story.get("category_tag", story.get("category", ""))), NAVY_DEEP)};box-shadow:0 1px 3px rgba(0,0,0,0.06);">
              {cat_badge}
              <h3 style="margin:0 0 8px 0;font-size:16px;color:{NAVY_DEEP};font-family:Georgia,serif;line-height:1.4;">
                {_link_or_text(headline, url, style=f"color:{NAVY_DEEP};text-decoration:none;")}
              </h3>
              <p style="margin:0 0 8px 0;font-size:13px;line-height:1.6;color:#444;">{body}</p>
              <div style="font-size:10px;color:#AAA;margin-top:6px;">{src_line}</div>
            </div>"""
        sections.append(f"""
        <div {_SEC}>
          <a name="top-stories" id="top-stories"></a>{_sec_label("Top Stories")}
          {html}
        </div>""")

    # ── 5. Overnight Flash ───────────────────────────────────────────────
    overnight = _real_items(digest, "overnight_items")
    if overnight:
        # A scan list, not a second Top Stories. One rule down the left, one
        # line per item, so the eye runs vertically instead of stopping at a
        # card border every three lines. The cards above carry the weight;
        # this section carries the breadth.
        #
        # It also drops the alert frame. Every issue has an overnight section,
        # so a red top rule on all of them said nothing about any of them; a
        # genuine signal still gets its badge on the row.
        html = ""
        for item in overnight:
            cat = _esc(_str(item.get("category", "")))
            h = _esc(item.get("headline", ""))
            b = _esc(item.get("body_text", ""))
            src = _esc(_clean_src(_str(item.get("source", ""))))
            url = item.get("url", "")
            badge = _signal_badge(item.get("signal_type", ""))
            tail = (f'<span style="color:{MUTE};"> &mdash; {b}</span>' if b else "")
            html += (f'<tr>'
                     f'<td style="padding:7px 10px 7px 0;vertical-align:top;white-space:nowrap;'
                     f'font-family:Arial,sans-serif;font-size:10px;font-weight:700;'
                     f'letter-spacing:0.5px;text-transform:uppercase;color:{TEAL};'
                     f'border-bottom:1px solid #EEF0F3;">{cat}</td>'
                     f'<td style="padding:7px 0;vertical-align:top;font-family:Georgia,serif;'
                     f'font-size:13px;line-height:1.45;color:{INK};'
                     f'border-bottom:1px solid #EEF0F3;">'
                     f'{_link_or_text(h, url)}{tail}'
                     f'<span style="font-family:Arial,sans-serif;font-size:11px;color:{MUTE};">'
                     f' &middot; {src}</span>'
                     + (f'<div style="margin-top:4px;">{badge}</div>' if badge else "")
                     + f'</td></tr>')
        html = (f'<table width="100%" cellpadding="0" cellspacing="0" border="0" '
                f'class="flash-table" style="border-top:2px solid {TEAL};">{html}</table>')
        sections.append(f"""
        <div {_SEC}>
          <a name="overnight" id="overnight"></a>{_sec_label("Overnight")}
          {html}
        </div>""")

    # ── 5b. Stat of the Day ──────────────────────────────────────────────
    # A light panel, matching the other editions. It is the one number the
    # reader should carry out of the brief, so it gets the column the rest of
    # the brief reads down rather than a band across the page.
    #
    # The model returns {} on a day whose articles hold no figure worth
    # pulling out, and an absent panel is correct on such a day — a stat
    # invented to fill the slot is exactly what SOURCE-OR-SKIP forbids.
    key_stat = digest.get("key_stat") or {}
    _ks_num = str(key_stat.get("number", "")).strip() if isinstance(key_stat, dict) else ""
    if _ks_num:
        _ks_context = _esc(key_stat.get("context", ""))
        _ks_source = _esc(_clean_src(_str(key_stat.get("source", ""))))
        sections.append(f"""
        <div {_SEC}>
          <a name="key-stat" id="key-stat"></a>{_sec_label("Stat of the Day")}
          <table width="100%" cellpadding="0" cellspacing="0" border="0" style="background:#EFF6F8;border-left:3px solid {TEAL};border-radius:3px;">
            <tr><td style="padding:14px 16px;">
              <div style="font-family:Georgia,serif;font-size:26px;font-weight:700;color:{TEAL};line-height:1;">{_esc(_ks_num)}</div>
              <div style="font-family:Georgia,serif;font-size:14px;color:{INK};margin-top:5px;line-height:1.4;">{_esc(key_stat.get("label", ""))}</div>
              {"<div style='font-family:Georgia,serif;font-size:13px;color:#4A5260;margin-top:4px;line-height:1.5;'>" + _ks_context + "</div>" if _ks_context else ""}
              {"<div style='font-family:Arial,sans-serif;font-size:11px;color:#55607A;margin-top:7px;'>" + _ks_source + "</div>" if _ks_source else ""}
            </td></tr>
          </table>
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
          <a name="aukus" id="aukus"></a>{_sec_label("AUKUS Watch")}
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
          <a name="pacific" id="pacific"></a>{_sec_label("Pacific Wire", color=TEAL)}
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
          <a name="nz" id="nz"></a>{_sec_label("New Zealand", color=NZ_GREEN)}
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
            reaction = ('<span style="display:inline-block;font-size:10px;padding:1px 6px;'
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
              <div style="font-size:13px;line-height:1.5;color:rgba(255,255,255,0.75);">{_esc(item.get("body_text", ""))}</div>
            </div>"""
        sections.append(f"""
        <div bgcolor="{NAVY_DEEP}" style="background-color:{NAVY_DEEP};padding:20px 32px;" class="sec china-dark">
          <a name="china-pacific" id="china-pacific"></a>
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
          <a name="canberra" id="canberra"></a>{_sec_label("Canberra Politics")}
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
          <a name="business" id="business"></a>{_sec_label("Business and Economy")}
          {html}
        </div>""")

    # ── 12. Primary Documents ────────────────────────────────────────────
    docs = _real_items(digest, "primary_documents")
    if docs:
        html = ""
        for item in docs:
            key_line = _esc(item.get("key_line", ""))
            quote = (f"<div style='margin-top:6px;padding:8px 12px;background:#F5F8F9;"
                     f"border-left:2px solid {TEAL};font-size:13px;line-height:1.5;"
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
          <a name="documents" id="documents"></a>{_sec_label("Primary Documents")}
          {html}
        </div>""")

    # ── 13. Upcoming ─────────────────────────────────────────────────────
    # "Calendar Watch" in this edition, "Upcoming" everywhere else; one name.
    # A confirmed date now gets the solid accent block Korea uses, so the
    # column scans as a calendar. An entry with only a window keeps its text
    # stamp — inventing a day for "expected in August" would be worse than
    # showing the phrase.
    calendar = _real_items(digest, "calendar_watch")
    if calendar:
        rows = ""
        for entry in calendar:
            confirmed = bool(entry.get("confirmed"))
            mon, day = _cal_block(_str(entry.get("date", "")), confirmed)
            if mon:
                stamp_cell = (f'<table cellpadding="0" cellspacing="0" border="0" '
                              f'style="background:{TEAL};"><tr>'
                              f'<td align="center" style="padding:4px 0 5px;width:46px;">'
                              f'<div style="font-family:Arial,sans-serif;font-size:10px;'
                              f'font-weight:700;letter-spacing:1.5px;'
                              f'color:rgba(255,255,255,0.85);">{_esc(mon)}</div>'
                              f'<div style="font-family:Georgia,serif;font-size:16px;'
                              f'font-weight:700;color:#fff;line-height:1;">{_esc(day)}</div>'
                              f'</td></tr></table>')
                cell_width = "54"
            else:
                stamp = _esc(_cal_stamp(_str(entry.get("date", "")),
                                        _str(entry.get("window", "")), confirmed))
                stamp_cell = (f'<div class="cal-date" style="font-size:11px;font-weight:700;'
                              f'color:{MUTE};font-family:Arial,sans-serif;'
                              f'letter-spacing:0.5px;">{stamp}</div>')
                cell_width = "110"
            rows += f"""
              <tr>
                <td width="{cell_width}" style="vertical-align:top;padding:9px 12px 9px 0;">{stamp_cell}</td>
                <td style="vertical-align:top;padding:9px 0;border-bottom:1px solid #E8E8E8;">
                  <div style="font-family:Georgia,serif;font-size:14px;font-weight:700;color:{NAVY};">{_esc(entry.get("event", ""))}</div>
                  <div style="font-family:Georgia,serif;font-size:13px;line-height:1.45;color:#4A5260;margin-top:3px;">{_esc(entry.get("why_it_matters", ""))}</div>
                </td>
              </tr>"""
        sections.append(f"""
        <div {_SEC}>
          <a name="calendar" id="calendar"></a>{_sec_label("Upcoming")}
          <table width="100%" cellpadding="0" cellspacing="0" border="0" class="cal-table">{rows}</table>
        </div>""")

    # ── 14. Also Today (the wire) ────────────────────────────────────────
    wire = _real_items(digest, "also_today")
    if wire:
        # Grouped by subject. Ungrouped it was a run of identical grey bars
        # with nothing to tell them apart, so a reader looking for the Pacific
        # item had to read all of them.
        _groups = {}
        for i in wire:
            key = _str(i.get("category", "")).strip() or "Other"
            _groups.setdefault(key.title(), []).append(i)
        html = ""
        _multi = len(_groups) > 1
        for _cat, _items in _groups.items():
            rows = "".join(
                _compact_row(cat="" if _multi else _esc(_cat),
                             headline=_esc(i.get("headline", "")),
                             url=i.get("url", ""),
                             src=_esc(_clean_src(_str(i.get("source", "")))),
                             body=_esc(i.get("body_text", "")))
                for i in _items)
            html += ((_subhead(_esc(_cat)) if _multi else "")
                     + rows)
        sections.append(f"""
        <div {_SEC}>
          <a name="wire" id="wire"></a>{_sec_label("The Wire")}
          {html}
        </div>""")

    # ── 15. Op-Eds ───────────────────────────────────────────────────────
    opeds = _real_items(digest, "opeds_today")
    if opeds:
        html = ""
        for item in opeds:
            authors = _esc(_str(item.get("authors", "")))
            arg = _esc(item.get("central_argument", ""))
            extra = ""
            if arg:
                extra += (f"<div style='font-size:13px;line-height:1.5;color:#555;"
                          f"margin-top:3px;'><strong>Argument:</strong> {arg}</div>")
            html += _item_block(
                authors, _esc(_clean_src(_str(item.get("source", "")))),
                _esc(item.get("headline", "")), _esc(item.get("summary", "")),
                item.get("url", ""), bar_color="#8E44AD", extra_html=extra)
        sections.append(f"""
        <div {_SEC}>
          <a name="opeds" id="opeds"></a>{_sec_label("Analysis and Opinion")}
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
            extra = ""
            html += _item_block(
                meta, _esc(_clean_src(_str(item.get("source", "")))),
                _esc(item.get("headline", "")), _esc(item.get("summary", "")),
                item.get("url", ""), bar_color="#16A085", extra_html=extra)
        sections.append(f"""
        <div {_SEC}>
          <a name="academic" id="academic"></a>{_sec_label("From the Journals")}
          {html}
        </div>""")

    # ── 17. Footer ───────────────────────────────────────────────────────
    otd_footer = ""
    on_this_day = _real_items(digest, "on_this_day")
    if on_this_day:
        item = on_this_day[0]
        otd_footer = f"""
        <div style="text-align:left;margin-bottom:18px;padding:12px 16px;background:rgba(46,156,176,0.10);border-radius:3px;border-left:2px solid {TEAL_LT};">
          <div style="font-size:10px;text-transform:uppercase;letter-spacing:2px;color:{TEAL_LT};margin-bottom:6px;font-weight:600;">On This Day</div>
          <div style="font-size:13px;color:rgba(255,255,255,0.85);line-height:1.5;font-family:Georgia,serif;"><strong>{_esc(item.get("date", ""))}:</strong> {_esc(item.get("event", ""))}</div>
          <div style="font-size:11px;color:rgba(255,255,255,0.6);font-style:italic;margin-top:4px;line-height:1.4;">{_esc(item.get("relevance", ""))}</div>
        </div>"""

    # Both footer links point into the published archive, so neither exists
    # until a run has published one.
    _foot_links = ""
    if web_url:
        _fa = 'color:rgba(255,255,255,0.95);text-decoration:none;'
        _fbase = web_url[:-len("latest.html")] if web_url.endswith("latest.html") else ""
        _parts = [f'<a href="{_esc(web_url)}" style="display:inline-block;padding:6px 15px;margin:0 4px;font-family:Arial,sans-serif;font-size:11px;font-weight:700;letter-spacing:0.5px;color:#14181F;background:#FFFFFF;border-radius:14px;text-decoration:none;white-space:nowrap;">Read online</a>']
        if _fbase:
            _parts.append(f'<a href="{_esc(_fbase + "archive.html")}" style="display:inline-block;padding:6px 15px;margin:0 4px;font-family:Arial,sans-serif;font-size:11px;font-weight:700;letter-spacing:0.5px;color:#14181F;background:#FFFFFF;border-radius:14px;text-decoration:none;white-space:nowrap;">Past issues</a>')
        _foot_links = ('<div style="margin-top:11px;font-family:Arial,sans-serif;'
                       'font-size:11px;letter-spacing:0.5px;">'
                       + '<span style="color:rgba(255,255,255,0.45);">&nbsp;&middot;&nbsp;</span>'.join(_parts)
                       + '</div>')

    sections.append(f"""
    <!-- The house footer. Korea carries a CSIS lockup built in HTML; this
         edition has no wordmark to reproduce, so it leads with the chair name
         instead. Everything else matches: centred, the city and domain on
         their own line, the links as links rather than a run-on sentence, and
         the disclaimer set in the reading face. -->
    <table width="100%" cellpadding="0" cellspacing="0" border="0" class="sec footer" style="background:#14181F;border-top:4px solid {TEAL};">
      <tr><td style="padding:20px 32px 6px;text-align:center;">
        {otd_footer}
        <div style="font-family:Georgia,serif;font-size:38px;font-weight:700;color:#FFFFFF;letter-spacing:1px;line-height:1.1;">CSIS Australia Chair</div>
        <div style="font-family:Arial,sans-serif;font-size:10px;letter-spacing:2px;text-transform:uppercase;color:rgba(255,255,255,0.72);margin-top:6px;">Australia Daily Brief</div>
        <div style="font-family:Georgia,serif;font-size:13px;color:rgba(255,255,255,0.72);margin-top:12px;">Washington, D.C.</div>
        {_foot_links}
      </td></tr>
      <tr><td style="padding:16px 32px 4px;text-align:center;">
    <div style="font-family:Georgia,serif;font-size:13px;line-height:1.6;color:rgba(255,255,255,0.80);max-width:520px;margin:0 auto;">
      You are receiving the Australia Daily Brief as a member of the CSIS Australia Chair distribution list.
    </div>
  </td></tr>
  <tr><td style="padding:14px 32px 10px;text-align:center;">
        <div style="border-top:1px solid rgba(255,255,255,0.14);padding-top:12px;font-family:Georgia,serif;font-size:13px;line-height:1.6;color:rgba(255,255,255,0.82);max-width:560px;margin:0 auto;">
          This newsletter is automatically generated, so it may contain errors. Please check all information and sources before citing.
          To report errors or other issues, please contact Andy Lim at <a href="mailto:alim@csis.org" style="color:rgba(255,255,255,0.95);">alim@csis.org</a>.
        </div>
      </td></tr>
      <tr><td style="padding:0 32px 20px;text-align:center;">
        <div style="font-family:Arial,sans-serif;font-size:10px;letter-spacing:0.5px;color:rgba(255,255,255,0.70);margin-bottom:9px;">generated {gen_time}</div>
        <a href="#top" style="font-family:Arial,sans-serif;font-size:10px;letter-spacing:1.5px;text-transform:uppercase;color:rgba(255,255,255,0.95);text-decoration:none;">&#8593; Back to top</a>
      </td></tr>
    </table>
<table width="100%" cellpadding="0" cellspacing="0" border="0" class="footer-end" style="background:#FFFFFF;">
  <tr><td style="padding:12px 32px 18px;text-align:center;font-family:Arial,sans-serif;font-size:10px;letter-spacing:0.5px;color:#6B7280;">
    &copy; {now.year} Center for Strategic and International Studies
  </td></tr>
</table>""")

    # ── Jump row ──────────────────────────────────────────────────────────
    # The brief is too long to scan end to end and the only link in it was
    # "back to top". Label and anchor are paired here and each pair is kept
    # only when the section actually emitted its anchor, so a quiet day that
    # drops sections simply gets fewer links rather than dead ones.
    _NAV = [("Top Stories", "top-stories"), ("Overnight", "overnight"),
            ("Stat", "key-stat"), ("AUKUS", "aukus"), ("Pacific", "pacific"),
            ("New Zealand", "nz"), ("China-Pacific", "china-pacific"),
            ("Canberra", "canberra"), ("Markets", "business"),
            ("Documents", "documents"), ("Upcoming", "calendar"),
            ("The Wire", "wire"), ("Analysis", "opeds")]
    body = "\n".join(sections)
    _links = [f'<a href="#{_a}" style="color:{TEAL};text-decoration:underline;'
              f'text-underline-offset:2px;white-space:nowrap;">{_l}</a>'
              for _l, _a in _NAV if f'a name="{_a}"' in body]
    _nav_html = ""
    if len(_links) >= 4:
        # Named and underlined. Unlabelled and unadorned it reads as a
        # subtitle rather than a menu, and goes unused.
        _nav_html = ('<div class="nav-row sec" style="background:#F7F8FA;'
                     'border-bottom:1px solid #E4E7EB;padding:9px 32px;'
                     'text-align:center;font-family:Arial,sans-serif;'
                     'font-size:11px;line-height:1.9;color:#6B7280;">'
                     '<span style="font-size:10px;font-weight:700;'
                     'text-transform:uppercase;letter-spacing:1.5px;'
                     'color:#6B7280;">In this issue &nbsp;</span>'
                     + ' &nbsp;&middot;&nbsp; '.join(_links) + '</div>')
    body = body.replace("%%NAV%%", _nav_html)
    return _shell(body, date_str)


def _shell(body: str, date_str: str) -> str:
    """The forwarding-safe email frame, shared by the daily and weekly editions.

    Extracted so weekly.py cannot drift away from the fixed-width table that
    survives a forward. Anything that lives here is inherited by both.
    """
    return f"""<!DOCTYPE html>
<html lang="en" xmlns="http://www.w3.org/1999/xhtml">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <meta http-equiv="X-UA-Compatible" content="IE=edge">
  <title>Australia Daily Brief &ndash; {_esc(date_str)}</title>
  <style type="text/css">
    body, table, td, div, p {{ margin:0; padding:0; }}
    img {{ border:0; display:block; }}

    @media only screen and (max-width: 620px) {{
      .util-row .util-cell {{ display:block !important; text-align:center !important;
        padding:5px 8px !important; white-space:normal !important; }}
      .util-row .util-cell a {{ padding:4px 7px !important; margin:1px !important;
        font-size:11px !important; letter-spacing:0.3px !important; }}

      .wrapper {{ width:100% !important; }}
      .sec, .footer {{ padding:16px 14px !important; }}
      .mast-main, .mast-meta {{ display:block !important; width:100% !important;
                              text-align:left !important; }}
      .mast-meta {{ text-align:left !important; padding-top:10px !important; }}
      .mkt {{ font-size:11px !important; padding-right:12px !important; }}
      .cal-table td[width="110"] {{ width:80px !important; padding:8px 8px 8px 0 !important; }}
      .cal-date {{ font-size:11px !important; }}
      .china-dark > div {{ padding:16px 14px !important; }}
      h1 {{ font-size:22px !important; }}   /* the shared phone nameplate size */
      h2 {{ font-size:13px !important; }}
      h3 {{ font-size:14px !important; }}
      .story-card {{ padding:12px 10px !important; }}
      p, div {{ word-wrap:break-word !important; overflow-wrap:break-word !important; }}
      img {{ max-width:100% !important; height:auto !important; }}
      body, td, div, p, span {{ font-size:14px !important; -webkit-text-size-adjust:100%; }}
      div[style*="font-size:10px"], div[style*="font-size:10px"],
      span[style*="font-size:10px"], span[style*="font-size:10px"] {{ font-size:11px !important; }}
      a {{ min-height:44px; min-width:44px; display:inline-block; line-height:44px; }}
      p a, div a, td a {{ min-height:auto; min-width:auto; display:inline; padding:6px 0; line-height:inherit; }}
    }}

    @media only screen and (min-width: 621px) and (max-width: 768px) {{
      .wrapper {{ width:100% !important; }}
      .sec, .footer {{ padding:14px 20px !important; }}
      h1 {{ font-size:22px !important; }}
    }}

    @media print {{
      /* The PDF is generated from this same HTML, so the print rules are what
         decide whether it reads as a document or a long screenshot. */
      body {{ background:#FFFFFF !important; }}
      .wrapper {{ width:100% !important; max-width:100% !important;
                  box-shadow:none !important; }}
      /* The read-online bar and the back-to-top links are navigation. On
         paper they are dead text. */
      .no-print {{ display:none !important; }}
      /* An item split across a page break loses its source line, which is the
         part that makes it checkable. */
      .story-card, .cal-table tr {{ page-break-inside:avoid;
                                    break-inside:avoid; }}
      h1, h2, h3 {{ page-break-after:avoid; break-after:avoid; }}
      a {{ text-decoration:none !important; }}
    }}

    @media (prefers-color-scheme: dark) {{
      body {{ background:#121212 !important; }}
      .wrapper {{ background:#1a1a1a !important; }}
      .wrapper .sec {{ background:#222 !important; border-bottom-color:#333 !important; }}
      .wrapper h1, .wrapper h2, .wrapper h3 {{ color:#E0E0E0 !important; }}
      .wrapper p, .wrapper div, .wrapper td, .wrapper span {{ color:#CCC !important; }}
      .wrapper a {{ color:{TEAL_LT} !important; }}
      .wrapper .footer {{ background:#0F1A2E !important; }}
      /* The terminal strip is white by design in light mode. Left
         unmapped it stays white in dark mode, which is a bright band
         across the bottom of an otherwise dark brief. The coverage
         guard misses it because #FFFFFF is on the exempt list, being
         legitimate as type on an accent fill. */
      .wrapper .footer-end {{ background:#1a1a1a !important; }}
      .wrapper .footer-end td {{ color:#9AA3AE !important; }}
      .wrapper .story-card {{ background:#2a2a2a !important; border-color:#333 !important; }}
      .wrapper .china-dark {{ background:#101E2A !important; }}
    /* Filled from a measured audit of the rendered brief: these
       colours reached the output with no dark rule, so they kept
       their light values and rendered near-black on near-black. */
      .wrapper [style*="color:#0d1b2a"] {{ color:#D5D8DC !important; }}
      .wrapper [style*="color:#0D1B2A"] {{ color:#D5D8DC !important; }}
      .wrapper [style*="color:#17798c"] {{ color:#5FC2D6 !important; }}
      .wrapper [style*="color:#17798C"] {{ color:#5FC2D6 !important; }}
      .wrapper [style*="color:#1b2a4a"] {{ color:#D5D8DC !important; }}
      .wrapper [style*="color:#1B2A4A"] {{ color:#D5D8DC !important; }}
      .wrapper [style*="color:#2c3e50"] {{ color:#D5D8DC !important; }}
      .wrapper [style*="color:#2C3E50"] {{ color:#D5D8DC !important; }}
      .wrapper [style*="color:#444"] {{ color:#C4C8CE !important; }}
      .wrapper [style*="color:#4a5260"] {{ color:#C4C8CE !important; }}
      .wrapper [style*="color:#4A5260"] {{ color:#C4C8CE !important; }}
      .wrapper [style*="color:#555"] {{ color:#C4C8CE !important; }}
      .wrapper [style*="color:#55607a"] {{ color:#C4C8CE !important; }}
      .wrapper [style*="color:#55607A"] {{ color:#C4C8CE !important; }}
      .wrapper [style*="color:#6b7280"] {{ color:#9AA3AE !important; }}
      .wrapper [style*="color:#6B7280"] {{ color:#9AA3AE !important; }}
      .wrapper [style*="color:#888"] {{ color:#9AA3AE !important; }}
      .wrapper [style*="color:#aaa"] {{ color:#9AA3AE !important; }}
      .wrapper [style*="color:#AAA"] {{ color:#9AA3AE !important; }}
      .wrapper [style*="background:#e9f2f4"] {{ background-color:#16262A !important; }}
      .wrapper [style*="background:#E9F2F4"] {{ background-color:#16262A !important; }}
      .wrapper [style*="background:#eff6f8"] {{ background-color:#16262A !important; }}
      .wrapper [style*="background:#EFF6F8"] {{ background-color:#16262A !important; }}
      .wrapper [style*="background:#f2f3f5"] {{ background-color:#22262C !important; }}
      .wrapper [style*="background:#F2F3F5"] {{ background-color:#22262C !important; }}
      .wrapper [style*="background:#f7f8fa"] {{ background-color:#1A1D22 !important; }}
      .wrapper [style*="background:#F7F8FA"] {{ background-color:#1A1D22 !important; }}
      .wrapper [style*="background:#fff"] {{ background-color:#262A30 !important; }}
      .wrapper [style*="background:#FFF"] {{ background-color:#262A30 !important; }}
      .wrapper [style*="background:#ffffff"] {{ background-color:#262A30 !important; }}
      .wrapper [style*="background:#FFFFFF"] {{ background-color:#262A30 !important; }}
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
        <table role="presentation" class="wrapper" width="680" cellpadding="0" cellspacing="0" border="0" align="center" style="width:680px;max-width:100%;margin:0 auto;background:#FFFFFF;box-shadow:0 2px 20px rgba(0,0,0,0.08);">
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
