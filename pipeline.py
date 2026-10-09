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
from render import (render_leaderboard, render_hot_take, render_intel_stat, render_live_alert, render_weekly_picks,
                    render_pick_detail, render_stat_list, render_closer, render_playing_card, render_hand,
                    render_reel, render_round_wrap)
from distribute import image_host, post_x, post_meta, post_tiktok, notify_telegram, music

OUTPUT_DIR = os.path.join(os.path.dirname(__file__), "output")

TOUR_NAMES = {"pga": "PGA Tour", "euro": "DP World Tour", "alt": "LIV Golf", "kft": "Korn Ferry Tour"}


def tour_label(tour: str) -> str:
    """Prefix shown on cards for non-PGA tours ("DP WORLD TOUR · "); empty for the PGA Tour."""
    return "" if tour == "pga" else f"{TOUR_NAMES.get(tour, tour).upper()} · "


ALL_PLATFORMS = {"x", "facebook", "instagram", "tiktok"}


def enabled_platforms() -> set:
    """Platforms real posts go to: the POST_PLATFORMS repo variable (comma list, e.g. "x"),
    or all four if it's unset. Only matters once AUTO_POST is true."""
    raw = os.environ.get("POST_PLATFORMS", "").strip()
    if not raw:
        return set(ALL_PLATFORMS)
    chosen = {p.strip().lower() for p in raw.split(",") if p.strip()}
    unknown = chosen - ALL_PLATFORMS
    if unknown:
        raise ValueError(f"POST_PLATFORMS has unknown platform(s): {', '.join(sorted(unknown))}")
    return chosen


def _post_everywhere(local_image_path: str, caption: str, tiktok_title: str = "",
                     dry_run: bool = False, draft: bool = False, alternatives: list = None, live: bool = False):
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
        pinged = None
        if draft:   # drafts go to Mike's phone (no-op until the Telegram secrets exist)
            alts = ("\n\nOther options:\n" + "\n".join(f"- {a}" for a in alternatives)) if alternatives else ""
            pinged = notify_telegram.send_file(local_image_path, f"DRAFT (post by hand)\n\n{caption}{alts}",
                                               respect_quiet=live)
        return {
            "DRY_RUN" if dry_run else "DRAFT": True,
            "telegram": pinged,
            "would_post_image": local_image_path,
            "would_post_caption": caption,
            "caption_file": caption_path,
            "would_post_tiktok_title": tiktok_title,
        }

    results = {}
    platforms = enabled_platforms()
    results["platforms"] = sorted(platforms)

    if "x" in platforms:  # X takes a direct upload; it doesn't need the public image URL
        try:
            results["x"] = post_x.post_image(local_image_path, caption)
        except Exception as e:
            results["x"] = f"FAILED: {e}"

    url_platforms = platforms & {"facebook", "instagram", "tiktok"}
    if url_platforms:
        try:
            public_url = image_host.publish_image(local_image_path)
        except Exception as e:
            results["image_host"] = f"FAILED: {e} (skipped {', '.join(sorted(url_platforms))})"
            return results
        for name, fn in [
            ("facebook", lambda: post_meta.post_to_facebook_page(public_url, caption)),
            ("instagram", lambda: _instagram_post(local_image_path, public_url, caption)),
            ("tiktok", lambda: post_tiktok.post_photo([public_url], tiktok_title or caption[:90], caption)),
        ]:
            if name in url_platforms:
                try:
                    results[name] = fn()
                except Exception as e:
                    results[name] = f"FAILED: {e}"
    return results


def _instagram_post(local_image_path: str, public_url: str, caption: str):
    """Instagram gets a 9 s Reel of the card with a licensed music clip (Reels reach non-followers);
    if there's no clip/key or the Reel fails at any step, it falls back to the plain image post."""
    reel_note = "no music clip available"
    clip = music.pick_clip()
    if clip:
        try:
            reel = render_reel([{"path": local_image_path, "seconds": 9, "deal": 0.8}],   # 0.8 s entrance (Mike)
                               os.path.splitext(local_image_path)[0] + "_reel.mp4", audio=clip)
            return {"reel": post_meta.post_reel_to_instagram(reel, caption)}
        except Exception as e:
            reel_note = f"reel failed ({type(e).__name__}: {str(e)[:200]})"
    return {"image": post_meta.post_to_instagram(public_url, caption), "note": reel_note}


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


IG_HASHTAGS = {"pga": "#golf #pgatour #golfpicks", "euro": "#golf #dpworldtour #golfpicks"}

