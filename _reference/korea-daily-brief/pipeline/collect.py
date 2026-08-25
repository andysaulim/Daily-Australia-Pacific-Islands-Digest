"""
Korea Daily Brief — Collector
Scrapes RSS feeds across four tiers + market data.
Uses threaded fetching for performance (~15s vs ~60s sequential).
"""
import feedparser
import requests
import json
import os
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone, timedelta

# ─────────────────────────────────────────────────────────────────────────────
# FEED CONFIGURATION
# ─────────────────────────────────────────────────────────────────────────────

def _gnews(query: str) -> str:
    """Build a Google News RSS search URL."""
    return f"https://news.google.com/rss/search?q={query}&hl=en-US&gl=US&ceid=US:en"


TIER1_FEEDS = {
    # ── Korean English-language dailies ────────────────────────────────────
    "Korea Herald":       "https://www.koreaherald.com/common/rss_xml.php?ct=102",
    "Korea Times":        "https://www.koreatimes.co.kr/www/rss/nation.xml",
    "Yonhap English":     "https://en.yna.co.kr/RSS/news.xml",
    "JoongAng Daily":     "https://koreajoongangdaily.joins.com/section/rss",
    "Chosun English":     _gnews("Korea+site:english.chosun.com"),
    "Hankyoreh English":  _gnews("Korea+site:english.hani.co.kr"),
    "Dong-A English":     _gnews("Korea+site:donga.com/en"),
    "NK News":            "https://www.nknews.org/feed/",
    # ── Korean-language feeds (Claude translates during analysis) ──────────
    "조선일보":            _gnews("site:chosun.com+-english"),
    "한겨레":              _gnews("site:hani.co.kr+-english"),
    "동아일보":            _gnews("site:donga.com+-en"),
    "MBN":                _gnews("Korea+site:mbn.co.kr"),
    "경향신문":            _gnews("site:khan.co.kr"),
    "뉴스1":              _gnews("site:news1.kr"),
    "연합뉴스":            _gnews("site:yna.co.kr+-en"),
    # ── Korean broadcast & cable news ────────────────────────────────────
    "JTBC":               _gnews("site:news.jtbc.co.kr"),
    "KBS":                _gnews("site:news.kbs.co.kr"),
    "MBC":                _gnews("site:imnews.imbc.com"),
    "SBS":                _gnews("site:news.sbs.co.kr"),
    "YTN":                _gnews("site:ytn.co.kr"),
    "Channel A":          _gnews("site:ichannela.com"),
    "Arirang News":       _gnews("Korea+site:arirang.com"),
    # ── Korean business dailies ──────────────────────────────────────────
    "매일경제":            _gnews("site:mk.co.kr"),
    "한국경제":            _gnews("site:hankyung.com"),
    "Korea Economic Daily": _gnews("Korea+site:kedglobal.com"),
    # ── Major international — Korea correspondents ────────────────────────
    "WSJ Korea":          _gnews("Korea+site:wsj.com"),
    "NYT Korea":          _gnews("Korea+site:nytimes.com"),
    "WaPo Korea":         _gnews("Korea+site:washingtonpost.com"),
    "FT Korea":           _gnews("Korea+site:ft.com"),
    "Reuters Korea":      _gnews("Korea+site:reuters.com"),
    "AP Korea":           _gnews("Korea+site:apnews.com"),
    "Bloomberg Korea":    _gnews("Korea+site:bloomberg.com"),
    "BBC Korea":          _gnews("Korea+site:bbc.com"),
    "CNN Korea":          _gnews("Korea+site:cnn.com"),
    "CNBC Korea":         _gnews("Korea+site:cnbc.com"),
    "MSNBC Korea":        _gnews("Korea+site:msnbc.com"),
    "Guardian Korea":     _gnews("Korea+site:theguardian.com"),
    "Al Jazeera Korea":   _gnews("Korea+site:aljazeera.com"),
    # ── Regional Asia ─────────────────────────────────────────────────────
    "Nikkei Korea":       _gnews("Korea+site:asia.nikkei.com"),
    "Japan Times Korea":  _gnews("Korea+site:japantimes.co.jp"),
    "SCMP Korea":         _gnews("Korea+site:scmp.com"),
    "Kyodo Korea":        _gnews("Korea+site:english.kyodonews.net"),
    "Mainichi Korea":     _gnews("Korea+site:mainichi.jp/english"),
    "Asahi Korea":        _gnews("Korea+site:asahi.com/ajw"),
    "CNA Korea":          _gnews("Korea+site:channelnewsasia.com"),
    # ── ROK/US Government ─────────────────────────────────────────────────
    "White House":        _gnews("Korea+site:whitehouse.gov"),
    "State Dept":         _gnews("Korea+site:state.gov"),
    "Pentagon":           _gnews("Korea+site:defense.gov"),
    "Stars and Stripes":  _gnews("Korea+site:stripes.com"),
    # ── ROK/Japan Government ─────────────────────────────────────────────
    "USFK":               _gnews("site:usfk.mil"),
    "ROK MOFA":           _gnews("site:mofa.go.kr"),
    "ROK MOTIE":          _gnews("Korea+site:motie.go.kr"),
    "ROK MND":            _gnews("site:mnd.go.kr"),
    "Japan MOFA":         _gnews("Korea+site:mofa.go.jp"),
    # ── US Economic agencies ────────────────────────────────────────────
    "Dept of Commerce":   _gnews("Korea+site:commerce.gov"),
    "Dept of Treasury":   _gnews("Korea+site:treasury.gov"),
    "OFAC":               _gnews("Korea+OR+DPRK+site:ofac.treasury.gov"),
    "BIS":                _gnews("Korea+OR+DPRK+site:bis.doc.gov"),
    # ── US Congress ─────────────────────────────────────────────────────
    "Senate Foreign Relations": _gnews("Korea+site:foreign.senate.gov"),
    "Senate Armed Services":    _gnews("Korea+site:armed-services.senate.gov"),
    "House Foreign Affairs":    _gnews("Korea+site:foreignaffairs.house.gov"),
    # ── US Military ─────────────────────────────────────────────────────
    "INDOPACOM":          _gnews("Korea+site:pacom.mil"),
    # ── International organizations ─────────────────────────────────────
    "IAEA":               _gnews("Korea+OR+DPRK+site:iaea.org"),
    "UN Security Council": _gnews("Korea+OR+DPRK+site:un.org/securitycouncil"),
    # ── Cyber/enforcement ───────────────────────────────────────────────
    "CISA":               _gnews("Korea+OR+DPRK+OR+Lazarus+site:cisa.gov"),
    # ── Reaction layer (China/Russia) ─────────────────────────────────────
    "Global Times Korea": _gnews("Korea+site:globaltimes.cn"),
    "Xinhua Korea":       _gnews("Korea+site:xinhuanet.com"),
    "TASS Korea":         _gnews("Korea+site:tass.com"),
    "Caixin Korea":       _gnews("Korea+site:caixinglobal.com"),
    "China Daily Korea":  _gnews("Korea+site:chinadaily.com.cn"),
    "People's Daily Korea": _gnews("Korea+site:en.people.cn"),
}

TIER2_FEEDS = {
    "CSIS":              (_gnews("Korea+site:csis.org"), "A"),
    "Brookings":         ("https://www.brookings.edu/feed/", "A"),
    "Carnegie":          ("https://carnegieendowment.org/rss/solr?query=korea", "A"),
    "RAND":              ("https://www.rand.org/topics/north-korea.xml", "A"),
    "CFR":               (_gnews("Korea+site:cfr.org"), "A"),
    "38 North":          (_gnews("site:38north.org"), "A"),
    "AccessDPRK":        (_gnews("site:accessdprk.com"), "A"),
    "ArmsControlWonk":   (_gnews("site:armscontrolwonk.com"), "A"),
    "Stimson":           ("https://www.stimson.org/feed/", "B"),
    "IISS":              (_gnews("Korea+site:iiss.org"), "B"),
    "ASAN Institute":    (_gnews("Korea+site:asaninst.org"), "B"),
    "EAI":               ("https://www.eai.or.kr/new/en/etc/rss.asp", "B"),
    "Sejong Institute":  (_gnews("Korea+site:sejong.org"), "B"),
    "SIPRI":             (_gnews("Korea+site:sipri.org"), "B"),
    "War on the Rocks":  ("https://warontherocks.com/feed/", "B"),
    "Foreign Affairs":   ("https://www.foreignaffairs.com/rss.xml", "A"),
    "Foreign Policy":    ("https://foreignpolicy.com/feed/", "B"),
    "Diplomat":          ("https://thediplomat.com/feed/", "C"),
    "NKPro":             ("https://www.nknews.org/pro/feed/", "A"),
    # ── Additional think tanks ───────────────────────────────────────────
    "AEI":               (_gnews("Korea+site:aei.org"), "A"),
    "Hudson Institute":  (_gnews("Korea+site:hudson.org"), "B"),
    "Heritage":          (_gnews("Korea+site:heritage.org"), "B"),
    "Atlantic Council":  (_gnews("Korea+site:atlanticcouncil.org"), "B"),
    "KEIA":              (_gnews("Korea+site:keia.org"), "A"),
    "NBR":               (_gnews("Korea+site:nbr.org"), "B"),
    "PIIE":              (_gnews("Korea+site:piie.com"), "B"),
    "USIP":              (_gnews("Korea+site:usip.org"), "B"),
    # ── CRS / Congressional research ────────────────────────────────────────
    "CRS Korea":         (_gnews("Korea+OR+DPRK+site:crsreports.congress.gov"), "B"),
    "CRS North Korea":   (_gnews("North+Korea+site:everycrsreport.com"), "B"),
    # ── DPRK-specialist direct feeds ────────────────────────────────────────
    "Beyond Parallel":   (_gnews("site:beyondparallel.csis.org"), "A"),
}

