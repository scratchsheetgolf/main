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
            out = pipeline._post_everywhere("card.png", "caption")
        px.assert_called_once_with("card.png", "caption")
        host.assert_not_called()
        ig.assert_not_called()
        self.assertEqual(out["x"], {"id": "1"})

    def test_image_host_failure_no_longer_blocks_x(self):
        with mock.patch.dict(os.environ, {"POST_PLATFORMS": "x,instagram"}), \
             mock.patch.object(pipeline.post_x, "post_image", return_value={"id": "1"}), \
             mock.patch.object(pipeline.image_host, "publish_image", side_effect=RuntimeError("git")):
            out = pipeline._post_everywhere("card.png", "caption")
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