# Instagram music is added by hand in the app, so caption.txt suggests songs. Curated, card/luck themed to fit
# "This Week's Hand"; rotates by ISO week so it doesn't repeat. Availability depends on the account type
# (Business accounts only get Meta's royalty-free library) — the backups are there for when one is missing.
IG_SONGS_PREVIEW = [
    "The Gambler – Kenny Rogers", "Poker Face – Lady Gaga", "Luck Be a Lady – Frank Sinatra",
    "Ace of Spades – Motörhead", "Lucky Man – The Verve", "Viva Las Vegas – Elvis Presley",
    "Mr. Blue Sky – Electric Light Orchestra", "Feel Good Inc. – Gorillaz",
]


# Engagement question closing the weekly picks post (X caption + Instagram caption.txt). A real question
# about the picks, not "like and share" bait. Rotates weekly.
PICKS_CTAS = [
    "Who's your winner this week?",
    "Which of these four are we wrong about?",
    "Who are you fading this week?",
    "Your best longshot this week, go.",
    "Tail the fade or fade the fade?",
    "Who's the value pick we missed?",
]
X_LIMIT = 280


def with_cta(caption: str, cta: str, limit: int = X_LIMIT) -> str:
    """Put the question after the caption text and before any trailing hashtags; skipped if it won't fit on X."""
    words = caption.split(" ")
    i = len(words)
    while i > 0 and words[i - 1].startswith("#"):
        i -= 1
    body, tags = " ".join(words[:i]).rstrip(), " ".join(words[i:])
    out = f"{body}\n\n{cta}" + (f" {tags}" if tags else "")
    return out if len(out) <= limit else caption


def song_suggestions(songs: list, week: int = None, n: int = 3) -> list:
    """n songs for this week's post, starting at a weekly offset (first = the pick, rest = backups)."""
    import datetime
    week = datetime.date.today().isocalendar()[1] if week is None else week
    return [songs[(week + i) % len(songs)] for i in range(n)]


def _note(facts: str) -> str:
    """Marker note for a carousel slide; blank if generation fails (slides are posted by hand)."""
    try:
        lines = content.generate_supporting_line(facts)
        return lines[0] if lines else ""
    except Exception as e:
        print(f"carousel note failed ({type(e).__name__}); leaving it blank", file=sys.stderr)
        return ""


