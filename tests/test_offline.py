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
import render  # noqa: E402

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

    def test_picks_refused_once_event_started(self):
        ev = transform.check_picks_window("Fake Invitational", SCHEDULE, TODAY)
        self.assertEqual(ev["event_id"], "92")
        with self.assertRaises(transform.PicksError):
            transform.check_picks_window("Fake Classic", SCHEDULE, TODAY)        # already started
        with self.assertRaises(transform.PicksError):
            transform.check_picks_window("Unknown Open", SCHEDULE, TODAY)        # not in schedule

    def test_top_finishers_sorts_ties_and_cuts(self):
        top = transform.top_finishers(RESULTS)
        self.assertEqual([t["pos"] for t in top], ["1", "T2", "T2", "4", "5"])


class PipelineDryRunTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.patches = [
            mock.patch.object(state, "STATE_DIR", self.tmp.name),
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

    def fake_llm(self, system, user, max_tokens=300, temperature=1.0):
        self.llm_prompts.append(user)
        if "KICKER" in system:
            return "\n".join(f"OPTION {i}\nLINE1: TAKE {i}\nLINE2: B\nLINE3: C\nLINE4: D\nKICKER: — k{i}"
                             for i in (1, 2, 3)).replace("TAKE 1", "TAKE ONE").replace("TAKE 2", "TAKE TWO").replace("TAKE 3", "TAKE THREE").replace("k1", "ka").replace("k2", "kb").replace("k3", "kc")
        if "SUPPORTING" in system:
            return "OPTION 1\nSUPPORTING: the irons are just unfair right now\nOPTION 2\nSUPPORTING: other line"
        if "CAPTION" in system:
            return "OPTION 1\nCAPTION: Caption alpha.\nOPTION 2\nCAPTION: Caption beta.\nOPTION 3\nCAPTION: Caption gamma."
        return "Fake recap paragraph one.\nFake recap paragraph two."

    def test_picks_dry_run(self):
        out = pipeline.run_pretournament_picks(dry_run=True)
        self.assertTrue(out["DRY_RUN"])
        self.assertIn("Test Player00", out["picks"]["win"])
        self.assertTrue(os.path.exists(out["would_post_image"]))
        saved = state.load()["last_picks"]
        self.assertEqual((saved["event_id"], saved["year"]), ("92", SCHEDULE["schedule"][1]["start_date"][:4]))
        self.assertTrue(any("VALUE: Test Player07" in p for p in self.llm_prompts))  # caption is fed the real picks

    def test_live_dry_run_formats_names_and_scores(self):
        with mock.patch.object(pipeline, "render_leaderboard") as rl:
            pipeline.run_live_poll(dry_run=True, min_leaderboard_gap_minutes=0)
        kwargs = rl.call_args.kwargs
        self.assertEqual(kwargs["event"], "FAKE INVITATIONAL")
        self.assertEqual(kwargs["players"][0], {"pos": "1", "name": "Test Player00", "score": "-14"})
        self.assertEqual(kwargs["round_label"], "ROUND 4 · LIVE")
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

    def test_no_hourly_leaderboard_when_top5_unchanged(self):
        first = pipeline.run_live_poll(draft=True, min_leaderboard_gap_minutes=0)
        self.assertTrue(first["actions"][-1][1]["DRAFT"])
        again = pipeline.run_live_poll(draft=True, min_leaderboard_gap_minutes=0)   # same feed: no news
        self.assertEqual(again["actions"][-1][1], "skipped: top 5 unchanged since the last leaderboard")
        moved = {**LIVE, "data": [dict(r) for r in LIVE["data"]]}
        lead = min(moved["data"], key=lambda r: r["current_score"])
        lead["current_score"] -= 1                                                  # leader birdies
        with mock.patch.object(datagolf, "get_live_in_play", return_value=moved):
            out = pipeline.run_live_poll(draft=True, min_leaderboard_gap_minutes=0)
        self.assertTrue(out["actions"][-1][1]["DRAFT"])

class ContentCheckTests(unittest.TestCase):
    """The checks that make a small model safe to use: limits, grounded numbers, retry, SKIP."""

    FACTS = "Event: Fake Open, round 4\nNew leader: Test Player at -14\nNext: Other Guy at -12 (2 shots clear)"

    def run_with(self, replies, fn, *args):
        with mock.patch.object(content, "_generate", side_effect=list(replies)) as gen:
            return fn(*args), gen

    def test_keeps_valid_options_and_drops_bad_ones(self):
        raw = ("OPTION 1\nLINE1: THIS LINE IS WAY TOO LONG FOR THE CARD\nLINE2: B\nLINE3: C\nLINE4: D\nKICKER: — k\n"
               "OPTION 2\nLINE1: TWO CLEAR\nLINE2: AT 14 UNDER\nLINE3: AND COUNTING\nLINE4: FOLKS.\nKICKER: — calm\n"
               "OPTION 3\nLINE1: LEADS BY 9\nLINE2: B\nLINE3: C\nLINE4: D\nKICKER: — k")
        take, gen = self.run_with([raw], content.generate_hot_take, self.FACTS)
        self.assertEqual(take["lines"][0], "TWO CLEAR")      # too-long and invented-"9" options dropped
        self.assertEqual(take["alternatives"], [])
        self.assertEqual(gen.call_count, 1)

    def test_retries_once_with_feedback_then_succeeds(self):
        bad = "OPTION 1\nLINE1: LEADS BY 9 NOW\nLINE2: B\nLINE3: C\nLINE4: D\nKICKER: — k"
        good = "OPTION 1\nLINE1: TWO CLEAR\nLINE2: B\nLINE3: C\nLINE4: D\nKICKER: — k"
        take, gen = self.run_with([bad, good], content.generate_hot_take, self.FACTS)
        self.assertEqual(take["lines"][0], "TWO CLEAR")
        retry_prompt = gen.call_args_list[1].args[1]
        self.assertIn("numbers not in the facts: 9", retry_prompt)

    def test_gives_up_after_one_retry(self):
        bad = "OPTION 1\nLINE1: LEADS BY 9\nLINE2: B\nLINE3: C\nLINE4: D\nKICKER: — k"
        with self.assertRaises(content.ContentError):
            self.run_with([bad, bad], content.generate_hot_take, self.FACTS)

    def test_skip_returns_none(self):
        take, _ = self.run_with(["SKIP"], content.generate_hot_take, self.FACTS)
        self.assertIsNone(take)

    def test_numbers_check(self):
        self.assertEqual(content.ungrounded_numbers("leads at -14, 2 clear", self.FACTS), [])
        self.assertEqual(content.ungrounded_numbers("his 3rd win, 65% of fairways", self.FACTS), ["3", "65"])

    def test_rejects_invented_history_claims(self):
        self.assertEqual(content.unsupported_claims("He just won his first PGA Tour event", self.FACTS), ["first"])
        self.assertEqual(content.unsupported_claims("a first-time winner", self.FACTS), ["first"])
        self.assertEqual(content.unsupported_claims("back-to-back wins", self.FACTS), ["back-to-back"])
        self.assertEqual(content.unsupported_claims("his first win", "Note: first PGA Tour win"), [])
        caption = "OPTION 1\nCAPTION: His first PGA Tour win, two clear.\nOPTION 2\nCAPTION: Wire to wire, two clear."
        opts, _ = self.run_with([caption], content.generate_caption_options, "final leaderboard", self.FACTS)
        self.assertEqual(opts, ["Wire to wire, two clear."])

    def test_recap_rejects_invented_numbers(self):
        with mock.patch.object(content, "_generate", side_effect=["He shot 63 on Sunday.", "He closed it out."]):
            self.assertEqual(content.generate_newsletter_recap(self.FACTS), "He closed it out.")

    def test_lead_change_facts_have_real_numbers(self):
        facts = transform.lead_change_facts(LIVE, "Test Player01")
        self.assertIn("round 4", facts)
        self.assertIn("Test Player00 at -14", facts)
        self.assertIn("3 shots clear", facts)
        self.assertIn("Previous leader: Test Player01", facts)


class AlternativesInDraftTests(DraftModeTests):
    def test_caption_alternatives_written_to_draft(self):
        out = pipeline.run_pretournament_picks(draft=True)
        self.assertTrue(out["would_post_caption"].startswith("Caption alpha.\n\n"))
        self.assertTrue(out["would_post_caption"].endswith("? #golfpicks") or out["would_post_caption"].endswith(", go. #golfpicks"))
        with open(out["caption_file"], encoding="utf-8") as f:
            text = f.read()
        self.assertIn("- Caption beta.", text)
        self.assertIn("- Caption gamma.", text)

    def test_alternative_hot_take_cards_rendered(self):
        state.save({"event_name": "Fake Invitational", "leader_name": "Test Player01"}, commit=False)
        pipeline.run_live_poll(draft=True, min_leaderboard_gap_minutes=600)
        for name in ("hot_take_live.png", "hot_take_live_alt1.png", "hot_take_live_alt2.png"):
            self.assertTrue(os.path.exists(os.path.join(self.tmp.name, name)), name)
        self.assertIn("round 4", self.llm_prompts[0])   # the hot-take writer (first call) got real numbers

class RealSdkAndFallbackTests(PipelineDryRunTests):
    def test_generate_call_matches_installed_sdk_signature(self):
        """Mocks with autospec of the real SDK method, so a removed keyword (like temperature in
        anthropic 1.x) raises TypeError here instead of in production."""
        self.patches[2].stop()  # use the real content._generate
        fake = mock.MagicMock()
        fake.content = [mock.MagicMock(type="text", text="ok")]
        with mock.patch.object(type(content.client.messages), "create", autospec=True, return_value=fake) as create:
            self.assertEqual(content._generate("sys", "hi", max_tokens=10, temperature=0.3), "ok")
        self.assertEqual(create.call_args.kwargs["extra_body"], {"temperature": 0.3})
        self.patches[2].start()

    def test_in_progress_event_is_not_recapped(self):
        sched = {"schedule": SCHEDULE["schedule"] + [
            {"event_id": "93", "event_name": "Fake Midweek", "start_date": (TODAY - timedelta(days=2)).isoformat(),
             "status": "in progress", "winner": "TBD"}]}
        last, nxt = transform.last_completed_and_next(sched, TODAY)
        self.assertEqual((last["event_id"], nxt["event_id"]), ("91", "92"))

    def test_recap_falls_back_to_live_feed_when_results_forbidden(self):
        live_final = {"info": {"event_name": "Fake Classic"}, "data": [
            {"dg_id": 1005, "player_name": "Player05, Test", "current_pos": "1", "current_score": -20},
            {"dg_id": 1000, "player_name": "Player00, Test", "current_pos": "T2", "current_score": -18}]}
        with mock.patch.object(datagolf, "get_event_results", side_effect=RuntimeError("403 Forbidden")), \
             mock.patch.object(datagolf, "get_live_in_play", return_value=live_final):
            out = pipeline.run_weekly_newsletter(dry_run=True)
        with open(out["preview_file"], encoding="utf-8") as f:
            self.assertIn("<b>1</b> Test Player05", f.read())

    def test_recap_skipped_when_no_results_anywhere(self):
        with mock.patch.object(datagolf, "get_event_results", side_effect=RuntimeError("403 Forbidden")):
            out = pipeline.run_weekly_newsletter(dry_run=True)   # LIVE fixture is a different event
        with open(out["preview_file"], encoding="utf-8") as f:
            html = f.read()
        self.assertNotIn("recap", html)
        self.assertIn("Up next", html)

    def test_recap_uses_standings_saved_by_live_poll(self):
        state.save({"standings": {"event_name": "Fake Classic", "event_stats": [
            {"dg_id": 1009, "player_name": "Player09, Test", "fin_text": "1"},
            {"dg_id": 1000, "player_name": "Player00, Test", "fin_text": "T2"}]}}, commit=False)
        with mock.patch.object(datagolf, "get_event_results", side_effect=RuntimeError("403 Forbidden")):
            out = pipeline.run_weekly_newsletter(dry_run=True)
        with open(out["preview_file"], encoding="utf-8") as f:
            self.assertIn("<b>1</b> Test Player09", f.read())

    def test_unsorted_feed_is_put_in_leaderboard_order(self):
        unsorted = {"info": {"event_name": "Fake Invitational", "current_round": 4}, "data": [
            {"dg_id": 1, "player_name": "Cut, Guy", "current_pos": "CUT", "current_score": 4},
            {"dg_id": 2, "player_name": "Third, Tied", "current_pos": "T3", "current_score": -10},
            {"dg_id": 3, "player_name": "Leader, The", "current_pos": "1", "current_score": -26},
            {"dg_id": 4, "player_name": "Second, Solo", "current_pos": "2", "current_score": -19},
            {"dg_id": 5, "player_name": "Third, Other", "current_pos": "T3", "current_score": -10}]}
        order = [r["dg_id"] for r in transform.sorted_leaderboard(unsorted)]
        self.assertEqual(order[:2], [3, 4])
        self.assertEqual(order[-1], 1)
        with mock.patch.object(datagolf, "get_live_in_play", return_value=unsorted), \
             mock.patch.object(pipeline, "render_leaderboard") as rl:
            pipeline.run_live_poll(dry_run=True, min_leaderboard_gap_minutes=0)
        players = rl.call_args.kwargs["players"]
        self.assertEqual([p["pos"] for p in players], ["1", "2", "T3", "T3", "CUT"])
        self.assertEqual(players[0]["name"], "The Leader")

    def test_final_round_labelled_final(self):
        done = {"info": {"event_name": "Fake Invitational", "current_round": 4}, "data": [
            {**r, "thru": "F"} for r in LIVE["data"]]}
        self.assertTrue(transform.is_final(done))
        self.assertFalse(transform.is_final(LIVE))
        real_shape = {"info": {"current_round": 4}, "data": [{**r, "thru": 18} for r in LIVE["data"]]}
        self.assertTrue(transform.is_final(real_shape))     # what the live feed actually sent
        mid_round = {"info": {"current_round": 4}, "data": [{**r, "thru": 12} for r in LIVE["data"]]}
        self.assertFalse(transform.is_final(mid_round))
        with mock.patch.object(datagolf, "get_live_in_play", return_value=done), \
             mock.patch.object(pipeline, "render_leaderboard") as rl:
            pipeline.run_live_poll(dry_run=True, min_leaderboard_gap_minutes=0)
        self.assertEqual(rl.call_args.kwargs["round_label"], "FINAL")
        self.assertIn("FINAL results", self.llm_prompts[-1])

    def test_recap_shows_scores_from_live_standings(self):
        live_final = {"info": {"event_name": "Fake Classic", "current_round": 4}, "data": [
            {"dg_id": 1005, "player_name": "Player05, Test", "current_pos": "1", "current_score": -20, "thru": "F"}]}
        with mock.patch.object(datagolf, "get_event_results", side_effect=RuntimeError("403 Forbidden")), \
             mock.patch.object(datagolf, "get_live_in_play", return_value=live_final):
            out = pipeline.run_weekly_newsletter(dry_run=True)
        with open(out["preview_file"], encoding="utf-8") as f:
            self.assertIn("<b>1</b> Test Player05 -20", f.read())
        self.assertIn("1 Test Player05 (-20)", self.llm_prompts[-1])

    def test_live_poll_saves_standings(self):
        with mock.patch.object(pipeline, "render_leaderboard"):
            pipeline.run_live_poll(dry_run=True, min_leaderboard_gap_minutes=0)
        saved = state.load()["standings"]
        self.assertEqual(saved["event_name"], "Fake Invitational")
        self.assertEqual(saved["event_stats"][0]["fin_text"], "1")

class PlatformSwitchTests(unittest.TestCase):
    def test_enabled_platforms(self):
        with mock.patch.dict(os.environ, {"POST_PLATFORMS": ""}):
            self.assertEqual(pipeline.enabled_platforms(), pipeline.ALL_PLATFORMS)
        with mock.patch.dict(os.environ, {"POST_PLATFORMS": " X "}):
            self.assertEqual(pipeline.enabled_platforms(), {"x"})
        with mock.patch.dict(os.environ, {"POST_PLATFORMS": "x,threads"}):
            with self.assertRaises(ValueError):
                pipeline.enabled_platforms()

    def test_x_only_posts_to_x_and_skips_image_host(self):
        with mock.patch.dict(os.environ, {"POST_PLATFORMS": "x"}), \
             mock.patch.object(pipeline.post_x, "post_image", return_value={"id": "1"}) as px, \
             mock.patch.object(pipeline.image_host, "publish_image") as host, \
             mock.patch.object(pipeline.post_meta, "post_to_instagram") as ig:
            out = pipeline._post_everywhere("card.png", "Bridgeman leads at -11 after round 2.")
        px.assert_called_once_with("card.png", "Bridgeman leads at -11 after round 2.")
        host.assert_not_called()
        ig.assert_not_called()
        self.assertEqual(out["x"], {"id": "1"})

    def test_image_host_failure_no_longer_blocks_x(self):
        with mock.patch.dict(os.environ, {"POST_PLATFORMS": "x,instagram"}), \
             mock.patch.object(pipeline.post_x, "post_image", return_value={"id": "1"}), \
             mock.patch.object(pipeline.image_host, "publish_image", side_effect=RuntimeError("git")):
            out = pipeline._post_everywhere("card.png", "Bridgeman leads at -11 after round 2.")
        self.assertEqual(out["x"], {"id": "1"})
        self.assertIn("FAILED", out["image_host"])


class XCheckTests(unittest.TestCase):
    def fake_me(self, level):
        resp = mock.MagicMock()
        resp.json.return_value = {"data": {"id": "42", "username": "TheScratchSheet"}}
        resp.headers = {"x-access-level": level}
        return resp

    def test_check_reports_account_and_write_access(self):
        from distribute import post_x
        with mock.patch.multiple(post_x, API_KEY="k", API_SECRET="s", ACCESS_TOKEN="42-t", ACCESS_SECRET="a"), \
             mock.patch.object(post_x.tweepy.Client, "get_me", return_value=self.fake_me("read-write")):
            out = post_x.check()
        self.assertEqual((out["username"], out["can_post"]), ("TheScratchSheet", True))
        with mock.patch.multiple(post_x, API_KEY="k", API_SECRET="s", ACCESS_TOKEN="42-t", ACCESS_SECRET="a"), \
             mock.patch.object(post_x.tweepy.Client, "get_me", return_value=self.fake_me("read")):
            self.assertFalse(post_x.check()["can_post"])

    def test_check_catches_common_key_mixups(self):
        from distribute import post_x
        with mock.patch.multiple(post_x, API_KEY="k", API_SECRET="s", ACCESS_TOKEN="AAAAbearerlike", ACCESS_SECRET="a"):
            with self.assertRaisesRegex(RuntimeError, "Bearer Token"):
                post_x.check()
        with mock.patch.multiple(post_x, API_KEY="k ", API_SECRET="s", ACCESS_TOKEN="42-t", ACCESS_SECRET="a"):
            with self.assertRaisesRegex(RuntimeError, "whitespace"):
                post_x.check()

class MetaCheckTests(unittest.TestCase):
    GOOD_TOKEN = {"data": {"is_valid": True, "type": "PAGE", "expires_at": 0, "app_id": "9", "profile_id": "111",
                           "scopes": ["pages_show_list", "pages_read_engagement", "pages_manage_posts",
                                      "instagram_basic", "instagram_content_publish"]}}
    PAGE = {"name": "The Scratch Sheet", "instagram_business_account": {"id": "222", "username": "scratchsheetgolf"}}

    def run_check(self, token, page, **env):
        from distribute import post_meta
        def fake_get(url, params=None, timeout=None):
            resp = mock.MagicMock(ok=True)
            resp.json.return_value = token if url.endswith("/debug_token") else page
            return resp
        ids = {"PAGE_ID": "111", "ACCESS_TOKEN": "tok", "IG_USER_ID": "222", **env}
        with mock.patch.multiple(post_meta, **ids), mock.patch.object(post_meta.requests, "get", side_effect=fake_get), \
             mock.patch.object(post_meta.requests, "post") as post:
            out = post_meta.check()
        post.assert_not_called()   # a check never posts
        return out

    def test_good_setup_has_no_problems(self):
        out = self.run_check(self.GOOD_TOKEN, self.PAGE)
        self.assertEqual((out["page_name"], out["instagram_username"], out["expires"], out["problems"]),
                         ("The Scratch Sheet", "scratchsheetgolf", "never", []))

    def test_check_catches_user_token_missing_scope_and_wrong_ig(self):
        bad = {"data": {**self.GOOD_TOKEN["data"], "type": "USER", "expires_at": 1999999999,
                        "scopes": ["pages_show_list"]}}
        problems = " | ".join(self.run_check(bad, self.PAGE, IG_USER_ID="333")["problems"])
        for want in ("expected PAGE", "token expires", "instagram_content_publish", "not META_IG_USER_ID"):
            self.assertIn(want, problems)
        unlinked = self.run_check(self.GOOD_TOKEN, {"name": "The Scratch Sheet"})["problems"]
        self.assertIn("no Instagram Business/Creator account is linked to this Page", unlinked)

class MetaCarouselTests(unittest.TestCase):
    def test_instagram_carousel_and_facebook_album_call_order(self):
        from distribute import post_meta
        calls, n = [], iter(range(100))
        def fake_post(url, data=None, timeout=None):
            calls.append((url.rsplit("/", 1)[-1], dict(data)))
            r = mock.Mock(ok=True)
            r.json.return_value = {"id": f"id{next(n)}"}
            return r
        with mock.patch.object(post_meta, "PAGE_ID", "P"), mock.patch.object(post_meta, "IG_USER_ID", "IG"), \
             mock.patch.object(post_meta, "ACCESS_TOKEN", "T"), \
             mock.patch.object(post_meta.requests, "post", side_effect=fake_post), \
             mock.patch.object(post_meta, "_get", return_value={"status_code": "FINISHED", "permalink": "L"}):
            out = post_meta.post_carousel_to_instagram(["u1", "u2", "u3"], "cap")
            self.assertEqual([c[0] for c in calls], ["media"] * 4 + ["media_publish"])
            self.assertTrue(all(c[1]["is_carousel_item"] == "true" for c in calls[:3]))
            self.assertEqual((calls[3][1]["media_type"], calls[3][1]["children"], calls[3][1]["caption"]),
                             ("CAROUSEL", "id0,id1,id2", "cap"))
            self.assertEqual(out["permalink"], "L")
            with self.assertRaises(ValueError):
                post_meta.post_carousel_to_instagram(["only one"], "cap")
            calls.clear()
            post_meta.post_album_to_facebook(["u1", "u2"], "cap")
            self.assertEqual([c[0] for c in calls], ["photos", "photos", "feed"])
            self.assertEqual(calls[0][1]["published"], "false")
            self.assertEqual(calls[2][1]["attached_media[1]"], '{"media_fbid":"id' + "%d" % 6 + '"}')


class InstagramImageTests(unittest.TestCase):
    def test_png_cards_are_hosted_as_jpeg(self):
        from PIL import Image
        from distribute import image_host
        with tempfile.TemporaryDirectory() as d:
            png = os.path.join(d, "card.png")
            Image.new("RGBA", (40, 50), (10, 90, 60, 128)).save(png)
            jpg = image_host.to_jpeg(png)
            self.assertTrue(jpg.endswith("card.jpg"))
            with Image.open(jpg) as im:
                self.assertEqual((im.format, im.mode, im.size), ("JPEG", "RGB", (40, 50)))
            self.assertEqual(image_host.to_jpeg(jpg), jpg)

    def test_meta_check_makes_a_container_but_never_publishes(self):
        from distribute import post_meta
        token = MetaCheckTests.GOOD_TOKEN
        def fake_get(url, params=None, timeout=None):
            r = mock.MagicMock(ok=True)
            r.json.return_value = (token if url.endswith("/debug_token") else
                                   {"status_code": "FINISHED"} if url.endswith("/c1") else MetaCheckTests.PAGE)
            return r
        posted = mock.MagicMock(ok=True)
        posted.json.return_value = {"id": "c1"}
        with mock.patch.multiple(post_meta, PAGE_ID="111", ACCESS_TOKEN="tok", IG_USER_ID="222"), \
             mock.patch.object(post_meta.requests, "get", side_effect=fake_get), \
             mock.patch.object(post_meta.requests, "post", return_value=posted) as post:
            out = post_meta.check("https://example.test/card.jpg")
        self.assertEqual((out["instagram_test_container"], out["problems"]), ("FINISHED", []))
        self.assertEqual(post.call_count, 1)
        self.assertTrue(post.call_args[0][0].endswith("/222/media"))      # container only, no media_publish

class ReelMusicTests(unittest.TestCase):
    def _clip_dir(self, d, key, names=("a", "b")):
        import subprocess
        for n in names:   # a real 2 s tone, encrypted exactly like the committed clips
            raw = os.path.join(d, f"{n}.m4a")
            subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "sine=f=440:d=2",
                            "-c:a", "aac", raw], check=True)
            subprocess.run(["openssl", "enc", "-aes-256-cbc", "-pbkdf2", "-iter", "200000", "-salt",
                            "-pass", "env:AUDIO_KEY", "-in", raw, "-out", raw + ".enc"], check=True,
                           env={**os.environ, "AUDIO_KEY": key})
            os.remove(raw)

    def test_pick_clip_decrypts_rotates_and_needs_the_key(self):
        import datetime
        from distribute import music
        with tempfile.TemporaryDirectory() as d:
            self._clip_dir(d, "k3y")
            with mock.patch.object(music, "AUDIO_DIR", d):
                with mock.patch.dict(os.environ, {"AUDIO_KEY": ""}):
                    self.assertIsNone(music.pick_clip())                    # no key -> image post
                with mock.patch.dict(os.environ, {"AUDIO_KEY": "wrong"}):
                    self.assertIsNone(music.pick_clip())                    # bad key -> image post
                with mock.patch.dict(os.environ, {"AUDIO_KEY": "k3y"}):
                    t = datetime.datetime(2026, 10, 9, 14, tzinfo=datetime.timezone.utc)
                    a = music.pick_clip(t)
                    b = music.pick_clip(t + datetime.timedelta(hours=1))
        self.assertEqual({os.path.basename(a), os.path.basename(b)}, {"a.m4a", "b.m4a"})  # next hour, next track

    def test_reel_with_music_has_sound_and_the_videos_length(self):
        import subprocess
        from PIL import Image
        with tempfile.TemporaryDirectory() as d:
            card, tone = os.path.join(d, "card.png"), os.path.join(d, "tone.m4a")
            Image.new("RGB", (1080, 1350), (240, 234, 219)).save(card)
            subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "sine=f=440:d=9",
                            "-c:a", "aac", tone], check=True)
            out = render.render_reel([{"path": card, "seconds": 2}], os.path.join(d, "r.mp4"), audio=tone)
            probe = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "stream=codec_type,width,height",
                                    "-show_entries", "format=duration", "-of", "csv=p=0", out],
                                   capture_output=True, text=True).stdout
            self.assertFalse(os.path.exists(out + ".silent.mp4"))
        self.assertIn("video,1080,1920", probe)
        self.assertIn("audio", probe)
        self.assertAlmostEqual(float(probe.strip().splitlines()[-1]), 2.0, delta=0.15)

    def test_reel_check_never_publishes(self):
        from distribute import post_meta
        calls = []
        def fake_post(url, **kw):
            calls.append(url)
            r = mock.MagicMock(ok=True)
            r.json.return_value = {"id": "c9"}
            return r
        with tempfile.NamedTemporaryFile(suffix=".mp4") as v, \
             mock.patch.multiple(post_meta, ACCESS_TOKEN="tok", IG_USER_ID="222"), \
             mock.patch.object(post_meta.requests, "post", side_effect=fake_post), \
             mock.patch.object(post_meta, "_get", return_value={"status_code": "FINISHED"}):
            out = post_meta.post_reel_to_instagram(v.name, "x", publish=False)
        self.assertEqual(out["status"], "FINISHED")
        self.assertTrue(calls[0].endswith("/222/media") and "rupload.facebook.com" in calls[1])
        self.assertFalse(any(c.endswith("media_publish") for c in calls))

    def test_instagram_falls_back_to_image_when_reel_fails(self):
        with mock.patch.object(pipeline.post_meta, "post_reel_to_instagram", side_effect=RuntimeError("boom")), \
             mock.patch.object(pipeline.post_meta, "post_to_instagram", return_value={"id": "img"}) as img:
            out = pipeline._instagram_post("/tmp/r.mp4", "", "https://x/card.jpg", "cap")
        img.assert_called_once_with("https://x/card.jpg", "cap")
        self.assertIn("reel failed", out["note"])
        with mock.patch.object(pipeline.post_meta, "post_reel_to_instagram", return_value={"id": "reel"}) as reel, \
             mock.patch.object(pipeline.post_meta, "post_to_instagram") as img2:
            self.assertEqual(pipeline._instagram_post("/tmp/r.mp4", "", "u", "cap"), {"reel": {"id": "reel"}})
        reel.assert_called_once_with("/tmp/r.mp4", "cap", thumb_offset_ms=3000)
        img2.assert_not_called()
        with mock.patch.object(pipeline.post_meta, "post_to_instagram", return_value={"id": "img"}):   # no Reel built
            self.assertEqual(pipeline._instagram_post(None, "no music clip available", "u", "cap"),
                             {"image": {"id": "img"}, "note": "no music clip available"})

    def test_facebook_gets_the_same_reel_and_falls_back_to_the_photo(self):
        with mock.patch.object(pipeline.post_meta, "post_reel_to_facebook", return_value={"video_id": "v"}) as fb, \
             mock.patch.object(pipeline.post_meta, "post_to_facebook_page") as photo:
            self.assertEqual(pipeline._facebook_post("/tmp/r.mp4", "", "u", "cap"), {"reel": {"video_id": "v"}})
        fb.assert_called_once_with("/tmp/r.mp4", "cap")
        photo.assert_not_called()
        with mock.patch.object(pipeline.post_meta, "post_reel_to_facebook", side_effect=RuntimeError("boom")), \
             mock.patch.object(pipeline.post_meta, "post_to_facebook_page", return_value={"id": "p"}) as photo:
            out = pipeline._facebook_post("/tmp/r.mp4", "", "u", "cap")
        photo.assert_called_once_with("u", "cap")
        self.assertIn("reel failed", out["note"])

    def test_reel_is_built_once_for_both_platforms(self):
        with mock.patch.object(pipeline, "enabled_platforms", return_value={"facebook", "instagram"}), \
             mock.patch.object(pipeline.image_host, "publish_image", return_value="https://x/c.jpg"), \
             mock.patch.object(pipeline, "_build_reel", return_value=("/tmp/r.mp4", "")) as build, \
             mock.patch.object(pipeline.post_meta, "post_reel_to_facebook", return_value={"video_id": "v"}), \
             mock.patch.object(pipeline.post_meta, "post_reel_to_instagram", return_value={"id": "i"}):
            out = pipeline._post_everywhere("/tmp/card.png", "Bridgeman leads at -11 after round 2.", hook="BRIDGEMAN LEADS")
        build.assert_called_once_with("/tmp/card.png", "BRIDGEMAN LEADS")
        self.assertEqual((out["facebook"], out["instagram"]), ({"reel": {"video_id": "v"}}, {"reel": {"id": "i"}}))

    def test_facebook_reel_upload_flow(self):
        from distribute import post_meta
        calls = []
        def fake_post(url, data=None, headers=None, timeout=None):
            calls.append((url, dict(data) if isinstance(data, dict) else "BYTES"))
            r = mock.Mock(ok=True)
            r.json.return_value = {"video_id": "V1"}
            return r
        st = {"status": {"video_status": "upload_complete", "processing_phase": {"status": "complete"},
                         "publishing_phase": {"status": "complete"}}}
        with tempfile.NamedTemporaryFile(suffix=".mp4") as v, \
             mock.patch.object(post_meta, "PAGE_ID", "P"), mock.patch.object(post_meta, "ACCESS_TOKEN", "T"), \
             mock.patch.object(post_meta.requests, "post", side_effect=fake_post), \
             mock.patch.object(post_meta, "_get", return_value=st):
            v.write(b"video"); v.flush()
            out = post_meta.post_reel_to_facebook(v.name, "cap")
            self.assertEqual(out, {"video_id": "V1", "status": "upload_complete", "published": True})
            self.assertEqual(calls[0][1]["upload_phase"], "start")
            self.assertIn("rupload.facebook.com/video-upload/", calls[1][0])
            self.assertEqual((calls[2][1]["upload_phase"], calls[2][1]["video_state"], calls[2][1]["description"]),
                             ("finish", "PUBLISHED", "cap"))
            with mock.patch.object(post_meta, "_get", return_value={"status": {"video_status": "error"}}):
                with self.assertRaises(RuntimeError):
                    post_meta.post_reel_to_facebook(v.name, "cap")

