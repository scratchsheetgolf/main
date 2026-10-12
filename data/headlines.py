"""
headlines.py — golf news from public RSS feeds, filtered by priority keywords (the CCC Intel pattern from
CigarCityCrypto/news_feed.py, adapted for golf and for GitHub Actions).

  fetch_all()     every feed -> list of story dicts (one bad feed never stops the rest)
  score()         2 = top tier (withdrawals, caddie splits, suspensions...), 1 = worth a newsletter line, 0 = skip
  same_story()    the same story from several outlets (shared player names + same top-tier topic)
  update()        merges a fetch into the saved state: new stories, 8-day history, story clusters

Feeds checked 2026-10-11. Golf Channel and Golfweek have no public RSS (404 / 402 paywall); the Google
News search feed covers them (titles end " - Golfweek", the source is in <source>). Links are the
outlet's own URL except Google News, whose links redirect to the outlet.

State: data/_headlines.json, committed back like the snapshot files (Actions runners start empty).
Facts only come from here: the headline, the outlet and its summary. Content.py writes the take.
"""
import calendar
import hashlib
import html
import re
import time

FEEDS = [
    ("Golf Monthly", "https://www.golfmonthly.com/feeds/all"),
    ("ESPN", "https://www.espn.com/espn/rss/golf/news"),
    ("CBS Sports", "https://www.cbssports.com/rss/headlines/golf/"),
    ("BBC Sport", "https://feeds.bbci.co.uk/sport/golf/rss.xml"),
    ("Sky Sports", "https://www.skysports.com/rss/12176"),
    ("Golf.com", "https://golf.com/feed/"),
    ("National Club Golfer", "https://www.nationalclubgolfer.com/feed/"),
    ("Google News", "https://news.google.com/rss/search?q=%22PGA+Tour%22+OR+%22DP+World+Tour%22+OR+%22LIV+Golf%22"
                    "+OR+LPGA+when:1d&hl=en-US&gl=US&ceid=US:en"),
]

# Top tier: the story is about something happening to a named pro (a social draft + the newsletter).
P1_KEYWORDS = [
    "withdraws", "withdrew", "withdrawal", "wd", "disqualified", "disqualification", "dq", "dq'd",
    "caddie", "caddy", "splits with", "parts ways", "parted ways", "quits", "quit",
    "suspended", "suspension", "banned", "penalty", "penalized", "rules official", "ruling",
    "injury", "injured", "surgery", "hospital", "out for", "ruled out",
    "joins liv", "leaves liv", "defects", "signs with", "reinstated", "merger",
    "ryder cup captain", "ryder cup pick", "captain's pick", "wildcard", "wild card",
    "retire", "retires", "retirement", "dies", "died", "death",
    "hole-in-one", "ace", "albatross", "59", "course record", "course-record",
]
# Second tier: newsletter material (results, rankings, gear, schedule) but not a draft on its own.
P2_KEYWORDS = [
    "wins", "won", "victory", "champion", "playoff", "leads", "lead", "rankings", "world no",
    "fedex", "race to dubai", "q-school", "qualifying", "card", "major", "masters", "pga championship",
    "u.s. open", "us open", "open championship", "ryder cup", "presidents cup", "solheim",
    "driver", "putter", "equipment", "schedule", "purse", "prize money", "field",
]
# Never a story for us: betting promos, buyer's guides, deals, amateur how-tos.
SKIP_PATTERNS = [
    "best golf", "deal", "deals", "discount", "% off", "buyer", "buying guide", "review:", "tested",
    "odds", "picks", "prediction", "predictions", "betting", "bet365", "sportsbook", "promo code",
    "how to", "tips", "drill", "drills", "lesson", "fix your", "quiz", "gift",
]


def _pattern(words: list):
    return re.compile(r"(?<![\w'])(" + "|".join(re.escape(w) for w in words) + r")(?![\w'])", re.IGNORECASE)


_P1, _P2, _SKIP = _pattern(P1_KEYWORDS), _pattern(P2_KEYWORDS), _pattern(SKIP_PATTERNS)
_TAGS = re.compile(r"<[^>]+>")
_SPACE = re.compile(r"\s+")
# Words too common to tie two headlines to one story ("tour", "wins"); a shared rarer word ("yamashita",
# "baycurrent") plus the same topic does.
GENERIC = {"golf", "golfer", "golfers", "tour", "tours", "after", "with", "from", "into", "over", "their", "this",
           "that", "what", "when", "will", "have", "says", "said", "report", "reports", "news", "latest", "update",
           "player", "players", "round", "final", "first", "second", "third", "week", "year", "years", "win", "wins",
           "won", "winner", "course", "record", "championship", "classic", "open", "invitational", "event", "field",
           "star", "veteran", "about", "amid", "ahead", "another", "time", "world", "card", "cards", "season",
           "finish", "mid-tournament", "mid-round", "makes", "gets", "still", "could", "would", "more", "than"}