def build_picks_carousel(tour: str, event_name: str, preds: dict, picks: dict, cover_path: str, caption: str) -> dict:
    """Instagram preview carousel as a numbered set in output/carousel_<tour>/ (posted by hand):
    01 hand (the four playing cards fanned), 02 picks card, 03-06 one playing card per pick,
    07 course fit, 08 favorites, 09 closer, + caption.txt. A second full set with the photos in the
    brand duotone goes to output/carousel_<tour>/brand/ so the two looks can be A/B tested week to week."""
    import shutil
    from tools import player_photos
    out_dir = os.path.join(OUTPUT_DIR, f"carousel_{tour}")
    brand_dir = os.path.join(out_dir, "brand")
    os.makedirs(brand_dir, exist_ok=True)
    event_line = f"{tour_label(tour)}{transform.short_event_name(event_name).upper()}"
    slides = {}          # name -> {"color": path, "brand": path}

    def shared(name, path):
        shutil.copyfile(path, os.path.join(brand_dir, name))
        slides[name] = {"color": path, "brand": os.path.join(brand_dir, name)}

    skills = datagolf.get_skill_ratings()
    credits = []
    for i, slot in enumerate(("win", "value", "fade", "sleeper"), start=3):
        pick = picks[slot]
        profile = transform.skill_profile(skills, pick["dg_id"])
        note = _note(transform.pick_facts(slot, pick, event_name, profile))
        photo, entry = player_photos.lookup(pick["name"])
        if entry:
            credits.append(f"{pick['name']}: {player_photos.credit(entry)[len('Photo: '):]} ({entry.get('source_page', '')})")
        name = f"{i:02d}_{slot}.png"
        slides[name] = {}
        for style, folder in (("color", out_dir), ("brand", brand_dir)):
            path = os.path.join(folder, name)
            render_playing_card(slot, pick["name"], event_line, f"{pick['model_win']:.1%}",
                                f"{pick['market_win']:.1%}" if pick.get("market_win") else "—",
                                transform.format_odds(pick.get("median_odds")), profile, note, path,
                                photo, player_photos.credit(entry) if entry else "", style=style)
            slides[name][style] = path

    card_names = [n for n in slides]
    for style, folder in (("color", out_dir), ("brand", brand_dir)):
        render_hand([slides[n][style] for n in card_names], f"{event_line} · WIN · VALUE · FADE · SLEEPER",
                    os.path.join(folder, "01_hand.png"))
    slides["01_hand.png"] = {"color": os.path.join(out_dir, "01_hand.png"), "brand": os.path.join(brand_dir, "01_hand.png")}
    path = os.path.join(out_dir, "02_picks.png")
    shutil.copyfile(cover_path, path)
    shared("02_picks.png", path)

    fit = transform.course_fit_boost(preds)
    if fit:
        facts = (f"{event_name}: players whose win chance rises most when DataGolf adds course history and fit "
                 "to its baseline model, in percentage points: " + "; ".join(f"{n} {v}" for n, v, _ in fit) + ".")
        path = os.path.join(out_dir, "07_course_fit.png")
        render_stat_list("Who the Course Suits", "WIN CHANCE ADDED BY COURSE HISTORY + FIT (DATAGOLF)", "BOOST",
                         [(n, v) for n, v, _ in fit], _note(facts), path)
        shared("07_course_fit.png", path)

    favs = transform.favorites(preds)
    facts = f"{event_name}: DataGolf model win chances, top 5: " + "; ".join(f"{n} {v}" for n, v in favs) + "."
    path = os.path.join(out_dir, "08_favorites.png")
    render_stat_list("The Favorites", f"DATAGOLF MODEL WIN CHANCE · {event_line}", "WIN %", favs, _note(facts), path)
    shared("08_favorites.png", path)

    path = os.path.join(out_dir, "09_closer.png")
    render_closer(event_line, path)
    shared("09_closer.png", path)

    tags = IG_HASHTAGS.get(tour, "#golf")
    body = " ".join(w for w in caption.split(" ") if w not in tags.split())   # no tag twice
    text = f"{body}\n\n{tags}\n"
    # Reel for Instagram (+ Facebook via cross-posting): Reels reach non-followers, carousels mostly don't
    reel_path = None
    try:
        meaning = {"win": "the model's most likely winner", "value": "model rates him above the books' price",
                   "fade": "a books' favorite the model rates lower", "sleeper": "50-1 or longer with a real chance"}
        reel_hand = os.path.join(out_dir, "reel_hand.png")    # same fan, Reel wording (no "swipe")
        render_hand([slides[n]["color"] for n in card_names], f"{event_line} · WIN · VALUE · FADE · SLEEPER",
                    reel_hand, kicker="here's the deal")
        reel_slides = [{"path": reel_hand, "seconds": 2.2, "top": transform.short_event_name(event_name).upper(),
                        "sub": "4 picks. DataGolf's model vs the sportsbooks.", "bottom": "picks from the model, not our gut"}]
        for n in sorted(slides):
            slot = n[3:-4]
            if slot in meaning:
                reel_slides.append({"path": slides[n]["color"], "seconds": 2.4, "top": f"{slot.upper()} PICK",
                                    "sub": meaning[slot], "bottom": f"why: {picks[slot]['why']}"})
        reel_slides.append({"path": slides["09_closer.png"]["color"], "seconds": 1.8, "top": "RECEIPTS SUNDAY",
                            "sub": "win or lose, we post how these did", "bottom": "follow for live alerts thu-sun"})
        reel_path = render_reel(reel_slides, os.path.join(out_dir, "reel.mp4"))
    except Exception as e:  # the carousel stands on its own
        print(f"reel failed ({type(e).__name__}: {e}); carousel unaffected", file=sys.stderr)
    song, *backups = song_suggestions(IG_SONGS_PREVIEW)
    text += f"\nSong (add in the Instagram app): {song}. Backups: {'; '.join(backups)}\n"
    if credits:   # CC BY-SA photos: attribution + license link travel with the post
        text += "\nPhotos (Wikimedia Commons; cards shared under the same licenses):\n" + "\n".join(credits) + "\n"
    caption_path = os.path.join(out_dir, "caption.txt")
    for folder in (out_dir, brand_dir):
        with open(os.path.join(folder, "caption.txt"), "w", encoding="utf-8") as f:
            f.write(text)
    return {"carousel_dir": out_dir, "brand_dir": brand_dir, "slides": sorted(slides), "caption_file": caption_path,
            "reel": reel_path}


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
        event_name=f"{tour_label(tour)}{event_name.upper()}",
        win=picks["win"]["name"], value=picks["value"]["name"],
        fade=picks["fade"]["name"], sleeper=picks["sleeper"]["name"],
        out_path=image_path,
    )
    caption, alternatives = _caption(
        "weekly picks", f"Tour: {TOUR_NAMES.get(tour, tour)}\n" + transform.picks_summary(event_name, picks),
        f"{event_tag} #golfpicks".strip(),
        fallback=f"{event_name} picks. Win: {picks['win']['name']}. Value: {picks['value']['name']}. "
                 f"Fade: {picks['fade']['name']}. Sleeper: {picks['sleeper']['name']}.",
        allow_fallback=dry_run or draft,
    )
    cta = song_suggestions(PICKS_CTAS, n=1)[0]   # same weekly rotation as the songs
    caption = with_cta(caption, cta)
    alternatives = [with_cta(a, cta) for a in alternatives]
    prev = state.load(tour)
    state.save({**prev, "last_picks": {
        "event_name": event_name,
        "event_id": event.get("event_id"),
        "year": (event.get("start_date") or "")[:4] or None,
        "picks": picks,
    }}, commit=not dry_run, tour=tour)
    result = _post_everywhere(image_path, caption, tiktok_title=f"{event_name} picks",
                              dry_run=dry_run, draft=draft, alternatives=alternatives)
    try:
        result["carousel"] = build_picks_carousel(tour, event_name, preds, picks, image_path, caption)
        car = result["carousel"]
        if draft and notify_telegram.enabled():   # Instagram set to the phone: slides, Reel, caption
            slides = [os.path.join(car["carousel_dir"], n) for n in car["slides"]]
            with open(car["caption_file"], encoding="utf-8") as f:
                ig_caption = f.read()
            notify_telegram.send_album(slides, f"INSTAGRAM carousel ({tour_label(tour) or 'PGA TOUR · '}{event_name}) — caption:\n\n{ig_caption}")
            if car.get("reel"):
                notify_telegram.send_file(car["reel"], "INSTAGRAM Reel (add the song in the app; share to Story)")
    except Exception as e:  # the picks card above stands on its own
        print(f"carousel failed ({type(e).__name__}: {e}); picks card unaffected", file=sys.stderr)
        result["carousel"] = f"FAILED: {e}"
    if dry_run or draft:
        result["picks"] = {slot: f"{p['name']} — {p['why']}" for slot, p in picks.items()}
    return result