class ReelHookTests(unittest.TestCase):
    def rows(self, *scores):
        names = ["Bridgeman, Jacob", "Smith, Jordan", "Meissner, Mac", "Castillo, Ricky"]
        return [{"player_name": n, "current_score": s, "current_pos": ""} for n, s in zip(names, scores)]

    def test_leader_hook_only_says_what_the_feed_says(self):
        self.assertEqual(transform.leader_hook(self.rows(-8, -7, -6)), "BRIDGEMAN LEADS BY 1")
        self.assertEqual(transform.leader_hook(self.rows(-8, -8, -6)), "BRIDGEMAN AND SMITH SHARE THE LEAD")
        self.assertEqual(transform.leader_hook(self.rows("-5", "-5", "-5", -4)), "3-WAY TIE AT -5")
        self.assertEqual(transform.leader_hook(self.rows(-18, -15), final=True), "BRIDGEMAN WINS AT -18")
        self.assertEqual(transform.leader_hook(self.rows(-18, -18), final=True), "")   # playoff: no claim
        self.assertEqual(transform.leader_hook(self.rows(0, 1)), "BRIDGEMAN LEADS BY 1")
        self.assertEqual(transform.leader_hook([]), "")

    def test_reel_with_hook_runs_the_full_length(self):
        import subprocess
        from PIL import Image
        with tempfile.TemporaryDirectory() as d:
            card, tone = os.path.join(d, "card.png"), os.path.join(d, "tone.m4a")
            Image.new("RGB", (1080, 1350), (240, 234, 219)).save(card)
            subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "sine=f=440:d=9",
                            "-c:a", "aac", tone], check=True)
            out = render.render_reel([{"path": card, "seconds": 2, "hook": "Bridgeman leads by 1", "zoom": 0.03,
                                       "closer": "stay tuned for more"}],
                                     os.path.join(d, "r.mp4"), audio=tone)
            dur = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", out],
                                 capture_output=True, text=True).stdout
        self.assertAlmostEqual(float(dur), 6.5, delta=0.15)   # 1.9 s hook + 2 s card + 2.6 s closer

