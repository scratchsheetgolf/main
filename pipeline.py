"""
pipeline.py — the decision layer. Each function here is a separate trigger
point, called by a different GitHub Actions workflow on its own schedule.

NOTE ON DATAGOLF FIELD NAMES: the exact JSON key names below (player_name,
current_score, etc.) are best-guess based on DataGolf's documented response
shape, but I haven't run this against a live API key. The FIRST thing to do
once you have DATAGOLF_API_KEY set is run each data.datagolf function once,
print the raw response, and adjust the key names in this file to match
reality exactly. Everything else (rendering, posting, content gen) doesn't
care about DataGolf's schema, so that's a contained, quick fix.
"""
import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__)))

from data import datagolf, state
from content import content
from render import render_leaderboard, render_hot_take, render_intel_stat, render_live_alert, render_weekly_picks
from distribute import image_host, post_x, post_meta, post_tiktok

OUTPUT_DIR = os.path.join(os.path.dirname(__file__), "output")


def _post_everywhere(local_image_path: str, caption: str, tiktok_title: str = "", dry_run: bool = False):
    """Posts the same image+caption to all four platforms. Each call is wrapped
    so one platform's failure (e.g. TikTok still pre-audit) doesn't block the rest.

    dry_run=True skips every real network call (image hosting, all 4 platforms)
    and just returns what WOULD have been posted. Use this to validate the data
    pipeline and generated content against real DataGolf/Claude API responses
    before anything goes public."""
    if dry_run:
        return {
            "DRY_RUN": True,
            "would_post_image": local_image_path,
            "would_post_caption": caption,
            "would_post_tiktok_title": tiktok_title or caption[:90],
        }

    results = {}
    public_url = None
    try:
        public_url = image_host.publish_image(local_image_path)
    except Exception as e:
        results["image_host"] = f"FAILED: {e}"
        return results  # nothing else can proceed without a public URL

    for name, fn in [
        ("x", lambda: post_x.post_image(local_image_path, caption)),
        ("facebook", lambda: post_meta.post_to_facebook_page(public_url, caption)),
        ("instagram", lambda: post_meta.post_to_instagram(public_url, caption)),
        ("tiktok", lambda: post_tiktok.post_photo([public_url], tiktok_title or caption[:90], caption)),
    ]:
        try:
            results[name] = fn()
        except Exception as e:
            results[name] = f"FAILED: {e}"
    return results


def run_pretournament_picks(tour: str = "pga", dry_run: bool = False, event_tag: str = ""):
    """Run once, the morning the field is set (Tue/Wed of tournament week)."""
    schedule = datagolf.get_schedule(tour=tour, upcoming_only=True)
    preds = datagolf.get_pre_tournament_predictions(tour=tour)
    # TODO: once you see the real response shape, pull actual top picks / value /
    # fade / sleeper names out of `preds` instead of placeholders below.
    event_name = schedule.get("events", [{}])[0].get("event_name", "THIS WEEK'S EVENT")

    image_path = os.path.join(OUTPUT_DIR, "weekly_picks.png")
    render_weekly_picks(
        event_name=event_name.upper(),
        win="TBD", value="TBD", fade="TBD", sleeper="TBD",  # wire up from `preds`
        out_path=image_path,
    )
    caption = content.generate_social_caption(
        "weekly picks", f"Win/value/fade/sleeper picks for {event_name}", event_tag=event_tag
    )
    return _post_everywhere(image_path, caption, tiktok_title=f"{event_name} picks", dry_run=dry_run)


def run_live_poll(tour: str = "pga", min_leaderboard_gap_minutes: int = 60, dry_run: bool = False, event_tag: str = ""):
    """Called every 5 minutes during live tournament rounds (matches DataGolf's
    own refresh cadence — polling faster gains nothing). NOTE: polling every 5
    min does NOT mean posting every 5 min — see throttle below. A leaderboard
    image every 5 minutes for 4 days is 100+ posts and reads as spam, not content."""
    import time as time_module

    live = datagolf.get_live_in_play(tour=tour)
    prev = state.load()
    actions_taken = []

    # TODO: confirm field names against a real response. Expecting something
    # like live["data"] = [{"player_name": ..., "current_pos": ..., "current_score": ...}, ...]
    current_leaderboard = live.get("data", [])
    if not current_leaderboard:
        return {"status": "no data returned, check field names / API key"}

    prev_leader = prev.get("leader_name")
    current_leader = current_leaderboard[0].get("player_name") if current_leaderboard else None

    # Trigger 1: leader change -> Hot Take (always worth posting, this is rare by definition)
    if current_leader and current_leader != prev_leader:
        take = content.generate_hot_take(
            f"{current_leader} has taken the lead, passing {prev_leader or 'the previous leader'}."
        )
        image_path = os.path.join(OUTPUT_DIR, "hot_take_live.png")
        render_hot_take(lines=take["lines"], kicker=take["kicker"], out_path=image_path)
        actions_taken.append(("hot_take", _post_everywhere(image_path, take["kicker"], dry_run=dry_run)))

    # Trigger 2: leaderboard snapshot — throttled to once per min_leaderboard_gap_minutes,
    # NOT every poll. A poll that doesn't clear the gap just updates state and exits.
    now = time_module.time()
    last_post_ts = prev.get("last_leaderboard_post_ts", 0)
    minutes_since_last = (now - last_post_ts) / 60

    if minutes_since_last >= min_leaderboard_gap_minutes:
        top5 = [{"name": p.get("player_name", ""), "score": p.get("current_score", "")}
                for p in current_leaderboard[:5]]
        image_path = os.path.join(OUTPUT_DIR, "leaderboard_live.png")
        render_leaderboard(event=prev.get("event_name", "LIVE"), round_label="LIVE",
                            players=top5, out_path=image_path)
        caption = content.generate_social_caption(
            "live leaderboard", f"current top 5, leader {current_leader}", event_tag=event_tag
        )
        actions_taken.append(("leaderboard", _post_everywhere(image_path, caption, dry_run=dry_run)))
        if not dry_run:
            last_post_ts = now
    else:
        actions_taken.append(("leaderboard", f"skipped, only {minutes_since_last:.0f} min since last post"))

    state.save({**prev, "leader_name": current_leader, "last_leaderboard_post_ts": last_post_ts}, commit=not dry_run)
    return {"actions": actions_taken}


def run_weekly_newsletter(tour: str = "pga"):
    from newsletter import newsletter
    schedule = datagolf.get_schedule(tour=tour, upcoming_only=False)
    event_name = schedule.get("events", [{}])[0].get("event_name", "This Week")
    html = newsletter.compile_digest_html(
        week_label=event_name,
        sections=[{"heading": "Recap", "body_html": "<p>TODO: pull real recap content here.</p>"}],
    )
    return newsletter.send_campaign(subject=f"The Scratch Sheet — {event_name}", html_content=html)


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=["picks", "live", "newsletter"])
    parser.add_argument("--tour", default="pga")
    parser.add_argument("--dry-run", action="store_true",
                         help="Run against real data/APIs but print what would be posted instead of posting it.")
    parser.add_argument("--event-tag", default="",
                         help='Real event hashtag to append during majors, e.g. "#USOpen". Leave blank normally.')
    args = parser.parse_args()

    if args.action == "picks":
        print(run_pretournament_picks(args.tour, dry_run=args.dry_run, event_tag=args.event_tag))
    elif args.action == "live":
        print(run_live_poll(args.tour, dry_run=args.dry_run, event_tag=args.event_tag))
    elif args.action == "newsletter":
        print(run_weekly_newsletter(args.tour))  # newsletter has no dry-run yet — low volume, lower risk
