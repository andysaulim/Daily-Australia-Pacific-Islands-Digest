"""The house archive page: Japan's layout, Korea's search.

One file, shared by every edition. The rows are baked into the HTML rather than
fetched, so the page still lists every issue if archive.json is unreachable,
and the search filters what is already in the DOM, so it answers instantly and
works offline. Korea's page fetched its data and would show nothing when that
failed; Japan's had no search. This has neither problem.

Field names differ across the editions (url/filename, headline_re/re_line,
top_stories_count/top_stories), so every read accepts either.
"""
from __future__ import annotations

from datetime import datetime
from html import escape


def _row(entry: dict, accent: str, prefix: str) -> str:
    d = str(entry.get("date") or "")
    try:
        label = datetime.strptime(d, "%Y-%m-%d").strftime("%A, %B %-d, %Y")
    except ValueError:
        label = d
    url = str(entry.get("url") or entry.get("filename") or f"{prefix}{d}.html")
    re_line = str(entry.get("headline_re") or entry.get("re_line") or "")
    wc = entry.get("word_count") or 0
    words = f"{wc:,}" if isinstance(wc, int) else str(wc)

    # Link the PDF unless the entry says there isn't one. The China edition
    # records pdf: false when the export failed, and linking it anyway hands
    # the reader a 404; the others do not record the field at all and always
    # produce one, so an absent field means present.
    pdf_link = ""
    if entry.get("pdf", True):
        pdf = url.rsplit(".", 1)[0] + ".pdf"
        pdf_link = (f' &middot; <a href="{escape(pdf)}" '
                    f'style="color:{accent};text-decoration:none;">PDF</a>')

    # data-search carries what the box matches on, lowercased once here rather
    # than on every keystroke.
    hay = escape(f"{d} {label} {re_line}".lower(), quote=True)
    return (
        f'<tr class="issue" data-search="{hay}">'
        f'<td style="padding:11px 8px;border-bottom:1px solid #EBEBEB;white-space:nowrap;'
        f'font-family:Arial,sans-serif;font-size:13px;font-weight:700;color:#14181F;">'
        f'<a href="{escape(url)}" style="color:#14181F;text-decoration:none;">{escape(label)}</a>'
        f'{pdf_link}</td>'
        f'<td style="padding:11px 8px;border-bottom:1px solid #EBEBEB;'
        f'font-family:Georgia,serif;font-size:13px;line-height:1.45;color:#444;">{escape(re_line)}</td>'
        f'<td style="padding:11px 8px;border-bottom:1px solid #EBEBEB;'
        f'font-family:Arial,sans-serif;font-size:11px;color:#767676;'
        f'text-align:right;white-space:nowrap;">{words} words</td>'
        f'</tr>')


def build(entries: list, *, title: str, chair: str, accent: str,
          latest_href: str = "index.html", prefix: str = "") -> str:
    entries = sorted([e for e in (entries or []) if isinstance(e, dict)],
                     key=lambda e: str(e.get("date") or ""), reverse=True)
    rows = "".join(_row(e, accent, prefix) for e in entries)
    if not rows:
        rows = ('<tr><td colspan="3" style="padding:16px;font-family:Arial,sans-serif;'
                'color:#767676;">No issues archived yet.</td></tr>')
    n = len(entries)
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="color-scheme" content="light dark">
<meta name="supported-color-schemes" content="light dark">
<title>{escape(title)} &middot; Archive</title>
<style>
  body {{ margin:0; background:#F4F4F1; }}
  .wrap {{ max-width:1000px; margin:0 auto; background:#fff; }}
  .mast {{ background:#14181F; color:#fff; padding:22px 40px 18px; }}
  .chair {{ font-family:Arial,sans-serif; font-size:11px; font-weight:700;
           text-transform:uppercase; letter-spacing:2px; color:{accent}; margin-bottom:8px; }}
  .mast h1 {{ margin:0 0 6px 0; font-size:34px; font-weight:700; font-family:Georgia,serif; }}
  .meta {{ font-size:15px; color:rgba(255,255,255,0.85); font-family:Georgia,serif; }}
  .meta a {{ color:{accent}; text-decoration:none; }}
  .searchbar {{ padding:16px 40px 4px; }}
  .searchbar input {{ width:100%; box-sizing:border-box; padding:10px 12px;
      font-family:Georgia,serif; font-size:15px; color:#14181F;
      border:1px solid #D8D8D8; border-radius:3px; background:#fff; }}
  .searchbar input:focus {{ outline:2px solid {accent}; outline-offset:-1px; }}
  .count {{ padding:6px 40px 0; font-family:Arial,sans-serif; font-size:11px;
            color:#767676; text-transform:uppercase; letter-spacing:1px; }}
  table {{ width:100%; border-collapse:collapse; padding:8px 32px 32px; }}
  .foot {{ padding:18px 40px 28px; font-family:Arial,sans-serif; font-size:11px;
           color:#767676; text-align:center; }}
  @media (max-width:620px) {{
    .mast, .searchbar, .count, .foot {{ padding-left:18px; padding-right:18px; }}
    .mast h1 {{ font-size:26px; }}
    td {{ font-size:12px !important; }}
  }}
  @media (prefers-color-scheme: dark) {{
    body {{ background:#101215; }}
    .wrap {{ background:#15181C; }}
    td {{ border-color:#2A2F36 !important; }}
    td a {{ color:#E8E6E1 !important; }}
    td, .count, .foot {{ color:#B9BDC4 !important; }}
    .searchbar input {{ background:#1D2126; color:#E8E6E1; border-color:#2A2F36; }}
  }}
</style></head>
<body>
<div class="wrap">
  <div class="mast">
    <div class="chair">{escape(chair)}</div>
    <h1>{escape(title)}</h1>
    <div class="meta">Archive &middot; <span id="n">{n}</span> issues &middot;
      <a href="{escape(latest_href)}">Latest issue &#8594;</a></div>
  </div>
  <div class="searchbar">
    <input id="q" type="search" autocomplete="off"
           placeholder="Search by date or headline, e.g. Yongbyon or September 8" aria-label="Search the archive">
  </div>
  <div class="count" id="count">{n} issues</div>
  <table><tbody id="rows">{rows}</tbody></table>
  <div class="foot">Generated automatically; every issue links its own sources.</div>
</div>
<script>
(function () {{
  var q = document.getElementById('q');
  var rows = [].slice.call(document.querySelectorAll('tr.issue'));
  var count = document.getElementById('count');
  if (!q) return;
  function apply() {{
    var s = q.value.trim().toLowerCase();
    var shown = 0;
    for (var i = 0; i < rows.length; i++) {{
      var hit = !s || rows[i].getAttribute('data-search').indexOf(s) !== -1;
      rows[i].style.display = hit ? '' : 'none';
      if (hit) shown++;
    }}
    count.textContent = s ? (shown + (shown === 1 ? ' match' : ' matches'))
                          : (shown + (shown === 1 ? ' issue' : ' issues'));
  }}
  q.addEventListener('input', apply);
  apply();
}})();
</script>
</body></html>"""