# Outlets whose stories can become a social draft or lead a newsletter item. Anything else (Google News
# pulls in aggregators and clickbait) is kept only as a second source on a story a trusted outlet has.
TRUSTED = {"golf monthly", "espn", "cbs sports", "bbc sport", "sky sports", "golf.com", "national club golfer",
           "golfweek", "golf channel", "nbc sports", "pga tour", "lpga", "dp world tour", "liv golf", "golf digest",
           "the athletic", "associated press", "ap news", "reuters", "yahoo sports", "usa today", "the guardian",
           "the independent", "theScore".lower(), "golf.com", "bunkered", "today's golfer", "golf magic",
           "sports illustrated", "fox sports", "the times", "the telegraph", "irish golfer", "golf australia"}

KEEP_DAYS = 8   # a week of newsletter material plus a day of slack


def clean(text: str) -> str:
    """Feed text -> plain text: tags stripped, entities decoded, whitespace collapsed."""
    return _SPACE.sub(" ", html.unescape(_TAGS.sub(" ", text or ""))).strip()


def score(title: str, summary: str = "") -> int:
    """2 top tier, 1 newsletter-worthy, 0 skip. Skip patterns are checked on the title only (a news
    story's summary can mention odds), and win over keywords."""
    if _SKIP.search(title):
        return 0
    text = f"{title} {summary}"
    if _P1.search(title) or (_P1.search(summary) and _P2.search(title)):
        return 2
    if _P1.search(summary) or _P2.search(text):
        return 1
    return 0


def topic(title: str, summary: str = "") -> str:
    """The first top-tier keyword in the story ('caddie', 'withdraws'), for matching the same story."""
    m = _P1.search(title) or _P1.search(summary)
    return m.group(1).lower() if m else ""


def tokens(text: str) -> set:
    """Distinctive words in text: 4+ letters, not generic, not a topic keyword."""
    words = set(re.findall(r"[a-zà-ÿ0-9'’-]{4,}", (text or "").lower()))
    return {w.strip("'’-") for w in words} - GENERIC - _TOPIC_WORDS


def trusted(source: str) -> bool:
    return (source or "").strip().lower() in TRUSTED


def story_hash(title: str, source: str) -> str:
    return hashlib.sha1(f"{source}|{title.strip().lower()}".encode()).hexdigest()[:16]


def _split_google_title(title: str, entry) -> tuple:
    """Google News titles are 'Headline - Outlet'; the outlet is also in <source>."""
    outlet = ""
    src = entry.get("source") if hasattr(entry, "get") else None
    if src is not None:
        outlet = getattr(src, "title", None) or (src.get("title") if hasattr(src, "get") else "") or ""
    if " - " in title:
        head, tail = title.rsplit(" - ", 1)
        outlet = outlet or tail
        if tail.strip().lower() == outlet.strip().lower():
            title = head
    return title.strip(), outlet.strip()


def parse_feed(source: str, raw) -> list:
    """One feed's XML/bytes -> stories. Entries without a title or link are dropped."""
    import feedparser
    parsed = feedparser.parse(raw)
    out = []
    for e in parsed.entries:
        title, link = clean(e.get("title", "")), e.get("link", "")
        if not title or not link:
            continue
        outlet = source
        if source == "Google News":
            title, outlet = _split_google_title(title, e)
            outlet = outlet or "Google News"
        summary = clean(e.get("summary", ""))
        if source == "Google News":
            summary = ""   # Google's summary is just the title again plus the outlet's name
        published = e.get("published_parsed") or e.get("updated_parsed")
        ts = calendar.timegm(published) if published else time.time()   # feedparser gives UTC
        out.append({"id": story_hash(title, outlet), "title": title, "source": outlet, "url": link,
                    "summary": summary[:600], "ts": ts, "score": score(title, summary),
                    "topic": topic(title, summary)})
    return out


def fetch_all(feeds: list = None, timeout: int = 15) -> tuple:
    """(stories, errors). Every feed is tried; a dead or slow one is reported, not raised."""
    import requests
    stories, errors = [], {}
    for source, url in feeds or FEEDS:
        try:
            r = requests.get(url, timeout=timeout, headers={"User-Agent": "Mozilla/5.0 (ScratchSheet headlines)"})
            r.raise_for_status()
            stories.extend(parse_feed(source, r.content))
        except Exception as e:
            errors[source] = f"{type(e).__name__}: {str(e)[:120]}"
    return stories, errors


# Topics that rarely happen twice in two days: the same topic is enough to call it one story.
_TOPIC_GROUPS = [({"caddie", "caddy", "splits with", "parts ways", "parted ways"}, True),
                 ({"quits", "quit", "joins liv", "leaves liv", "defects", "signs with", "merger"}, False),
                 ({"withdraws", "withdrew", "withdrawal", "wd", "injury", "injured", "surgery", "hospital",
                   "out for", "ruled out"}, False),
                 ({"disqualified", "disqualification", "dq", "dq'd", "penalty", "penalized", "rules official",
                   "ruling"}, False),
                 ({"suspended", "suspension", "banned", "reinstated"}, True),
                 ({"ryder cup captain", "ryder cup pick", "captain's pick", "wildcard", "wild card"}, False),
                 ({"retire", "retires", "retirement"}, False), ({"dies", "died", "death"}, False),
                 ({"hole-in-one", "ace", "albatross", "59", "course record", "course-record"}, False)]
