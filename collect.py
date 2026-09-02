"""
Australia Chair Daily Brief: Collector
CSIS Australia Chair

Scrapes RSS feeds across four tiers, filters for Australia / New Zealand /
Pacific Islands relevance, and marks anything already published in a recent
issue so the digest stage can avoid repeating itself.

Forked from the Korea Daily Brief collector. Threaded fetching (~15s).

Feed routing rule: paywalled or bot-protected publishers (The Australian, AFR,
WSJ, NZ Herald, Stuff) go through Google News search, never direct fetch. A
publisher that returns 403 in testing gets ADDED to the Google News routing
list, not removed from the feed set.
"""
import feedparser
import requests
import json
import os
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone, timedelta

# Opt-in escape hatch for networks that inspect TLS. Setting USE_OS_TRUSTSTORE=1
# makes Python trust the operating system's certificate store instead of the
# bundled roots, which is what a corporate inspection CA lives in.
#
# Off by default, and deliberately so: tested on the CSIS network it made things
# worse, taking live feeds from 23 to 3. Try it only when the default path is
# failing wholesale, and compare the source-health line before keeping it.
# Irrelevant in GitHub Actions, which has no middlebox.
if os.environ.get("USE_OS_TRUSTSTORE") == "1":
    try:
        import truststore as _truststore
        _truststore.inject_into_ssl()
        print("  Using the OS certificate store (USE_OS_TRUSTSTORE=1)")
    except ImportError:
        print("  !  USE_OS_TRUSTSTORE=1 but truststore is not installed")

# ─────────────────────────────────────────────────────────────────────────────
# FEED CONFIGURATION
# ─────────────────────────────────────────────────────────────────────────────

def _gnews(query: str) -> str:
    """Build a Google News RSS search URL (Australian edition)."""
    return f"https://news.google.com/rss/search?q={query}&hl=en-AU&gl=AU&ceid=AU:en"


def _gnews_us(query: str) -> str:
    """Google News RSS, US edition, for US outlets covering the region."""
    return f"https://news.google.com/rss/search?q={query}&hl=en-US&gl=US&ceid=US:en"


# Shared query fragment: "this outlet, but only on our region"
_REGION_Q = (
    "Australia+OR+AUKUS+OR+%22New+Zealand%22+OR+%22Pacific+Islands%22"
    "+OR+Fiji+OR+%22Papua+New+Guinea%22+OR+%22Solomon+Islands%22"
)