# Tier 3: Use Google Scholar RSS and site-specific searches to reduce noise
TIER3_FEEDS = {
    # A+ tier — top IR/security journals
    "Int'l Security":         (_gnews("%22International+Security%22+%22Korea%22+OR+%22DPRK%22+OR+%22Pyongyang%22"), "A+"),
    "Int'l Organization":     (_gnews("%22International+Organization%22+%22Korea%22+OR+%22DPRK%22"), "A+"),
    "World Politics":         (_gnews("%22World+Politics%22+%22Korea%22+OR+%22DPRK%22+OR+%22Korean+Peninsula%22"), "A+"),
    "American Pol. Sci. Review": (_gnews("%22American+Political+Science+Review%22+%22Korea%22+OR+%22DPRK%22"), "A+"),
    # A tier — strong IR/area studies journals
    "J. Conflict Resolution": (_gnews("%22Journal+of+Conflict+Resolution%22+%22Korea%22+OR+%22DPRK%22"), "A"),
    "J. Peace Research":      (_gnews("%22Journal+of+Peace+Research%22+%22Korea%22+OR+%22DPRK%22"), "A"),
    "Security Studies":       (_gnews("%22Security+Studies%22+%22Korea%22+OR+%22DPRK%22+OR+%22North+Korea%22"), "A"),
    "Int'l Studies Quarterly": (_gnews("%22International+Studies+Quarterly%22+%22Korea%22+OR+%22DPRK%22"), "A"),
    "J. Strategic Studies":   (_gnews("%22Journal+of+Strategic+Studies%22+%22Korea%22+OR+%22DPRK%22"), "A"),
    "Asian Survey":           (_gnews("%22Asian+Survey%22+%22Korea%22+OR+%22DPRK%22+OR+%22Korean+Peninsula%22"), "A"),
    "Pacific Review":         (_gnews("%22Pacific+Review%22+%22Korea%22+OR+%22DPRK%22"), "A"),
    "Foreign Affairs":        (_gnews("%22Foreign+Affairs%22+%22Korea%22+OR+%22DPRK%22+OR+%22North+Korea%22"), "A"),
    "Survival":               (_gnews("%22Survival%22+IISS+%22Korea%22+OR+%22DPRK%22"), "A"),
    # B tier — specialized Korea/Asia journals
    "Korean J. Def. Analysis": (_gnews("%22Korean+Journal+of+Defense+Analysis%22"), "B"),
    "North Korean Review":    (_gnews("%22North+Korean+Review%22"), "B"),
    "Asian Security":         (_gnews("%22Asian+Security%22+%22Korea%22+OR+%22DPRK%22"), "B"),
    "Pacific Affairs":        (_gnews("%22Pacific+Affairs%22+%22Korea%22+OR+%22DPRK%22"), "B"),
    "Korean Studies":         (_gnews("%22Korean+Studies%22+journal+%22North+Korea%22+OR+%22DPRK%22+OR+%22Korean+Peninsula%22"), "B"),
    "Nonproliferation Rev.":  (_gnews("%22Nonproliferation+Review%22+%22Korea%22+OR+%22DPRK%22"), "B"),
    "Washington Quarterly":   (_gnews("%22Washington+Quarterly%22+%22Korea%22+OR+%22DPRK%22"), "B"),
}

TIER4_FEEDS = {
    # Direct KCNA sources (often blocked — kept for when they work)
    "KCNA Watch":         "https://kcnawatch.org/newstream/feed/",
    "KCNA":               _gnews("site:kcna.kp"),
    "Rodong Sinmun":      _gnews("site:rodong.rep.kp"),
    # Wire services / outlets that relay KCNA content daily (primary indirect)
    "KCNA (Yonhap)":      _gnews("KCNA+Yonhap"),
    "KCNA (Reuters)":     _gnews("KCNA+site:reuters.com"),
    "KCNA (AP)":          _gnews("KCNA+site:apnews.com"),
    "KCNA (AFP)":         _gnews("KCNA+OR+%22North+Korea+state+media%22+site:france24.com"),
    # NK-specialist outlets that cite KCNA
    "KCNA (38 North)":    _gnews("KCNA+site:38north.org"),
    "KCNA (Daily NK)":    _gnews("KCNA+site:dailynk.com"),
    "KCNA (NK News)":     _gnews("KCNA+site:nknews.org"),
    "KCNA (NK Pro)":      _gnews("KCNA+site:korearisgroup.com"),
    # Major newspapers that relay KCNA statements
    "KCNA (BBC)":         _gnews("KCNA+OR+%22North+Korea+state+media%22+site:bbc.com"),
    "KCNA (NYT)":         _gnews("KCNA+site:nytimes.com"),
    "KCNA (WaPo)":        _gnews("KCNA+site:washingtonpost.com"),
    # Broad catch-all queries (redundant by design — ensures coverage)
    "KCNA (KCBS)":        _gnews("%22Korean+Central+Broadcasting%22+OR+%22KCNA+reported%22+OR+%22KCNA+said%22"),
    "KCNA (state media)": _gnews("%22North+Korea+state+media%22+OR+%22KCNA+said%22+OR+%22Pyongyang+said%22"),
    "KCNA (Kim stmt)":    _gnews("%22Kim+Jong+Un%22+%22KCNA%22+OR+%22state+media%22+statement+OR+message+OR+order"),
}

# ── Kim Jong Un appearance tracking feeds ──────────────────────────────────
KIM_TRACKER_FEEDS = {
    "KJU Appearance":     _gnews("%22Kim+Jong+Un%22+appearance+OR+appeared+OR+attended+OR+inspected+OR+observed"),
    "KJU Activity":       _gnews("%22Kim+Jong+Un%22+guidance+OR+visit+OR+presided+OR+oversaw"),
    "NK Leadership Watch": _gnews("%22Kim+Jong+Un%22+site:nkleadershipwatch.org"),
    "Daily NK KJU":       _gnews("%22Kim+Jong+Un%22+site:dailynk.com"),
    "KCNA KJU":           _gnews("%22Kim+Jong+Un%22+site:kcnawatch.org"),
}

KOREA_KEYWORDS = re.compile(
    r"korea|dprk|pyongyang|seoul|rok\b|kim jong|yoon suk|korean peninsula"
    r"|denucleariz|kaesong|yongbyon|hwasong|punggye|38th parallel"
    r"|usfk|combined forces|kim yo jong|choe son hui"
    r"|north korea|south korea|inter-korean"
    r"|한반도|북한|남북|조선민주주의|평양|서울|통일부|국방부",
    re.IGNORECASE,
)

PRESTIGE_JOURNALISTS = {
    "Timothy Martin", "Dasl Yoon", "Choe Sang-Hun", "Michelle Ye Hee Lee",
    "Hyonhee Shin", "Josh Smith", "Joyce Lee",
    "Ankit Panda", "Jeongmin Kim",
    "Jean Lee", "Simon Mundy", "Edward White",
    "Dagyum Ji", "Ifang Bremer", "Chad O'Carroll",
    # FT Seoul
    "Jean Mackenzie", "Daniel Tudor", "Song Jung-a",
    # Additional journalists
    "Victoria Kim", "Jeong-ho Lee", "Kim Tong-hyung",
    "Sotaro Suzuki", "Takashi Umekawa",
}

REQUEST_TIMEOUT = 8
HEADERS = {"User-Agent": "CSISKoreaDigest/1.0"}
MAX_WORKERS = 25  # Thread pool size for parallel fetching

# ─────────────────────────────────────────────────────────────────────────────
# SOURCE HEALTH TRACKING (per-run, not persistent)
# ─────────────────────────────────────────────────────────────────────────────
_source_health = {}  # {feed_name: {articles: int, success: bool, error_msg: str|None}}

MAJOR_FEEDS = {"Yonhap English", "Korea Herald", "Reuters Korea", "AP Korea"}


# ─────────────────────────────────────────────────────────────────────────────
# HELPERS
# ─────────────────────────────────────────────────────────────────────────────

def _parse_feed(url: str) -> list:
    for attempt in range(3):
        try:
            resp = requests.get(url, timeout=REQUEST_TIMEOUT, headers=HEADERS)
            if resp.status_code in (401, 403):
                print(f"    ⚠  Feed blocked ({resp.status_code}): {url[:80]}")
                return []
            resp.raise_for_status()
            return feedparser.parse(resp.content).entries
        except (requests.ConnectionError, requests.Timeout) as e:
            if attempt < 2:
                time.sleep(2 * (attempt + 1))
                continue
            print(f"    ⚠  Feed error (after 3 tries): {e}")
            return []
        except Exception as e:
            print(f"    ⚠  Feed error: {e}")
            return []
    return []