MAX_LIVE_ALERTS = 3   # per round, per tour


def _qa_gate(tour: str, event_name: str, post_text: str, expect_final: bool, check_row=None, extra: dict = None):
    """Last check before an UNATTENDED post: re-fetch the live feed, verify it in code, then an independent
    Claude review of the post against the fresh numbers. Returns (passed, reason). Any doubt = fail."""
    import json
    try:
        live = datagolf.get_live_in_play(tour=tour)
    except Exception as e:
        return False, f"re-fetch failed ({type(e).__name__})"
    info = live.get("info") or {}
    if transform.event_key(info.get("event_name")) != transform.event_key(event_name):
        return False, f"feed moved on to {info.get('event_name')!r}"
    if transform.is_final(live) != expect_final:
        return False, "event final status changed" if expect_final else "event is now final"
    rows = transform.sorted_leaderboard(live)
    if check_row:
        ok, why = check_row(info, rows)
        if not ok:
            return False, why
    def slim(r):
        return {k: r.get(k) for k in ("player_name", "current_pos", "current_score", "today", "thru", "round")}
    raw = {"event": info.get("event_name"), "current_round": info.get("current_round"), "final": expect_final,
           "top_5": [slim(r) for r in rows[:5]], **(extra or {})}
    return content.qa_review(json.dumps(raw, ensure_ascii=False, indent=1), post_text)


def _alert_row_check(m: dict):
    """Code checks for one alert: the player's numbers in the fresh feed still match what the alert says."""
    def check(info, rows):
        if str(info.get("current_round")) != str(m["round"]):
            return False, f"round changed ({m['round']} -> {info.get('current_round')})"
        row = next((r for r in rows if str(r.get("dg_id")) == str(m["dg_id"])), None)
        if not row:
            return False, "player not in the fresh feed"
        now = {"score": transform._score_int(row.get("current_score")), "pos": str(row.get("current_pos", "")),
               "thru": transform._thru_int(row.get("thru"))}
        for k, v in now.items():
            if v != m[k]:
                return False, f"{k} changed since detection ({m[k]} -> {v})"
        if m["kind"] == "big_hole" and not (2 <= m["drop"] <= 4 and 1 <= m["thru"] <= 18):
            return False, f"implausible single-hole move ({m['drop']} under, thru {m['thru']})"
        return True, ""
    return check


