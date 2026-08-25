"""
Australia Chair Daily Brief: Archive and cross-day memory
CSIS Australia Chair

The Korea and Japan briefs have no memory between runs, which is why the Japan
Chair sees the same story on Monday and Tuesday. This module gives the Australia
brief a persistent record of what it has collected and what it has published.

Two tables:
  items: everything ever collected, keyed by URL. Makes the corpus
               queryable, which is what unlocks weekly rollups and trend counts.
  published: what actually shipped, by date and section. This is the table
               the cross-day dedup reads.

The database file is committed back by the workflow each run so state survives
between GitHub Actions runners.
"""
import json
import os
import re
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

# ARCHIVE_DB redirects the ledger elsewhere. The smoke test sets it, because
# its fixtures are real writes: without the override they land in the
# published table and suppress a genuine story carrying the same headline.
DB_PATH = Path(os.environ.get("ARCHIVE_DB")
               or Path(__file__).parent / "data" / "archive.db")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS items (
    url            TEXT PRIMARY KEY,
    collected_date TEXT,
    source         TEXT,
    tier           TEXT,
    region         TEXT,
    title          TEXT,
    title_norm     TEXT,
    summary        TEXT,
    published      TEXT
);
CREATE INDEX IF NOT EXISTS idx_items_date  ON items (collected_date);
CREATE INDEX IF NOT EXISTS idx_items_norm  ON items (title_norm);