def _entry_to_article(entry, source: str, lang: str = "EN", extra: dict | None = None) -> dict:
    title = entry.get("title", "").strip()
    link = entry.get("link", "").strip()
    summary = entry.get("summary", entry.get("description", "")).strip()
    summary = re.sub(r"<[^>]+>", " ", summary)
    summary = re.sub(r"\s+", " ", summary).strip()

    pub_date = None
    for attr in ("published_parsed", "updated_parsed"):
        parsed = getattr(entry, attr, None)
        if parsed:
            pub_date = datetime(*parsed[:6], tzinfo=timezone.utc).isoformat()
            break

    # Extract RSS category tags (feedparser stores them in entry.tags)
    tags = []
    for tag in getattr(entry, "tags", []) or []:
        term = tag.get("term", "").strip()
        if term:
            tags.append(term)

    article = {
        "title": title,
        "url": link,
        "summary": summary[:800],
        "source": source,
        "lang": lang,
        "pub_date": pub_date,
    }
    if tags:
        article["tags"] = tags
    if extra:
        article.update(extra)
    return article


def _is_recent(entry, hours: int = 48) -> bool:
    cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
    for attr in ("published_parsed", "updated_parsed"):
        parsed = getattr(entry, attr, None)
        if parsed:
            return datetime(*parsed[:6], tzinfo=timezone.utc) >= cutoff
    return True


_ENTERTAINMENT_FILTER = re.compile(
    r"\bk-?pop\b|\bbts\b|\bblackpink\b|\bnewjeans\b|\baespa\b|\bstray\s*kids\b"
    r"|\btwice\b|\b(?:k-?)?drama\b|\bidol\b|\bkcon\b|\bhybe\b|\bjyp\b|\bsm\s*ent",
    re.IGNORECASE,
)


def _is_entertainment(entry) -> bool:
    text = f"{entry.get('title', '')} {entry.get('summary', entry.get('description', ''))}"
    return bool(_ENTERTAINMENT_FILTER.search(text))


def _is_korea_related(entry) -> bool:
    text = f"{entry.get('title', '')} {entry.get('summary', entry.get('description', ''))}"
    return bool(KOREA_KEYWORDS.search(text))


def _flag_journalist(article: dict) -> dict:
    text = f"{article['title']} {article['summary']}".lower()
    for name in PRESTIGE_JOURNALISTS:
        if name.lower() in text:
            article["flagged_journalist"] = name
            break
    return article


def _dedup(articles: list) -> list:
    seen = set()
    out = []
    for a in articles:
        if a["url"] and a["url"] not in seen:
            seen.add(a["url"])
            out.append(a)
    return out


# ─────────────────────────────────────────────────────────────────────────────
# PARALLEL FEED FETCHER
# ─────────────────────────────────────────────────────────────────────────────

def _fetch_feeds_parallel(feed_dict: dict, is_tiered: bool = False) -> dict:
    """Fetch all feeds in parallel. Returns {source: (entries, extra_info)}.
    Also records per-source health data in the module-level _source_health dict."""
    results = {}

    def _fetch_one(source, url_or_tuple):
        if is_tiered:
            url, tier_val = url_or_tuple
        else:
            url = url_or_tuple
            tier_val = None
        entries = _parse_feed(url)
        return source, entries, tier_val

    items = list(feed_dict.items())
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
        futures = {pool.submit(_fetch_one, src, val): src for src, val in items}
        for future in as_completed(futures):
            src = futures[future]
            try:
                source, entries, tier_val = future.result()
                results[source] = (entries, tier_val)
                _source_health[source] = {
                    "articles": len(entries),
                    "success": len(entries) > 0,
                    "error_msg": None,
                }
            except Exception as e:
                print(f"    ⚠  Thread error: {e}")
                _source_health[src] = {
                    "articles": 0,
                    "success": False,
                    "error_msg": str(e),
                }
    return results


# ─────────────────────────────────────────────────────────────────────────────
# TIER COLLECTORS
# ─────────────────────────────────────────────────────────────────────────────

def _collect_tier1() -> list:
    articles = []
    results = _fetch_feeds_parallel(TIER1_FEEDS)
    for source, (entries, _) in results.items():
        # Korean-language feeds get lang="KO"
        lang = "KO" if source in ("조선일보", "한겨레", "동아일보", "MBN",
                                    "JTBC", "KBS", "MBC", "SBS", "YTN", "Channel A",
                                    "매일경제", "한국경제",
                                    "경향신문", "뉴스1", "연합뉴스") else "EN"
        for entry in entries:
            if not _is_recent(entry, hours=24):
                continue
            if not _is_korea_related(entry):
                continue
            if _is_entertainment(entry):
                continue
            article = _entry_to_article(entry, source, lang=lang)
            article = _flag_journalist(article)
            articles.append(article)
    return _dedup(articles)


def _collect_tier2() -> list:
    articles = []
    results = _fetch_feeds_parallel(TIER2_FEEDS, is_tiered=True)
    for source, (entries, prestige) in results.items():
        for entry in entries:
            if not _is_recent(entry, hours=36):
                continue
            if not _is_korea_related(entry):
                continue
            article = _entry_to_article(entry, source, extra={"prestige": prestige})
            articles.append(article)
    return _dedup(articles)


def _collect_tier3() -> list:
    articles = []
    results = _fetch_feeds_parallel(TIER3_FEEDS, is_tiered=True)
    for source, (entries, tier) in results.items():
        for entry in entries:
            if not _is_recent(entry, hours=72):
                continue
            if not _is_korea_related(entry):
                continue
            # Extra filter: must mention academic-like terms or the journal name
            text = f"{entry.get('title', '')} {entry.get('summary', entry.get('description', ''))}".lower()
            academic_signals = ("journal", "paper", "study", "research", "analysis",
                                "findings", "abstract", "doi", "vol.", "issue",
                                source.lower())
            if not any(s in text for s in academic_signals):
                continue
            article = _entry_to_article(entry, source, extra={"journal_tier": tier})
            articles.append(article)
    return _dedup(articles)


def _collect_tier4() -> list:
    articles = []
    results = _fetch_feeds_parallel(TIER4_FEEDS)
    for source, (entries, _) in results.items():
        for entry in entries:
            if not _is_recent(entry, hours=48):
                continue
            article = _entry_to_article(entry, source, lang="KO")
            articles.append(article)
    return _dedup(articles)


def _scrape_kcna_watch() -> list:
    """Scrape KCNA Watch newstream page for today's article headlines and categories.
    Falls back to Google cache / web search if direct scrape is blocked (403).
    Returns a list of dicts with title, url, category."""
    articles = []

    # Attempt 1: direct scrape
    try:
        resp = requests.get(
            "https://kcnawatch.org/newstream/",
            timeout=REQUEST_TIMEOUT,
            headers={**HEADERS, "Accept": "text/html"},
        )
        if resp.status_code == 403:
            print(f"  ── KCNA Watch scrape: blocked (403) — trying fallback")
        else:
            resp.raise_for_status()
            articles = _parse_kcna_watch_html(resp.text)
            if articles:
                print(f"  ── KCNA Watch scrape: {len(articles)} articles from newstream page")
                return articles
    except Exception as e:
        print(f"  ── KCNA Watch scrape: ⚠ direct failed ({e})")

    # Attempt 2: Google News search for kcnawatch.org articles from today
    try:
        gnews_url = _gnews("site:kcnawatch.org/newstream/")
        entries = _parse_feed(gnews_url)
        for entry in entries:
            if not _is_recent(entry, hours=48):
                continue
            title = entry.get("title", "").strip()
            link = entry.get("link", "").strip()
            if title and link:
                articles.append({
                    "title": re.sub(r"<[^>]+>", "", title).strip(),
                    "url": link,
                    "category": "Uncategorized",
                })
        if articles:
            print(f"  ── KCNA Watch scrape: {len(articles)} articles via Google News fallback")
    except Exception as e:
        print(f"  ── KCNA Watch scrape: ⚠ Google fallback also failed ({e})")

    if not articles:
        print(f"  ── KCNA Watch scrape: 0 articles (all methods failed)")

    return articles


def _parse_kcna_watch_html(html: str) -> list:
    """Parse KCNA Watch newstream HTML for article entries."""
    articles = []
    import re as _re

    link_pattern = _re.compile(
        r'<a\s+href="(https?://kcnawatch\.org/newstream/\d{4}/\d{2}/\d{2}/[^"]+)"[^>]*>\s*([^<]+?)\s*</a>',
        _re.IGNORECASE,
    )
    cat_pattern = _re.compile(
        r'<span[^>]*class="[^"]*category[^"]*"[^>]*>\s*([^<]+?)\s*</span>',
        _re.IGNORECASE,
    )

    today_str = datetime.now(timezone.utc).strftime("%Y/%m/%d")
    yesterday_str = (datetime.now(timezone.utc) - timedelta(days=1)).strftime("%Y/%m/%d")

    seen_urls = set()
    for match in link_pattern.finditer(html):
        url, title = match.group(1), match.group(2).strip()
        if url in seen_urls:
            continue
        if today_str not in url and yesterday_str not in url:
            continue
        seen_urls.add(url)

        start = max(0, match.start() - 500)
        context = html[start:match.end() + 200]
        cat_match = cat_pattern.search(context)
        category = cat_match.group(1).strip() if cat_match else "Uncategorized"

        articles.append({
            "title": re.sub(r"<[^>]+>", "", title).strip(),
            "url": url,
            "category": category,
        })

    return articles