def _live_alert(m: dict, tour: str, event_name: str, dry_run: bool, draft: bool, auto: bool = False):
    """Render + post one Live Alert card. Drafts: lines from the writer (checked: numbers must be in the facts,
    and no eagle/ace wording when the moment was inferred), data-only fallback otherwise.
    auto (posting for real, unattended): data-only lines and caption, no AI wording at all."""
    facts = f"Tour: {TOUR_NAMES.get(tour, tour)}. Event: {event_name}.\n{m['facts']}"
    options = []
    if auto:
        line1, line2, reaction = transform.moment_fallback(m)
        image_path = os.path.join(OUTPUT_DIR, "live_alert.png")
        render_live_alert(hole_moment=tour_label(tour) + m["hole_moment"], event_line_1=line1.upper(),
                          event_line_2=line2.upper(), reaction=reaction, out_path=image_path)
        extra = {"alert_player_previous_poll": {k: m[k] for k in ("prev_score", "prev_thru", "prev_pos") if k in m},
                 "alert_player_today_vs_par": m.get("today")}
        passed, why = _qa_gate(tour, event_name,
                               f"CARD: {tour_label(tour)}{m['hole_moment']} | {line1} {line2} | {reaction}\nCAPTION: {m['caption']}",
                               expect_final=False, check_row=_alert_row_check(m), extra=extra)
        if not passed:   # never posts on doubt: goes to Mike as a draft with the reason
            out = _post_everywhere(image_path, f"QA HELD ({why}). Check before posting:\n{m['caption']}",
                                   dry_run=dry_run, draft=True, live=True)
            return {**out, "qa": why}
        return {**_post_everywhere(image_path, m["caption"], dry_run=dry_run, draft=draft, live=True), "qa": "PASS"}
    try:
        res = content.generate_live_reaction(facts)
        if res:
            options = [res] + res.get("alternatives", [])
    except Exception as e:
        if not (dry_run or draft):
            print(f"live alert writer failed ({type(e).__name__}); using data-only lines", file=sys.stderr)
    if m.get("inferred"):
        options = [o for o in options if not any(w in " ".join((o["event_line_1"], o["event_line_2"], o["reaction"])).lower()
                                                  for w in transform.INFERRED_HOLE_WORDS)]
    if options:
        best = options[0]
        line1, line2, reaction = best["event_line_1"], best["event_line_2"], best["reaction"]
    else:
        line1, line2, reaction = transform.moment_fallback(m)
    image_path = os.path.join(OUTPUT_DIR, "live_alert.png")
    render_live_alert(hole_moment=tour_label(tour) + m["hole_moment"], event_line_1=line1.upper(),
                      event_line_2=line2.upper(), reaction=reaction, out_path=image_path)
    alt_notes = [f"{o['event_line_1']} / {o['event_line_2']} — {o['reaction']}" for o in options[1:]]
    caption = f"{m['name']}: {reaction}" if reaction else m["name"]
    return _post_everywhere(image_path, caption, dry_run=dry_run, draft=draft, alternatives=alt_notes, live=True)