class HoleTrackingTests(unittest.TestCase):
    def feed(self, thru, today, rnd=2, end_hole=18, course="YH"):
        return {"info": {"current_round": rnd}, "data": [{"dg_id": 7, "thru": thru, "today": today, "round": rnd,
                                                         "end_hole": end_hole, "course": course}]}

    def test_one_hole_between_polls_is_recorded_exactly(self):
        h = transform.track_holes({}, self.feed(3, -1))
        h = transform.track_holes(h, self.feed(4, -3))           # one hole, two better -> eagle on 4
        h = transform.track_holes(h, self.feed(5, -2))           # bogey on 5
        self.assertEqual(h["7"]["rel"], {"4": -2, "5": 1})

    def test_missed_poll_leaves_holes_blank_instead_of_guessing(self):
        h = transform.track_holes({}, self.feed(3, -1))
        h = transform.track_holes(h, self.feed(5, -3))           # two holes, -2 total: can't split it
        h = transform.track_holes(h, self.feed(6, -4))
        self.assertEqual(h["7"]["rel"], {"6": -1})

    def test_back_nine_starter_and_new_round(self):
        h = transform.track_holes({}, self.feed(0, 0, end_hole=9))
        h = transform.track_holes(h, self.feed(1, -1, end_hole=9))   # first hole played is the 10th
        self.assertEqual(h["7"]["rel"], {"10": -1})
        h = transform.track_holes(h, self.feed(0, 0, rnd=3))          # new round: fresh card
        self.assertEqual(h["7"]["rel"], {})

    def test_scorecard_shows_the_nine_with_pars_and_known_holes(self):
        stats = {"courses": [{"course_code": "YH", "rounds": [{"holes": [{"hole": n, "par": 3 if n in (3, 7) else 4}
                                                                          for n in range(1, 19)]}]}]}
        pars = transform.pars_from_hole_stats(stats)
        h = transform.track_holes({}, self.feed(1, 0))
        h = transform.track_holes(h, self.feed(2, -1))
        sc = transform.alert_scorecard("7", h, pars)
        self.assertEqual(sc["holes"], list(range(1, 10)))
        self.assertEqual(sc["pars"][:3], [4, 4, 3])
        self.assertEqual(sc["rel"][:3], [None, -1, None])
        h = transform.track_holes(h, self.feed(10, -3))               # on the back nine now
        self.assertEqual(transform.alert_scorecard("7", h, pars)["holes"], list(range(10, 19)))