_DIRECT_KCNA_SOURCES = {"KCNA Watch", "KCNA", "Rodong Sinmun", "KCNA (Yonhap)"}

_KCNA_TITLE_PATTERNS = re.compile(
    r"KCNA|Korean Central News Agency|Rodong Sinmun|"
    r"Pyongyang\s+(said|says|reported|reports|announced|announces|claims|claimed)|"
    r"North Korea\s+(said|says)\s+.*(state media|official media)",
    re.IGNORECASE,
)


def _build_kcna_summary(tier4_articles: list, scraped_articles: list) -> dict:
    """Build a structured summary of today's KCNA output for the digest prompt."""
    # Combine sources for article count
    all_titles = set()
    categories = {}
    sources = {}
    direct_count = 0
    indirect_count = 0

    for art in tier4_articles:
        title = art.get("title", "").strip()
        if title:
            all_titles.add(title.lower())
        src = art.get("source", "Unknown")
        sources[src] = sources.get(src, 0) + 1
        # Track direct vs indirect
        if src in _DIRECT_KCNA_SOURCES:
            direct_count += 1
        else:
            indirect_count += 1
        for tag in art.get("tags", []):
            categories[tag] = categories.get(tag, 0) + 1
        # For indirect-source articles, check if title/summary mentions KCNA
        # and add a synthetic category so they're visible in the category breakdown
        if src not in _DIRECT_KCNA_SOURCES:
            text = f"{title} {art.get('summary', '')}".lower()
            if _KCNA_TITLE_PATTERNS.search(text):
                categories["KCNA-cited"] = categories.get("KCNA-cited", 0) + 1

    for art in scraped_articles:
        title = art.get("title", "").strip()
        if title and title.lower() not in all_titles:
            all_titles.add(title.lower())
            direct_count += 1  # scraped articles are always direct KCNA
        cat = art.get("category", "Uncategorized")
        if cat:
            categories[cat] = categories.get(cat, 0) + 1

    # Build headline list from scraped articles (more structured)
    headlines = []
    for art in scraped_articles:
        entry = art.get("title", "")
        cat = art.get("category", "")
        if entry:
            headlines.append(f"[{cat}] {entry}" if cat else entry)

    # Also add tier4 RSS headlines not already covered — include indirect sources
    scraped_titles = {a.get("title", "").lower() for a in scraped_articles}
    for art in tier4_articles:
        title = art.get("title", "")
        if title and title.lower() not in scraped_titles:
            src = art.get("source", "")
            tags = art.get("tags", [])
            # For indirect sources, label with "via <outlet>" so the LLM
            # knows this is a secondary report citing KCNA
            if src not in _DIRECT_KCNA_SOURCES and src:
                tag_str = f"via {src}"
            else:
                tag_str = tags[0] if tags else src
            headlines.append(f"[{tag_str}] {title}")

    return {
        "total_articles": len(all_titles),
        "direct_count": direct_count,
        "indirect_count": indirect_count,
        "categories": dict(sorted(categories.items(), key=lambda x: -x[1])),
        "sources": sources,
        "headlines": headlines[:50],  # Cap at 50 headlines
    }


SATELLITE_IMAGERY_FEEDS = {
    # Specialist sources (primary imagery analysts)
    "BP Imagery":       _gnews("satellite+imagery+site:beyondparallel.csis.org"),
    "38N Imagery":      _gnews("satellite+imagery+site:38north.org"),
    "AEI Imagery":      _gnews("North+Korea+satellite+imagery+site:aei.org"),
    "Middlebury MIIS":  _gnews("North+Korea+satellite+site:nonproliferation.org"),
    # Major wire services / newspapers covering imagery reports
    "Imagery Reuters":  _gnews("%22satellite+imagery%22+%22North+Korea%22+site:reuters.com"),
    "Imagery AP":       _gnews("%22satellite+imagery%22+%22North+Korea%22+site:apnews.com"),
    "Imagery BBC":      _gnews("%22satellite%22+%22North+Korea%22+site:bbc.com"),
    "Imagery NYT":      _gnews("%22satellite%22+%22North+Korea%22+site:nytimes.com"),
    "Imagery WaPo":     _gnews("%22satellite%22+%22North+Korea%22+site:washingtonpost.com"),
    "Imagery WSJ":      _gnews("%22satellite%22+%22North+Korea%22+site:wsj.com"),
    "Imagery CNN":      _gnews("%22satellite%22+%22North+Korea%22+site:cnn.com"),
    # Broad catch-all for any outlet reporting DPRK imagery
    "NK Imagery":       _gnews("%22satellite+imag%22+%22North+Korea%22+OR+%22DPRK%22+Yongbyon+OR+Sinpo+OR+Sohae+OR+Punggye+OR+%22nuclear+site%22"),
    "NK Facility":      _gnews("%22North+Korea%22+%22imagery+shows%22+OR+%22images+show%22+OR+%22images+reveal%22+OR+%22imagery+reveals%22"),
}


def _collect_satellite_imagery() -> list:
    """Collect satellite imagery analysis articles (72h window — published sporadically)."""
    articles = []
    results = _fetch_feeds_parallel(SATELLITE_IMAGERY_FEEDS)
    for source, (entries, _) in results.items():
        for entry in entries:
            if not _is_recent(entry, hours=72):
                continue
            article = _entry_to_article(entry, source, lang="EN")
            articles.append(article)
    return _dedup(articles)


def _collect_kim_tracker() -> list:
    """Collect recent Kim Jong Un appearance/activity reports from multiple sources."""
    articles = []
    results = _fetch_feeds_parallel(KIM_TRACKER_FEEDS)
    for source, (entries, _) in results.items():
        for entry in entries:
            if not _is_recent(entry, hours=72):  # wider window for appearance tracking
                continue
            article = _entry_to_article(entry, source, lang="EN")
            articles.append(article)
    return _dedup(articles)


# ─────────────────────────────────────────────────────────────────────────────
# MARKET DATA
# ─────────────────────────────────────────────────────────────────────────────

def _collect_markets() -> dict:
    """Fetch KOSPI, Brent Crude, USD/KRW with Yahoo primary + Stooq fallback.

    Data source chain per symbol:
      1. Yahoo Finance query1 host
      2. Yahoo Finance query2 host (Yahoo's load-balanced mirror)
      3. Stooq CSV (stable free source, daily resolution)
    Each result is validated for sanity range + staleness (>5 days = reject).
    """
    YAHOO_SYMBOLS = {
        "kospi": "^KS11",
        "brent": "BZ=F",
        "usd_krw": "KRW=X",
    }
    STOOQ_SYMBOLS = {
        "kospi": "^ks11",      # Stooq ticker for KOSPI Composite
        "brent": "cb.f",       # ICE Brent Crude continuous futures
        "usd_krw": "usdkrw",
    }
    # Sanity ranges — if price falls outside, the data is likely stale/garbage.
    # KOSPI upper bound set generously (2026+ headroom); tighten only if
    # scraped data starts drifting wildly above 10k.
    _SANITY_RANGES = {
        "kospi": (1500, 10000),
        "brent": (40, 200),
        "usd_krw": (1000, 1700),
    }

    def _format_value(key, price):
        if key == "usd_krw" or key == "kospi":
            return f"{price:,.2f}"
        return f"{price:.2f}"

    def _validate(key, price, mkt_time):
        """Return (ok, reason). mkt_time is an aware datetime or None."""
        lo, hi = _SANITY_RANGES.get(key, (0, float("inf")))
        if not price or price < lo or price > hi:
            return False, f"price {price} outside sanity range ({lo}-{hi})"
        if mkt_time:
            age_days = (datetime.now(timezone.utc) - mkt_time).days
            if age_days > 5:
                return False, f"data is {age_days} days old"
        return True, ""

    def _fetch_yahoo(key):
        """Try Yahoo Finance — query1 then query2 as mirror fallback."""
        symbol = YAHOO_SYMBOLS[key]
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                          "AppleWebKit/537.36 (KHTML, like Gecko) "
                          "Chrome/120.0.0.0 Safari/537.36"
        }
        for host in ("query1.finance.yahoo.com", "query2.finance.yahoo.com"):
            try:
                url = f"https://{host}/v8/finance/chart/{symbol}?range=5d&interval=1d"
                resp = requests.get(url, timeout=10, headers=headers)
                resp.raise_for_status()
                data = resp.json()
                meta = data["chart"]["result"][0]["meta"]
                price = meta.get("regularMarketPrice", 0)
                prev_close = meta.get("chartPreviousClose",
                                      meta.get("previousClose", price))
                mkt_ts = meta.get("regularMarketTime", 0)
                mkt_time = (datetime.fromtimestamp(mkt_ts, tz=timezone.utc)
                            if mkt_ts else None)
                ok, reason = _validate(key, price, mkt_time)
                if not ok:
                    print(f"    ⚠  {key}: Yahoo {host} {reason}")
                    continue
                change_pct = (((price - prev_close) / prev_close * 100)
                              if prev_close else 0)
                return {
                    "value": _format_value(key, price),
                    "change_pct": round(change_pct, 2),
                    "as_of": mkt_time.strftime("%b %d") if mkt_time else "",
                }
            except (requests.RequestException, KeyError, ValueError, TypeError) as e:
                print(f"    ⚠  {key}: Yahoo {host} error: {e}")
                continue
        return None

    def _fetch_stooq(key):
        """Fallback: Stooq.com daily CSV. Free, no auth, very stable."""
        import csv as _csv
        import io as _io
        symbol = STOOQ_SYMBOLS[key]
        try:
            url = f"https://stooq.com/q/d/l/?s={symbol}&i=d"
            resp = requests.get(url, timeout=10,
                                headers={"User-Agent": "Mozilla/5.0"})
            resp.raise_for_status()
            text = resp.text.strip()
            if not text or "no data" in text.lower():
                print(f"    ⚠  {key}: Stooq returned no data for {symbol}")
                return None
            reader = _csv.DictReader(_io.StringIO(text))
            rows = [r for r in reader if r.get("Close")]
            if len(rows) < 2:
                print(f"    ⚠  {key}: Stooq returned <2 rows")
                return None
            latest = rows[-1]
            prev = rows[-2]
            price = float(latest["Close"])
            prev_close = float(prev["Close"])
            try:
                latest_dt = datetime.strptime(
                    latest["Date"], "%Y-%m-%d").replace(tzinfo=timezone.utc)
            except (ValueError, KeyError):
                latest_dt = None
            ok, reason = _validate(key, price, latest_dt)
            if not ok:
                print(f"    ⚠  {key}: Stooq {reason}")
                return None
            change_pct = (((price - prev_close) / prev_close * 100)
                          if prev_close else 0)
            return {
                "value": _format_value(key, price),
                "change_pct": round(change_pct, 2),
                "as_of": latest_dt.strftime("%b %d") if latest_dt else "",
            }
        except (requests.RequestException, KeyError, ValueError, TypeError) as e:
            print(f"    ⚠  {key}: Stooq error: {e}")
            return None

    def _fetch_symbol(key):
        """Chain: Yahoo → Stooq. Returns (key, result_dict)."""
        result = _fetch_yahoo(key)
        if result:
            return key, result
        print(f"    ↻  {key}: Yahoo failed/stale — falling back to Stooq")
        result = _fetch_stooq(key)
        if result:
            return key, result
        print(f"    ⚠  {key}: all data sources exhausted")
        return key, {"value": "—", "change_pct": 0, "as_of": ""}

    result = {}
    with ThreadPoolExecutor(max_workers=7) as pool:
        symbol_futures = {pool.submit(_fetch_symbol, k): k
                          for k in YAHOO_SYMBOLS.keys()}
        bok_f = pool.submit(_fetch_bok_rate)
        cds_f = pool.submit(_fetch_korea_cds)
        gdp_f = pool.submit(_fetch_gdp_estimate)
        ecos_f = pool.submit(_fetch_bok_ecos)

        for future in as_completed(symbol_futures):
            k, v = future.result()
            result[k] = v

    result["bok_rate"] = bok_f.result()
    result["korea_cds"] = cds_f.result()
    result["gdp_estimate"] = gdp_f.result()

    # BOK ECOS data — only include if API key was set and data returned
    ecos_data = ecos_f.result()
    if ecos_data:
        result["bok_ecos"] = ecos_data

    # Always return market data — even if all fetches fail, BOK indicators
    # have hardcoded fallbacks so there's always something to show
    return result