TIER1_FEEDS = {
    # ── Requester's named Australian outlets ─────────────────────────────
    # The Australian and AFR are hard-paywalled; direct RSS returns 403.
    "The Australian":         _gnews("site:theaustralian.com.au"),
    "AFR":                    _gnews("site:afr.com"),
    "SMH":                    "https://www.smh.com.au/rss/feed.xml",
    "SMH Federal Politics":   "https://www.smh.com.au/rss/politics/federal.xml",
    "SMH World":              "https://www.smh.com.au/rss/world.xml",
    "ABC News":               "https://www.abc.net.au/news/feed/51120/rss.xml",
    # Feed id 56166 has returned a hard 500 from the ABC on every live run.
    # Rerouted through Google News rather than deleted: the ABC political
    # unit is one of the outlets the Chair actually reads, and a publisher
    # 500 is usually a feed id being retired, not the desk closing.
    "ABC Politics":           _gnews(
        "site:abc.net.au+politics+OR+defence+OR+foreign+OR+China"),

    # ── Requester's named international outlets ──────────────────────────
    "WSJ":                    _gnews_us(f"({_REGION_Q})+site:wsj.com"),
    "NYT Asia Pacific":       "https://rss.nytimes.com/services/xml/rss/nyt/AsiaPacific.xml",
    "NYT (region)":           _gnews_us(f"({_REGION_Q})+site:nytimes.com"),
    "Politico Defense":       "https://rss.politico.com/defense.xml",
    "Politico (region)":      _gnews_us(f"({_REGION_Q})+site:politico.com"),
    # Politico's daily on Australian federal politics, and the only
    # Canberra-desk product any of the named international outlets runs.
    # AU edition rather than US: it is an Australian product and the AU
    # index carries it better. This catches the free editions only. The
    # subscriber issues come in over IMAP instead, where the publisher has
    # already mailed you the whole thing; see newsletters.py and SETUP 5b.
    "Politico Canberra Playbook": _gnews(
        'site:politico.com+%22Canberra+Playbook%22'),

    # ── Australian national ──────────────────────────────────────────────
    "The Age":                "https://www.theage.com.au/rss/feed.xml",
    "Guardian Australia":     "https://www.theguardian.com/australia-news/rss",
    "news.com.au":            "https://www.news.com.au/content-feeds/latest-news-national/",
    "The Conversation AU":    "https://theconversation.com/au/politics/articles.atom",
    "Crikey":                 "https://www.crikey.com.au/feed/",
    "Sky News Australia":     _gnews("site:skynews.com.au+politics+OR+defence+OR+China"),
    "AAP":                    _gnews("site:aap.com.au"),

    # ── New Zealand ──────────────────────────────────────────────────────
    "RNZ National":           "https://www.rnz.co.nz/rss/national.xml",
    "RNZ Political":          "https://www.rnz.co.nz/rss/political.xml",
    "RNZ World":              "https://www.rnz.co.nz/rss/world.xml",
    "Newsroom NZ":            "https://www.newsroom.co.nz/feed",
    "NZ Herald":              _gnews("site:nzherald.co.nz+politics+OR+defence+OR+foreign"),
    "Stuff":                  _gnews("site:stuff.co.nz+politics+OR+defence+OR+foreign"),
    "The Post (Wellington)":  _gnews("site:thepost.co.nz"),
    "1News":                  _gnews("site:1news.co.nz+politics+OR+pacific"),

    # ── Pacific Islands: requester's named outlets ──────────────────────
    "RNZ Pacific":            "https://www.rnz.co.nz/rss/pacific.xml",
    "Islands Business":       "https://islandsbusiness.com/feed/",
    "Pacific Island Times":   _gnews("site:pacificislandtimes.com"),

    # ── Pacific Islands: national and regional press ────────────────────
    "PACNEWS":                _gnews("site:pina.com.fj+OR+%22PACNEWS%22"),
    "Benar News Pacific":     _gnews("site:benarnews.org+Pacific"),
    "Fiji Times":             "https://www.fijitimes.com.fj/feed/",
    "Samoa Observer":         _gnews("site:samoaobserver.ws"),
    "PNG Post-Courier":       "https://www.postcourier.com.pg/feed/",
    "The National (PNG)":     _gnews("site:thenational.com.pg"),
    "Solomon Star":           _gnews("site:solomonstarnews.com"),
    "Vanuatu Daily Post":     _gnews("site:dailypost.vu"),
    "Cook Islands News":      _gnews("site:cookislandsnews.com"),
    "Kaniva Tonga":           _gnews("site:kanivatonga.nz"),
    "Marianas Variety":       _gnews("site:mvariety.com"),

    # ── Pacific Islands: second wave ─────────────────────────────────────
    # Added to lift Pacific input volume, which is what the pacific_wire
    # floor depends on. Every one of these must also appear in
    # _SOURCE_REGION below, or its copy files as Australian and the Pacific
    # count, the number that protects Pacific coverage, reads low.
    "ABC Pacific":            _gnews(
        "site:abc.net.au+%22Pacific+Beat%22+OR+%22Pacific+Islands%22+OR+PNG+OR+Vanuatu"),
    "Loop Pacific":           _gnews("site:loopnews.com"),
    "FBC News (Fiji)":        _gnews("site:fbcnews.com.fj"),
    "Fiji Sun":               _gnews("site:fijisun.com.fj"),
    "Fijivillage":            _gnews("site:fijivillage.com"),
    "Pacific Islands Report": _gnews("site:pireport.org"),
    "Islands Business (wire)": _gnews("site:islandsbusiness.com"),
    "Pacific Daily News":     _gnews_us("site:guampdn.com"),
    "Saipan Tribune":         _gnews_us("site:saipantribune.com"),
    "RNZ Pacific (wire)":     _gnews("site:rnz.co.nz+Pacific+Islands+OR+Fiji+OR+PNG"),
    # French Pacific. New Caledonia and French Polynesia are live files that
    # the anglophone Pacific press covers thinly and late.
    "Tahiti Infos":           _gnews("site:tahiti-infos.com"),
    "NC la 1ere":             _gnews(
        "site:la1ere.francetvinfo.fr+%22Nouvelle-Cal%C3%A9donie%22"),
    "Pacific Islands (wire)": _gnews(
        "%22Pacific+Islands%22+OR+%22Pacific+Islands+Forum%22+diplomacy+OR+security+OR+agreement"),
    # AUKUS as a TOPIC, not as a term inside somebody else's site: query.
    # Every other AUKUS query here is scoped to one masthead, so the brief
    # could only find AUKUS news on the days the WSJ or Breaking Defense
    # happened to run it. Across the first seven issues that produced 9
    # AUKUS-mentioning items out of 2,284 collected, and aukus_watch ran
    # empty five issues in a row. This is the Pacific Islands (wire)
    # pattern applied to the beat the brief is named for.
    "AUKUS (wire)":           _gnews("AUKUS"),
    "AUKUS submarines (wire)": _gnews(
        "AUKUS+OR+%22SSN-AUKUS%22+submarine+Australia+OR+%22submarine+industrial+base%22"),

    # ── Wires and international correspondents ───────────────────────────
    "Reuters":                _gnews(f"({_REGION_Q})+site:reuters.com"),
    "AP":                     _gnews(f"({_REGION_Q})+site:apnews.com"),
    "AFP":                    _gnews(f"({_REGION_Q})+AFP"),
    "Bloomberg":              _gnews(f"({_REGION_Q})+site:bloomberg.com"),
    "Financial Times":        _gnews(f"({_REGION_Q})+site:ft.com"),
    "The Economist":          _gnews(f"({_REGION_Q})+site:economist.com"),
    "BBC Asia-Pacific":       "https://feeds.bbci.co.uk/news/world/asia/rss.xml",
    "Nikkei Asia":            _gnews(f"({_REGION_Q})+site:asia.nikkei.com"),
    "SCMP":                   _gnews(f"({_REGION_Q})+site:scmp.com"),
    "Washington Post":        _gnews_us(f"({_REGION_Q})+site:washingtonpost.com"),
    "Defense News":           _gnews_us("AUKUS+OR+Australia+site:defensenews.com"),
    "Breaking Defense":       _gnews_us("AUKUS+OR+Australia+site:breakingdefense.com"),
    "USNI News":              _gnews_us("Australia+OR+AUKUS+site:news.usni.org"),

    # ── Government and official ──────────────────────────────────────────
    "PM&C / Prime Minister":  _gnews("site:pm.gov.au+OR+site:pmc.gov.au"),
    "DFAT":                   _gnews("site:dfat.gov.au"),
    "AU Defence":             _gnews("site:defence.gov.au"),
    "AU Defence Minister":    _gnews("site:minister.defence.gov.au"),
    "AU Parliament":          _gnews("site:aph.gov.au+committee+OR+inquiry+OR+report"),
    "Beehive (NZ Govt)":      "https://www.beehive.govt.nz/rss.xml",
    "NZ MFAT":                _gnews("site:mfat.govt.nz"),
    "NZDF":                   _gnews("site:nzdf.mil.nz+OR+%22New+Zealand+Defence+Force%22"),
    "US State (region)":      _gnews_us(f"({_REGION_Q})+site:state.gov"),
    "INDOPACOM":              _gnews_us("Australia+OR+Pacific+site:pacom.mil"),
    "US Embassy Canberra":    _gnews_us("site:au.usembassy.gov"),
    "US Congress (AUKUS)":    _gnews_us("AUKUS+Congress+OR+%22National+Defense+Authorization%22+Australia"),
    "Pacific Islands Forum":  _gnews("site:forumsec.org+OR+%22Pacific+Islands+Forum+Secretariat%22"),

    # ── China reaction layer ─────────────────────────────────────────────
    "Global Times (region)":  _gnews("Australia+OR+Pacific+site:globaltimes.cn"),
    "Xinhua (region)":        _gnews("Australia+OR+Pacific+site:news.cn+OR+site:xinhuanet.com"),
    "China Daily (region)":   _gnews("Australia+OR+Pacific+site:chinadaily.com.cn"),
}

