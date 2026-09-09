"""Masthead rendering for the CSIS daily briefs.

Part of the shared engine — see shared/__init__.py.

This file is the single source of truth for the top of every edition: the
accent rule, the navy header block, the RE: line, the utility link bar, and
the auto-generation disclaimer. It is byte-identical in all four repositories.
Everything that differs between editions lives in BRAND, which each repo
overrides in its own `brand.py`.

To adopt in another edition:
  1. Copy the whole shared/ directory in unchanged. Per-repo edits are what
     caused the four briefs to drift apart in the first place.
  2. Add a `brand.py` defining BRAND (see brand.py in this repo for the shape).
  3. In render.py, replace the hand-written bar/header/disclaimer blocks with:
         from masthead import render_masthead
         sections.append(render_masthead(...))
  4. Run `python -m shared` to confirm the engine matches the other repos.

Any change to the shared look is made here once and copied to all four.
"""
from __future__ import annotations

MASTHEAD_VERSION = "1.0.0"

# Layout constants shared by every edition. Regional palettes override colors
# through BRAND; these are the structural values that must not vary.
_PAD = "22px 32px 18px"
_KICKER = ("font-size:11px;text-transform:uppercase;letter-spacing:3px;"
           "font-family:Arial,sans-serif;margin-bottom:7px;")
_TITLE = ("margin:0;font-size:24px;font-weight:700;"
          "font-family:Georgia,'Times New Roman',serif;letter-spacing:0.3px;")
_DATE = ("margin-top:7px;font-size:14px;font-weight:400;letter-spacing:0.3px;"
         "font-family:Georgia,serif;")
_LINK = "text-decoration:none;white-space:nowrap;"
_SEP = "&nbsp;&nbsp;&middot;&nbsp;&nbsp;"

DISCLAIMER = (
    "This newsletter is automatically generated, so it may contain errors. "
    "Please check all information and sources before citing. "
    "To report errors or other issues, please contact {contact_name} at "
    '<a href="mailto:{contact_email}" style="color:rgba(255,255,255,0.82);'
    'text-decoration:underline;">{contact_email}</a>.')


def _esc(text) -> str:
    """Minimal HTML escape (kept local so this file has no dependencies)."""
    return (str(text or "").replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;").replace('"', "&quot;"))


def render_link_bar(web_url: str, brand: dict) -> str:
    """Read online · Print / PDF · Archive.

    Returns "" when no web URL is configured, so a local run degrades quietly.
    """
    if not web_url:
        return ""
    base = web_url[:-len("latest.html")] if web_url.endswith("latest.html") else ""
    style = f"color:{brand['link_color']};{_LINK}"
    links = [f'<a href="{_esc(web_url)}" style="{style}">Read online &#8594;</a>']
    if base:
        links.append(f'<a href="{_esc(base + "latest.pdf")}" style="{style}">'
                     f'Print / PDF &#8595;</a>')
        links.append(f'<a href="{_esc(base + "archive.html")}" style="{style}">'
                     f'Archive</a>')
    return (f'<div style="background:{brand["bar_bg"]};padding:6px 32px;'
            f'text-align:center;font-size:11px;color:#888;" class="sec">'
            f'{_SEP.join(links)}</div>')


def render_header(date_str: str, gen_time: str, word_count: int,
                  read_min: int, re_line: str, brand: dict) -> str:
    """The navy masthead: chair kicker, title, date, run meta, RE: line."""
    navy = brand["navy"]
    mono = brand["mono"]
    accent = brand["accent"]
    mark = brand.get("title_mark", "")
    kicker_color = brand.get("kicker_color", "rgba(255,255,255,0.65)")
    re_html = ""
    if re_line:
        re_html = (
            f"<div style='margin-top:14px;padding-top:12px;border-top:1px solid "
            f"{brand['re_rule']};font-size:13px;color:rgba(255,255,255,0.92);"
            f"font-family:Georgia,serif;line-height:1.55;'>"
            f"<strong style='color:{accent};font-size:11px;letter-spacing:1.5px;"
            f"font-family:Arial,sans-serif;'>RE:</strong>&nbsp; {re_line}</div>")
    return f"""
    <a name="top"></a>
    {brand.get("top_rule", "")}
    <div bgcolor="{navy}" style="background-color:{navy};color:#fff;padding:{_PAD};" class="sec">
      <table width="100%" cellpadding="0" cellspacing="0" border="0"><tr>
        <td style="vertical-align:top;">
          <div style="{_KICKER}color:{kicker_color};">{_esc(brand["chair"])}</div>
          <h1 style="{_TITLE}color:#fff;">{mark}{_esc(brand["title"])}</h1>
          <div style="{_DATE}color:rgba(255,255,255,0.88);">{_esc(date_str)}</div>
        </td>
        <td style="vertical-align:top;text-align:right;">
          <div style="font-family:{mono};font-size:11px;color:rgba(255,255,255,0.55);white-space:nowrap;">{gen_time}<br>{word_count:,} words &middot; {read_min} min read</div>
        </td>
      </tr></table>
      {re_html}
    </div>
    """


def render_disclaimer(brand: dict) -> str:
    """Auto-generation notice, rendered as the closing row of the banner."""
    text = DISCLAIMER.format(contact_name=brand["contact_name"],
                             contact_email=brand["contact_email"])
    return f"""
    <table width="100%" cellpadding="0" cellspacing="0" border="0" style="background:{brand['disclaimer_bg']};border-bottom:3px solid {brand['accent']};">
      <tr>
        <td style="padding:9px 32px 11px;font-family:Arial,sans-serif;font-size:10.5px;line-height:1.5;color:rgba(255,255,255,0.62);">
          {text}
        </td>
      </tr>
    </table>
    """


def render_masthead(*, web_url: str, date_str: str, gen_time: str,
                    word_count: int, read_min: int, re_line: str,
                    brand: dict, market_strip: str = "") -> str:
    """Full banner in the fixed order every edition uses.

    market_strip is the edition's own rendered market rows, passed in so the
    disclaimer always lands underneath it as the last row of the banner.
    """
    return "".join([
        render_link_bar(web_url, brand),
        render_header(date_str, gen_time, word_count, read_min, re_line, brand),
        market_strip,
        render_disclaimer(brand),
    ])