def _fetch_bok_rate() -> dict:
    """Fetch BOK base rate. Falls back to last known value."""
    # BOK ECOS open API — base rate series (dynamic date range)
    try:
        now = datetime.now(timezone.utc)
        start = (now - timedelta(days=365)).strftime("%Y%m")
        end = now.strftime("%Y%m")
        url = f"https://ecos.bok.or.kr/api/StatisticSearch/json/en/1/5/722Y001/M/{start}/{end}/0101000/"
        resp = requests.get(url, timeout=10, headers={"User-Agent": "Mozilla/5.0"})
        if resp.ok:
            rows = resp.json().get("StatisticSearch", {}).get("row", [])
            if rows:
                val = rows[-1].get("DATA_VALUE", "")
                if val:
                    return {"value": f"{float(val):.2f}%", "last_change": ""}
    except Exception:
        pass
    # Fallback: last known BOK rate (updated manually if API unavailable)
    print("    ⚠  BOK rate: using fallback (2.50%)")
    fallback_date = datetime.now(timezone.utc).strftime("%b %Y")
    return {"value": "2.50%", "last_change": fallback_date}


def _fetch_bok_ecos() -> dict | None:
    """Fetch key economic indicators from Bank of Korea ECOS API.

    Requires BOK_API_KEY environment variable. Returns None gracefully if
    the key is not set or any request fails.

    Stats fetched:
      - CPI year-over-year % change
      - Unemployment rate (%)
      - Trade balance (million USD)
      - Consumer Confidence Index (composite)
    """
    api_key = os.environ.get("BOK_API_KEY", "").strip()
    if not api_key:
        return None

    now = datetime.now(timezone.utc)
    # Use a 2-year lookback to ensure we get at least one data point
    start = (now - timedelta(days=730)).strftime("%Y%m")
    end = now.strftime("%Y%m")

    STAT_SPECS = {
        "cpi_yoy": ("722Y001", "M", "0101000"),
        "unemployment": ("403Y001", "M", "0101000"),
        "trade_balance": ("301Y013", "M", "101000"),
        "consumer_confidence": ("732Y001", "M", "0101000"),
    }

    base_url = "https://ecos.bok.or.kr/api/StatisticSearch"
    results = {}

    for key, (stat_code, freq, item_code) in STAT_SPECS.items():
        try:
            url = (
                f"{base_url}/{api_key}/json/kr/1/10/"
                f"{stat_code}/{freq}/{start}/{end}/{item_code}"
            )
            resp = requests.get(url, timeout=5, headers={"User-Agent": "Mozilla/5.0"})
            if not resp.ok:
                continue
            data = resp.json()
            rows = data.get("StatisticSearch", {}).get("row", [])
            if not rows:
                continue
            # Take the most recent data point (last row)
            val_str = rows[-1].get("DATA_VALUE", "")
            if not val_str:
                continue
            val = float(val_str)

            # Format based on indicator type
            if key == "cpi_yoy":
                results[key] = f"{val:.1f}%"
            elif key == "unemployment":
                results[key] = f"{val:.1f}%"
            elif key == "trade_balance":
                # Value is in million USD; convert to billions for display
                val_b = val / 1000.0
                sign = "+" if val_b >= 0 else "-"
                results[key] = f"{sign}${abs(val_b):.1f}B"
            elif key == "consumer_confidence":
                results[key] = f"{val:.1f}"
        except Exception:
            # Never crash the pipeline — skip this indicator
            continue

    return results if results else None


def _fetch_korea_cds() -> dict:
    """Fetch Korea 5-year CDS spread (bps) from WorldGovernmentBonds."""
    try:
        url = "https://www.worldgovernmentbonds.com/cds-historical-data/south-korea/5-years/"
        resp = requests.get(url, timeout=12, headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
        })
        if resp.ok:
            # Parse latest two CDS values from the history table
            # Table rows contain: Date | CDS spread (bps)
            matches = re.findall(
                r'<td[^>]*>\s*(\d{1,2}\s+\w+\s+\d{4})\s*</td>\s*<td[^>]*>\s*([\d.]+)\s*</td>',
                resp.text
            )
            if len(matches) >= 2:
                latest_date, latest_val = matches[0]
                _, prev_val = matches[1]
                spread = float(latest_val)
                prev_spread = float(prev_val)
                change = spread - prev_spread
                as_of = ""
                try:
                    as_of = datetime.strptime(latest_date.strip(), "%d %B %Y").strftime("%b %d")
                except ValueError:
                    try:
                        as_of = datetime.strptime(latest_date.strip(), "%d %b %Y").strftime("%b %d")
                    except ValueError:
                        pass
                return {"value": f"{spread:.0f}", "change_bps": round(change, 1), "as_of": as_of}
            elif matches:
                spread = float(matches[0][1])
                return {"value": f"{spread:.0f}", "change_bps": 0, "as_of": ""}
    except Exception as e:
        print(f"    ⚠  Korea CDS fetch error: {e}")

    # Fallback
    print("    ⚠  Korea 5Y CDS: using fallback (25 bps)")
    return {"value": "25", "change_bps": 0, "as_of": ""}