# Tier 2: analysis and commentary. name -> (url, prestige_tier)
TIER2_FEEDS = {
    "Lowy Interpreter":  ("https://www.lowyinstitute.org/the-interpreter/rss.xml", "A"),
    "Lowy Institute":    (_gnews("site:lowyinstitute.org"), "A"),
    "ASPI Strategist":   ("https://www.aspistrategist.org.au/feed/", "A"),
    "USSC":              (_gnews("site:ussc.edu.au"), "A"),
    "Devpolicy Blog":    ("https://devpolicy.org/feed/", "A"),
    "CSIS":              (_gnews_us(f"({_REGION_Q})+site:csis.org"), "A"),
    "ANU SDSC":          (_gnews("%22Strategic+and+Defence+Studies+Centre%22+Australia"), "A"),
    "Brookings":         ("https://www.brookings.edu/feed/", "A"),
    "Carnegie":          (_gnews_us(f"({_REGION_Q})+site:carnegieendowment.org"), "B"),
    "Pacific Forum":     (_gnews_us("site:pacforum.org"), "B"),
    "East-West Center":  (_gnews_us("site:eastwestcenter.org+Pacific"), "B"),
    "Griffith Asia":     (_gnews("%22Griffith+Asia+Institute%22"), "B"),
    "NZIIA":             (_gnews("%22New+Zealand+Institute+of+International+Affairs%22"), "B"),
    "War on the Rocks":  ("https://warontherocks.com/feed/", "B"),
    "RUSI":              (_gnews("site:rusi.org+Australia+OR+Pacific+OR+AUKUS"), "B"),
    "The Diplomat":      ("https://thediplomat.com/feed/", "C"),
    "Foreign Policy":    (_gnews_us(f"({_REGION_Q})+site:foreignpolicy.com"), "C"),

    # ── Newsletters ──────────────────────────────────────────────────────
    # Substack serves RSS at /feed on every publication, so these are the
    # one class of new source whose URL shape is not a guess. They are
    # analysis, not news, which is why they sit in tier 2 on the 36h window.
    "ASPI Fault Lines":  (_gnews("site:aspidefence.substack.com"), "A"),
    "Futura Doctrina":   (_gnews("site:mickryan.substack.com"), "B"),
    "Democracy Project NZ": (_gnews("site:democracyproject.substack.com"), "B"),
    "Declassified Australia": (_gnews("site:declassifiedaustralia.substack.com"), "C"),
    "Australian Defence Magazine": (_gnews("site:australiandefence.com.au"), "B"),
}