_TOPIC_WORDS = {w for g, _ in _TOPIC_GROUPS for k in g for w in k.split()}
SAME_STORY_HOURS = 48


def _group(topic_word: str):
    return next(((g, rare) for g, rare in _TOPIC_GROUPS if topic_word in g), (None, False))


def same_story(a: dict, b: dict) -> bool:
    """Two outlets on one story. Same id, near-identical titles, or: published within 48 h, same topic
    group, and either a shared distinctive word ('yamashita', 'baycurrent') or a rare topic (caddie split,
    suspension). 'Lipsky changes caddies' and 'Another caddie quits mid-tournament in Japan' match."""
    if a["id"] == b["id"]:
        return True
    ta = set(re.findall(r"[a-z0-9']+", a["title"].lower()))
    tb = set(re.findall(r"[a-z0-9']+", b["title"].lower()))
    if ta and tb and len(ta & tb) / len(ta | tb) >= 0.6:
        return True
    if abs(a.get("ts", 0) - b.get("ts", 0)) > SAME_STORY_HOURS * 3600 or not (a.get("topic") and b.get("topic")):
        return False
    group, rare = _group(a["topic"])
    if group is None or b["topic"] not in group:
        return False
    if rare:
        return True
    return bool(tokens(f"{a['title']} {a.get('summary', '')}") & tokens(f"{b['title']} {b.get('summary', '')}"))


def update(saved: dict, fetched: list, now: float = None) -> tuple:
    """Merge a fetch into saved state. Returns (new_state, added): every story not seen before, best first.
    A second outlet on a known story joins its cluster (same 'story' id) rather than starting a new one.
    Stories older than KEEP_DAYS, or scored 0, are dropped."""
    now = now or time.time()
    keep_after = now - KEEP_DAYS * 86400
    stories = [s for s in (saved.get("stories") or []) if s.get("ts", 0) >= keep_after]
    known = {s["id"] for s in stories}
    added = []
    for s in sorted(fetched, key=lambda x: x["ts"]):
        if s["id"] in known or s["ts"] < keep_after or s["score"] == 0:
            continue
        match = next((o for o in stories if same_story(o, s)), None)
        s = {**s, "story": match["story"] if match else s["id"]}
        stories.append(s)
        known.add(s["id"])
        added.append(s)
    added.sort(key=lambda x: (-x["score"], -x["ts"]))
    return {**saved, "stories": stories}, added


DRAFT_MAX_AGE_HOURS = 12   # older than this and it's stale for a social post (newsletter still gets it)


def draft_candidates(saved: dict, now: float = None) -> list:
    """Stories worth a social draft: a cluster seen in the last 12 h, not drafted yet, with a top-tier
    keyword in the title of a trusted outlet's version. One entry per cluster (that version), newest first.
    Age-based rather than 'new this fetch', so a story held during quiet hours is still drafted after."""
    now = now or time.time()
    done = set(saved.get("drafted") or [])
    clusters = {}
    for s in saved.get("stories") or []:
        clusters.setdefault(s.get("story", s["id"]), []).append(s)
    out = []
    for c, members in clusters.items():
        if c in done:
            continue
        good = sorted((m for m in members if m["score"] == 2 and trusted(m["source"]) and _P1.search(m["title"])
                       and m["ts"] >= now - DRAFT_MAX_AGE_HOURS * 3600), key=lambda m: m["ts"])
        if good:
            out.append({**good[0], "sources": sorted({m["source"] for m in members})})
    out.sort(key=lambda x: -x["ts"])
    return out


TOURS = [("PGA TOUR CHAMPIONS", r"champions tour|pga tour champions"), ("LIV GOLF", r"\bliv\b"),
         ("LPGA", r"\blpga\b"), ("DP WORLD TOUR", r"dp world|european tour|race to dubai"),
         ("KORN FERRY TOUR", r"korn ferry|\bkft\b"), ("RYDER CUP", r"ryder cup"), ("PGA TOUR", r"pga tour")]


def tour_of(text: str) -> str:
    """Label for the card's source line, from the story's own words; 'GOLF' when it names no tour."""
    low = (text or "").lower()
    return next((label for label, pat in TOURS if re.search(pat, low)), "GOLF")


def week_stories(saved: dict, now: float = None, days: int = 7) -> list:
    """One entry per story from the last `days`, best first: the earliest outlet's version, with every
    outlet that covered it in 'sources'."""
    now = now or time.time()
    groups = {}
    for s in saved.get("stories") or []:
        if s.get("ts", 0) >= now - days * 86400:
            groups.setdefault(s.get("story", s["id"]), []).append(s)
    out = []
    for members in groups.values():
        members.sort(key=lambda x: (not trusted(x["source"]), x["ts"]))
        first = members[0]
        out.append({**first, "score": max(m["score"] for m in members), "trusted": trusted(first["source"]),
                    "sources": sorted({m["source"] for m in members}), "coverage": len(members)})
    out.sort(key=lambda x: (not x["trusted"], -x["score"], -x["coverage"], -x["ts"]))
    return out
