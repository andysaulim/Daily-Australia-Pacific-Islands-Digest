"""
Best-effort article body fetcher.

Ported from the Middle East pipeline, which built it after noticing the model
was only ever shown a headline plus a thin RSS blurb, so its items rarely got
past restating the headline. This brief had the same ceiling: `_entry_to_article`
keeps `summary[:800]` of whatever the feed chose to publish, and for most
Australian outlets that is one sentence.

Fetching the real article text gives the model grounded material for the
figures, quotes and context that make an item worth reading. It cannot make the
brief less accurate: the model may still only use what is in front of it, and
this puts more real text in front of it, not less.

Three design notes carried over, each of which was a bug in the first version
of the Middle East original:

  - Fetch ONLY canonical URLs. A Google News redirect returns an interstitial
    page, not article text, and roughly half this brief's feeds are Google News
    routed. Those items keep their RSS summary.
  - Paywalled outlets still contribute. The <meta>/og:description sits before
    the wall, so The Australian, the AFR, the WSJ and the FT each give a
    sentence of real detail even though the body is unreachable.
  - Fetch in parallel. One-at-a-time with a short timeout enriched a small
    fraction of items in the same wall-clock budget.

Bodies are cached in the archive database, so a re-run costs nothing and the
cache survives across days. Best-effort throughout: a fetch failure, a bare
page, or a dead host just leaves the existing summary alone. Disable with
FULLTEXT=0.

Stdlib only. No new dependency.
"""
import concurrent.futures as cf
import os
import re
import sqlite3
import urllib.request

import archive   # DB_PATH, which honours the ARCHIVE_DB override
import collect   # HEADERS

ENABLED = os.environ.get("FULLTEXT", "1") not in ("0", "false", "False", "")

MAX_ITEMS = 150      # fetches per run; parallel, so this is affordable
WORKERS = 8
BODY_CHARS = 2200    # kept per article, with room for a full quote
TIMEOUT = 6

# Bodies sit behind a wall, so take the meta description and stop. Listing an
# outlet here is not a judgement about it, only about what a fetch can reach.
PAYWALLED = (
    "theaustralian.com.au", "afr.com", "smh.com.au", "theage.com.au",
    "nzherald.co.nz", "thepost.co.nz",
    "wsj.com", "nytimes.com", "ft.com", "economist.com", "bloomberg.com",
    "washingtonpost.com", "telegraph.co.uk", "thetimes.co.uk",
)

# The outlets the requester named. Enriching these first matters because the
# prompt carries a mandatory-inclusion rule for them, so their items are the
# most likely to actually reach the reader.
_PRESTIGE_HINTS = (
    "The Australian", "SMH", "AFR", "ABC", "WSJ", "NYT", "Politico",
    "RNZ Pacific", "Islands Business", "Pacific Island Times",
    "Australian Foreign Affairs",
)

_SCRIPT_RE = re.compile(r"<(script|style|noscript)[^>]*>.*?</\1>", re.DOTALL | re.IGNORECASE)
_REGION_RE = re.compile(r"<(article|main)[^>]*>(.*?)</\1>", re.DOTALL | re.IGNORECASE)
_P_RE = re.compile(r"<p[^>]*>(.*?)</p>", re.DOTALL | re.IGNORECASE)
_TAG_RE = re.compile(r"<[^>]+>")
# <meta ... (name|property)="[og:]description" ... content="...">, either attribute order.
_META_A = re.compile(
    r'<meta[^>]+?(?:name|property)=["\'](?:og:)?description["\'][^>]+?content=["\']([^"\']+)["\']',
    re.IGNORECASE)
_META_B = re.compile(
    r'<meta[^>]+?content=["\']([^"\']+)["\'][^>]+?(?:name|property)=["\'](?:og:)?description["\']',
    re.IGNORECASE)


def _clean(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "")).strip()


def is_gnews(url: str) -> bool:
    """True for a Google News redirect, which serves an interstitial, not text."""
    return "news.google.com" in (url or "").lower()


def _is_paywalled(url: str) -> bool:
    u = (url or "").lower()
    return any(p in u for p in PAYWALLED)


def extract_meta(html: str) -> str:
    """The page's meta or og description, a sentence or two, or ''."""
    for rx in (_META_A, _META_B):
        m = rx.search(html or "")
        if m:
            return _clean(m.group(1))
    return ""


def extract_body(html: str) -> str:
    """Readable paragraph text from an article page, best-effort."""
    if not html:
        return ""
    html = _SCRIPT_RE.sub(" ", html)
    m = _REGION_RE.search(html)          # prefer <article>/<main> when present
    region = m.group(2) if m else html
    paras = [_clean(_TAG_RE.sub(" ", pm.group(1))) for pm in _P_RE.finditer(region)]
    # The 40-character floor drops nav links, bylines and share prompts, which
    # otherwise dominate the paragraph list on a modern news page.
    return " ".join(p for p in paras if len(p) >= 40)[:BODY_CHARS].strip()