# Tier 3: academic journals. name -> (url, journal_tier)
TIER3_FEEDS = {
    "Aust. J. Intl Affairs":     (_gnews("%22Australian+Journal+of+International+Affairs%22"), "A"),
    "Aust. J. Pol. Science":     (_gnews("%22Australian+Journal+of+Political+Science%22"), "A"),
    "Security Challenges":       (_gnews("%22Security+Challenges%22+journal+Australia"), "A"),
    "Intl Security":             (_gnews_us("%22International+Security%22+journal+Australia+OR+Pacific+OR+AUKUS"), "A+"),
    "Pacific Affairs":           (_gnews("%22Pacific+Affairs%22+journal"), "A"),
    "The Contemporary Pacific":  (_gnews("%22The+Contemporary+Pacific%22+journal"), "A"),
    "J. Pacific History":        (_gnews("%22Journal+of+Pacific+History%22"), "B"),
    "Asian Survey":              (_gnews("%22Asian+Survey%22+Australia+OR+Pacific"), "A"),
    "Australian Foreign Affairs": (_gnews("%22Australian+Foreign+Affairs%22+essay+OR+quarterly"), "A"),
    "Washington Quarterly":      (_gnews_us("%22Washington+Quarterly%22+Australia+OR+AUKUS+OR+Pacific"), "B"),
}

# Tier 4: primary documents. Unlike the Korea brief's DPRK tier, these are
# authoritative sources and MAY drive editorial framing.
TIER4_FEEDS = {
    "AUSMIN / joint statements": _gnews("AUSMIN+OR+%22joint+statement%22+Australia+United+States+alliance"),
    "AUKUS official":            _gnews("AUKUS+%22joint+statement%22+OR+communique+OR+trilateral"),
    "PIF communiques":           _gnews("%22Pacific+Islands+Forum%22+communique+OR+declaration+OR+%22leaders+meeting%22"),
    "AU ministerial":            _gnews("site:foreignminister.gov.au+OR+site:trademinister.gov.au"),
    "NZ ministerial":            _gnews("site:beehive.govt.nz+foreign+OR+defence+OR+Pacific"),
    "White House (region)":      _gnews_us("site:whitehouse.gov+Australia+OR+AUKUS+OR+%22Pacific+Islands%22"),
}

# ─────────────────────────────────────────────────────────────────────────────
# RELEVANCE FILTER
# ─────────────────────────────────────────────────────────────────────────────
# Bare "pacific" is deliberately NOT a match token. It hits Pacific Gas and
# Electric, Pacific Northwest, Asia-Pacific boilerplate, and time zones.
# Pacific relevance is carried by named states and the multi-word phrases below.
AUSPAC_KEYWORDS = re.compile(
    # Australia
    r"australia|australian|canberra|\badf\b|defence force|aukus|anzus|ausmin"
    # Opposition names change with the leadership and this regex is how
    # opposition stories are picked up at all. Taylor took the Liberal
    # leadership from Ley in February 2026; Ley and Dutton stay because both
    # are still quoted. "angus taylor" in full: bare "taylor" is far too broad.
    r"|albanese|marles|penny wong|richard marles|conroy|sussan ley|dutton"
    r"|angus taylor|james paterson|ted o'brien|jane hume|andrew hastie"
    r"|virginia-class|talisman sabre|hmas |osborne|henderson shipyard"
    r"|submarine rotational force|\brba\b|reserve bank of australia"
    # AUKUS programme vocabulary. Defence trade copy writes about the
    # programme without naming the country, so "Submarine industrial base
    # funding boosted" was being dropped at the gate as off-region. Each
    # of these names one specific boat class, yard or programme, which is
    # why bare "submarine" and "nuclear-powered" are NOT here: they would
    # pull in every Russian and Chinese boat story on earth.
    r"|ssn-aukus|submarine industrial base|collins-class|collins class"
    r"|hunter-class|astute-class|barrow-in-furness|aukus pillar"
    r"|defence strategic review|guided weapons and explosive ordnance"
    # New Zealand
    r"|new zealand|aotearoa|wellington|\bnzdf\b|anzmin|christopher luxon"
    r"|winston peters|judith collins|five eyes"
    # Pacific Islands
    r"|pacific island|pacific islands forum|\bpif\b|melanesia|polynesia|micronesia"
    r"|papua new guinea|\bpng\b|port moresby|bougainville"
    r"|fiji|fijian|suva|solomon islands|honiara|vanuatu|port vila"
    r"|samoa|apia|tonga|kiribati|tarawa|tuvalu|nauru"
    r"|palau|marshall islands|majuro|cook islands|rarotonga|niue|tokelau"
    r"|new caledonia|noumea|kanak|french polynesia|tahiti|wallis and futuna"
    r"|compact of free association|\bcofa\b|pacific patrol boat"
    r"|forum fisheries|melanesian spearhead|blue pacific",
    re.IGNORECASE,
)