def _fetch_gdp_estimate() -> dict:
    """Fetch latest GDP growth estimate from BOK, with OECD as secondary source."""
    estimates = []

    # Source 1: BOK API (quarterly GDP data)
    try:
        now = datetime.now(timezone.utc)
        start = (now - timedelta(days=730)).strftime("%Y") + "Q1"
        end = now.strftime("%Y") + "Q4"
        url = f"https://ecos.bok.or.kr/api/StatisticSearch/json/en/1/20/200Y002/Q/{start}/{end}/10111/"
        resp = requests.get(url, timeout=10, headers={"User-Agent": "Mozilla/5.0"})
        if resp.ok:
            rows = resp.json().get("StatisticSearch", {}).get("row", [])
            if rows:
                latest = rows[-1]
                val = latest.get("DATA_VALUE", "")
                period = latest.get("TIME", "")
                if "Q" in str(period):
                    parts = str(period).split("Q")
                    period = f"Q{parts[1]} {parts[0]}"
                estimates.append({"value": f"{float(val):.1f}%", "period": period, "source": "BOK"})
    except Exception as e:
        print(f"    ⚠  BOK GDP API error: {e}")

    # Source 2: OECD forecast (scrape from news)
    try:
        url = _gnews("OECD+Korea+GDP+growth+forecast+2026")
        entries = _parse_feed(url)
        for entry in entries[:5]:
            text = f"{entry.get('title', '')} {entry.get('summary', entry.get('description', ''))}"
            match = re.search(r'(\d+\.\d+)\s*(?:percent|%|pct)', text, re.IGNORECASE)
            if match:
                val = float(match.group(1))
                if 0 < val < 10:
                    estimates.append({"value": f"{val:.1f}%", "period": "2026 forecast", "source": "OECD"})
                    break
    except Exception:
        pass

    # Source 3: BOK forecast from news (fallback)
    if not estimates:
        try:
            url = _gnews("Bank+of+Korea+GDP+growth+forecast+2026")
            entries = _parse_feed(url)
            for entry in entries[:5]:
                text = f"{entry.get('title', '')} {entry.get('summary', entry.get('description', ''))}"
                match = re.search(r'(\d+\.\d+)\s*(?:percent|%|pct)', text, re.IGNORECASE)
                if match:
                    val = float(match.group(1))
                    if 0 < val < 10:
                        estimates.append({"value": f"{val:.1f}%", "period": "2026 forecast", "source": "BOK"})
                        break
        except Exception:
            pass

    if estimates:
        # Return the most recent estimate; attach all sources for the digest prompt
        primary = estimates[0]
        if len(estimates) > 1:
            primary["alt_estimates"] = estimates[1:]
        return primary

    # Hardcoded fallback (updated manually if all sources fail)
    print("    ⚠  GDP estimate: using fallback (1.5%)")
    return {"value": "1.5%", "period": "2026 forecast", "source": "BOK"}


# ─────────────────────────────────────────────────────────────────────────────
# PUBLIC SENTIMENT — Korean Wikipedia structured tables
# ─────────────────────────────────────────────────────────────────────────────

def _scrape_wiki_polling(sentiment: dict) -> bool:
    """Scrape Korean Wikipedia for latest Gallup Korea polling data.

    Korean Wikipedia maintains well-structured HTML tables of weekly polling
    data at:
      - 대한민국의_대통령_지지율 (presidential approval)
      - 대한민국의_정당_지지율 (party support)

    Returns True if all 4 metrics were successfully extracted from the
    same source (guaranteeing consistency), False otherwise.
    """
    # ── Presidential approval ─────────────────────────────────────────────
    pres_url = "https://ko.wikipedia.org/wiki/%EB%8C%80%ED%95%9C%EB%AF%BC%EA%B5%AD%EC%9D%98_%EB%8C%80%ED%86%B5%EB%A0%B9_%EC%A7%80%EC%A7%80%EC%9C%A8"
    party_url = "https://ko.wikipedia.org/wiki/%EB%8C%80%ED%95%9C%EB%AF%BC%EA%B5%AD%EC%9D%98_%EC%A0%95%EB%8B%B9_%EC%A7%80%EC%A7%80%EC%9C%A8"

    headers = {
        "User-Agent": "KoreaDailyBrief/1.0 (research; contact: korea-brief@csis.org)",
        "Accept": "text/html",
    }

    pres_val = None
    pres_date = None
    pres_source = None

    # Fetch presidential approval page
    try:
        resp = requests.get(pres_url, timeout=15, headers=headers)
        resp.raise_for_status()
        html = resp.text

        # Find Gallup Korea rows in wikitable. The tables have rows like:
        #   <td>3월 3주차</td><td>한국갤럽</td>...<td>67%</td>...
        # We look for the last (most recent) Gallup Korea row with a percentage.
        # Pattern: find all rows containing "갤럽" and extract the percentage.
        gallup_rows = re.findall(
            r'<tr[^>]*>(.*?)</tr>',
            html, re.DOTALL
        )
        for row in reversed(gallup_rows):
            if '갤럽' not in row and 'Gallup' not in row:
                continue
            # Extract all cell contents
            cells = re.findall(r'<td[^>]*>(.*?)</td>', row, re.DOTALL)
            if len(cells) < 3:
                continue
            # Clean HTML tags from cells
            clean_cells = [re.sub(r'<[^>]+>', '', c).strip() for c in cells]
            # Find the percentage value (긍정/approval column)
            for cell in clean_cells:
                m = re.search(r'(\d{2})(?:\.\d+)?%', cell)
                if m:
                    val = float(m.group(1))
                    if 30 <= val <= 85:
                        pres_val = val
                        break
            if pres_val:
                # Extract date from the row (e.g., "3월 3주차" or "2026-03-17")
                for cell in clean_cells:
                    if re.search(r'\d+월.*주|20\d{2}', cell):
                        pres_date = cell
                        break
                pres_source = "Gallup Korea" if "갤럽" in row else "Realmeter"
                break
    except Exception as e:
        print(f"      ⚠ Wikipedia presidential page fetch failed: {e}")
        return False

    if not pres_val:
        return False

    # ── Party support ─────────────────────────────────────────────────────
    dp_val = None
    ppp_val = None
    ind_val = None
    party_date = None

    try:
        resp = requests.get(party_url, timeout=15, headers=headers)
        resp.raise_for_status()
        html = resp.text

        gallup_rows = re.findall(
            r'<tr[^>]*>(.*?)</tr>',
            html, re.DOTALL
        )
        for row in reversed(gallup_rows):
            if '갤럽' not in row and 'Gallup' not in row:
                continue
            cells = re.findall(r'<td[^>]*>(.*?)</td>', row, re.DOTALL)
            if len(cells) < 4:
                continue
            clean_cells = [re.sub(r'<[^>]+>', '', c).strip() for c in cells]
            # In the party support table, columns typically are:
            # Date | Pollster | DP% | PPP% | (others) | Independents%
            # We need to find the DP and PPP columns by looking for
            # cells with percentages in the expected range.
            pcts = []
            for cell in clean_cells:
                m = re.search(r'(\d{1,2})(?:\.\d+)?%', cell)
                if m:
                    pcts.append(float(m.group(1)))
                else:
                    pcts.append(None)

            # Heuristic: DP is usually the largest party %, PPP second
            valid_pcts = [(i, v) for i, v in enumerate(pcts) if v is not None and 5 <= v <= 60]
            if len(valid_pcts) >= 2:
                # Sort by value descending - largest is likely ruling party (DP)
                valid_pcts.sort(key=lambda x: x[1], reverse=True)
                dp_val = valid_pcts[0][1]
                ppp_val = valid_pcts[1][1]
                # Independents are often the 3rd largest or labeled 무당층
                if len(valid_pcts) >= 3:
                    ind_val = valid_pcts[2][1]
                # Extract date
                for cell in clean_cells:
                    if re.search(r'\d+월.*주|20\d{2}', cell):
                        party_date = cell
                        break
                break
    except Exception as e:
        print(f"      ⚠ Wikipedia party page fetch failed: {e}")
        # Still use presidential data if we got it
        pass

    # ── Set sentiment values ──────────────────────────────────────────────
    date_label = pres_date or party_date or "recent"
    source = pres_source or "Gallup Korea"

    sentiment["presidential_approval"] = {
        "value": f"{pres_val:g}%", "trend": None,
        "source": source, "last_updated": date_label,
    }

    if dp_val and ppp_val:
        sentiment["party_ruling"] = {
            "value": f"{dp_val:g}%",
            "party": "Democratic Party", "party_kr": "더불어민주당",
            "source": source, "last_updated": party_date or date_label,
        }
        sentiment["party_opposition"] = {
            "value": f"{ppp_val:g}%",
            "party": "People Power Party", "party_kr": "국민의힘",
            "source": source, "last_updated": party_date or date_label,
        }
        if ind_val:
            sentiment["party_independent"] = {
                "value": f"{ind_val:g}%",
                "source": source, "last_updated": party_date or date_label,
            }
        return True

    # Got presidential but not party - partial success
    return False


# ─────────────────────────────────────────────────────────────────────────────
# PUBLIC SENTIMENT — Gallup Korea, Realmeter (headline scraping fallback)
# ─────────────────────────────────────────────────────────────────────────────