class MultiTourTests(PipelineDryRunTests):
    def test_tours_keep_separate_state(self):
        pipeline.run_pretournament_picks(dry_run=True, tour="pga")
        state.save({"leader_name": "Someone Euro"}, commit=False, tour="euro")
        self.assertIn("last_picks", state.load("pga"))
        self.assertNotIn("last_picks", state.load("euro"))
        self.assertEqual(state.load("euro")["leader_name"], "Someone Euro")
        self.assertNotEqual(state.state_path("pga"), state.state_path("euro"))
        self.assertTrue(state.state_path("pga").endswith("_last_snapshot.json"))   # PGA keeps the original file name

    def test_dp_world_cards_and_captions_name_the_tour(self):
        with mock.patch.object(pipeline, "render_weekly_picks") as rp:
            pipeline.run_pretournament_picks(dry_run=True, tour="euro")
        self.assertTrue(rp.call_args.kwargs["event_name"].startswith("DP WORLD TOUR · "))
        self.assertIn("Tour: DP World Tour", self.llm_prompts[-1])
        with mock.patch.object(pipeline, "render_leaderboard") as rl:
            pipeline.run_live_poll(dry_run=True, min_leaderboard_gap_minutes=0, tour="euro")
        self.assertEqual(rl.call_args.kwargs["round_label"], "DP WORLD TOUR · ROUND 4 · LIVE")
        with mock.patch.object(pipeline, "render_leaderboard") as rl:
            pipeline.run_live_poll(dry_run=True, min_leaderboard_gap_minutes=0, tour="pga")
        self.assertEqual(rl.call_args.kwargs["round_label"], "ROUND 4 · LIVE")   # PGA cards unchanged

    def test_state_push_retries_after_a_rejected_push(self):
        results = iter([mock.Mock(returncode=0),                      # add
                        mock.Mock(returncode=0),                      # commit
                        mock.Mock(returncode=0), mock.Mock(returncode=1),   # pull, push rejected
                        mock.Mock(returncode=0), mock.Mock(returncode=0)])  # pull, push ok
        with mock.patch.object(state.subprocess, "run", side_effect=lambda *a, **k: next(results)) as run:
            state.save({"x": 1}, commit=True, tour="euro")
        cmds = [c.args[0][:2] for c in run.call_args_list]
        self.assertEqual(cmds.count(["git", "push"]), 2)
        self.assertIn(["git", "pull"], cmds)

    def test_probe_reports_each_endpoint_without_raising(self):
        with mock.patch.object(datagolf, "get_dg_rankings", side_effect=RuntimeError("403 Forbidden key=SECRET")), \
             mock.patch.object(datagolf, "get_skill_ratings", return_value={"players": [{"player_name": "A", "sg_app": 1}]}), \
             mock.patch.object(datagolf, "API_KEY", "SECRET"):
            out = pipeline.probe("euro")
        self.assertFalse(out["dg_rankings"]["ok"])
        self.assertNotIn("SECRET", out["dg_rankings"]["error"])
        self.assertTrue(out["skill_ratings"]["ok"])
        self.assertEqual(out["skill_ratings"]["rows"], 1)
        self.assertEqual(out["pre_tournament"]["event_name"], "Fake Invitational")

RANKINGS = {"rankings": [
    {"dg_id": 1, "player_name": "Top, Guy", "datagolf_rank": 1, "owgr_rank": 1, "primary_tour": "PGA"},
    {"dg_id": 2, "player_name": "Rahm, Jon", "datagolf_rank": 3, "owgr_rank": 73, "primary_tour": "LIV"},
    {"dg_id": 3, "player_name": "Unranked, Al", "datagolf_rank": 5, "owgr_rank": None, "primary_tour": "LIV"},
    {"dg_id": 4, "player_name": "Deep, Field", "datagolf_rank": 80, "owgr_rank": 900, "primary_tour": "KFT"}]}
SKILLS = {"players": [
    {"dg_id": 1, "player_name": "Top, Guy", "sg_app": 1.42, "sg_ott": 0.8, "sg_putt": 0.2, "sg_arg": 0.3},
    {"dg_id": 5, "player_name": "Putter, Pete", "sg_app": 0.1, "sg_ott": 0.1, "sg_putt": 1.05, "sg_arg": 0.4}]}


class WeeklyIntelTests(PipelineDryRunTests):
    def test_underrated_uses_top_50_with_real_world_rank(self):
        pick = transform.weekly_intel(RANKINGS, SKILLS, week=0)     # rotation slot 0 = underrated
        self.assertEqual(pick["stat"], "70")                        # 73 - 3; skips None OWGR and DG #80
        self.assertEqual(pick["what_it_means"], "JON RAHM: DATAGOLF #3, WORLD #73")
        self.assertIn("#73", pick["facts"])

    def test_rotation_and_sg_leaders(self):
        app = transform.weekly_intel(RANKINGS, SKILLS, week=1)                                  # sg_app
        self.assertEqual(app["stat"], "+1.42")
        self.assertIn("Second is Pete Putter at +0.10, so the lead is 1.32", app["facts"])   # comparisons grounded
        putt = transform.weekly_intel(RANKINGS, SKILLS, week=3)
        self.assertEqual((putt["stat"], putt["what_it_means"]), ("+1.05", "PETE PUTTER: SG PUTTING PER ROUND"))

    def test_falls_through_when_an_angle_has_no_data(self):
        pick = transform.weekly_intel({"rankings": []}, SKILLS, week=0)   # no rankings -> next angle
        self.assertEqual(pick["kind"], "sg_app")

    def test_intel_dry_run_renders_card_with_model_line(self):
        with mock.patch.object(datagolf, "get_dg_rankings", return_value=RANKINGS, create=True), \
             mock.patch.object(datagolf, "get_skill_ratings", return_value=SKILLS, create=True), \
             mock.patch.object(pipeline, "render_intel_stat") as ri:
            out = pipeline.run_weekly_intel(dry_run=True, week=1)
        kw = ri.call_args.kwargs
        self.assertEqual((kw["stat"], kw["supporting_line"]), ("+1.42", "the irons are just unfair right now"))
        self.assertIn("Guy Top", out["intel"])
        with open(out["caption_file"], encoding="utf-8") as f:
            self.assertIn("(card line) other line", f.read())