# Pacific-specific matcher: tags items for the Pacific sections and measures
# whether the Pacific feed set is actually producing.
PACIFIC_KEYWORDS = re.compile(
    r"pacific island|pacific islands forum|\bpif\b|blue pacific|melanesia|polynesia"
    r"|papua new guinea|\bpng\b|port moresby|bougainville"
    r"|fiji|fijian|suva|solomon islands|honiara|vanuatu|port vila"
    r"|\bsamoa\b|apia|\btonga\b|kiribati|tuvalu|nauru|palau"
    r"|marshall islands|cook islands|niue|tokelau|new caledonia|noumea|kanak"
    r"|french polynesia|compact of free association|\bcofa\b",
    re.IGNORECASE,
)

NZ_KEYWORDS = re.compile(
    r"new zealand|aotearoa|wellington|\bnzdf\b|anzmin|luxon|winston peters",
    re.IGNORECASE,
)

# Checked after the other two, so "Australia funds a Fiji hospital" stays a
# Pacific item. Its job is to catch a Canberra story carried by a Pacific or NZ
# outlet before the source fallback files it under that outlet's region.
AU_KEYWORDS = re.compile(
    r"australia|australian|canberra|\badf\b|albanese|marles|penny wong"
    r"|\brba\b|reserve bank of australia|\baph\b|federal parliament"
    r"|new south wales|victoria state|queensland|western australia"
    r"|south australia|tasmania|sydney|melbourne|brisbane|perth|adelaide",
    re.IGNORECASE,
)

# Region fallback by source. Keyword matching alone mis-files a Port Moresby
# story that never says "Papua New Guinea" because its readers already know
# where they are. Defaulting everything unmatched to Australia would undercount
# the Pacific, which is the one number this brief uses to protect Pacific
# coverage, so a Pacific outlet's copy is Pacific unless it says otherwise.
_SOURCE_REGION = {
    "RNZ Pacific": "Pacific", "Islands Business": "Pacific",
    "Pacific Island Times": "Pacific", "PACNEWS": "Pacific",
    "Benar News Pacific": "Pacific", "Fiji Times": "Pacific",
    "Samoa Observer": "Pacific", "PNG Post-Courier": "Pacific",
    "The National (PNG)": "Pacific", "Solomon Star": "Pacific",
    "Vanuatu Daily Post": "Pacific", "Cook Islands News": "Pacific",
    "Kaniva Tonga": "Pacific", "Marianas Variety": "Pacific",
    "Pacific Islands (wire)": "Pacific", "Pacific Islands Forum": "Pacific",
    "Devpolicy Blog": "Pacific", "Pacific Forum": "Pacific",
    "ABC Pacific": "Pacific", "Loop Pacific": "Pacific",
    "FBC News (Fiji)": "Pacific", "Fiji Sun": "Pacific",
    "Fijivillage": "Pacific", "Pacific Islands Report": "Pacific",
    "Islands Business (wire)": "Pacific", "Pacific Daily News": "Pacific",
    "Saipan Tribune": "Pacific", "RNZ Pacific (wire)": "Pacific",
    "Tahiti Infos": "Pacific", "NC la 1ere": "Pacific",
    "RNZ National": "NZ", "RNZ Political": "NZ", "RNZ World": "NZ",
    "Newsroom NZ": "NZ", "NZ Herald": "NZ", "Stuff": "NZ",
    "The Post (Wellington)": "NZ", "1News": "NZ",
    "Beehive (NZ Govt)": "NZ", "NZ MFAT": "NZ", "NZDF": "NZ", "NZIIA": "NZ",
    "Democracy Project NZ": "NZ",
}

# ─────────────────────────────────────────────────────────────────────────────
# HARD BLOCK: SPORT AND ENTERTAINMENT
# ─────────────────────────────────────────────────────────────────────────────
# The analogue of the Korea brief's K-pop block, and more necessary: Australian
# and NZ feeds are dominated by sport. Blocked in every section, no exceptions.
_SPORT_FILTER = re.compile(
    r"\bafl\b|\bnrl\b|rugby|wallabies|all blacks|springboks|state of origin"
    r"|cricket|the ashes|\bbbl\b|test match|batting|bowler|wicket"
    r"|melbourne cup|australian open|netball|matildas|socceroos|a-league"
    r"|olympic|commonwealth games|grand final|premiership|\bgolf\b|tennis"
    r"|surfing|swimming championship|formula 1|supercars|horse racing"
    # Entertainment and celebrity
    r"|reality tv|masterchef|married at first sight|celebrity|red carpet"
    r"|box office|film festival|eurovision|royal tour|royal visit",
    re.IGNORECASE,
)

# ─────────────────────────────────────────────────────────────────────────────
# JOURNALIST WATCH LIST
# ─────────────────────────────────────────────────────────────────────────────
# Kept short on purpose. Do not expand without an editorial reason, and verify
# spelling exactly: a misspelled name here silently never matches.
# Grouped rather than a flat set, so list_sources.py can publish the beats
# alongside the names. The groups were comments before, which meant the only
# way to see who this brief watches was to read the collector.
JOURNALIST_BEATS = {
    "Australian foreign affairs and defence": [
        "Andrew Tillett", "Matthew Knott", "Ben Packham", "Daniel Hurst",
        "Peter Hartcher", "Greg Sheridan", "Laura Tingle", "David Speers",
        "Stephen Dziedzic", "Andrew Greene",
    ],
    "Pacific Islands specialists": [
        "Kirsty Needham", "Lice Movono", "Marian Faa", "Prianka Srinivasan",
        "Stefan Armbruster", "Ben Bohane",
    ],
    "New Zealand": [
        "Thomas Manch", "Sam Sachdeva", "Jane Patterson",
    ],
    "International correspondents on the region": [
        "Rod McGuirk", "Damien Cave", "Nic Fildes", "Michael Smith",
    ],
}