def run_live_poll(tour: str = "pga", min_leaderboard_gap_minutes: int = 60, dry_run: bool = False,
                  event_tag: str = "", draft: bool = False):
    """Called every 5 minutes during live tournament rounds (matches DataGolf's
    own refresh cadence — polling faster gains nothing). NOTE: polling every 5
    min does NOT mean posting every 5 min — see throttle below. A leaderboard
    image every 5 minutes for 4 days is 100+ posts and reads as spam, not content."""
    import time as time_module

    live = datagolf.get_live_in_play(tour=tour)
    prev = state.load(tour)
    actions_taken = []

    # shape per DataGolf docs: {"info": {"event_name", ...}, "data": [{"player_name": "Last, First",
    # "current_pos": "T2", "current_score": -14, ...}]} — sorted by position
    current_leaderboard = transform.sorted_leaderboard(live)   # the feed isn't in position order
    event_name = (live.get("info") or {}).get("event_name") or prev.get("event_name") or "LIVE"
    current_round = (live.get("info") or {}).get("current_round")
    final = transform.is_final(live)
    if dry_run:  # feed diagnostics for checking the docs' assumptions (no player data beyond the top 3)
        print("in-play info:", live.get("info"), file=sys.stderr)
        print("top 3 thru/round/end_hole:", [(r.get("current_pos"), r.get("thru"), r.get("round"), r.get("end_hole"))
                                              for r in current_leaderboard[:3]], "final:", final, file=sys.stderr)
        import collections
        print("feed rows:", len(live.get("data") or []), "| thru values:",
              dict(collections.Counter(repr(r.get("thru")) for r in live.get("data") or []).most_common(12)),
              "| row keys:", sorted((live.get("data") or [{}])[0].keys()), file=sys.stderr)
        print("info:", {k: v for k, v in (live.get("info") or {}).items()}, file=sys.stderr)
    if not current_leaderboard:
        return {"status": "no data returned, check field names / API key"}

    # only compare leaders within the same event: a fresh state or last week's leader isn't a lead change
    prev_leader = prev.get("leader_name") if prev.get("event_name") == event_name else None
    current_leader = transform.display_name(current_leaderboard[0].get("player_name", "")) if current_leaderboard else None

    # Auto mode (AUTO_POST on, unattended) posts everything, but every post first passes _qa_gate (fresh
    # feed re-check in code + independent Claude review against the voice doc's facts). A post that fails
    # QA becomes a Telegram draft marked "QA HELD" instead. Alerts/final use data-only wording.
    auto = not (dry_run or draft)

    # Trigger 1: leader change -> Hot Take (AI-written: in auto mode it posts only if QA passes)
    if current_leader and prev_leader and current_leader != prev_leader:
        try:
            take = content.generate_hot_take(f"Tour: {TOUR_NAMES.get(tour, tour)}\n"
                                             + transform.lead_change_facts(live, prev_leader))
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
            if dry_run or draft or auto:  # render the other options too, so a person can post the best card
                for i, alt in enumerate(take["alternatives"], start=1):
                    alt_path = os.path.join(OUTPUT_DIR, f"hot_take_live_alt{i}.png")
                    render_hot_take(lines=alt["lines"], kicker=alt["kicker"], out_path=alt_path)
                    alt_notes.append(f"{os.path.basename(alt_path)}: {' / '.join(alt['lines'])} {alt['kicker']}")
            ht_draft, ht_caption = draft, take["kicker"]
            if auto:   # unattended: the fresh feed must still have this leader, then the QA review
                def same_leader(info, rows):
                    lead = transform.display_name(rows[0].get("player_name", "")) if rows else None
                    return lead == current_leader, ("" if lead == current_leader else "leader changed since the card was made")
                passed, why = _qa_gate(tour, event_name, f"CARD: {' / '.join(take['lines'])}\nCAPTION: {ht_caption}",
                                       expect_final=False, check_row=same_leader)
                if not passed:
                    ht_draft, ht_caption = True, f"QA HELD ({why}). Check before posting:\n{ht_caption}"
            actions_taken.append(("hot_take", _post_everywhere(image_path, ht_caption, dry_run=dry_run,
                                                               draft=ht_draft, alternatives=alt_notes, live=True)))

    # Trigger 2: big moments (big hole / charge / collapse) -> Live Alert card. Capped so the account
    # doesn't read like a bot: at most one per poll, 20 min apart, MAX_LIVE_ALERTS per round.
    alerts = prev.get("alerts") if prev.get("event_name") == event_name else None
    if not alerts or alerts.get("round") != current_round:
        alerts = {"round": current_round, "sent": [], "last_ts": 0}
    prev_scores = prev.get("live_scores") if prev.get("event_name") == event_name else {}
    moments = [] if final else transform.detect_moments(live, prev_scores, alerts["sent"])
    gap_ok = (time_module.time() - alerts.get("last_ts", 0)) / 60 >= 20
    if moments and len(alerts["sent"]) < MAX_LIVE_ALERTS and gap_ok:
        m = moments[0]
        actions_taken.append(("live_alert", _live_alert(m, tour, event_name, dry_run, draft, auto=auto)))
        alerts["sent"].append(m["key"])
        if not dry_run:
            alerts["last_ts"] = time_module.time()
    elif moments:
        actions_taken.append(("live_alert", f"held: {len(moments)} moment(s), cap {len(alerts['sent'])}/"
                                            f"{MAX_LIVE_ALERTS} or under 20 min since the last alert"))

    # Trigger 2b: round wrap ("After Round N": leader + where our picks stand), once per round, data-only.
    # Held during quiet hours so overseas rounds become a morning post for a US audience.
    wraps = list(prev.get("wraps") or []) if prev.get("event_name") == event_name else []
    saved_picks = prev.get("last_picks") or {}
    have_picks = transform.event_key(saved_picks.get("event_name")) == transform.event_key(event_name)
    if not final and current_round and current_round not in wraps and have_picks and transform.round_complete(live):
        if notify_telegram.quiet_now():
            actions_taken.append(("round_wrap", f"round {current_round} done; held until quiet hours end"))
        else:
            wrap = transform.round_wrap(live, saved_picks, event_tag)
            image_path = os.path.join(OUTPUT_DIR, "round_wrap.png")
            render_round_wrap(wrap["title"], f"{tour_label(tour)}{transform.short_event_name(event_name).upper()}",
                              wrap["leader"], wrap["leader_score"], wrap["picks"], wrap["note"], image_path)
            caption, wrap_draft = wrap["caption"], draft
            if auto:
                def same_wrap(info, rows):
                    fresh = transform.round_wrap({"info": info, "data": rows}, saved_picks, event_tag)
                    same = (fresh["picks"], fresh["leader"], fresh["leader_score"]) == (wrap["picks"], wrap["leader"], wrap["leader_score"])
                    return same, ("" if same else "standings changed since the card was made")
                passed, why = _qa_gate(tour, event_name, f"CARD: {wrap}\nCAPTION: {caption}", expect_final=False,
                                       check_row=same_wrap)
                if not passed:
                    wrap_draft, caption = True, f"QA HELD ({why}). Check before posting:\n{caption}"
            actions_taken.append(("round_wrap", _post_everywhere(image_path, caption, dry_run=dry_run,
                                                                 draft=wrap_draft, live=False)))
            if not dry_run:
                wraps.append(current_round)

    # Trigger 3: leaderboard snapshot — throttled to once per min_leaderboard_gap_minutes,
    # NOT every poll. A poll that doesn't clear the gap just updates state and exits.
    now = time_module.time()
    last_post_ts = prev.get("last_leaderboard_post_ts", 0)
    minutes_since_last = (now - last_post_ts) / 60

    final_done = final and prev.get("final_posted") == event_name   # one final leaderboard per event
    top5 = [{"pos": str(p.get("current_pos", "")),
             "name": transform.display_name(p.get("player_name", "")),
             "score": transform.format_to_par(p.get("current_score"))}
            for p in current_leaderboard[:5]]
    # no news, no post: between rounds (or a quiet hour) the top 5 doesn't move, and the hourly
    # timer alone kept drafting the same board every 60 min (Open de Espana after R1, 2026-10-08)
    last_top5 = prev.get("last_leaderboard_top5") if prev.get("event_name") == event_name else None
    unchanged = not final and top5 == last_top5
    if final_done:
        actions_taken.append(("leaderboard", "skipped: final leaderboard already done for this event"))
    elif unchanged and minutes_since_last >= min_leaderboard_gap_minutes:
        actions_taken.append(("leaderboard", "skipped: top 5 unchanged since the last leaderboard"))
    elif final or minutes_since_last >= min_leaderboard_gap_minutes:
        image_path = os.path.join(OUTPUT_DIR, "leaderboard_live.png")
        render_leaderboard(event=event_name.upper(),
                           round_label=tour_label(tour) + ("FINAL" if final else (f"ROUND {current_round} · LIVE" if current_round else "LIVE")),
                            players=top5, out_path=image_path)
        if auto and final:      # unattended post: data-only caption
            caption, alternatives = transform.final_caption(event_name, top5) + (f" {event_tag}" if event_tag else ""), []
        else:
            caption, alternatives = _caption(
              "final leaderboard" if final else "live leaderboard",
              f"{TOUR_NAMES.get(tour, tour)}. "
              + (f"{event_name} FINAL results (the event is over), top 5: " if final
                 else f"{event_name} round {current_round}, in progress, top 5: ")
              + "; ".join(f"{p['pos']} {p['name']} {p['score']}" for p in top5), event_tag,
              fallback=(f"{event_name} final: {current_leader} wins." if final
                        else f"{event_name} leaderboard: {current_leader} leads."),
              allow_fallback=dry_run or draft or not final,
            )
        lb_draft = draft
        if auto:   # unattended leaderboard (hourly or final): same QA gate
            expected = [(p["pos"], p["name"], p["score"]) for p in top5]
            def same_top5(info, rows):
                fresh = [(str(r.get("current_pos", "")), transform.display_name(r.get("player_name", "")),
                          transform.format_to_par(r.get("current_score"))) for r in rows[:5]]
                return (fresh == expected), ("top 5 changed since the card was made" if fresh != expected else "")
            passed, why = _qa_gate(tour, event_name,
                                   f"CARD: {'final ' if final else 'live '}top 5 {expected}\nCAPTION: {caption}",
                                   expect_final=final, check_row=same_top5)
            if not passed:
                lb_draft, caption = True, f"QA HELD ({why}). Check before posting:\n{caption}"
        actions_taken.append(("leaderboard", _post_everywhere(image_path, caption, dry_run=dry_run,
                                                              draft=lb_draft,
                                                              alternatives=alternatives, live=True)))
        if not dry_run:  # a draft counts as posted, so the hourly throttle still applies
            last_post_ts = now
            last_top5 = top5
            if final:
                prev = {**prev, "final_posted": event_name}
    else:
        actions_taken.append(("leaderboard", f"skipped, only {minutes_since_last:.0f} min since last post"))

    state.save({**prev, "event_name": event_name, "leader_name": current_leader,
                "last_leaderboard_post_ts": last_post_ts, "last_leaderboard_top5": last_top5,
                "standings": transform.standings_snapshot(live),
                "live_scores": transform.live_scores(live), "alerts": alerts, "wraps": wraps}, commit=not dry_run, tour=tour)
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
    saved = state.load(tour).get("standings") or {}
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
        saved = state.load(tour).get("last_picks") or {}
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


