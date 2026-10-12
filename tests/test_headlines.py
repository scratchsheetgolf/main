"""
Offline tests for data/headlines.py: feed parsing, scoring, same-story matching, draft selection.
No network: the feeds below are hand-written samples (made-up players) in each outlet's real shape.
Run: python -m unittest tests.test_headlines -v
"""
import os
import sys
import time
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from data import headlines  # noqa: E402

NOW = time.time()


def _rss(items, source_tag=False):
    def pub(hours_ago):
        return time.strftime("%a, %d %b %Y %H:%M:%S GMT", time.gmtime(NOW - hours_ago * 3600))
    body = "".join(
        f"<item><title>{t}</title><link>{u}</link><description>{d}</description><pubDate>{pub(h)}</pubDate>"
        + (f'<source url="https://x.example">{src}</source>' if source_tag else "") + "</item>"
        for t, u, d, h, src in items)
    return f'<?xml version="1.0"?><rss version="2.0"><channel><title>t</title>{body}</channel></rss>'.encode()


GOLF_MONTHLY = _rss([
    ("Report: PGA Tour Caddie Resigns Mid-Tournament At Fake Classic", "https://gm.example/caddie",
     "Cy Hot and his caddie Bo Bagman parted ways before the final round of the Fake Classic.", 5, ""),
    ("Best Golf Drivers 2026: 12 Tested", "https://gm.example/drivers", "Our buyer's guide.", 3, ""),
    ("Ann Example Wins Fake Open With Course-Record 61", "https://gm.example/win",
     "Ann Example shot 61 to win by four.", 2, ""),
])
GOOGLE = _rss([
    ("Another caddie quits mid-tournament on the PGA Tour, this time in Japan - Golfweek", "https://news.google/1",
     "Another caddie quits mid-tournament - Golfweek", 6, "Golfweek"),
    ("Cy Hot Has Career News Emerge Amid Surgery Talks - Heavy.com", "https://news.google/2",
     "x - Heavy.com", 1, "Heavy.com"),
], source_tag=True)


class ParseAndScore(unittest.TestCase):
    def test_parse_drops_buyers_guides_and_reads_google_outlet(self):
        gm = headlines.parse_feed("Golf Monthly", GOLF_MONTHLY)
        self.assertEqual(len(gm), 3)
        scores = {s["title"][:12]: s["score"] for s in gm}
        self.assertEqual(scores["Best Golf Dr"], 0)
        self.assertEqual(scores["Report: PGA "], 2)
        g = headlines.parse_feed("Google News", GOOGLE)
        self.assertEqual(g[0]["source"], "Golfweek")
        self.assertEqual(g[0]["title"], "Another caddie quits mid-tournament on the PGA Tour, this time in Japan")
        self.assertEqual(g[0]["summary"], "")   # Google's summary is only the title again

    def test_score_tiers(self):
        self.assertEqual(headlines.score("Star withdraws before first round"), 2)
        self.assertEqual(headlines.score("Ann Example wins in Spain"), 1)
        self.assertEqual(headlines.score("Weekend weather at the course"), 0)
        self.assertEqual(headlines.score("Best odds and picks for the Fake Open"), 0)
        # golf's "fired a 65" isn't a firing
        self.assertEqual(headlines.score("Cy Hot fired a 65 on Friday"), 0)

    def test_tour_label(self):
        self.assertEqual(headlines.tour_of("Rahm to quit LIV Golf"), "LIV GOLF")
        self.assertEqual(headlines.tour_of("caddie quits on the PGA Tour"), "PGA TOUR")
        self.assertEqual(headlines.tour_of("Ann wins the Aramco Korea Championship"), "GOLF")


class Clustering(unittest.TestCase):
    def setUp(self):
        self.fetched = headlines.parse_feed("Golf Monthly", GOLF_MONTHLY) + headlines.parse_feed("Google News", GOOGLE)
        self.state, self.added = headlines.update({}, self.fetched, now=NOW)

    def test_two_outlets_one_caddie_story(self):
        caddie = [s for s in self.state["stories"] if "caddie" in s["title"].lower()]
        self.assertEqual(len(caddie), 2)
        self.assertEqual(caddie[0]["story"], caddie[1]["story"])

    def test_refetch_adds_nothing(self):
        again, added = headlines.update(self.state, self.fetched, now=NOW)
        self.assertEqual(added, [])
        self.assertEqual(len(again["stories"]), len(self.state["stories"]))

    def test_old_stories_drop_off(self):
        later, _ = headlines.update(self.state, [], now=NOW + 9 * 86400)
        self.assertEqual(later["stories"], [])

    def test_draft_candidates_trusted_title_keyword_fresh(self):
        cands = headlines.draft_candidates(self.state, now=NOW)
        titles = [c["title"] for c in cands]
        # caddie story (trusted, keyword in title) and the course record: yes. Heavy.com clickbait: no.
        self.assertTrue(any("Caddie" in t or "caddie" in t for t in titles))
        self.assertTrue(any("Course-Record" in t for t in titles))
        self.assertFalse(any("Career News" in t for t in titles))
        caddie = next(c for c in cands if "caddie" in c["title"].lower())
        self.assertEqual(caddie["sources"], ["Golf Monthly", "Golfweek"])
        # once drafted, never again; and nothing is drafted after 12 h
        self.assertEqual(headlines.draft_candidates({**self.state, "drafted": [c["story"] for c in cands]}, now=NOW), [])
        self.assertEqual(headlines.draft_candidates(self.state, now=NOW + 13 * 3600), [])

    def test_week_stories_one_per_story_trusted_first(self):
        week = headlines.week_stories(self.state, now=NOW)
        self.assertEqual(len(week), len({s["story"] for s in self.state["stories"]}))
        self.assertTrue(week[0]["trusted"])
        self.assertFalse(week[-1]["trusted"])   # Heavy.com sinks to the bottom


if __name__ == "__main__":
    unittest.main()