PRESTIGE_JOURNALISTS = {n for names in JOURNALIST_BEATS.values() for n in names}

# (connect, read): a slow publisher gets time to answer, an unreachable host
# does not tie up a worker.
REQUEST_TIMEOUT = (5, 12)
HEADERS = {"User-Agent": "CSISAustraliaBrief/1.0"}
MAX_WORKERS = 25  # Thread pool size for parallel fetching
FEED_RETRIES = 2  # Total attempts per feed, not additional attempts

# ─────────────────────────────────────────────────────────────────────────────
# SOURCE HEALTH TRACKING (per-run, not persistent)
# ─────────────────────────────────────────────────────────────────────────────
_source_health = {}  # {feed_name: {articles: int, success: bool, error_msg: str|None}}

# Feeds whose silence is a real problem, not a slow news day.
MAJOR_FEEDS = {"ABC News", "SMH", "The Australian", "AFR", "RNZ Pacific", "Reuters"}


# ─────────────────────────────────────────────────────────────────────────────
# HELPERS
# ─────────────────────────────────────────────────────────────────────────────

def _parse_feed(url: str) -> list:
    for attempt in range(FEED_RETRIES):
        try:
            resp = requests.get(url, timeout=REQUEST_TIMEOUT, headers=HEADERS)
            if resp.status_code in (401, 403):
                print(f"    !  Feed blocked ({resp.status_code}): {url[:80]}")
                return []
            resp.raise_for_status()
            return feedparser.parse(resp.content).entries
        except requests.exceptions.SSLError as e:
            # A TLS failure does not fix itself on retry. It usually means the
            # host is blocked or intercepted by a network middlebox, and each
            # attempt burns 30 seconds. Give up immediately.
            print(f"    !  TLS failure (not retried): {url[:70]}, {str(e)[:60]}")
            return []
        except (requests.ConnectionError, requests.Timeout) as e:
            if attempt < FEED_RETRIES - 1:
                time.sleep(2)
                continue
            print(f"    !  Feed error after {FEED_RETRIES} tries: {str(e)[:90]}")
            return []
        except Exception as e:
            print(f"    !  Feed error: {str(e)[:90]}")
            return []
    return []


def _entry_to_article(entry, source: str, extra: dict | None = None) -> dict:
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

    tags = []
    for tag in getattr(entry, "tags", []) or []:
        term = tag.get("term", "").strip()
        if term:
            tags.append(term)

    # The byline. feedparser maps dc:creator onto .author as well, which is
    # what most of the direct RSS feeds here actually publish. Captured
    # because the journalist watch list has no other honest way to fire:
    # a byline appears in neither the title nor the summary.
    author = (entry.get("author") or "").strip()
    if not author:
        names = [a.get("name", "").strip()
                 for a in (getattr(entry, "authors", None) or [])]
        author = ", ".join(n for n in names if n)

    article = {
        "title": title,
        "url": link,
        "summary": summary[:800],
        "source": source,
        "author": author,
        "pub_date": pub_date,
    }

    # Region tag: drives the Pacific and New Zealand sections downstream, and
    # lets the collector report whether those feed sets are producing.
    # Content wins over provenance: an Australian outlet reporting on Fiji is a
    # Pacific item, and a Pacific outlet reporting on Canberra is an AU item.
    # The source map only decides what an unmatched item falls back to.
    text = f"{title} {summary}"
    if PACIFIC_KEYWORDS.search(text):
        article["region"] = "Pacific"
    elif NZ_KEYWORDS.search(text):
        article["region"] = "NZ"
    elif AU_KEYWORDS.search(text):
        article["region"] = "AU"
    else:
        article["region"] = _SOURCE_REGION.get(source, "AU")

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


def _entry_text(entry) -> str:
    return f"{entry.get('title', '')} {entry.get('summary', entry.get('description', ''))}"


def _is_sport(entry) -> bool:
    return bool(_SPORT_FILTER.search(_entry_text(entry)))


def _is_region_related(entry) -> bool:
    return bool(AUSPAC_KEYWORDS.search(_entry_text(entry)))


def _flag_journalist(article: dict) -> dict:
    """Flag a watch-list byline.

    Matches the feed's author field, and failing that a "By <name>" line at
    the head of any fetched article text.

    It used to search the title and the summary for a bare name, which found
    nothing whatsoever: across the 366 items collected on the first live runs
    it fired zero times, because a byline appears in neither field. The bare
    name was also the wrong test even where it would have hit. "David Speers
    pressed the minister" is a mention, not a byline, and the prompt names the
    flagged correspondent in the source line, so a false positive is a factual
    error in the published product rather than a missed opportunity.
    """
    author = (article.get("author") or "").lower()
    head = (article.get("summary") or "")[:300].lower()
    for name in PRESTIGE_JOURNALISTS:
        low = name.lower()
        if low in author or f"by {low}" in head:
            article["flagged_journalist"] = name
            break
    return article