def _collect_sentiment() -> dict:
    """Scrape latest Korean polling data from news headlines.

    Returns structured sentiment data for presidential approval,
    party support ratings (ruling, opposition, independents), and
    weekly spotlight topic. Claude will merge any new poll data
    from articles with these baseline numbers.
    """
    sentiment = {
        "presidential_approval": None,
        "party_ruling": None,
        "party_opposition": None,
        "party_independent": None,
        "gallup_spotlight": None,
        "discourse_flag": None,
    }

    # ── Pass 0: Korean Wikipedia structured tables (most reliable) ──────
    # Korean Wikipedia maintains well-structured tables of weekly polling
    # data from Gallup Korea and Realmeter. This is the most reliable
    # source because volunteer editors verify the numbers and the table
    # format is consistent.
    wiki_scraped = False
    try:
        wiki_scraped = _scrape_wiki_polling(sentiment)
        if wiki_scraped:
            print("      ✓ Wikipedia polling data scraped successfully")
    except Exception as e:
        print(f"      ⚠ Wikipedia scrape failed: {e}")

    # ── Fallback: News headline scraping ──────────────────────────────
    # If Wikipedia scrape failed or returned no data, fall back to
    # extracting from Korean news headlines about Gallup/Realmeter polls.
    #
    # IMPORTANT: Presidential approval regex MUST require "대통령" or
    # "presidential" near the number. The old regex matched generic
    # "support"/"지지율" which would grab party ratings or unrelated
    # percentages as presidential approval — the #1 cause of wrong numbers.

    # Presidential approval: MUST have "대통령" or "presidential" nearby.
    # The [^\d]{0,10} gap between the keyword block and the % capture is
    # critical — without it, the space before the digit isn't consumed.
    _RE_PRES_APPROVAL = re.compile(
        r'(?:대통령\s*(?:직무)?(?:수행)?(?:긍정)?[^\d]{0,20}지지율|'    # 대통령 지지율 XX%
        r'대통령[^\d]{0,30}(?:긍정|approval))[^\d]{0,10}'              # 대통령...긍정/approval
        r'(\d{1,3}(?:\.\d+)?)%|'
        r'(?:presidential\s+approval)[^\d]{0,20}(\d{1,3}(?:\.\d+)?)%|'  # presidential approval XX%
        r'(\d{1,3}(?:\.\d+)?)%\s*(?:presidential\s+approval)',          # XX% presidential approval
        re.IGNORECASE,
    )
    # Ruling party: 민주당 / Democratic Party
    _RE_RULING = re.compile(
        r'(?:민주당|Democratic\s*Party)[^\d]{0,30}(\d{1,3}(?:\.\d+)?)%|'
        r'(\d{1,3}(?:\.\d+)?)%\s*(?:민주당|Democratic\s*Party)',
        re.IGNORECASE,
    )
    # Opposition: 국민의힘 / People Power Party
    _RE_OPP = re.compile(
        r'(?:국민의힘|People\s*Power\s*Party)[^\d]{0,30}(\d{1,3}(?:\.\d+)?)%|'
        r'(\d{1,3}(?:\.\d+)?)%\s*(?:국민의힘|People\s*Power\s*Party)',
        re.IGNORECASE,
    )
    # Independents: 무당층 / 무당파
    _RE_IND = re.compile(
        r'(?:무당층|무당파|no\s*party\s*preference|independents?)[^\d]{0,30}(\d{1,3}(?:\.\d+)?)%|'
        r'(\d{1,3}(?:\.\d+)?)%\s*(?:무당층|무당파|no\s*party|independents?)',
        re.IGNORECASE,
    )

    # ── Korean news headline pattern ─────────────────────────────────────
    # Korean news outlets reliably report Gallup results in a standard
    # headline format that includes ALL numbers together, e.g.:
    #   "李대통령 지지율 67%…민주 46%·국힘 20% [한국갤럽]"
    #   "이재명 대통령 지지율 67%...민주 46%·국민의힘 20%"
    # This is far more reliable than trying to parse article body text.
    # We extract all percentages from these compact headline strings.
    _RE_HEADLINE_ALL = re.compile(
        r'(?:대통령|이재명|李)\s*(?:대통령)?\s*지지율?'
        r'.*?(?<!\d)(\d{2})%(?!p)'              # group 1: presidential approval (2 digits, skip %p)
        r'.*?(?:민주당?|민주|DP)\s*(\d{1,2})%'  # group 2: ruling party (DP)
        r'.*?(?:국힘|국민의힘|PPP)\s*(\d{1,2})%', # group 3: opposition (PPP)
        re.IGNORECASE,
    )

    def _gnews_kr(query: str) -> str:
        return f"https://news.google.com/rss/search?q={query}&hl=ko&gl=KR&ceid=KR:ko"

    # Queries: Korean news headlines about Gallup polls (most reliable source)
    # Use Korean-locale Google News for better Korean-language polling coverage
    combined_queries = [
        _gnews_kr("한국갤럽+대통령+지지율+민주+국힘"),
        _gnews_kr("한국갤럽+대통령+지지율+민주당+국민의힘"),
        _gnews("한국갤럽+대통령+지지율+민주+국힘"),
        _gnews("한국갤럽+대통령+지지율+민주당+국민의힘"),
        _gnews("한국갤럽+대통령+지지율"),
        _gnews_kr("리얼미터+대통령+지지율+민주+국힘"),
        _gnews("리얼미터+대통령+지지율+민주+국힘"),
    ]

    def _sane_pct_any(match) -> float | None:
        """Extract percentage from any non-None group in a match."""
        for i in range(1, (match.lastindex or 0) + 1):
            g = match.group(i)
            if g is not None:
                val = float(g)
                return val if 5 <= val <= 85 else None
        return None

    def _is_polling_article(text: str) -> bool:
        """Check that article is actually about polling."""
        poll_signals = ("갤럽", "gallup", "리얼미터", "realmeter", "여론조사",
                        "poll", "survey", "지지율", "approval rating")
        return any(s in text.lower() for s in poll_signals)

    def _get_pub_date(entry) -> str | None:
        for attr in ("published_parsed", "updated_parsed"):
            parsed = getattr(entry, attr, None)
            if parsed:
                return datetime(*parsed[:6], tzinfo=timezone.utc).strftime("%b %d, %Y")
        return None

    def _get_source(text: str) -> str:
        if "gallup" in text.lower() or "갤럽" in text:
            return "Gallup Korea"
        if "realmeter" in text.lower() or "리얼미터" in text:
            return "Realmeter"
        return "Poll"

    def _try_headline_extraction(entry) -> bool:
        """Strategy 1 (preferred): Extract all metrics from a Korean news headline.
        Korean outlets report Gallup results in compact format like:
          '李대통령 지지율 67%…민주 46%·국힘 20% [한국갤럽]'
        This grabs presidential + both parties in one regex match,
        guaranteeing they come from the same source and same article."""
        text = f"{entry.get('title', '')} {entry.get('summary', entry.get('description', ''))}"
        if not _is_polling_article(text):
            return False
        m = _RE_HEADLINE_ALL.search(text)
        if not m:
            return False
        pres_val = float(m.group(1))
        dp_val = float(m.group(2))
        ppp_val = float(m.group(3))
        # Sanity: presidential should be higher than individual party ratings
        if not (30 <= pres_val <= 85 and 5 <= dp_val <= 60 and 5 <= ppp_val <= 50):
            return False
        if pres_val < dp_val or pres_val < ppp_val:
            return False
        source = _get_source(text)
        pub_date = _get_pub_date(entry)
        # Set all from the same source/date
        sentiment["presidential_approval"] = {
            "value": f"{pres_val:g}%", "trend": None,
            "source": source, "last_updated": pub_date or "recent",
        }
        sentiment["party_ruling"] = {
            "value": f"{dp_val:g}%",
            "party": "Democratic Party", "party_kr": "더불어민주당",
            "source": source, "last_updated": pub_date or "recent",
        }
        sentiment["party_opposition"] = {
            "value": f"{ppp_val:g}%",
            "party": "People Power Party", "party_kr": "국민의힘",
            "source": source, "last_updated": pub_date or "recent",
        }
        # Try to get independents from the same text
        ind_m = _RE_IND.search(text)
        sentiment["party_independent"] = None
        if ind_m:
            val = _sane_pct_any(ind_m)
            if val is not None:
                sentiment["party_independent"] = {
                    "value": f"{val:g}%",
                    "source": source, "last_updated": pub_date or "recent",
                }
        return True

    def _try_field_extraction(entry) -> bool:
        """Strategy 2 (fallback): Extract metrics individually.
        Presidential approval requires '대통령' or 'presidential' nearby."""
        text = f"{entry.get('title', '')} {entry.get('summary', entry.get('description', ''))}"
        if not _is_polling_article(text):
            return False
        appr_m = _RE_PRES_APPROVAL.search(text)
        if not appr_m:
            return False
        appr_val = _sane_pct_any(appr_m)
        if appr_val is None:
            return False
        source = _get_source(text)
        pub_date = _get_pub_date(entry)
        sentiment["presidential_approval"] = {
            "value": f"{appr_val:g}%", "trend": None,
            "source": source, "last_updated": pub_date or "recent",
        }
        sentiment["party_ruling"] = None
        sentiment["party_opposition"] = None
        sentiment["party_independent"] = None
        for regex, key, extra in [
            (_RE_RULING, "party_ruling", {"party": "Democratic Party", "party_kr": "더불어민주당"}),
            (_RE_OPP, "party_opposition", {"party": "People Power Party", "party_kr": "국민의힘"}),
            (_RE_IND, "party_independent", {}),
        ]:
            m = regex.search(text)
            if m:
                val = _sane_pct_any(m)
                if val is not None:
                    sentiment[key] = {"value": f"{val:g}%", "source": source,
                                      "last_updated": pub_date or "recent", **extra}
        return True

    # ── Pass 1: Headline extraction (skip if Wikipedia already got data)
    if not (sentiment["presidential_approval"] and sentiment["party_ruling"]):
        for query in combined_queries:
            try:
                entries = _parse_feed(_gnews(query))
                for entry in entries[:8]:
                    if _try_headline_extraction(entry):
                        break
                if sentiment["presidential_approval"] and sentiment["party_ruling"]:
                    break
            except Exception as e:
                print(f"    ⚠  Sentiment pass 1 ({query[:40]}): {e}")
                continue

    # ── Pass 2: Field-by-field extraction (fallback if no headline match)
    if not sentiment["presidential_approval"]:
        for query in combined_queries:
            try:
                entries = _parse_feed(_gnews(query))
                for entry in entries[:5]:
                    if _try_field_extraction(entry) and (
                        sentiment["party_ruling"] or sentiment["party_opposition"]
                    ):
                        break
                if sentiment["presidential_approval"] and (
                    sentiment["party_ruling"] or sentiment["party_opposition"]
                ):
                    break
            except Exception as e:
                print(f"    ⚠  Sentiment pass 2 ({query[:40]}): {e}")
                continue

    # ── Gallup Korea Spotlight (weekly special-topic finding) ───────────
    # Each weekly poll includes a rotating social/policy topic beyond the
    # standard presidential + party numbers.  Grab the headline finding.
    spotlight_queries = [
        "한국갤럽+여론조사+이번주",
        "한국갤럽+데일리+오피니언",
        "Gallup+Korea+weekly+poll+survey+2026",
        "한국갤럽+조사+결과",
    ]
    for query in spotlight_queries:
        try:
            entries = _parse_feed(_gnews(query))
            for entry in entries[:5]:
                if not _is_recent(entry, hours=168):
                    continue
                text = f"{entry.get('title', '')} {entry.get('summary', entry.get('description', ''))}"
                # Skip standard approval/party support headlines — spotlight is for special topics
                if re.search(r'(대통령|지지율|presidential|party\s*approval|approval\s*rat)', text, re.IGNORECASE):
                    continue
                # Look for poll-result-style content (percentages + survey language)
                if re.search(r'갤럽|gallup', text, re.IGNORECASE) and \
                   re.search(r'\d{1,2}%|찬성|반대|응답|survey|poll', text, re.IGNORECASE):
                    title = entry.get("title", "").strip()
                    pub_date = None
                    for attr in ("published_parsed", "updated_parsed"):
                        parsed = getattr(entry, attr, None)
                        if parsed:
                            pub_date = datetime(*parsed[:6], tzinfo=timezone.utc).strftime("%b %d, %Y")
                            break
                    sentiment["gallup_spotlight"] = {
                        "headline": title[:250],
                        "poll_date": pub_date or "recent",
                    }
                    break
            if sentiment.get("gallup_spotlight"):
                break
        except Exception:
            continue

    # ── Discourse flag (protests, viral hashtags) ───────────────────────
    discourse_queries = [
        "Korea+protest+rally+demonstration",
        "한국+시위+집회",
    ]
    for query in discourse_queries:
        try:
            entries = _parse_feed(_gnews(query))
            for entry in entries[:3]:
                if not _is_recent(entry, hours=24):
                    continue
                text = f"{entry.get('title', '')} {entry.get('summary', entry.get('description', ''))}"
                protest_signals = ("protest", "rally", "demonstration", "시위", "집회",
                                   "candlelight", "march on", "tens of thousands")
                if any(s in text.lower() for s in protest_signals):
                    title = entry.get("title", "").strip()
                    sentiment["discourse_flag"] = title[:200]
                    break
            if sentiment["discourse_flag"]:
                break
        except Exception:
            continue

    # ── Fallback baselines (carry-forward when scraping fails) ─────────
    # These are the most recent known values from Gallup Korea weekly polls.
    # They ensure the sentiment tracker always renders with data.
    fallback_used = []
    # Fallback: Gallup Korea weekly poll #664, surveyed May 19-21 2026
    # Lee approval at 64% (up 3pp from 61%). DP 45%, PPP 22%, independents 26%.
    # Source: Seoul Economic Daily, 경향신문, 머니투데이 reporting Gallup Korea
    if not sentiment["presidential_approval"]:
        fallback_used.append("presidential_approval")
        sentiment["presidential_approval"] = {
            "value": "64%", "trend": "up",
            "source": "Gallup Korea", "last_updated": "May 19-21, 2026",
        }
    if not sentiment["party_ruling"]:
        fallback_used.append("party_ruling")
        sentiment["party_ruling"] = {
            "value": "45%", "party": "Democratic Party",
            "party_kr": "더불어민주당", "trend": "stable",
            "source": "Gallup Korea", "last_updated": "May 19-21, 2026",
        }
    if not sentiment["party_opposition"]:
        fallback_used.append("party_opposition")
        sentiment["party_opposition"] = {
            "value": "22%", "party": "People Power Party",
            "party_kr": "국민의힘", "trend": "down",
            "source": "Gallup Korea", "last_updated": "May 19-21, 2026",
        }
    if not sentiment["party_independent"]:
        fallback_used.append("party_independent")
        sentiment["party_independent"] = {
            "value": "26%", "trend": "down",
            "source": "Gallup Korea", "last_updated": "May 19-21, 2026",
        }
    if fallback_used:
        print(f"    ⚠  Sentiment: using fallbacks for {', '.join(fallback_used)}")
    if not sentiment["gallup_spotlight"]:
        fallback_used.append("gallup_spotlight")
        sentiment["gallup_spotlight"] = {
            "headline": "지방선거 여당 승리 전망 과반 — 한국갤럽 5월 4주차",
            "poll_date": "May 22, 2026",
        }
        print("    ⚠  Sentiment: using fallback for gallup_spotlight")

    return sentiment