def run_weekly_intel(dry_run: bool = False, draft: bool = False, week: int = None, event_tag: str = ""):
    """Off-week Intel Drop card from DataGolf rankings / skill ratings (updated weekly), rotating angles
    by ISO week. The number and headline come from the data; Claude writes only the comment line."""
    import datetime
    week = week if week is not None else datetime.date.today().isocalendar()[1]
    intel = transform.weekly_intel(datagolf.get_dg_rankings(), datagolf.get_skill_ratings(), week)
    try:
        lines = content.generate_supporting_line(intel["facts"])
    except Exception as e:
        if not (dry_run or draft):
            raise
        print(f"supporting line failed ({type(e).__name__}); leaving it blank", file=sys.stderr)
        lines = None
    image_path = os.path.join(OUTPUT_DIR, "intel_drop.png")
    render_intel_stat(stat=intel["stat"], what_it_means=intel["what_it_means"],
                      supporting_line=(lines or [""])[0], out_path=image_path)
    caption, alternatives = _caption("intel drop stat card", intel["facts"], event_tag,
                                     fallback=intel["summary"] + ".", allow_fallback=dry_run or draft)
    if lines and len(lines) > 1:
        alternatives = alternatives + [f"(card line) {l}" for l in lines[1:]]
    result = _post_everywhere(image_path, caption, tiktok_title=intel["summary"][:90],
                              dry_run=dry_run, draft=draft, alternatives=alternatives)
    result["intel"] = intel["summary"]
    return result