def extract(html: str, want_body: bool = True) -> str:
    """Meta description as a floor, plus the body where a fetch can reach it."""
    desc = extract_meta(html)
    body = extract_body(html) if want_body else ""
    combined = f"{desc} {body}".strip() if desc else body
    return combined[:BODY_CHARS].strip()


def _fetch(url: str, want_body: bool, timeout: int = TIMEOUT) -> str:
    try:
        req = urllib.request.Request(url, headers=collect.HEADERS)
        html = urllib.request.urlopen(req, timeout=timeout).read().decode("utf-8", "ignore")
        return extract(html, want_body)
    except Exception:                                       # noqa: BLE001
        return ""


def _is_prestige(item: dict) -> bool:
    src = item.get("source", "") or ""
    return any(h in src for h in _PRESTIGE_HINTS)


def _rank(item: dict):
    # Prestige first, then the items carrying the least text, since those are
    # the ones a body fetch actually changes.
    return (_is_prestige(item), -len(item.get("summary") or ""))


def _apply(item: dict, body: str) -> None:
    base = item.get("summary") or ""
    item["summary"] = (f"{base} {body}".strip() if base else body)[:2400]


def enrich(items: list, limit: int = MAX_ITEMS) -> list:
    """Append fetched article text to each item's `summary`, in place."""
    if not ENABLED:
        print("  [fulltext] disabled (FULLTEXT=0)")
        return items

    archive.DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(archive.DB_PATH)
    con.execute("CREATE TABLE IF NOT EXISTS fulltext (url TEXT PRIMARY KEY, body TEXT)")
    con.commit()

    cands = [it for it in items if it.get("url") and not is_gnews(it["url"])]
    cands.sort(key=_rank, reverse=True)
    cands = cands[:limit]

    todo = []
    for it in cands:
        row = con.execute("SELECT body FROM fulltext WHERE url=?", (it["url"],)).fetchone()
        if row is None:
            todo.append(it)
        elif row[0]:
            _apply(it, row[0])

    fetched = {}
    if todo:
        with cf.ThreadPoolExecutor(max_workers=WORKERS) as ex:
            futs = {ex.submit(_fetch, it["url"], not _is_paywalled(it["url"])): it
                    for it in todo}
            for fut in cf.as_completed(futs):
                url = futs[fut]["url"]
                try:
                    fetched[url] = fut.result() or ""
                except Exception:                           # noqa: BLE001
                    fetched[url] = ""
        for it in todo:
            body = fetched.get(it["url"], "")
            # An empty result is cached too, so a dead URL is not re-fetched
            # every morning for the rest of its life.
            con.execute("INSERT OR REPLACE INTO fulltext(url, body) VALUES (?,?)",
                        (it["url"], body))
            if body:
                _apply(it, body)
        con.commit()
    con.close()

    gnews_skipped = len(items) - len([i for i in items if i.get("url") and not is_gnews(i["url"])])
    print(f"  [fulltext] {len(cands)} canonical items considered "
          f"({len(cands) - len(todo)} cached, {len(todo)} fetched), "
          f"{gnews_skipped} Google News items skipped")
    return items


def enrich_payload(payload: dict) -> dict:
    """Enrich every tier in a collected payload, tier 1 first."""
    if not ENABLED:
        print("  [fulltext] disabled (FULLTEXT=0)")
        return payload
    pool = []
    for tier in ("tier1", "tier2", "tier3", "tier4"):
        pool.extend(payload.get(tier) or [])
    enrich(pool)
    return payload


if __name__ == "__main__":
    html = (
        '<html><head>'
        '<meta property="og:description" content="Canberra tied the next tranche of '
        'AUKUS industrial-base payments to shipyard milestones, the minister said.">'
        '</head><body><nav><p>Home</p></nav>'
        '<article>'
        '<p>Australia will release the next tranche of AUKUS industrial-base funding only '
        'once Osborne reaches an agreed milestone, the defence minister said on Tuesday.</p>'
        '<p>Short.</p>'
        '<p>A second substantial paragraph carrying more than forty characters of real '
        'text, so the extractor keeps it as body content rather than navigation.</p>'
        '</article></body></html>'
    )
    assert extract_meta(html).startswith("Canberra tied"), extract_meta(html)
    body = extract_body(html)
    assert "Australia will release" in body, body
    assert "Home" not in body and "Short." not in body, body
    both = extract(html, want_body=True)
    assert both.startswith("Canberra tied") and "Australia will release" in both
    meta_only = extract(html, want_body=False)
    assert "Canberra tied" in meta_only and "second substantial" not in meta_only
    assert is_gnews("https://news.google.com/rss/search?q=x")
    assert not is_gnews("https://www.abc.net.au/news/story")
    assert _is_paywalled("https://www.afr.com/politics/x")
    assert not _is_paywalled("https://www.abc.net.au/news/x")
    print(both)
    print("\nfulltext.py self-test passed")
