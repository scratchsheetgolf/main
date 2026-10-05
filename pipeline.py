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


def _post_everywhere(local_image_path: str, caption: str, tiktok_title: str = "",
                     dry_run: bool = False, draft: bool = False, alternatives: list = None):
    """Posts the same image+caption to all four platforms. Each call is wrapped
    so one platform's failure (e.g. TikTok still pre-audit) doesn't block the rest.

    dry_run / draft skip every real network call (image hosting, all 4 platforms)
    and save the caption next to the image (<image>.txt) so a person can post the
    pair by hand. The difference is in the callers: a dry run saves no state, a
    draft saves state exactly like a real post (so throttles, leader tracking and
    saved picks keep working while posting is manual)."""
    if dry_run or draft:
        tiktok_title = tiktok_title or caption[:90]
        caption_path = os.path.splitext(local_image_path)[0] + ".txt"
        with open(caption_path, "w", encoding="utf-8") as f:
            f.write(f"{caption}\n\nTikTok title: {tiktok_title}\n")
            if alternatives:
                f.write("\nOther options (swap in by hand if better):\n")
                f.write("".join(f"- {alt}\n" for alt in alternatives))
        return {
            "DRY_RUN" if dry_run else "DRAFT": True,
            "would_post_image": local_image_path,
            "would_post_caption": caption,
            "caption_file": caption_path,
            "would_post_tiktok_title": tiktok_title,
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


def _caption(post_type: str, summary: str, event_tag: str, fallback: str, allow_fallback: bool):
    """(caption, alternatives). Claude writes a few options; the first is used, the rest go in the
    draft for a person to choose from. If the model says SKIP, the plain factual fallback is used.
    In dry-run/draft mode an error (e.g. no ANTHROPIC_API_KEY yet) also falls back; real posts re-raise."""
    plain = f"{fallback} {event_tag}".strip()
    try:
        options = content.generate_caption_options(post_type, summary, event_tag=event_tag)
    except Exception as e:
        if not allow_fallback:
            raise
        print(f"caption generation failed ({type(e).__name__}); using plain fallback caption", file=sys.stderr)
        return plain, []
    if not options:
        return plain, []
    return options[0], options[1:]


def run_pretournament_picks(tour: str = "pga", dry_run: bool = False, event_tag: str = "", draft: bool = False):
    """Run once, the morning the field is set (Tue/Wed of tournament week).
    Picks come from data/transform.choose_picks (DataGolf model vs sportsbook consensus);
    they're saved to state so Monday's newsletter can report how they finished."""
    preds = datagolf.get_pre_tournament_predictions(tour=tour)
    event_name = preds.get("event_name") or "THIS WEEK'S EVENT"
    event = transform.check_picks_window(event_name, datagolf.get_schedule(tour=tour, upcoming_only=False))
    outrights = datagolf.get_outright_odds(market="win", tour=tour)
    picks = transform.choose_picks(preds, outrights)

    image_path = os.path.join(OUTPUT_DIR, "weekly_picks.png")
    render_weekly_picks(
        event_name=event_name.upper(),
        win=picks["win"]["name"], value=picks["value"]["name"],
        fade=picks["fade"]["name"], sleeper=picks["sleeper"]["name"],
        out_path=image_path,
    )
    caption, alternatives = _caption(
        "weekly picks", transform.picks_summary(event_name, picks), event_tag,
        fallback=f"{event_name} picks. Win: {picks['win']['name']}. Value: {picks['value']['name']}. "
                 f"Fade: {picks['fade']['name']}. Sleeper: {picks['sleeper']['name']}.",
        allow_fallback=dry_run or draft,
    )
    prev = state.load()
    state.save({**prev, "last_picks": {
        "event_name": event_name,
        "event_id": event.get("event_id"),
        "year": (event.get("start_date") or "")[:4] or None,
        "picks": picks,
    }}, commit=not dry_run)
    result = _post_everywhere(image_path, caption, tiktok_title=f"{event_name} picks",
                              dry_run=dry_run, draft=draft, alternatives=alternatives)
    if dry_run or draft:
        result["picks"] = {slot: f"{p['name']} — {p['why']}" for slot, p in picks.items()}
    return result


def run_live_poll(tour: str = "pga", min_leaderboard_gap_minutes: int = 60, dry_run: bool = False,
                  event_tag: str = "", draft: bool = False):
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
    current_leaderboard = transform.sorted_leaderboard(live)   # the feed isn't in position order
    event_name = (live.get("info") or {}).get("event_name") or prev.get("event_name") or "LIVE"
    current_round = (live.get("info") or {}).get("current_round")
    final = transform.is_final(live)
    if not current_leaderboard:
        return {"status": "no data returned, check field names / API key"}

    # only compare leaders within the same event: a fresh state or last week's leader isn't a lead change
    prev_leader = prev.get("leader_name") if prev.get("event_name") == event_name else None
    current_leader = transform.display_name(current_leaderboard[0].get("player_name", "")) if current_leaderboard else None

    # Trigger 1: leader change -> Hot Take (always worth posting, this is rare by definition)
    if current_leader and prev_leader and current_leader != prev_leader:
        try:
            take = content.generate_hot_take(transform.lead_change_facts(live, prev_leader))
        except Exception as e:
            if not (dry_run or draft):
                raise
            take = False
            actions_taken.append(("hot_take", f"skipped: hot take generation failed ({type(e).__name__})"))
        if take is None:
            actions_taken.append(("hot_take", "skipped: model judged the facts too thin (SKIP)"))
        elif take:
            image_path = os.path.join(OUTPUT_DIR, "hot_take_live.png")
            render_hot_take(lines=take["lines"], kicker=take["kicker"], out_path=image_path)
            alt_notes = []
            if dry_run or draft:  # render the other options too, so a person can post the best card
                for i, alt in enumerate(take["alternatives"], start=1):
                    alt_path = os.path.join(OUTPUT_DIR, f"hot_take_live_alt{i}.png")
                    render_hot_take(lines=alt["lines"], kicker=alt["kicker"], out_path=alt_path)
                    alt_notes.append(f"{os.path.basename(alt_path)}: {' / '.join(alt['lines'])} {alt['kicker']}")
            actions_taken.append(("hot_take", _post_everywhere(image_path, take["kicker"], dry_run=dry_run,
                                                               draft=draft, alternatives=alt_notes)))

    # Trigger 2: leaderboard snapshot — throttled to once per min_leaderboard_gap_minutes,
    # NOT every poll. A poll that doesn't clear the gap just updates state and exits.
    now = time_module.time()
    last_post_ts = prev.get("last_leaderboard_post_ts", 0)
    minutes_since_last = (now - last_post_ts) / 60

    if minutes_since_last >= min_leaderboard_gap_minutes:
        top5 = [{"pos": str(p.get("current_pos", "")),
                 "name": transform.display_name(p.get("player_name", "")),
                 "score": transform.format_to_par(p.get("current_score"))}
                for p in current_leaderboard[:5]]
        image_path = os.path.join(OUTPUT_DIR, "leaderboard_live.png")
        render_leaderboard(event=event_name.upper(),
                           round_label="FINAL" if final else (f"ROUND {current_round} · LIVE" if current_round else "LIVE"),
                            players=top5, out_path=image_path)
        caption, alternatives = _caption(
            "final leaderboard" if final else "live leaderboard",
            (f"{event_name} FINAL results (the event is over), top 5: " if final
             else f"{event_name} round {current_round}, in progress, top 5: ")
            + "; ".join(f"{p['pos']} {p['name']} {p['score']}" for p in top5), event_tag,
            fallback=(f"{event_name} final: {current_leader} wins." if final
                      else f"{event_name} leaderboard: {current_leader} leads."),
            allow_fallback=dry_run or draft,
        )
        actions_taken.append(("leaderboard", _post_everywhere(image_path, caption, dry_run=dry_run, draft=draft,
                                                              alternatives=alternatives)))
        if not dry_run:  # a draft counts as posted, so the hourly throttle still applies
            last_post_ts = now
    else:
        actions_taken.append(("leaderboard", f"skipped, only {minutes_since_last:.0f} min since last post"))

    state.save({**prev, "event_name": event_name, "leader_name": current_leader,
                "last_leaderboard_post_ts": last_post_ts,
                "standings": transform.standings_snapshot(live)}, commit=not dry_run)
    return {"actions": actions_taken}


def _event_results(event: dict, tour: str):
    """Final results for a finished event, from the best source available:
    1. historical-event-data (403 on plans without historical data)
    2. the in-play feed, if it's still on that event
    3. the standings the live poll saved to state during the event
    Returns None if none of them has the event (e.g. a team event like the Presidents Cup)."""
    try:
        return datagolf.get_event_results(event["event_id"], int(event["start_date"][:4]), tour=tour)
    except Exception as e:
        print(f"historical results unavailable for {event['event_name']} ({e}); trying fallbacks", file=sys.stderr)
    try:
        live = transform.results_from_live(datagolf.get_live_in_play(tour=tour), event["event_name"])
        if live:
            return live
    except Exception as e:
        print(f"live feed unavailable ({e})", file=sys.stderr)
    saved = state.load().get("standings") or {}
    if (saved.get("event_name") or "").strip().lower() == event["event_name"].strip().lower() and saved.get("event_stats"):
        return {**saved, "source": "standings saved by the live poll"}
    return None


def _recap_sections(tour: str) -> tuple:
    """Newsletter sections built from DataGolf. Numbers and names come from the data;
    content.generate_newsletter_recap only writes the prose around those facts."""
    from html import escape
    last, upcoming = transform.last_completed_and_next(datagolf.get_schedule(tour=tour, upcoming_only=False))
    sections = []
    results = _event_results(last, tour) if last else None
    if last and results is None:
        print(f"newsletter: no individual results for {last['event_name']} (team event, or none saved); "
              "skipping the recap section", file=sys.stderr)
    if last and results is not None:
        top5 = transform.top_finishers(results)
        saved = state.load().get("last_picks") or {}
        ours = transform.pick_results(saved, results) if str(saved.get("event_id")) == str(last["event_id"]) else []
        facts = [f"Event: {last['event_name']} at {last.get('course', '')}",
                 "Top finishers: " + "; ".join(f"{t['pos']} {t['name']}" + (f" ({t['score']})" if t["score"] else "")
                                               for t in top5)]
        if ours:
            facts.append("Our picks: " + "; ".join(f"{r['slot'].upper()} {r['name']} finished {r['finish']}" for r in ours))
        else:
            facts.append("We did not publish picks for this event.")
        blurb = content.generate_newsletter_recap("\n".join(facts))
        body = "".join(f"<p>{escape(par.strip())}</p>" for par in blurb.split("\n") if par.strip())
        body += "<p>" + "<br>".join(f"<b>{escape(t['pos'])}</b> {escape(t['name'])}"
                                    + (f" {escape(t['score'])}" if t["score"] else "") for t in top5) + "</p>"
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
    return sections, ((last if results is not None else None) or upcoming or last)["event_name"]


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
    parser.add_argument("--draft", action="store_true",
                         help="Generate and save state like a real run, but write image + caption to output/ "
                              "for posting by hand instead of posting. (Newsletter: same as --dry-run.)")
    parser.add_argument("--event-tag", default="",
                         help='Real event hashtag to append during majors, e.g. "#USOpen". Leave blank normally.')
    args = parser.parse_args()

    if args.action == "picks":
        print(run_pretournament_picks(args.tour, dry_run=args.dry_run, event_tag=args.event_tag, draft=args.draft))
    elif args.action == "live":
        print(run_live_poll(args.tour, dry_run=args.dry_run, event_tag=args.event_tag, draft=args.draft))
    elif args.action == "newsletter":
        print(run_weekly_newsletter(args.tour, dry_run=args.dry_run or args.draft))