CREATE TABLE IF NOT EXISTS published (
    url         TEXT,
    title_norm  TEXT,
    headline    TEXT,
    digest_date TEXT,
    section     TEXT,
    PRIMARY KEY (url, digest_date)
);
CREATE INDEX IF NOT EXISTS idx_pub_date ON published (digest_date);
CREATE INDEX IF NOT EXISTS idx_pub_norm ON published (title_norm);
"""

# Sections whose items carry URLs and headlines: the ones worth remembering.
_PUBLISHED_SECTIONS = (
    "top_stories", "overnight_items", "aukus_watch", "pacific_wire",
    "new_zealand", "china_in_the_pacific", "canberra_politics",
    "business_economy", "primary_documents", "also_today",
    "opeds_today", "academic_today",
)

_NORM_STRIP = re.compile(r"[^a-z0-9 ]+")
_NORM_STOP = frozenset({
    "the", "a", "an", "in", "on", "of", "to", "for", "and", "is", "at", "by",
    "as", "with", "from", "its", "new", "over", "after", "says", "said", "amid",
    "that", "has", "will", "may", "could", "been", "are", "was", "were", "this",
    "but", "not", "all", "more", "than", "also",
})


def _today() -> str:
    return datetime.now(ZoneInfo("America/New_York")).strftime("%Y-%m-%d")


def normalize_title(title: str) -> str:
    """Reduce a headline to a comparable key.

    Lowercased, punctuation stripped, stop words removed, remaining words
    sorted. Two outlets writing up the same event usually collapse to the same
    key even when the wording differs.
    """
    if not title:
        return ""
    text = _NORM_STRIP.sub(" ", title.lower())
    words = sorted(w for w in text.split() if len(w) > 2 and w not in _NORM_STOP)
    return " ".join(words)


def _connect() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.executescript(_SCHEMA)
    return conn


# ─────────────────────────────────────────────────────────────────────────────
# WRITE
# ─────────────────────────────────────────────────────────────────────────────

def store_items(articles: list) -> int:
    """Store collected articles. Returns the number of genuinely new rows."""
    if not articles:
        return 0
    today = _today()
    rows = []
    for a in articles:
        url = (a.get("url") or "").strip()
        if not url:
            continue
        title = a.get("title", "")
        rows.append((
            url, today, a.get("source", ""),
            "primary" if a.get("primary_document") else
            ("academic" if a.get("journal_tier") else
             ("analysis" if a.get("prestige") else "news")),
            a.get("region", ""), title, normalize_title(title),
            (a.get("summary") or "")[:800], a.get("pub_date") or "",
        ))

    with _connect() as conn:
        before = conn.execute("SELECT COUNT(*) FROM items").fetchone()[0]
        conn.executemany(
            "INSERT OR IGNORE INTO items "
            "(url, collected_date, source, tier, region, title, title_norm, summary, published) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            rows,
        )
        after = conn.execute("SELECT COUNT(*) FROM items").fetchone()[0]
    return after - before


def record_published(digest: dict, digest_date: str | None = None) -> int:
    """Record what shipped today, so tomorrow's run knows not to repeat it.

    Called only after validation passes, a failed run must not poison the
    ledger with items that were never sent.
    """
    digest_date = digest_date or _today()
    rows = []
    for section in _PUBLISHED_SECTIONS:
        for item in (digest.get(section) or []):
            if not isinstance(item, dict):
                continue
            url = (item.get("url") or "").strip()
            headline = item.get("headline", "") or item.get("title", "")
            if not url and not headline:
                continue
            rows.append((url, normalize_title(headline), headline, digest_date, section))

    if not rows:
        return 0
    with _connect() as conn:
        conn.executemany(
            "INSERT OR REPLACE INTO published "
            "(url, title_norm, headline, digest_date, section) VALUES (?, ?, ?, ?, ?)",
            rows,
        )
    return len(rows)


# ─────────────────────────────────────────────────────────────────────────────
# READ
# ─────────────────────────────────────────────────────────────────────────────

def _cutoff(days: int) -> str:
    return (datetime.now(ZoneInfo("America/New_York")) - timedelta(days=days)).strftime("%Y-%m-%d")


def lookup_published(url: str, title: str = "", days: int = 7) -> dict | None:
    """Has this story already run? Returns {date, section, headline} or None.

    Matches on exact URL first, then on the normalized headline, which catches
    the same event picked up by a second outlet.
    """
    if not url and not title:
        return None
    cutoff = _cutoff(days)
    try:
        with _connect() as conn:
            if url:
                row = conn.execute(
                    "SELECT digest_date, section, headline FROM published "
                    "WHERE url = ? AND digest_date >= ? ORDER BY digest_date DESC LIMIT 1",
                    (url, cutoff),
                ).fetchone()
                if row:
                    return {"date": row[0], "section": row[1], "headline": row[2],
                            "match": "url"}
            norm = normalize_title(title)
            if norm and len(norm) > 15:
                row = conn.execute(
                    "SELECT digest_date, section, headline FROM published "
                    "WHERE title_norm = ? AND digest_date >= ? ORDER BY digest_date DESC LIMIT 1",
                    (norm, cutoff),
                ).fetchone()
                if row:
                    return {"date": row[0], "section": row[1], "headline": row[2],
                            "match": "headline"}
    except sqlite3.Error:
        return None
    return None


def recent_published(days: int = 3) -> list[dict]:
    """The last N days of published headlines, newest first."""
    cutoff = _cutoff(days)
    try:
        with _connect() as conn:
            rows = conn.execute(
                "SELECT digest_date, section, headline FROM published "
                "WHERE digest_date >= ? AND headline != '' "
                "ORDER BY digest_date DESC, section",
                (cutoff,),
            ).fetchall()
    except sqlite3.Error:
        return []
    return [{"date": r[0], "section": r[1], "headline": r[2]} for r in rows]


def build_context_block(days: int = 3) -> str:
    """The ALREADY COVERED block injected into the digest prompt.

    Layer two of the cross-day defence. The prompt instruction that goes with
    this block lives in digest.py.
    """
    entries = recent_published(days=days)
    if not entries:
        return ""

    by_date: dict[str, list[str]] = {}
    for e in entries:
        by_date.setdefault(e["date"], []).append(
            f"    [{e['section']}] {e['headline']}")

    lines = [f"ALREADY COVERED: the last {days} issues published these stories:"]
    for date in sorted(by_date, reverse=True):
        lines.append(f"  {date}:")
        lines.extend(by_date[date][:25])
    lines.append("")
    lines.append(
        "Do NOT re-run any story above unless today's articles carry a MATERIAL "
        "development, a new decision, a new number, a new actor, a reversal. A "
        "fresh write-up of the same facts by a different outlet is NOT a new "
        "development. When there is a real development, LEAD with the "
        "development, not the background, and say what changed since it last ran."
    )
    return "\n".join(lines)


def is_stale_repeat(item: dict, days: int = 7) -> dict | None:
    """Validator check: did this exact URL already ship, with nothing new?

    Layer three. Returns the prior publication record if the item looks like a
    straight repeat, or None if it carries a date or number that suggests a
    genuine development.
    """
    url = (item.get("url") or "").strip()
    if not url:
        return None
    prior = lookup_published(url, "", days=days)
    if not prior:
        return None

    # A repeat that carries a fresh date or figure is probably a real update.
    body = " ".join(str(item.get(f, "")) for f in
                    ("body", "body_text", "summary", "detail", "so_what"))
    has_new_fact = bool(re.search(
        r"\b(?:today|yesterday|this morning|overnight|on \w+day)\b"
        r"|\b\d{1,2} (?:January|February|March|April|May|June|July|August|"
        r"September|October|November|December)\b"
        r"|[$A][\d,.]+ ?(?:million|billion|bn|m\b)"
        r"|\b\d+(?:\.\d+)? ?(?:percent|%)",
        body, re.IGNORECASE))
    return None if has_new_fact else prior


# ─────────────────────────────────────────────────────────────────────────────
# STATS
# ─────────────────────────────────────────────────────────────────────────────

def stats() -> dict:
    """Corpus counts, for the README and for sanity checks."""
    try:
        with _connect() as conn:
            items = conn.execute("SELECT COUNT(*) FROM items").fetchone()[0]
            pub = conn.execute("SELECT COUNT(*) FROM published").fetchone()[0]
            days = conn.execute(
                "SELECT COUNT(DISTINCT digest_date) FROM published").fetchone()[0]
            regions = dict(conn.execute(
                "SELECT region, COUNT(*) FROM items GROUP BY region").fetchall())
    except sqlite3.Error:
        return {}
    return {"items": items, "published": pub, "issues": days, "regions": regions}


if __name__ == "__main__":
    print(json.dumps(stats(), indent=2))
    block = build_context_block()
    print("\n" + (block or "(no publication history yet)"))