def reflag_journalists(payload: dict) -> int:
    """Re-run byline flagging after full-text enrichment.

    Collection flags before fulltext.py has fetched anything, so at that
    point the only byline available is whatever the feed published. Most of
    the Google News routed feeds publish none, and those are four fifths of
    the corpus. Once the bodies are in, the "By <name>" line at the top of
    the article is there to be read.
    """
    flagged = 0
    for key in ("tier1", "tier2", "tier3", "tier4"):
        for article in payload.get(key) or []:
            if article.get("flagged_journalist"):
                continue
            _flag_journalist(article)
            if article.get("flagged_journalist"):
                flagged += 1
    return flagged


# The eleven outlets the Australia Chair named, as they appear as feed names.
# The prompt carries a mandatory-inclusion rule for them, but it asked the model
# to match prose outlet names against a source string by eye. On the first live
# run SMH was collected and silently dropped, and the validator could only warn
# after the fact. Flagging the item itself puts the rule where the model reads
# the data rather than where it reads the instructions.
_PRESTIGE_FEEDS = {
    "The Australian", "SMH", "SMH Federal Politics", "SMH World", "AFR",
    "ABC News", "ABC Politics", "ABC Pacific", "WSJ",
    "NYT Asia Pacific", "NYT (region)", "Politico Defense", "Politico (region)",
    "Politico Canberra Playbook",
    "RNZ Pacific", "RNZ Pacific (wire)", "Islands Business",
    "Islands Business (wire)", "Pacific Island Times", "Australian Foreign Affairs",
    # Wires that assign this region selectively, per the same prompt rule.
    "Reuters", "AP", "AFP", "Financial Times", "The Economist", "Bloomberg",
    "Washington Post",
}


