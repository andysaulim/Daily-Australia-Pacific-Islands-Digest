"""
Subscriber-only newsletter ingestion over IMAP.

Ported from the Middle East pipeline. The insight there is worth restating,
because it inverts the usual paywall problem: for a newsletter you subscribe
to, the reliable source is the delivered email. There is no wall to get past,
the publisher sent it to you. So the pipeline logs into the inbox that receives
them, reads the day's issues, and pulls the article links and blurbs out of the
email HTML.

That matters here because the feed set can only reach newsletters with a public
feed. The Substack ones in collect.py serve RSS at /feed. Australian Foreign
Affairs, the mastheads' subscriber briefings, and most institutional mailouts
do not, and those are exactly the ones with editorial judgement already applied.

DEFAULT OFF, unlike the Middle East original. Nothing is subscribed yet, and an
always-on IMAP login against the sending account would print a failure line
every morning until it is, which is how you train yourself to ignore errors.
Turn it on with NEWSLETTERS=1 once the one-time setup below is done.

One-time setup:
  1. Subscribe the GMAIL_USER inbox to the newsletters you want ingested.
  2. Enable IMAP on that account: Gmail Settings, Forwarding and POP/IMAP,
     Enable IMAP.
  3. Add the sender or subject fingerprints to _PUBLISHERS / _NEWSLETTERS below.
  4. Set the repository variable NEWSLETTERS=1.

Credentials are the same GMAIL_USER / GMAIL_APP_PASS the send path already
uses, so there is no new secret. Best-effort throughout: no credentials, IMAP
disabled, or a missing issue just yields fewer items.

Relevance filtering, sport blocking, region tagging and the article shape are
all borrowed from collect.py rather than reimplemented, so a newsletter item is
indistinguishable downstream from a feed item and counts toward the same
regional balance.

Stdlib only.
"""
import email
import imaplib
import os
import re
from datetime import datetime, timedelta, timezone
from email.header import decode_header
from urllib.parse import urlparse

import collect   # _is_region_related, _is_sport, _entry_to_article

ENABLED = os.environ.get("NEWSLETTERS", "0") not in ("0", "false", "False", "")
IMAP_HOST = "imap.gmail.com"
MAX_MESSAGES = 100

# A newsletter is identified from its sender and/or its subject, so whichever
# the inbox happens to receive is picked up. Seeded with plausible candidates
# for this beat; an unsubscribed one simply never matches. Add a line to
# either list to recognise a new one.
_PUBLISHERS = [
    ("theaustralian", "The Australian"), ("afr.com", "AFR"),
    ("smh.com.au", "SMH"), ("theage.com.au", "The Age"),
    ("lowyinstitute", "Lowy Institute"), ("aspi.org.au", "ASPI"),
    ("ussc.edu.au", "USSC"), ("devpolicy", "Devpolicy"),
    ("islandsbusiness", "Islands Business"), ("rnz.co.nz", "RNZ"),
    ("australianforeignaffairs", "Australian Foreign Affairs"),
    ("thesaturdaypaper", "The Saturday Paper"),
    ("nzherald", "NZ Herald"), ("politico", "Politico"),
]
_NEWSLETTERS = [
    ("the briefing", "The Briefing"),
    ("morning edition", "Morning Edition"),
    ("daily briefing", "Daily Briefing"),
    ("afternoon update", "Afternoon Update"),
    ("the interpreter", "The Interpreter"),
    ("daily telegraph", "Daily Telegraph"),
    ("pacific brief", "Pacific Brief"),
    ("defence brief", "Defence Brief"),
    ("china brief", "China Brief"),
]

# Links that are never article links.
_SKIP_LINK = re.compile(
    r"(unsubscribe|/unsub|list-manage|mailchi|emailcampaign|/preferences|/profile|"
    r"twitter\.com|x\.com|facebook\.com|instagram\.com|linkedin\.com|youtube\.com|"
    r"mailto:|\.gif|\.png|\.jpg|\.jpeg)", re.IGNORECASE)
_A_RE = re.compile(r'<a\s[^>]*href="([^"#]+)"[^>]*>(.*?)</a>', re.DOTALL | re.IGNORECASE)
_TAG_RE = re.compile(r"<[^>]+>")


def _clean(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "")).strip()


def _decode(s) -> str:
    try:
        return "".join(
            (b.decode(enc or "utf-8", "ignore") if isinstance(b, bytes) else b)
            for b, enc in decode_header(s or ""))
    except Exception:                                       # noqa: BLE001
        return s or ""


def _match_source(frm: str, subj: str):
    """Identify a subscribed newsletter from sender and/or subject, else None."""
    hay = f"{frm} {subj}".lower()
    pub = next((label for key, label in _PUBLISHERS if key in hay), None)
    name = next((disp for key, disp in _NEWSLETTERS if key in hay), None)
    if pub and name:
        return f"{pub} ({name})"
    if pub:
        return f"{pub} (Newsletter)"
    if name:
        return name
    return None