class PreviewCarouselTests(PipelineDryRunTests):
    def setUp(self):
        super().setUp()
        skills = {"players": [{"dg_id": 1000 + i, "player_name": f"Player{i:02d}, Test",
                               "sg_ott": 1.0 - i * 0.02, "sg_app": 0.5 + (0.3 if i == 7 else 0), "sg_arg": 0.1,
                               "sg_putt": 0.2 - i * 0.01} for i in range(40)]}
        self.skills = mock.patch.object(datagolf, "get_skill_ratings", return_value=skills, create=True)
        self.skills.start()

    def tearDown(self):
        self.skills.stop()
        super().tearDown()

    def test_helpers(self):
        self.assertEqual(transform.format_odds(30.0), "29-1")
        self.assertEqual(transform.format_odds(1.8), "0.8-1")
        prof = transform.skill_profile(datagolf.get_skill_ratings(), 1007)
        self.assertEqual(prof["APP"], ("+0.80", "#1"))        # player 7 has the best approach
        self.assertEqual(prof["OTT"], ("+0.86", "#8"))
        preds, _ = fake_field()
        self.assertEqual(transform.favorites(preds)[0], ("Test Player00", "16.7%"))
        self.assertEqual(transform.course_fit_boost(preds), [])   # fixture models identical -> no slide

    def test_course_fit_uses_model_difference(self):
        preds, _ = fake_field()
        fit = [dict(r) for r in preds["baseline_history_fit"]]
        fit[5]["win"] = 10.0                                   # 26 -> 10 decimal: +6.2 pts for player 5
        boost = transform.course_fit_boost({**preds, "baseline_history_fit": fit})
        self.assertEqual(boost[0][:2], ("Test Player05", "+6.2 pts"))

    def test_picks_run_builds_numbered_carousel(self):
        out = pipeline.run_pretournament_picks(dry_run=True)
        car = out["carousel"]
        self.assertEqual(car["slides"], ["01_hand.png", "02_picks.png", "03_win.png", "04_value.png", "05_fade.png",
                                         "06_sleeper.png", "08_favorites.png", "09_closer.png"])
        for folder in (car["carousel_dir"], car["brand_dir"]):
            for name in car["slides"] + ["caption.txt"]:
                self.assertTrue(os.path.exists(os.path.join(folder, name)), (folder, name))
        with open(car["caption_file"], encoding="utf-8") as f:
            text = f.read()
        self.assertIn("#pgatour", text)
        import shutil
        if shutil.which("ffmpeg"):
            self.assertTrue(car["reel"] and os.path.getsize(car["reel"]) > 10_000)
        self.assertEqual(text.count("#golfpicks"), 1)
        self.assertIn("Song (add in the Instagram app):", text)
        self.assertEqual(pipeline.song_suggestions(["a", "b", "c", "d"], week=3), ["d", "a", "b"])
        win_facts = next(p for p in self.llm_prompts if "is our WIN pick" in p)
        self.assertIn("Strokes gained per round (world rank)", win_facts)

    def test_cta_goes_before_hashtags_and_respects_x_limit(self):
        self.assertEqual(pipeline.with_cta("Picks are in. #USOpen #golfpicks", "Who's your winner?"),
                         "Picks are in.\n\nWho's your winner? #USOpen #golfpicks")
        self.assertEqual(pipeline.with_cta("Plain.", "Q?"), "Plain.\n\nQ?")
        long = "x" * 270 + " #golfpicks"
        self.assertEqual(pipeline.with_cta(long, "Who's your winner this week?"), long)   # wouldn't fit -> unchanged

    def test_books_odds_have_vig_removed(self):
        odds = {"odds": [{"dg_id": i, "player_name": f"P{i}", "a": 4.0, "b": 4.0, "c": 4.0} for i in range(5)]}
        m = transform._market_by_player(odds)       # raw 25% each = 125% total -> 20% each
        self.assertAlmostEqual(sum(v["implied"] for v in m.values()), 1.0)
        self.assertAlmostEqual(m[0]["implied"], 0.20)

    def test_no_value_pick_when_model_never_beats_books(self):
        preds = {"baseline_history_fit": [{"dg_id": i, "player_name": f"P{i}, T", "win": 10.0} for i in range(12)]}
        odds = {"odds": [{"dg_id": i, "player_name": f"P{i}", "a": 10.0, "b": 10.0, "c": 10.0} for i in range(12)]}
        with self.assertRaises(transform.PicksError):   # model 10% = books 10% (no-vig) for everyone
            transform.choose_picks(preds, odds)

    def test_event_names_match_across_sponsor_and_accents(self):
        sched = {"schedule": [{"event_name": "acciona Open de Espana", "start_date": "2026-10-08"},
                              {"event_name": "Open de Portugal", "start_date": "2026-10-15"}]}
        ev = transform.check_picks_window("Open de España presented by Madrid", sched, today=date(2026, 10, 6))
        self.assertEqual(ev["start_date"], "2026-10-08")
        self.assertEqual(transform.event_key("Open de España presented by Madrid"), "open de espana")

    def test_note_cannot_flip_model_vs_books(self):
        facts = "The model is LOWER than the books on him (model 1.4% vs books 2.0%, books' margin removed)."
        self.assertEqual(content.wrong_direction("model loves him more than Vegas does at 50-1", facts),
                         ["more than vegas", "than vegas does"])
        self.assertEqual(content.wrong_direction("a longshot with a real chance", facts), [])

    def test_short_event_name_and_spacing_shrinks(self):
        self.assertEqual(transform.short_event_name("Open de España presented by Madrid"), "Open de España")
        self.assertEqual(transform.short_event_name("Baycurrent Classic"), "Baycurrent Classic")
        svg = ('<svg xmlns="http://www.w3.org/2000/svg"><text font-family="Barlow SemiBold" font-size="24" '
               'letter-spacing="4" data-fit="200">WIN PICK · DP WORLD TOUR · A VERY LONG EVENT NAME</text></svg>')
        out = render._apply_fit_and_score(svg, "t.svg")
        self.assertIn('letter-spacing="', out)
        self.assertNotIn('letter-spacing="4"', out)

    def test_telegram_drafts_off_without_secrets_and_on_with_them(self):
        from distribute import notify_telegram
        with mock.patch.dict(os.environ, {"TELEGRAM_DRAFTS_TOKEN": "", "TELEGRAM_DRAFTS_CHAT_ID": ""}):
            self.assertTrue(notify_telegram.send_text("hi").startswith("skipped"))
        ok = mock.Mock(ok=True)
        with mock.patch.dict(os.environ, {"TELEGRAM_DRAFTS_TOKEN": "T", "TELEGRAM_DRAFTS_CHAT_ID": "42"}), \
             mock.patch.object(notify_telegram.requests, "post", return_value=ok) as post:
            self.assertEqual(notify_telegram.send_text("hi"), "sent")
            self.assertEqual(post.call_args.kwargs["data"]["chat_id"], "42")
            bad = mock.Mock(ok=False, status_code=400)
            bad.json.return_value = {"description": "Bad Request: chat not found for T"}
            post.return_value = bad
            self.assertEqual(notify_telegram.send_text("hi"), "FAILED: Telegram 400 (Bad Request: chat not found for [token])")

    def test_quiet_hours_hold_live_pings_only(self):
        import datetime
        from zoneinfo import ZoneInfo
        from distribute import notify_telegram
        la = ZoneInfo("America/Los_Angeles")
        with mock.patch.dict(os.environ, {"QUIET_TZ": "", "QUIET_START": "", "QUIET_END": ""}):
            self.assertTrue(notify_telegram.quiet_now(datetime.datetime(2026, 10, 8, 2, 0, tzinfo=la)))
            self.assertTrue(notify_telegram.quiet_now(datetime.datetime(2026, 10, 8, 23, 30, tzinfo=la)))
            self.assertFalse(notify_telegram.quiet_now(datetime.datetime(2026, 10, 8, 7, 0, tzinfo=la)))
            self.assertFalse(notify_telegram.quiet_now(datetime.datetime(2026, 10, 8, 21, 59, tzinfo=la)))
        with mock.patch.object(notify_telegram, "quiet_now", return_value=True):
            self.assertEqual(notify_telegram.send_file("x.png", "c", respect_quiet=True), "held (quiet hours)")

    def _auto_poll(self, rows, prev, rnd=2, final_thru=None, qa=(True, "PASS"), refetch=None, qa_fn=None):
        live = {"info": {"event_name": "Fake Invitational", "current_round": rnd}, "data": rows}
        feeds = [live] + ([refetch] if refetch else [live] * 3)
        posts = []
        def fake_post(path, caption, **kw):
            posts.append({"path": os.path.basename(path), "caption": caption, "draft": kw.get("draft"), "dry": kw.get("dry_run")})
            return {"DRAFT": True} if kw.get("draft") else {"platforms": ["facebook", "instagram"]}
        with mock.patch.object(datagolf, "get_live_in_play", side_effect=feeds + [live] * 3), \
             mock.patch.object(state, "load", return_value=prev), \
             mock.patch.object(state, "save") as saved, \
             mock.patch.object(content, "qa_review", **({"side_effect": qa_fn} if qa_fn else {"return_value": qa})) as self.qa_mock, \
             mock.patch.object(pipeline, "_post_everywhere", side_effect=fake_post), \
             mock.patch.object(content, "generate_live_reaction") as writer, \
             mock.patch.object(content, "generate_hot_take", return_value={"lines": ["A", "B", "C", "D"], "kicker": "k", "alternatives": []}), \
             mock.patch.object(pipeline, "render_live_alert"), mock.patch.object(pipeline, "render_hot_take"), \
             mock.patch.object(pipeline, "render_leaderboard"):
            pipeline.run_live_poll(dry_run=False, draft=False)
        return posts, writer, saved.call_args.args[0]

    def test_auto_mode_posts_everything_that_passes_qa(self):
        prev = {"event_name": "Fake Invitational", "leader_name": "Old Leader", "last_leaderboard_post_ts": 0,
                "live_scores": {"3": {"name": "Cy Hot", "pos": "2", "score": -11, "today": -4, "thru": 10, "rank": 2}}}
        rows = [{"dg_id": 3, "player_name": "Hot, Cy", "current_pos": "1", "current_score": -14, "today": -7, "thru": 11}]
        posts, writer, saved = self._auto_poll(rows, prev)
        by = {p["path"]: p for p in posts}
        self.assertFalse(by["hot_take_live.png"]["draft"])                   # lead change -> QA passed -> posts
        self.assertEqual(by["hot_take_live.png"]["caption"], "A b c d\n\nK")  # full take, not just the kicker
        self.assertNotIn("live_alert.png", by)                               # same story: the alert waits
        self.assertNotIn("leaderboard_live.png", by)                         # waits: something already posted this poll
        self.assertEqual(self.qa_mock.call_count, 1)                         # every unattended post was reviewed
        self.assertIn("lead_change_verified_by_code", self.qa_mock.call_args_list[0].args[0])

    def test_auto_alert_posts_data_only_and_waits_after_any_post(self):
        prev = {"event_name": "Fake Invitational", "leader_name": "Cy Hot", "last_leaderboard_post_ts": 9e12,
                "live_scores": {"3": {"name": "Cy Hot", "pos": "1", "score": -11, "today": -4, "thru": 10, "rank": 1}}}
        rows = [{"dg_id": 3, "player_name": "Hot, Cy", "current_pos": "1", "current_score": -14, "today": -7, "thru": 11}]
        posts, writer, saved = self._auto_poll(rows, prev)
        self.assertEqual([p["path"] for p in posts], ["live_alert.png"])
        self.assertFalse(posts[0]["draft"])                                  # alert really posts
        self.assertEqual(posts[0]["caption"], "Cy Hot goes 3 under on one hole (round 2, thru 11). Now 1 at -14.")
        writer.assert_not_called()                                           # no AI wording when unattended
        self.assertGreater(saved["last_public_post_ts"], 0)
        import time as _t                                                    # a leaderboard 6 min ago -> alert waits
        posts, _, _ = self._auto_poll(rows, {**prev, "last_public_post_ts": _t.time() - 360})
        self.assertEqual(posts, [])

    def test_auto_leaderboard_falls_back_to_data_only_caption_when_qa_rejects_the_ai_one(self):
        rows = [{"dg_id": 3, "player_name": "Hot, Cy", "current_pos": "1", "current_score": -14, "today": -7, "thru": 11},
                {"dg_id": 4, "player_name": "Two, Bo", "current_pos": "2", "current_score": -13, "today": -2, "thru": 11}]
        prev = {"event_name": "Fake Invitational", "leader_name": "Cy Hot", "last_leaderboard_post_ts": 0,
                "live_scores": {"3": {"name": "Cy Hot", "pos": "1", "score": -14, "today": -7, "thru": 11, "rank": 1},
                                "4": {"name": "Bo Two", "pos": "2", "score": -13, "today": -2, "thru": 11, "rank": 2}}}
        with mock.patch.object(pipeline, "MAX_ALERTS_PER_HOUR", 0):
            posts, _, _ = self._auto_poll(rows, prev, qa=(False, "FAIL: running away"))
        self.assertTrue(posts[0]["draft"])                                    # both fail -> still held
        def qa(raw, text):
            return (False, "FAIL: running away") if "running away" in text or "CAPTION: k" in text \
                else (("Fake Invitational, round 2: Cy Hot leads at -14" in text), "PASS")
        with mock.patch.object(pipeline, "MAX_ALERTS_PER_HOUR", 0), \
             mock.patch.object(pipeline, "_caption", return_value=("Cy Hot's running away with it.", [])):
            posts, _, _ = self._auto_poll(rows, prev, qa=None, qa_fn=qa)
        self.assertFalse(posts[0]["draft"])
        self.assertEqual(posts[0]["caption"], "Fake Invitational, round 2: Cy Hot leads at -14, 1 shot clear of Bo Two (-13).")

    def test_auto_hourly_leaderboard_posts_when_nothing_else_did(self):
        rows = [{"dg_id": 3, "player_name": "Hot, Cy", "current_pos": "1", "current_score": -14, "today": -7, "thru": 11}]
        prev = {"event_name": "Fake Invitational", "leader_name": "Cy Hot", "last_leaderboard_post_ts": 0,
                "live_scores": {"3": {"name": "Cy Hot", "pos": "1", "score": -14, "today": -7, "thru": 11, "rank": 1}},
}
        with mock.patch.object(pipeline, "MAX_ALERTS_PER_HOUR", 0):          # no alert this poll
            posts, _, saved = self._auto_poll(rows, prev)
        self.assertEqual([p["path"] for p in posts], ["leaderboard_live.png"])
        self.assertFalse(posts[0]["draft"])
        self.assertGreater(saved["last_public_post_ts"], 0)
        import time as _t                                                    # an alert 10 min ago -> leaderboard waits
        with mock.patch.object(pipeline, "MAX_ALERTS_PER_HOUR", 0):
            posts, _, _ = self._auto_poll(rows, {**prev, "last_public_post_ts": _t.time() - 600})
        self.assertEqual(posts, [])

    def test_broken_caption_never_posts_for_real(self):
        self.assertIn("mid-sentence", transform.caption_problem("— one shot back is close enough to stay in this fight"))
        self.assertIn("too short", transform.caption_problem("Cy leads."))
        self.assertEqual(transform.caption_problem("Cy Hot goes 3 under on one hole (round 2, thru 11). Now 1 at -14."), "")
        with mock.patch.object(pipeline.notify_telegram, "send_file", return_value=None), \
             mock.patch.object(pipeline.image_host, "publish_image") as host:
            out = pipeline._post_everywhere(os.path.join(tempfile.mkdtemp(), "x.png"), "— one shot back is close enough to stay in this fight")
        host.assert_not_called()
        self.assertTrue(out["DRAFT"])
        self.assertTrue(out["would_post_caption"].startswith("QA HELD (caption starts mid-sentence"))

    def test_meta_captions_get_a_tag_line_and_x_does_not(self):
        self.assertEqual(pipeline.hashtags("pga", "Baycurrent Classic"), "#golf #PGATOUR #BaycurrentClassic")
        self.assertEqual(pipeline.hashtags("euro", "Open de España presented by Madrid", "#OpenDeEspana"),
                         "#golf #DPWorldTour #OpenDeEspana")                  # sponsor dropped, no duplicate tag
        self.assertEqual(pipeline.with_tags("Cy leads at -14. #USOpen", "#golf #USOpen"), "Cy leads at -14. #USOpen\n\n#golf")
        cap, tags = "Bridgeman leads at -11 after round 2.", "#golf #PGATOUR #BaycurrentClassic"
        with mock.patch.object(pipeline, "enabled_platforms", return_value={"x", "facebook", "instagram"}), \
             mock.patch.object(pipeline.post_x, "post_image", return_value={"id": "1"}) as px, \
             mock.patch.object(pipeline.image_host, "publish_image", return_value="https://x/c.jpg"), \
             mock.patch.object(pipeline, "_build_reel", return_value=("/tmp/r.mp4", "")), \
             mock.patch.object(pipeline.post_meta, "post_reel_to_facebook", return_value={"video_id": "v"}) as fb, \
             mock.patch.object(pipeline.post_meta, "post_reel_to_instagram", return_value={"id": "i"}) as ig:
            pipeline._post_everywhere("/tmp/card.png", cap, tags=tags)
        px.assert_called_once_with("/tmp/card.png", cap)
        self.assertEqual(fb.call_args.args[1], f"{cap}\n\n{tags}")
        self.assertEqual(ig.call_args.args[1], f"{cap}\n\n{tags}")

    def test_tied_leaders_swapping_order_is_not_a_lead_change(self):
        tied = [{"dg_id": 1, "player_name": "Syme, Connor", "current_pos": "T1", "current_score": -6, "today": -2, "thru": 6},
                {"dg_id": 2, "player_name": "Olesen, Thorbjorn", "current_pos": "T1", "current_score": -6, "today": -3, "thru": 9}]
        prev = {"event_name": "Fake Invitational", "leader_name": "Connor Syme", "last_leaderboard_post_ts": 9e12}
        posts, _, saved = self._auto_poll(list(reversed(tied)), prev)
        self.assertEqual([p for p in posts if p["path"] == "hot_take_live.png"], [])
        self.assertEqual(saved["leader_name"], "Connor Syme")               # keeps the last outright leader
        ahead = [{**tied[1], "current_pos": "1", "current_score": -7}, {**tied[0], "current_pos": "2"}]
        posts, _, saved = self._auto_poll(ahead, prev)                       # Olesen goes clear -> real change
        self.assertEqual(len([p for p in posts if p["path"] == "hot_take_live.png"]), 1)
        self.assertEqual(saved["leader_name"], "Thorbjorn Olesen")

    def test_auto_mode_holds_ai_posts_when_qa_fails(self):
        prev = {"event_name": "Fake Invitational", "leader_name": "Old Leader", "last_leaderboard_post_ts": 0}
        rows = [{"dg_id": 3, "player_name": "Hot, Cy", "current_pos": "1", "current_score": -14, "today": -7, "thru": 11}]
        posts, _, _ = self._auto_poll(rows, prev, qa=(False, "FAIL: off-voice"))
        by = {p["path"]: p for p in posts}
        for name in ("hot_take_live.png", "leaderboard_live.png"):
            self.assertTrue(by[name]["draft"])
            self.assertTrue(by[name]["caption"].startswith("QA HELD (FAIL: off-voice"))
        self.assertIn("data-only caption also failed", by["leaderboard_live.png"]["caption"])

    def test_auto_mode_final_leaderboard_posts_once(self):
        rows = [{"dg_id": 1, "player_name": "Win, Al", "current_pos": "1", "current_score": -20, "today": -3, "thru": 18},
                {"dg_id": 2, "player_name": "Two, Bo", "current_pos": "2", "current_score": -18, "today": -1, "thru": 18}]
        prev = {"event_name": "Fake Invitational", "leader_name": "Al Win", "last_leaderboard_post_ts": 0}
        posts, _, saved = self._auto_poll(rows, prev, rnd=4)
        final = [p for p in posts if p["path"] == "leaderboard_live.png"]
        self.assertEqual(len(final), 1)
        self.assertFalse(final[0]["draft"])
        self.assertEqual(final[0]["caption"], "Fake Invitational final: Al Win wins at -20. Then: 2 Bo Two -18.")
        self.assertEqual(saved["final_posted"], "Fake Invitational")
        posts2, _, _ = self._auto_poll(rows, {**prev, "final_posted": "Fake Invitational"}, rnd=4)
        self.assertEqual([p for p in posts2 if p["path"] == "leaderboard_live.png"], [])

    def test_alert_cap_is_two_per_rolling_hour(self):
        import time as _t
        rows = [{"dg_id": 3, "player_name": "Hot, Cy", "current_pos": "1", "current_score": -14, "today": -7, "thru": 11}]
        live_prev = {"3": {"name": "Cy Hot", "pos": "2", "score": -11, "today": -4, "thru": 10, "rank": 2}}
        now = _t.time()
        def poll(times):
            prev = {"event_name": "Fake Invitational", "leader_name": "Cy Hot", "last_leaderboard_post_ts": 9e12,
                    "live_scores": live_prev,
                    "alerts": {"round": 2, "sent": [], "last_ts": now - 25 * 60, "times": times}}
            posts, _, saved = self._auto_poll(rows, prev)
            return any(p["path"] == "live_alert.png" for p in posts), saved
        posted, saved = poll([now - 25 * 60])                     # 1 in the last hour -> 2nd allowed
        self.assertTrue(posted)
        self.assertEqual(len(saved["alerts"]["times"]), 2)
        posted, _ = poll([now - 50 * 60, now - 25 * 60])          # 2 in the last hour -> held
        self.assertFalse(posted)
        posted, _ = poll([now - 70 * 60, now - 25 * 60])          # oldest rolled off -> allowed
        self.assertTrue(posted)

    def test_auto_alert_held_as_draft_when_qa_fails(self):
        prev = {"event_name": "Fake Invitational", "leader_name": "Cy Hot", "last_leaderboard_post_ts": 9e12,
                "live_scores": {"3": {"name": "Cy Hot", "pos": "2", "score": -11, "today": -4, "thru": 10, "rank": 2}}}
        rows = [{"dg_id": 3, "player_name": "Hot, Cy", "current_pos": "1", "current_score": -14, "today": -7, "thru": 11}]
        posts, _, _ = self._auto_poll(rows, prev, qa=(False, "FAIL: position wrong"))
        alert = next(p for p in posts if p["path"] == "live_alert.png")
        self.assertTrue(alert["draft"])
        self.assertTrue(alert["caption"].startswith("QA HELD (FAIL: position wrong)"))
        raw = self.qa_mock.call_args.args[0]
        self.assertIn('"prev_score": -11', raw)                      # reviewer sees the previous poll too

    def test_auto_alert_held_when_fresh_feed_disagrees(self):
        prev = {"event_name": "Fake Invitational", "leader_name": "Cy Hot", "last_leaderboard_post_ts": 9e12,
                "live_scores": {"3": {"name": "Cy Hot", "pos": "2", "score": -11, "today": -4, "thru": 10, "rank": 2}}}
        rows = [{"dg_id": 3, "player_name": "Hot, Cy", "current_pos": "1", "current_score": -14, "today": -7, "thru": 11}]
        moved = {"info": {"event_name": "Fake Invitational", "current_round": 2},
                 "data": [{"dg_id": 3, "player_name": "Hot, Cy", "current_pos": "1", "current_score": -13, "today": -6, "thru": 11}]}
        posts, _, _ = self._auto_poll(rows, prev, refetch=moved)
        alert = next(p for p in posts if p["path"] == "live_alert.png")
        self.assertTrue(alert["draft"])
        self.assertIn("score changed since detection", alert["caption"])
        self.qa_mock.assert_not_called()                              # code check failed first; no AI call

    def _wrap_live(self, thru_last=18):
        rows = [{"dg_id": 1, "player_name": "Bridgeman, Jacob", "current_pos": "1", "current_score": -7, "today": -7, "thru": 18},
                {"dg_id": 2, "player_name": "Schauffele, Xander", "current_pos": "T14", "current_score": -2, "today": -2, "thru": 18},
                {"dg_id": 3, "player_name": "Gerard, Ryan", "current_pos": "T31", "current_score": 0, "today": 0, "thru": "F"},
                {"dg_id": 4, "player_name": "Hisatsune, Ryo", "current_pos": "T52", "current_score": 1, "today": 1, "thru": thru_last},
                {"dg_id": 5, "player_name": "Gone, Guy", "current_pos": "WD", "current_score": 5, "today": 5, "thru": 9}]
        return {"info": {"event_name": "Baycurrent Classic", "current_round": 1}, "data": rows}

    PICKS = {"event_name": "Baycurrent Classic", "picks": {
        "win": {"name": "Xander Schauffele", "dg_id": 2}, "value": {"name": "Ryan Gerard", "dg_id": 3},
        "fade": {"name": "Jacob Bridgeman", "dg_id": 1}, "sleeper": {"name": "Ryo Hisatsune", "dg_id": 4}}}

    def test_thru_is_strict_about_tee_times(self):
        for raw, want in [("F", 18), (18, 18), ("12", 12), ("9*", 9), ("1:20 PM", 0), ("", 0), (None, 0), ("120", 0)]:
            self.assertEqual(transform._thru_int(raw), want, raw)
        live = self._wrap_live()
        live["data"][3]["thru"] = "1:20 PM"          # hasn't teed off yet
        self.assertFalse(transform.round_complete(live))

    def test_round_complete_ignores_withdrawn_players(self):
        self.assertTrue(transform.round_complete(self._wrap_live()))
        self.assertFalse(transform.round_complete(self._wrap_live(thru_last=16)))

    def test_round_wrap_is_data_only_and_owns_the_fade(self):
        w = transform.round_wrap(self._wrap_live(), self.PICKS)
        self.assertEqual((w["leader"], w["leader_score"]), ("Jacob Bridgeman", "-7"))
        self.assertEqual(w["picks"][2], ("Jacob Bridgeman", "1", "-7"))
        self.assertEqual(w["note"], "our fade is leading. we'll own that sunday.")
        self.assertEqual(w["caption"], "Baycurrent Classic after round 1: Jacob Bridgeman leads at -7. Our card: "
                         "WIN Schauffele T14 · VALUE Gerard T31 · FADE Bridgeman 1 · SLEEPER Hisatsune T52. "
                         "Low round: Jacob Bridgeman -7.")

    def test_round_wrap_posts_once_and_waits_out_quiet_hours(self):
        from distribute import notify_telegram
        live = self._wrap_live()
        prev = {"event_name": "Baycurrent Classic", "leader_name": "Jacob Bridgeman", "last_leaderboard_post_ts": 9e12,
                "last_picks": self.PICKS}
        def poll(prev_state, quiet):
            with mock.patch.object(datagolf, "get_live_in_play", return_value=live), \
                 mock.patch.object(state, "load", return_value=prev_state), \
                 mock.patch.object(state, "save") as saved, \
                 mock.patch.object(notify_telegram, "quiet_now", return_value=quiet), \
                 mock.patch.object(pipeline, "render_round_wrap") as card, \
                 mock.patch.object(pipeline, "_post_carousel_everywhere", return_value={"DRAFT": True}) as post:
                out = pipeline.run_live_poll(draft=True)
            return dict(out["actions"]), card, post, saved.call_args.args[0]
        acts, card, post, saved = poll(prev, quiet=True)
        self.assertIn("held until quiet hours end", acts["round_wrap"])
        card.assert_not_called()
        acts, card, post, saved = poll(prev, quiet=False)
        card.assert_called_once()                                   # the "our card" slide of the recap
        slides = [os.path.basename(x) for x in post.call_args.args[0]]
        self.assertEqual(slides, ["01_leaderboard.png", "03_low_rounds.png", "04_our_card.png", "05_closer.png"])
        self.assertEqual(saved["wraps"], [1])
        acts, card, post, saved = poll({**prev, "wraps": [1]}, quiet=False)
        self.assertNotIn("round_wrap", acts)

    def _r2_live(self):
        # round 2: totals and today's scores -> positions before today vs now
        rows = [{"dg_id": 1, "player_name": "Lead, Al", "current_pos": "T1", "current_score": -9, "today": -2, "thru": 18},
                {"dg_id": 2, "player_name": "Tie, Bo", "current_pos": "T1", "current_score": -9, "today": -3, "thru": 18},
                {"dg_id": 3, "player_name": "Climb, Cy", "current_pos": "3", "current_score": -8, "today": -8, "thru": 18},
                {"dg_id": 4, "player_name": "Drop, Di", "current_pos": "4", "current_score": -1, "today": 6, "thru": 18},
                {"dg_id": 5, "player_name": "Flat, Ed", "current_pos": "5", "current_score": 0, "today": 0, "thru": 18}]
        return {"info": {"event_name": "Fake Open", "current_round": 2}, "data": rows}

    def test_live_caption_is_data_only(self):
        top5 = [{"pos": "1", "name": "Grant Forrest", "score": "-9"}, {"pos": "T2", "name": "Joel Girrbach", "score": "-8"},
                {"pos": "T2", "name": "Connor Syme", "score": "-8"}, {"pos": "T4", "name": "A B", "score": "-7"}]
        self.assertEqual(transform.live_caption("Open de España", 2, top5),
                         "Open de España, round 2: Grant Forrest leads at -9, 1 shot clear of Joel Girrbach and Connor Syme (-8).")
        tied = [{"pos": "T1", "name": "Jacob Bridgeman", "score": "-11"}, {"pos": "T1", "name": "Keith Mitchell", "score": "-11"},
                {"pos": "T3", "name": "Max Homa", "score": "-9"}]
        self.assertEqual(transform.live_caption("Baycurrent Classic", 3, tied),
                         "Baycurrent Classic, round 3: Jacob Bridgeman and Keith Mitchell share the lead at -11, 2 ahead of Max Homa (-9).")

    def test_movers_rank_before_and_after_today(self):
        live = self._r2_live()
        mv = transform.movers(live)
        # before today: Al -7, Di -7 (T1), Bo -6 (3), Cy 0, Ed 0 (T4). Now: Al/Bo T1, Cy 3, Di 4, Ed 5
        self.assertEqual([(m["name"], m["up"], m["before"], m["now"]) for m in mv],
                         [("Bo Tie", 2, "3", "T1"), ("Cy Climb", 1, "T4", "3")])
        self.assertNotIn("Di Drop", [m["name"] for m in mv])        # fell: not a mover
        self.assertEqual(transform.movers(self._wrap_live()), [])   # round 1: no "before"
        self.assertEqual(transform.low_rounds(live)[0], ("Cy Climb", "-8"))

    def test_round_of_the_day_needs_enough_tracked_holes(self):
        live = self._r2_live()
        rel = {str(h): (-1 if h in (2, 5, 7, 9, 11, 14, 16, 18) else 0) for h in range(1, 19) if h not in (12, 13)}
        holes = {"3": {"round": 2, "start": 1, "thru": 18, "today": -8, "course": "C", "rel": rel}}
        pars = {"C": {str(h): 4 for h in range(1, 19)}}
        best = transform.round_of_day(live, holes, pars)
        self.assertEqual((best["name"], best["today"], best["known"]), ("Cy Climb", -8, 16))
        self.assertEqual(best["back"]["rel"][2:4], [None, None])     # 12 and 13 stay blank, never guessed
        self.assertEqual(best["front"]["pars"], [4] * 9)
        few = {"3": {**holes["3"], "rel": dict(list(rel.items())[:10])}}
        self.assertIsNone(transform.round_of_day(live, few, pars))

    def test_tied_leaders_share_the_lead_in_the_wrap(self):
        w = transform.round_wrap(self._r2_live(), {"picks": {}})
        self.assertEqual(w["leader"], "Lead & Tie")
        self.assertTrue(w["caption"].startswith("Fake Open after round 2: Al Lead and Bo Tie share the lead at -9."))

    def test_recap_carousel_posts_slides_and_gives_qa_the_facts(self):
        live = self._r2_live()
        picks = {"event_name": "Fake Open", "picks": {"win": {"name": "Al Lead", "dg_id": 1}}}
        prev = {"event_name": "Fake Open", "leader_name": "Al Lead", "last_leaderboard_post_ts": 9e12, "last_picks": picks}
        from distribute import notify_telegram
        with mock.patch.object(datagolf, "get_live_in_play", return_value=live), \
             mock.patch.object(datagolf, "get_live_hole_stats", return_value={}), \
             mock.patch.object(state, "load", return_value=prev), mock.patch.object(state, "save"), \
             mock.patch.object(notify_telegram, "quiet_now", return_value=False), \
             mock.patch.object(content, "qa_review", return_value=(True, "PASS")) as qa, \
             mock.patch.object(pipeline, "_post_carousel_everywhere",
                               return_value={"platforms": ["facebook", "instagram"]}) as post:
            out = pipeline.run_live_poll(dry_run=False, draft=False)
        slides, caption = post.call_args.args
        self.assertEqual([os.path.basename(x) for x in slides],
                         ["01_leaderboard.png", "02_movers.png", "03_low_rounds.png", "04_our_card.png", "05_closer.png"])
        self.assertIn("Biggest climb: Bo Tie, up 2 spots to T1 (-3 today).", caption)
        self.assertFalse(post.call_args.kwargs["draft"])
        self.assertIn("movers_computed_from_feed", qa.call_args.args[0])

    def test_recap_now_refuses_unfinished_round_and_posts_a_finished_one(self):
        live = self._r2_live()
        prev = {"event_name": "Fake Open", "last_picks": {"event_name": "Fake Open", "picks": {}}}
        unfinished = {**live, "data": [{**live["data"][0], "thru": 12}] + live["data"][1:]}
        with mock.patch.object(datagolf, "get_live_in_play", return_value=unfinished), \
             mock.patch.object(state, "load", return_value=prev):
            self.assertIn("isn't complete", pipeline.run_recap_now("pga")["status"])
        with mock.patch.object(datagolf, "get_live_in_play", return_value=live), \
             mock.patch.object(datagolf, "get_live_hole_stats", return_value={}), \
             mock.patch.object(state, "load", return_value=prev), \
             mock.patch.object(content, "qa_review", return_value=(False, "FAIL: x")), \
             mock.patch.object(pipeline, "_post_carousel_everywhere", return_value={"DRAFT": True}) as post:
            out = pipeline.run_recap_now("pga")
        self.assertEqual(out["round"], 2)
        self.assertTrue(post.call_args.kwargs["draft"])                 # QA failure -> draft, never a post
        self.assertTrue(post.call_args.args[1].startswith("QA HELD (FAIL: x)"))

    def test_recap_falls_back_to_single_card(self):
        live = self._r2_live()
        prev = {"event_name": "Fake Open", "leader_name": "Al Lead", "last_leaderboard_post_ts": 9e12,
                "last_picks": {"event_name": "Fake Open", "picks": {}}}
        from distribute import notify_telegram
        with mock.patch.object(datagolf, "get_live_in_play", return_value=live), \
             mock.patch.object(datagolf, "get_live_hole_stats", return_value={}), \
             mock.patch.object(state, "load", return_value=prev), mock.patch.object(state, "save"), \
             mock.patch.object(notify_telegram, "quiet_now", return_value=False), \
             mock.patch.object(pipeline, "build_round_recap", side_effect=RuntimeError("font")), \
             mock.patch.object(pipeline, "render_round_wrap") as card, \
             mock.patch.object(pipeline, "_post_everywhere", return_value={"DRAFT": True}) as single:
            pipeline.run_live_poll(draft=True)
        card.assert_called_once()
        self.assertEqual(os.path.basename(single.call_args.args[0]), "round_wrap.png")

    def test_photo_library_lookup_and_credit(self):
        from tools import player_photos
        photo, entry = player_photos.lookup("Ludvig Aberg")          # matches "Ludvig Åberg"
        self.assertTrue(photo and os.path.exists(photo))
        self.assertTrue(player_photos.credit(entry).startswith("Photo: "))
        self.assertEqual(player_photos.lookup("Test Player00"), (None, None))

    def test_playing_card_photo_and_initials(self):
        import tempfile
        from tools import player_photos
        photo, entry = player_photos.lookup("Scottie Scheffler")
        with tempfile.TemporaryDirectory() as d:
            for style in ("color", "brand"):
                out = render.render_playing_card("win", "Scottie Scheffler", "TEST", "20.0%", "18.0%", "4-1",
                                                 {}, "note", os.path.join(d, f"{style}.png"), photo,
                                                 player_photos.credit(entry), style=style)
                self.assertTrue(os.path.exists(out))
            out = render.render_playing_card("fade", "J.J. Spaun", "TEST", "1.0%", "2.0%", "49-1", {}, "note",
                                             os.path.join(d, "none.png"))
            self.assertTrue(os.path.exists(out))
            hand = render.render_hand([os.path.join(d, n) for n in ("color.png", "brand.png", "none.png")],
                                      "TEST", os.path.join(d, "hand.png"))
            self.assertTrue(os.path.exists(hand))

    def _live(self, rows, rnd=2):
        return {"info": {"event_name": "Fake Invitational", "current_round": rnd}, "data": rows}

    def test_detect_big_hole_inferred_from_one_hole(self):
        before = self._live([
            {"dg_id": 1, "player_name": "Lead, Al", "current_pos": "1", "current_score": -10, "today": -3, "thru": 12},
            {"dg_id": 2, "player_name": "Chase, Bo", "current_pos": "2", "current_score": -8, "today": -2, "thru": 14}])
        after = self._live([
            {"dg_id": 1, "player_name": "Lead, Al", "current_pos": "T1", "current_score": -10, "today": -3, "thru": 12},
            {"dg_id": 2, "player_name": "Chase, Bo", "current_pos": "T1", "current_score": -10, "today": -4, "thru": 15}])
        moments = transform.detect_moments(after, transform.live_scores(before))
        self.assertEqual([m["kind"] for m in moments], ["big_hole"])
        m = moments[0]
        self.assertEqual((m["name"], m["drop"], m["hole_moment"]), ("Bo Chase", 2, "ROUND 2 · THRU 15"))
        self.assertIn("do NOT call it an eagle", m["facts"])
        self.assertEqual(transform.detect_moments(after, transform.live_scores(before), [m["key"]]), [])

    def test_two_holes_between_polls_is_not_a_big_hole(self):
        before = self._live([{"dg_id": 2, "player_name": "Chase, Bo", "current_pos": "2", "current_score": -8, "today": -2, "thru": 13}])
        after = self._live([{"dg_id": 2, "player_name": "Chase, Bo", "current_pos": "1", "current_score": -10, "today": -4, "thru": 15}])
        self.assertEqual(transform.detect_moments(after, transform.live_scores(before)), [])

    def test_detect_charge_and_collapse(self):
        before = self._live([
            {"dg_id": 1, "player_name": "Lead, Al", "current_pos": "1", "current_score": -10, "today": 2, "thru": 10},
            {"dg_id": 3, "player_name": "Hot, Cy", "current_pos": "5", "current_score": -12, "today": -5, "thru": 11}])
        after = self._live([
            {"dg_id": 1, "player_name": "Lead, Al", "current_pos": "2", "current_score": -9, "today": 3, "thru": 11},
            {"dg_id": 3, "player_name": "Hot, Cy", "current_pos": "1", "current_score": -13, "today": -6, "thru": 12}])
        kinds = {m["kind"]: m for m in transform.detect_moments(after, transform.live_scores(before))}
        self.assertEqual(set(kinds), {"charge", "collapse"})
        self.assertIn("-6 for round 2", kinds["charge"]["facts"])
        self.assertEqual(transform.moment_fallback(kinds["collapse"])[:2], ("LEAD IS", "SLIPPING"))

    def test_live_poll_posts_one_capped_alert(self):
        state_now = {"event_name": "Fake Invitational", "leader_name": "Hot Cy", "last_leaderboard_post_ts": 9e12,
                     "live_scores": {"3": {"name": "Cy Hot", "pos": "1", "score": -11, "today": -4, "thru": 10, "rank": 1}}}
        live = self._live([{"dg_id": 3, "player_name": "Hot, Cy", "current_pos": "1", "current_score": -14, "today": -7, "thru": 11}])
        reaction = {"event_line_1": "CY GOES", "event_line_2": "THREE UNDER", "reaction": "eagle-plus chaos", "alternatives": []}
        with mock.patch.object(datagolf, "get_live_in_play", return_value=live), \
             mock.patch.object(state, "load", return_value=state_now), \
             mock.patch.object(state, "save") as saved, \
             mock.patch.object(content, "generate_live_reaction", return_value=reaction), \
             mock.patch.object(pipeline, "render_live_alert") as card:
            out = pipeline.run_live_poll(dry_run=True)
        acts = dict(out["actions"])
        self.assertIn("live_alert", acts)
        kw = card.call_args.kwargs
        # the writer's option said "eagle" on an inferred moment -> rejected, data-only lines used
        self.assertEqual((kw["event_line_1"], kw["event_line_2"]), ("HOT GOES", "3 UNDER ON ONE"))
        self.assertEqual(saved.call_args.args[0]["alerts"]["sent"], ["hole:3:2:11"])

    def test_carousel_failure_does_not_block_picks_card(self):
        with mock.patch.object(datagolf, "get_skill_ratings", side_effect=RuntimeError("down")):
            out = pipeline.run_pretournament_picks(dry_run=True)
        self.assertTrue(out["DRY_RUN"])
        self.assertTrue(str(out["carousel"]).startswith("FAILED"))

if __name__ == "__main__":
    unittest.main()