def _flag_prestige(article: dict) -> dict:
    if article.get("source") in _PRESTIGE_FEEDS:
        article["prestige_outlet"] = True
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
    Records per-source health in the module-level _source_health dict."""
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
                print(f"    !  Thread error: {e}")
                _source_health[src] = {"articles": 0, "success": False, "error_msg": str(e)}
    return results


# ─────────────────────────────────────────────────────────────────────────────
# TIER COLLECTORS
# ─────────────────────────────────────────────────────────────────────────────

def _collect_tier1() -> list:
    articles = []
    results = _fetch_feeds_parallel(TIER1_FEEDS)
    for source, (entries, _) in results.items():
        for entry in entries:
            if not _is_recent(entry, hours=24):
                continue
            if not _is_region_related(entry):
                continue
            if _is_sport(entry):
                continue
            article = _entry_to_article(entry, source)
            article = _flag_journalist(article)
            article = _flag_prestige(article)
            articles.append(article)
    return _dedup(articles)


def _collect_tier2() -> list:
    articles = []
    results = _fetch_feeds_parallel(TIER2_FEEDS, is_tiered=True)
    for source, (entries, prestige) in results.items():
        for entry in entries:
            if not _is_recent(entry, hours=36):
                continue
            if not _is_region_related(entry):
                continue
            if _is_sport(entry):
                continue
            articles.append(_entry_to_article(entry, source, extra={"prestige": prestige}))
    return _dedup(articles)


def _collect_tier3() -> list:
    articles = []
    results = _fetch_feeds_parallel(TIER3_FEEDS, is_tiered=True)
    for source, (entries, tier) in results.items():
        for entry in entries:
            if not _is_recent(entry, hours=72):
                continue
            if not _is_region_related(entry):
                continue
            # Must look like scholarship, not a news story name-checking a journal
            text = _entry_text(entry).lower()
            academic_signals = ("journal", "paper", "study", "research", "analysis",
                                "findings", "abstract", "doi", "vol.", "issue",
                                source.lower())
            if not any(s in text for s in academic_signals):
                continue
            articles.append(_entry_to_article(entry, source, extra={"journal_tier": tier}))
    return _dedup(articles)


def _collect_tier4() -> list:
    """Primary documents, communiques, joint statements, ministerial transcripts."""
    articles = []
    results = _fetch_feeds_parallel(TIER4_FEEDS)
    for source, (entries, _) in results.items():
        for entry in entries:
            if not _is_recent(entry, hours=48):
                continue
            if not _is_region_related(entry):
                continue
            articles.append(_entry_to_article(entry, source, extra={"primary_document": True}))
    return _dedup(articles)


# ─────────────────────────────────────────────────────────────────────────────
# CROSS-DAY MARKING
# ─────────────────────────────────────────────────────────────────────────────

def _mark_seen_before(articles: list) -> int:
    """Flag articles already published in a recent issue.

    Layer one of three in the cross-day defence. It annotates rather than drops,
    so the digest prompt can still run a story that has a genuinely new
    development, and lead with the development rather than the background.
    """
    try:
        from archive import lookup_published
    except Exception as e:
        print(f"  !  Archive unavailable, skipping cross-day marking: {e}")
        return 0

    marked = 0
    for a in articles:
        prior = lookup_published(a.get("url", ""), a.get("title", ""), days=7)
        if prior:
            a["seen_before"] = prior
            marked += 1
    return marked


# ─────────────────────────────────────────────────────────────────────────────
# MAIN COLLECTOR
# ─────────────────────────────────────────────────────────────────────────────

def collect() -> dict:
    """Run all tier collectors and return the combined payload."""
    _source_health.clear()
    print("\n  Collecting Australia / New Zealand / Pacific news (parallel)...")

    collectors = {
        "tier1": ("Tier 1: News articles",         _collect_tier1),
        "tier2": ("Tier 2: Analysis & commentary", _collect_tier2),
        "tier3": ("Tier 3: Academic journals",     _collect_tier3),
        "tier4": ("Tier 4: Primary documents",     _collect_tier4),
    }

    results = {}
    with ThreadPoolExecutor(max_workers=4) as pool:
        futures = {pool.submit(fn): key for key, (_label, fn) in collectors.items()}
        for future in as_completed(futures):
            key = futures[future]
            label = collectors[key][0]
            try:
                results[key] = future.result()
                print(f"  -- {label}: {len(results[key])} items")
            except Exception as e:
                print(f"  -- {label}: FAILED ({e})")
                results[key] = []

    tier1 = results["tier1"]
    tier2 = results["tier2"]
    tier3 = results["tier3"]
    tier4 = results["tier4"]

    # Subscriber-only newsletters, read from the inbox that receives them.
    # Off unless NEWSLETTERS=1, and best-effort even then: this must never be
    # able to take the collection down. Items land in tier 1 already carrying a
    # region tag, so they count toward the regional balance below like any
    # other news item, and _dedup drops anything a feed already supplied.
    try:
        import newsletters
        letters = newsletters.collect_newsletters()
        if letters:
            tier1 = _dedup(tier1 + letters)
    except Exception as e:                                  # noqa: BLE001
        print(f"  !  Newsletter ingestion failed, continuing without it: {e}")

    # Market figures, so the model has real numbers to anchor on rather
    # than a rule telling it not to invent any. Best-effort.
    try:
        import markets
        market_indicators = markets.collect_markets()
    except Exception as e:                                  # noqa: BLE001
        print(f"  !  Market collection failed: {e}")
        market_indicators = {}

    total = len(tier1) + len(tier2) + len(tier3) + len(tier4)
    print(f"\n  Total collected: {total} items")

    # ── Regional balance ─────────────────────────────────────────────────
    # The Pacific and NZ counts are the numbers that matter most day to day. If
    # Pacific input sits near zero, the section floors get met by stand-in lines
    # and the feed set needs work, not a lower floor.
    all_news = tier1 + tier2
    region_counts = {"AU": 0, "NZ": 0, "Pacific": 0}
    for a in all_news:
        key = a.get("region", "AU")
        region_counts[key] = region_counts.get(key, 0) + 1
    print(f"  Regional balance: {region_counts['AU']} AU / "
          f"{region_counts['NZ']} NZ / {region_counts['Pacific']} Pacific")
    if region_counts["Pacific"] < 5:
        print("  !  PACIFIC INPUT LOW: fewer than 5 Pacific items collected")
    if region_counts["NZ"] < 3:
        print("  !  NEW ZEALAND INPUT LOW: fewer than 3 NZ items collected")

    # ── Cross-day marking ────────────────────────────────────────────────
    marked = _mark_seen_before(tier1 + tier2 + tier3 + tier4)
    if marked:
        print(f"  Cross-day: {marked} item(s) already appeared in a recent issue")

    # ── Store everything in the archive for trend queries ────────────────
    try:
        from archive import store_items
        stored = store_items(tier1 + tier2 + tier3 + tier4)
        print(f"  Archive: {stored} new item(s) stored")
    except Exception as e:
        print(f"  !  Archive store failed: {e}")

    # ── Source health ────────────────────────────────────────────────────
    total_feeds = len(_source_health)
    feeds_with_data = sum(1 for h in _source_health.values() if h["success"])
    feeds_failed = total_feeds - feeds_with_data
    print(f"\n  Source health: {feeds_with_data}/{total_feeds} feeds returned data, "
          f"{feeds_failed} blocked/failed")
    for major in sorted(MAJOR_FEEDS):
        health = _source_health.get(major)
        if health and not health["success"]:
            err = health.get("error_msg") or "0 articles"
            print(f"  !  MAJOR FEED WARNING: {major} returned no data ({err})")
        elif not health:
            print(f"  !  MAJOR FEED WARNING: {major} was not fetched")

    return {
        "market_indicators": market_indicators,
        "tier1": tier1,
        "tier2": tier2,
        "tier3": tier3,
        "tier4": tier4,
        "region_counts": region_counts,
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
    Path("collected.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print("  -> Written to collected.json")