def probe(tour: str = "pga") -> dict:
    """Which DataGolf endpoints this plan can use, and their top-level shape (no player data printed).
    Run once per tour on GitHub (needs the key): python pipeline.py probe --tour euro"""
    checks = {
        "schedule": lambda: datagolf.get_schedule(tour=tour, upcoming_only=True),
        "pre_tournament": lambda: datagolf.get_pre_tournament_predictions(tour=tour),
        "outrights_win": lambda: datagolf.get_outright_odds(market="win", tour=tour),
        "in_play": lambda: datagolf.get_live_in_play(tour=tour),
        "dg_rankings": datagolf.get_dg_rankings,
        "skill_ratings": datagolf.get_skill_ratings,
    }
    out = {}
    for name, fn in checks.items():
        try:
            data = fn()
            summary = {"ok": True, "keys": sorted(data)[:12] if isinstance(data, dict) else type(data).__name__}
            for k in ("event_name", "last_updated", "current_round"):
                if isinstance(data, dict) and k in data:
                    summary[k] = data[k]
            if isinstance(data, dict) and isinstance(data.get("info"), dict):
                summary["event_name"] = data["info"].get("event_name")
            rows = next((v for v in (data.values() if isinstance(data, dict) else []) if isinstance(v, list)), None)
            if rows is not None:
                summary["rows"] = len(rows)
                if rows and isinstance(rows[0], dict):
                    summary["row_fields"] = sorted(rows[0])[:20]
            out[name] = summary
        except Exception as e:
            out[name] = {"ok": False, "error": str(e)[:160].replace(datagolf.API_KEY or "<none>", "***")}
    return out


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=["picks", "live", "newsletter", "probe", "intel", "qa-selftest"])
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
    elif args.action == "intel":
        print(run_weekly_intel(dry_run=args.dry_run, draft=args.draft, event_tag=args.event_tag))
    elif args.action == "probe":
        import json
        print(json.dumps(probe(args.tour), indent=2, default=str))
    elif args.action == "newsletter":
        print(run_weekly_newsletter(args.tour, dry_run=args.dry_run or args.draft))
    elif args.action == "qa-selftest":   # proves the real reviewer passes a correct post and fails a wrong one
        import json
        raw = json.dumps({"event": "Fake Invitational", "current_round": 2, "final": False,
                          "top_5": [{"player_name": "Hot, Cy", "current_pos": "1", "current_score": -14, "today": -7, "thru": 11}],
                          "alert_player_previous_poll": {"prev_score": -11, "prev_thru": 10}}, indent=1)
        good = "CARD: ROUND 2 · THRU 11 | HOT GOES 3 UNDER ON ONE\nCAPTION: Cy Hot goes 3 under on one hole (round 2, thru 11). Now 1 at -14."
        bad = "CARD: ROUND 2 · THRU 11 | HOT EAGLES 18\nCAPTION: Cy Hot eagles the 18th. Now 2 at -14."
        results = {"correct_post": content.qa_review(raw, good), "wrong_post": content.qa_review(raw, bad)}
        print(json.dumps(results, indent=1))
        if not (results["correct_post"][0] and not results["wrong_post"][0]):
            raise SystemExit("QA self-test FAILED: reviewer did not pass the correct post and fail the wrong one")