# ─────────────────────────────────────────────────────────────────────────────
# PUBLIC API
# ─────────────────────────────────────────────────────────────────────────────

def collect() -> dict:
    """Run all tier collectors + market data and return combined payload."""
    _source_health.clear()  # Reset health tracking for this run
    print("\n📡  Collecting Korea news from 100+ sources (parallel)...")

    # Run all collectors concurrently — each already uses internal thread pools
    # for their own feeds, but the collectors themselves were running sequentially.
    collectors = {
        "tier1":        ("Tier 1: News articles",        _collect_tier1),
        "tier2":        ("Tier 2: Op-eds & commentary",   _collect_tier2),
        "tier3":        ("Tier 3: Academic journals",      _collect_tier3),
        "tier4":        ("Tier 4: KCNA / Rodong Sinmun",  _collect_tier4),
        "kcna_scrape":  ("KCNA Watch scrape",             _scrape_kcna_watch),
        "kim_tracker":  ("Kim Jong Un appearance tracker", _collect_kim_tracker),
        "satellite":    ("Satellite imagery analysis",     _collect_satellite_imagery),
        "markets":      ("Market data",                    _collect_markets),
        "sentiment":    ("Public sentiment polls",         _collect_sentiment),
    }

    results = {}
    with ThreadPoolExecutor(max_workers=9) as pool:
        futures = {pool.submit(fn): key for key, (_label, fn) in collectors.items()}
        for future in as_completed(futures):
            key = futures[future]
            label = collectors[key][0]
            try:
                results[key] = future.result()
                data = results[key]
                if key == "markets":
                    print(f"  ── {label}: {'OK' if data else 'unavailable'}")
                elif key == "sentiment":
                    found = sum(1 for v in data.values() if v) if data else 0
                    print(f"  ── {label}: {found} metric(s) found" if found else f"  ── {label}: no recent polls found")
                elif key in ("kim_tracker", "satellite"):
                    print(f"  ── {label}: {len(data)} articles (72h window)")
                else:
                    print(f"  ── {label}: {len(data)} items")
            except Exception as e:
                print(f"  ── {label}: ⚠ FAILED ({e})")
                results[key] = [] if key not in ("markets", "sentiment") else {}

    tier1 = results["tier1"]
    tier2 = results["tier2"]
    tier3 = results["tier3"]
    tier4 = results["tier4"]
    kcna_scraped = results.get("kcna_scrape", [])
    total = len(tier1) + len(tier2) + len(tier3) + len(tier4)
    print(f"\n  📊  Total collected: {total} items")

    # Build structured KCNA summary from RSS + scrape data
    kcna_summary = _build_kcna_summary(tier4, kcna_scraped)
    if kcna_summary["total_articles"]:
        print(f"  📡  KCNA summary: {kcna_summary['total_articles']} unique articles, {len(kcna_summary.get('categories', {}))} categories")

    # ── Source health summary ────────────────────────────────────────────
    total_feeds = len(_source_health)
    feeds_with_data = sum(1 for h in _source_health.values() if h["success"])
    feeds_failed = total_feeds - feeds_with_data
    print(f"\n  Source health: {feeds_with_data}/{total_feeds} feeds returned data, {feeds_failed} blocked/failed")

    # Warn if any major feed returned 0 articles
    for major in MAJOR_FEEDS:
        health = _source_health.get(major)
        if health and not health["success"]:
            err = health.get("error_msg") or "0 articles"
            print(f"  ⚠  MAJOR FEED WARNING: {major} returned no data ({err})")
        elif not health:
            print(f"  ⚠  MAJOR FEED WARNING: {major} was not fetched")

    satellite = results.get("satellite", [])
    if satellite:
        print(f"  🛰  Satellite imagery: {len(satellite)} articles found (72h window)")

    return {
        "tier1": tier1,
        "tier2": tier2,
        "tier3": tier3,
        "tier4": tier4,
        "kcna_summary": kcna_summary,
        "kim_tracker_articles": results["kim_tracker"],
        "satellite_imagery_articles": satellite,
        "market_indicators": results["markets"],
        "sentiment_baseline": results["sentiment"],
        "source_health": {
            "total_feeds": total_feeds,
            "feeds_with_data": feeds_with_data,
            "feeds_failed": feeds_failed,
            "per_source": dict(_source_health),
        },
    }


if __name__ == "__main__":
    from pathlib import Path
    payload = collect()
    Path("collected.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2))
    print("  → Written to collected.json")