def _looks_like_article(url: str) -> bool:
    """True only for a real article URL, not a homepage or section page.

    Newsletters link their own masthead, section fronts and topic pages
    constantly. Article slugs are long and hyphenated or carry an id; section
    roots are empty or one short word.
    """
    try:
        path = urlparse(url).path.rstrip("/")
    except Exception:                                       # noqa: BLE001
        return False
    if not path:
        return False                                        # homepage
    low = path.lower()
    if any(seg in low for seg in
           ("/newsletters", "/newsletter", "/tag/", "/tags/", "/topics/", "/topic/",
            "/author/", "/authors/", "/category/", "/categories/", "/subscribe",
            "/about", "/section/", "/live/", "/video/", "/videos/", "/podcast")):
        return False
    last = path.split("/")[-1]
    return len(last) >= 12 and ("-" in last or any(c.isdigit() for c in last))


def _html_part(msg) -> str:
    """The text/html body of an email message, or ''."""
    parts = msg.walk() if msg.is_multipart() else [msg]
    for part in parts:
        if part.get_content_type() == "text/html":
            try:
                return part.get_payload(decode=True).decode(
                    part.get_content_charset() or "utf-8", "ignore")
            except Exception:                               # noqa: BLE001
                continue
    return ""


def parse_newsletter_html(html: str, label: str) -> list:
    """Pull region-relevant articles out of one newsletter's HTML.

    Returns collect.py-shaped articles, so region tagging and everything
    downstream treats them exactly like feed items.
    """
    out, seen = [], set()
    for m in _A_RE.finditer(html or ""):
        url = m.group(1).strip()
        text = _clean(_TAG_RE.sub(" ", m.group(2)))
        if not url.startswith("http") or _SKIP_LINK.search(url):
            continue
        if not _looks_like_article(url):
            continue
        if len(text) < 25:                    # "read more", icons, nav
            continue
        if url in seen:
            continue
        seen.add(url)

        entry = {"title": text[:280], "link": url, "summary": text}
        # Same two gates the feed tiers use, so a newsletter cannot smuggle in
        # the sport and celebrity copy the mastheads pack their briefings with.
        if not collect._is_region_related(entry):
            continue
        if collect._is_sport(entry):
            continue
        out.append(collect._entry_to_article(entry, label))
    return out


def from_imap(days: int = 1) -> list:
    user = os.environ.get("GMAIL_USER")
    pw = os.environ.get("GMAIL_APP_PASS")
    if not (user and pw):
        print("  [newsletter] no GMAIL_USER/GMAIL_APP_PASS, skipping IMAP")
        return []
    out = []
    try:
        M = imaplib.IMAP4_SSL(IMAP_HOST)
        M.login(user, pw)
        M.select("INBOX")
        since = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%d-%b-%Y")
        _, data = M.search(None, f"(SINCE {since})")
        ids = data[0].split() if data and data[0] else []
        for mid in ids[-MAX_MESSAGES:]:
            _, md = M.fetch(mid, "(RFC822)")
            if not md or not md[0]:
                continue
            msg = email.message_from_bytes(md[0][1])
            label = _match_source(str(msg.get("From", "")),
                                  _decode(msg.get("Subject", "")))
            if not label:
                continue
            items = parse_newsletter_html(_html_part(msg), label)
            print(f"  [newsletter] {label}: {len(items)} items")
            out += items
        M.logout()
    except Exception as e:                                  # noqa: BLE001
        print(f"  [newsletter] IMAP error: {e!r}")
    return out


def collect_newsletters(days: int = 1) -> list:
    if not ENABLED:
        return []
    items = from_imap(days=days)
    print(f"  [newsletter] {len(items)} item(s) from subscribed newsletters")
    return items


if __name__ == "__main__":
    html = """
    <html><body>
      <a href="https://www.afr.com/politics/federal/canberra-lifts-aukus-payment-20260825-p5abcd">
        Canberra lifts the next AUKUS industrial-base payment
      </a>
      <a href="https://www.afr.com/topics/defence">Defence</a>
      <a href="https://www.afr.com/sport/afl/grand-final-preview-20260825-p5xyz">
        Grand final preview: everything you need to know about the AFL decider
      </a>
      <a href="https://example.com/unsubscribe">Unsubscribe</a>
      <a href="https://www.afr.com/x">Short</a>
    </body></html>
    """
    items = parse_newsletter_html(html, "AFR (Newsletter)")
    urls = [i["url"] for i in items]
    assert len(items) == 1, [i["title"] for i in items]
    assert "canberra-lifts-aukus-payment" in urls[0], urls
    assert items[0]["source"] == "AFR (Newsletter)"
    assert items[0]["region"] == "AU", items[0]["region"]
    assert _match_source("news@afr.com", "Daily Briefing") == "AFR (Daily Briefing)"
    assert _match_source("someone@example.com", "hello") is None
    assert not _looks_like_article("https://www.afr.com/")
    assert not _looks_like_article("https://www.afr.com/topics/defence")
    assert _looks_like_article("https://www.afr.com/politics/a-long-slug-here-20260825")
    print(f"parsed: {items[0]['title'][:60]}...")
    print("newsletters.py self-test passed")
