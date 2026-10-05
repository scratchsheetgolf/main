"""
pipeline.py — the decision layer. Each function here is a separate trigger
point, called by a different GitHub Actions workflow on its own schedule.

NOTE ON DATAGOLF FIELD NAMES: key names now follow the example responses in
DataGolf's API docs (checked 2026-10-04; see data/transform.py) and are covered by
tests/test_offline.py. They have NOT yet been run against a live API key. Once
DATAGOLF_API_KEY is set, run each command with --dry-run first and compare the raw
responses to the docs before turning schedules on.
"""
import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__)))

from data import datagolf, state, transform
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
    """Run once, the morning the field is set (Tue/Wed of tournament week).
    Picks come from data/transform.choose_picks (DataGolf model vs sportsbook consensus);
    they're saved to state so Monday's newsletter can report how they finished."""
    preds = datagolf.get_pre_tournament_predictions(tour=tour)
    outrights = datagolf.get_outright_odds(market="win", tour=tour)
    picks = transform.choose_picks(preds, outrights)

    event_name = preds.get("event_name") or "THIS WEEK'S EVENT"
    _, upcoming = transform.last_completed_and_next(datagolf.get_schedule(tour=tour, upcoming_only=True))
    event = upcoming or {}

    image_path = os.path.join(OUTPUT_DIR, "weekly_picks.png")
    render_weekly_picks(
        event_name=event_name.upper(),
        win=picks["win"]["name"], value=picks["value"]["name"],
        fade=picks["fade"]["name"], sleeper=picks["sleeper"]["name"],
        out_path=image_path,
    )
    caption = content.generate_social_caption(
        "weekly picks", transform.picks_summary(event_name, picks), event_tag=event_tag
    )
    prev = state.load()
    state.save({**prev, "last_picks": {
        "event_name": event_name,
        "event_id": event.get("event_id"),
        "year": (event.get("start_date") or "")[:4] or None,
        "picks": picks,
    }}, commit=not dry_run)
    result = _post_everywhere(image_path, caption, tiktok_title=f"{event_name} picks", dry_run=dry_run)
    if dry_run:
        result["picks"] = {slot: f"{p['name']} — {p['why']}" for slot, p in picks.items()}
    return result


def run_live_poll(tour: str = "pga", min_leaderboard_gap_minutes: int = 60, dry_run: bool = False, event_tag: str = ""):
    """Called every 5 minutes during live tournament rounds (matches DataGolf's
    own refresh cadence — polling faster gains nothing). NOTE: polling every 5
    min does NOT mean posting every 5 min — see throttle below. A leaderboard
    image every 5 minutes for 4 days is 100+ posts and reads as spam, not content."""
    import time as time_module

    live = datagolf.get_live_in_play(tour=tour)
    prev = state.load()
    actions_taken = []

    # shape per DataGolf docs: {"info": {"event_name", ...}, "data": [{"player_name": "Last, First",
    # "current_pos": "T2", "current_score": -14, ...}]} — sorted by position
    current_leaderboard = live.get("data", [])
    event_name = (live.get("info") or {}).get("event_name") or prev.get("event_name") or "LIVE"
    if not current_leaderboard:
        return {"status": "no data returned, check field names / API key"}

    prev_leader = prev.get("leader_name")
    current_leader = transform.display_name(current_leaderboard[0].get("player_name", "")) if current_leaderboard else None

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
        top5 = [{"name": transform.display_name(p.get("player_name", "")),
                 "score": transform.format_to_par(p.get("current_score"))}
                for p in current_leaderboard[:5]]
        image_path = os.path.join(OUTPUT_DIR, "leaderboard_live.png")
        render_leaderboard(event=event_name.upper(), round_label="LIVE",
                            players=top5, out_path=image_path)
        caption = content.generate_social_caption(
            "live leaderboard", f"current top 5, leader {current_leader}", event_tag=event_tag
        )
        actions_taken.append(("leaderboard", _post_everywhere(image_path, caption, dry_run=dry_run)))
        if not dry_run:
            last_post_ts = now
    else:
        actions_taken.append(("leaderboard", f"skipped, only {minutes_since_last:.0f} min since last post"))

    state.save({**prev, "event_name": event_name, "leader_name": current_leader,
                "last_leaderboard_post_ts": last_post_ts}, commit=not dry_run)
    return {"actions": actions_taken}


def _recap_sections(tour: str) -> tuple:
    """Newsletter sections built from DataGolf. Numbers and names come from the data;
    content.generate_newsletter_recap only writes the prose around those facts."""
    from html import escape
    last, upcoming = transform.last_completed_and_next(datagolf.get_schedule(tour=tour, upcoming_only=False))
    sections = []
    if last:
        results = datagolf.get_event_results(last["event_id"], int(last["start_date"][:4]), tour=tour)
        top5 = transform.top_finishers(results)
        saved = state.load().get("last_picks") or {}
        ours = transform.pick_results(saved, results) if str(saved.get("event_id")) == str(last["event_id"]) else []
        facts = [f"Event: {last['event_name']} at {last.get('course', '')}",
                 "Top finishers: " + "; ".join(f"{t['pos']} {t['name']}" for t in top5)]
        if ours:
            facts.append("Our picks: " + "; ".join(f"{r['slot'].upper()} {r['name']} finished {r['finish']}" for r in ours))
        else:
            facts.append("We did not publish picks for this event.")
        blurb = content.generate_newsletter_recap("\n".join(facts))
        body = "".join(f"<p>{escape(par.strip())}</p>" for par in blurb.split("\n") if par.strip())
        body += "<p>" + "<br>".join(f"<b>{escape(t['pos'])}</b> {escape(t['name'])}" for t in top5) + "</p>"
        sections.append({"heading": f"{escape(last['event_name'])} recap", "body_html": body})
        if ours:
            rows = "<br>".join(f"<b>{escape(r['slot'].upper())}</b> {escape(r['name'])}: {escape(r['finish'])}" for r in ours)
            sections.append({"heading": "How our picks did", "body_html": f"<p>{rows}</p>"})
    if upcoming:
        sections.append({"heading": "Up next", "body_html": (
            f"<p><b>{escape(upcoming['event_name'])}</b><br>{escape(upcoming.get('course', ''))}"
            f"<br>Starts {escape(upcoming.get('start_date', ''))}</p>"
            "<p>Picks drop when the field is set.</p>")})
    if not sections:
        raise RuntimeError("No completed or upcoming events in the schedule; nothing to put in the newsletter.")
    return sections, (last or upcoming)["event_name"]


def run_weekly_newsletter(tour: str = "pga", dry_run: bool = False):
    from newsletter import newsletter
    sections, event_name = _recap_sections(tour)
    html = newsletter.compile_digest_html(week_label=event_name, sections=sections)
    subject = f"The Scratch Sheet — {event_name}"
    if dry_run:
        return newsletter.preview(subject, html, os.path.join(OUTPUT_DIR, "newsletter_preview.html"))
    return newsletter.send_campaign(subject=subject, html_content=html)


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
        print(run_weekly_newsletter(args.tour, dry_run=args.dry_run))
