"""
Offline end-to-end tests: runs picks, live and newsletter in dry-run mode against
fake DataGolf responses shaped exactly like DataGolf's documented examples
(datagolf.com/api-access). No API keys, no network, nothing posted.

ALL PLAYER DATA HERE IS FAKE (made-up odds/finishes for testing the logic).

Run: python -m unittest tests/test_offline.py -v
"""
import os
import sys
import tempfile
import unittest
from datetime import date, timedelta
from unittest import mock

os.environ.setdefault("ANTHROPIC_API_KEY", "test-not-a-real-key")  # content.py builds a client at import
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pipeline  # noqa: E402
from content import content  # noqa: E402
from data import datagolf, state, transform  # noqa: E402

BOOKS = ["bet365", "draftkings", "fanduel", "pinnacle", "betmgm"]
TODAY = date.today()


def fake_field(n=40):
    """n fake players: model win odds and book odds (decimal) that disagree in known places."""
    rows, odds = [], []
    for i in range(n):
        dg_id = 1000 + i
        model_odds = 6.0 + i * 4          # player 0 is the model favourite
        book_odds = model_odds
        if i == 7:
            book_odds = model_odds * 2.5  # books underrate #7 -> VALUE
        if i == 3:
            book_odds = model_odds / 2.0  # books overrate #3 -> FADE
        rows.append({"dg_id": dg_id, "player_name": f"Player{i:02d}, Test", "win": round(model_odds, 2)})
        line = {"dg_id": dg_id, "player_name": f"Player{i:02d}, Test",
                "datagolf": {"baseline": model_odds, "baseline_history_fit": model_odds}}
        line.update({b: round(book_odds * (1 + 0.02 * k), 2) for k, b in enumerate(BOOKS)})
        odds.append(line)
    preds = {"event_name": "Fake Invitational", "models_available": ["baseline", "baseline_history_fit"],
             "baseline": rows, "baseline_history_fit": rows}
    return preds, {"event_name": "Fake Invitational", "market": "win", "odds": odds}


SCHEDULE = {"tour": "pga", "schedule": [
    {"event_id": "91", "event_name": "Fake Classic", "course": "Fake Links",
     "start_date": (TODAY - timedelta(days=4)).isoformat(), "status": "completed", "winner": "Player05, Test"},
    {"event_id": "92", "event_name": "Fake Invitational", "course": "Pretend National",
     "start_date": (TODAY + timedelta(days=3)).isoformat(), "status": "upcoming", "winner": "TBD"},
]}

RESULTS = {"event_id": "91", "event_name": "Fake Classic", "event_stats": [
    {"dg_id": 1005, "player_name": "Player05, Test", "fin_text": "1"},
    {"dg_id": 1000, "player_name": "Player00, Test", "fin_text": "T2"},
    {"dg_id": 1002, "player_name": "Player02, Test", "fin_text": "T2"},
    {"dg_id": 1009, "player_name": "Player09, Test", "fin_text": "4"},
    {"dg_id": 1003, "player_name": "Player03, Test", "fin_text": "CUT"},
    {"dg_id": 1001, "player_name": "Player01, Test", "fin_text": "5"},
]}

LIVE = {"info": {"current_round": 4, "event_name": "Fake Invitational"}, "data": [
    {"dg_id": 1000, "player_name": "Player00, Test", "current_pos": "1", "current_score": -14},
    {"dg_id": 1001, "player_name": "Player01, Test", "current_pos": "T2", "current_score": -11},
    {"dg_id": 1002, "player_name": "Player02, Test", "current_pos": "T2", "current_score": -11},
    {"dg_id": 1003, "player_name": "Player03, Test", "current_pos": "4", "current_score": 0},
    {"dg_id": 1004, "player_name": "Player04, Test", "current_pos": "5", "current_score": 3},
]}


class TransformTests(unittest.TestCase):
    def test_names_and_scores(self):
        self.assertEqual(transform.display_name("Scheffler, Scottie"), "Scottie Scheffler")
        self.assertEqual(transform.display_name("Byeong Hun An"), "Byeong Hun An")
        self.assertEqual([transform.format_to_par(x) for x in (-17, 0, 3, "E")], ["-17", "E", "+3", "E"])

    def test_picks_follow_the_rules(self):
        preds, outrights = fake_field()
        p = transform.choose_picks(preds, outrights)
        self.assertEqual(p["win"]["dg_id"], 1000)        # model favourite
        self.assertEqual(p["value"]["dg_id"], 1007)      # books underrate him
        self.assertEqual(p["fade"]["dg_id"], 1003)       # books overrate him
        self.assertGreaterEqual(p["sleeper"]["median_odds"], transform.SLEEPER_MIN_DECIMAL_ODDS)
        self.assertEqual(len({x["dg_id"] for x in p.values()}), 4)  # four different players
        self.assertEqual(p["win"]["name"], "Test Player00")

    def test_refuses_without_market_prices(self):
        preds, outrights = fake_field()
        with self.assertRaises(transform.PicksError):
            transform.choose_picks(preds, {"odds": []})

    def test_schedule_split(self):
        last, nxt = transform.last_completed_and_next(SCHEDULE, TODAY)
        self.assertEqual((last["event_id"], nxt["event_id"]), ("91", "92"))

    def test_top_finishers_sorts_ties_and_cuts(self):
        top = transform.top_finishers(RESULTS)
        self.assertEqual([t["pos"] for t in top], ["1", "T2", "T2", "4", "5"])


class PipelineDryRunTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.patches = [
            mock.patch.object(state, "STATE_PATH", os.path.join(self.tmp.name, "state.json")),
            mock.patch.object(pipeline, "OUTPUT_DIR", self.tmp.name),
            mock.patch.object(content, "_generate", side_effect=self.fake_llm),
            mock.patch.object(datagolf, "get_schedule", return_value=SCHEDULE),
            mock.patch.object(datagolf, "get_event_results", return_value=RESULTS),
            mock.patch.object(datagolf, "get_live_in_play", return_value=LIVE),
        ]
        preds, outrights = fake_field()
        self.patches += [mock.patch.object(datagolf, "get_pre_tournament_predictions", return_value=preds),
                         mock.patch.object(datagolf, "get_outright_odds", return_value=outrights)]
        for p in self.patches:
            p.start()
        self.llm_prompts = []

    def tearDown(self):
        for p in self.patches:
            p.stop()
        self.tmp.cleanup()

    def fake_llm(self, system, user, max_tokens=300):
        self.llm_prompts.append(user)
        if "LINE1" in system and "KICKER" in system:
            return "LINE1: A\nLINE2: B\nLINE3: C\nLINE4: D\nKICKER: k"
        return "Fake recap paragraph one.\nFake recap paragraph two."

    def test_picks_dry_run(self):
        out = pipeline.run_pretournament_picks(dry_run=True)
        self.assertTrue(out["DRY_RUN"])
        self.assertIn("Test Player00", out["picks"]["win"])
        self.assertTrue(os.path.exists(out["would_post_image"]))
        saved = state.load()["last_picks"]
        self.assertEqual((saved["event_id"], saved["year"]), ("92", SCHEDULE["schedule"][1]["start_date"][:4]))
        self.assertIn("VALUE: Test Player07", self.llm_prompts[-1])  # caption is fed the real picks

    def test_live_dry_run_formats_names_and_scores(self):
        with mock.patch.object(pipeline, "render_leaderboard") as rl:
            pipeline.run_live_poll(dry_run=True, min_leaderboard_gap_minutes=0)
        kwargs = rl.call_args.kwargs
        self.assertEqual(kwargs["event"], "FAKE INVITATIONAL")
        self.assertEqual(kwargs["players"][0], {"name": "Test Player00", "score": "-14"})
        self.assertEqual([p["score"] for p in kwargs["players"][3:]], ["E", "+3"])

    def test_newsletter_dry_run_reports_picks(self):
        state.save({"last_picks": {"event_id": "91", "event_name": "Fake Classic", "picks": {
            "win": {"dg_id": 1000, "name": "Test Player00"}, "value": {"dg_id": 1009, "name": "Test Player09"},
            "fade": {"dg_id": 1003, "name": "Test Player03"}, "sleeper": {"dg_id": 1030, "name": "Test Player30"}}}},
            commit=False)
        out = pipeline.run_weekly_newsletter(dry_run=True)
        with open(out["preview_file"], encoding="utf-8") as f:
            html = f.read()
        self.assertIn("Fake Classic recap", html)
        self.assertIn("How our picks did", html)
        self.assertIn("<b>FADE</b> Test Player03: CUT", html)
        self.assertIn("not in results", html)            # sleeper didn't appear in results
        self.assertIn("Up next", html)
        self.assertNotIn("TODO", html)
        self.assertIn("finished T2", self.llm_prompts[-1])  # recap writer only sees real results


class DraftModeTests(PipelineDryRunTests):
    """Draft = no posting, but state is saved (and committed) like a real run."""

    def setUp(self):
        super().setUp()
        self.git = mock.patch.object(state.subprocess, "run")
        self.git_run = self.git.start()
        self.no_post = mock.patch.object(pipeline.image_host, "publish_image",
                                         side_effect=AssertionError("draft must not post"))
        self.no_post.start()

    def tearDown(self):
        self.no_post.stop()
        self.git.stop()
        super().tearDown()

    def test_picks_draft_saves_state_and_caption_file(self):
        out = pipeline.run_pretournament_picks(draft=True)
        self.assertTrue(out["DRAFT"])
        with open(out["caption_file"], encoding="utf-8") as f:
            self.assertIn("TikTok title:", f.read())
        self.assertIn("last_picks", state.load())
        self.assertTrue(self.git_run.called)  # state committed, unlike a dry run

    def test_caption_falls_back_in_draft_but_not_for_real_posts(self):
        with mock.patch.object(content, "_generate", side_effect=RuntimeError("no api key")):
            out = pipeline.run_pretournament_picks(draft=True)
            self.assertIn("Win: Test Player00", out["would_post_caption"])
            with self.assertRaises(RuntimeError):
                pipeline.run_pretournament_picks()

    def test_no_hot_take_without_a_real_lead_change(self):
        pipeline.run_live_poll(draft=True, min_leaderboard_gap_minutes=0)       # fresh state
        self.assertFalse(os.path.exists(os.path.join(self.tmp.name, "hot_take_live.png")))
        state.save({**state.load(), "event_name": "Last Week Open", "leader_name": "Someone Else"}, commit=False)
        pipeline.run_live_poll(draft=True, min_leaderboard_gap_minutes=0)       # different event
        self.assertFalse(os.path.exists(os.path.join(self.tmp.name, "hot_take_live.png")))

    def test_hot_take_on_lead_change_in_same_event(self):
        state.save({"event_name": "Fake Invitational", "leader_name": "Test Player01"}, commit=False)
        out = pipeline.run_live_poll(draft=True, min_leaderboard_gap_minutes=600)
        self.assertEqual(out["actions"][0][0], "hot_take")
        self.assertTrue(out["actions"][0][1]["DRAFT"])

    def test_hourly_throttle_holds_between_draft_polls(self):
        pipeline.run_live_poll(draft=True, min_leaderboard_gap_minutes=60)
        out = pipeline.run_live_poll(draft=True, min_leaderboard_gap_minutes=60)
        self.assertIn("skipped", out["actions"][-1][1])

if __name__ == "__main__":
    unittest.main()
