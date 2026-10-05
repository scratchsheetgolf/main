"""
transform.py — turns raw DataGolf responses into what the cards and newsletter need.

Response shapes follow DataGolf's API docs (datagolf.com/api-access, checked
2026-10-04). Things the docs show that the rest of the code must not forget:
  - player_name comes as "Last, First"            -> display_name()
  - live current_score is an int (-17, 0, 3)       -> format_to_par()
  - get-schedule lists events under "schedule"
  - we request odds_format=decimal everywhere, so a probability is 1 / odds

Every number that reaches a card or the newsletter comes from here, never from
the language model. content.py only writes words around these facts.
"""
from datetime import date
from statistics import median

# ---- Pick rules (tune here; each pick explains itself in the returned "why") ----
VALUE_MIN_WIN_PROB = 0.015    # value pick must have >= 1.5% model win chance (no lottery tickets)
FADE_FROM_TOP_N = 10          # fade is chosen from the books' 10 shortest-priced players
SLEEPER_MIN_DECIMAL_ODDS = 51  # sleeper = 50-to-1 or longer at the books' median price
MIN_BOOKS = 3                 # need at least 3 sportsbook prices to call a market consensus


class PicksError(Exception):
    pass


def display_name(dg_name: str) -> str:
    """'Scheffler, Scottie' -> 'Scottie Scheffler'. Leaves names without a comma alone."""
    if not dg_name or "," not in dg_name:
        return (dg_name or "").strip()
    last, first = dg_name.split(",", 1)
    return f"{first.strip()} {last.strip()}"


def format_to_par(score) -> str:
    """-17 -> '-17', 0 -> 'E', 3 -> '+3'. Strings pass through ('E', '-5', 'CUT')."""
    if isinstance(score, str):
        return score.strip()
    if score is None:
        return ""
    score = int(score)
    return "E" if score == 0 else f"{score:+d}"


def _prob(decimal_odds):
    try:
        odds = float(decimal_odds)
    except (TypeError, ValueError):
        return None
    return 1.0 / odds if odds > 1.0 else None


def _model_rows(preds: dict) -> list:
    """Prefer the course-history-and-fit model; fall back to baseline."""
    for model in ("baseline_history_fit", "baseline"):
        rows = preds.get(model) or []
        if rows:
            return rows
    return []


def _market_by_player(outrights: dict) -> dict:
    """dg_id -> {'median_odds': x, 'implied': p} from the sportsbook columns (ignores DG's own column)."""
    skip = {"player_name", "dg_id", "datagolf"}
    out = {}
    for row in outrights.get("odds") or []:
        prices = [float(v) for k, v in row.items()
                  if k not in skip and isinstance(v, (int, float)) and v > 1.0]
        if len(prices) >= MIN_BOOKS:
            med = median(prices)
            out[row.get("dg_id")] = {"median_odds": med, "implied": 1.0 / med}
    return out


def choose_picks(preds: dict, outrights: dict) -> dict:
    """Returns {"win"|"value"|"fade"|"sleeper": {"name", "dg_id", "model_win", "market_win", "why"}}."""
    market = _market_by_player(outrights)
    players = []
    for row in _model_rows(preds):
        p = _prob(row.get("win"))
        if p is None:
            continue
        m = market.get(row.get("dg_id"))
        players.append({
            "dg_id": row.get("dg_id"),
            "name": display_name(row.get("player_name", "")),
            "model_win": p,
            "market_win": m["implied"] if m else None,
            "median_odds": m["median_odds"] if m else None,
        })
    if not players:
        raise PicksError("no model win probabilities in pre-tournament predictions")
    priced = [pl for pl in players if pl["market_win"]]
    if len(priced) < 10:
        raise PicksError(f"only {len(priced)} players have {MIN_BOOKS}+ sportsbook prices; can't judge value/fade")

    used = set()

    def take(pick, why):
        used.add(pick["dg_id"])
        return {**pick, "why": why}

    win = max(players, key=lambda pl: pl["model_win"])
    picks = {"win": take(win, f"highest model win chance ({win['model_win']:.1%})")}

    value_pool = [pl for pl in priced if pl["dg_id"] not in used and pl["model_win"] >= VALUE_MIN_WIN_PROB]
    if not value_pool:
        raise PicksError("no value candidate above the minimum win-chance floor")
    value = max(value_pool, key=lambda pl: pl["model_win"] / pl["market_win"])
    picks["value"] = take(value, f"model {value['model_win']:.1%} vs books {value['market_win']:.1%}")

    favourites = sorted(priced, key=lambda pl: pl["market_win"], reverse=True)[:FADE_FROM_TOP_N]
    fade_pool = [pl for pl in favourites if pl["dg_id"] not in used]
    fade = min(fade_pool, key=lambda pl: pl["model_win"] / pl["market_win"])
    picks["fade"] = take(fade, f"books {fade['market_win']:.1%} vs model {fade['model_win']:.1%}")

    sleeper_pool = [pl for pl in priced if pl["dg_id"] not in used and pl["median_odds"] >= SLEEPER_MIN_DECIMAL_ODDS]
    if not sleeper_pool:
        raise PicksError("no sleeper candidate at 50-to-1 or longer")
    sleeper = max(sleeper_pool, key=lambda pl: pl["model_win"])
    picks["sleeper"] = take(sleeper, f"best model chance ({sleeper['model_win']:.1%}) at {sleeper['median_odds'] - 1:.0f}-to-1")
    return picks


def picks_summary(event_name: str, picks: dict) -> str:
    """Plain-text facts for the caption writer. Only numbers computed above."""
    lines = [f"Event: {event_name}"]
    for slot in ("win", "value", "fade", "sleeper"):
        lines.append(f"{slot.upper()}: {picks[slot]['name']} ({picks[slot]['why']})")
    return "\n".join(lines)



def check_picks_window(event_name: str, schedule: dict, today: date = None) -> dict:
    """Picks compare DataGolf's PRE-tournament model with sportsbook odds. Once an event starts the
    books switch to live odds and the comparison is meaningless (a run during round 4 wanted to
    'fade' the 54-hole leader), so refuse after the start date. Returns the schedule entry."""
    today = today or date.today()
    for ev in schedule.get("schedule") or []:
        if (ev.get("event_name") or "").strip().lower() == (event_name or "").strip().lower():
            try:
                start = date.fromisoformat(ev.get("start_date", ""))
            except ValueError:
                raise PicksError(f"can't read start date for {event_name}")
            if start <= today:
                raise PicksError(f"{event_name} started {start.isoformat()}; picks are only made before round 1 "
                                 "(sportsbook odds are live once it starts)")
            return ev
    raise PicksError(f"{event_name} (from the predictions) isn't in the schedule; can't confirm it hasn't started")


def sorted_leaderboard(live: dict) -> list:
    """The in-play feed's rows in leaderboard order. The feed is NOT sorted by position (first real
    run's 'top 5' read -26, -5, -5, -16, -19), so sort by position, then score; CUT/WD/DQ last."""
    def key(r):
        rank = _finish_rank(r.get("current_pos", ""))
        try:
            score = int(r.get("current_score"))
        except (TypeError, ValueError):
            score = 10_000
        return (rank, score)
    return sorted(live.get("data") or [], key=key)


def is_final(live: dict) -> bool:
    """True once the event is over: round 4+ and everyone near the top has finished (thru 'F')."""
    info = live.get("info") or {}
    try:
        rnd = int(info.get("current_round") or 0)
    except (TypeError, ValueError):
        rnd = 0
    top = sorted_leaderboard(live)[:10]
    # the docs' example shows thru "F"; the real feed (Bank of Utah, 2026-10-04) sends thru 18 when done
    return rnd >= 4 and bool(top) and all(str(r.get("thru", "")).strip().upper() in ("F", "18") for r in top)


def standings_snapshot(live: dict, top_n: int = 70) -> dict:
    """What the live poll saves to state each run, so Monday's recap has final standings even when
    historical-event-data isn't on the plan and the feed has moved on to the next event."""
    info = live.get("info") or {}
    rows = sorted_leaderboard(live)[:top_n]
    return {"event_name": info.get("event_name"), "round": info.get("current_round"),
            "final": is_final(live),
            "event_stats": [{"dg_id": r.get("dg_id"), "player_name": r.get("player_name", ""),
                             "fin_text": str(r.get("current_pos", "")),
                             "score": format_to_par(r.get("current_score"))} for r in rows]}


def lead_change_facts(live: dict, prev_leader: str) -> str:
    """Plain-text facts for a lead-change hot take, from the in-play feed. Gives the writer real numbers
    (round, scores, margin) to work with; content.py rejects any number that isn't in here."""
    info = live.get("info") or {}
    rows = sorted_leaderboard(live)
    lead = rows[0]
    lines = [f"Event: {info.get('event_name', '')}, round {info.get('current_round', '?')}",
             f"New leader: {display_name(lead.get('player_name', ''))} at {format_to_par(lead.get('current_score'))}"
             + (f", thru {lead['thru']}" if lead.get("thru") not in (None, "") else "")]
    if len(rows) > 1:
        second = rows[1]
        try:
            margin = int(second.get("current_score")) - int(lead.get("current_score"))
            margin_text = "tied" if margin == 0 else f"{margin} shot{'s' if margin != 1 else ''} clear"
        except (TypeError, ValueError):
            margin_text = ""
        lines.append(f"Next: {display_name(second.get('player_name', ''))} at "
                     f"{format_to_par(second.get('current_score'))}" + (f" ({margin_text})" if margin_text else ""))
    lines.append(f"Previous leader: {prev_leader}")
    return "\n".join(lines)

# ---- Schedule / recap ----

def last_completed_and_next(schedule: dict, today: date = None):
    """(most recent completed event, next upcoming event) from get-schedule; either may be None."""
    today = today or date.today()
    events = schedule.get("schedule") or []
    done, upcoming = [], []
    for ev in events:
        try:
            start = date.fromisoformat(ev.get("start_date", ""))
        except ValueError:
            continue
        status = (ev.get("status") or "").lower()
        winner = (ev.get("winner") or "").strip()
        if status == "completed" or (winner and winner.upper() != "TBD"):
            done.append((start, ev))      # finished: only these get recapped
        elif start >= today:
            upcoming.append((start, ev))  # not started yet (an in-progress event is neither)
    last = max(done, key=lambda t: t[0])[1] if done else None
    nxt = min(upcoming, key=lambda t: t[0])[1] if upcoming else None
    return last, nxt


def results_from_live(live: dict, event_name: str):
    """Fallback when historical-event-data isn't on the plan (403): the in-play feed's final standings,
    in the same shape as get_event_results. None unless the feed is for event_name."""
    info = live.get("info") or {}
    if (info.get("event_name") or "").strip().lower() != (event_name or "").strip().lower():
        return None
    return {**standings_snapshot(live, top_n=10_000), "source": "live feed final standings"}


def _finish_rank(fin_text: str) -> int:
    digits = "".join(ch for ch in str(fin_text) if ch.isdigit())
    return int(digits) if digits else 10_000  # CUT / WD / DQ sort last


def top_finishers(event_results: dict, n: int = 5) -> list:
    """[{'pos': 'T2', 'name': 'Justin Rose'}] from historical-event-data/events."""
    rows = sorted(event_results.get("event_stats") or [], key=lambda r: _finish_rank(r.get("fin_text")))
    return [{"pos": str(r.get("fin_text", "")), "name": display_name(r.get("player_name", "")),
             "score": r.get("score", "")} for r in rows[:n]]


def pick_results(saved_picks: dict, event_results: dict) -> list:
    """[{'slot': 'win', 'name': ..., 'finish': 'T12' or 'not in results'}] for last week's saved picks."""
    by_id = {r.get("dg_id"): str(r.get("fin_text", "")) for r in event_results.get("event_stats") or []}
    out = []
    for slot in ("win", "value", "fade", "sleeper"):
        p = (saved_picks.get("picks") or {}).get(slot)
        if p:
            out.append({"slot": slot, "name": p["name"], "finish": by_id.get(p.get("dg_id"), "not in results")})
    return out



# ---- Weekly Intel Drop (off-week stat card) ----

INTEL_ROTATION = ["underrated", "sg_app", "sg_ott", "sg_putt", "sg_arg"]
SG_LABELS = {"sg_app": "APPROACH", "sg_ott": "OFF THE TEE", "sg_putt": "PUTTING", "sg_arg": "AROUND THE GREEN"}
SG_PLAIN = {"sg_app": "approach play", "sg_ott": "driving", "sg_putt": "putting", "sg_arg": "short game"}


def _intel_underrated(rankings: dict):
    """Biggest gap between DataGolf rank and world ranking, among DataGolf's top 50."""
    best = None
    for r in rankings.get("rankings") or []:
        dg, owgr = r.get("datagolf_rank"), r.get("owgr_rank")
        if not isinstance(dg, int) or not isinstance(owgr, int) or owgr <= 0 or dg > 50:
            continue
        gap = owgr - dg
        if gap > 0 and (best is None or gap > best[0]):
            best = (gap, r)
    if not best:
        return None
    gap, r = best
    name = display_name(r.get("player_name", ""))
    return {"kind": "underrated", "stat": str(gap),
            "what_it_means": f"{name.upper()}: DATAGOLF #{r['datagolf_rank']}, WORLD #{r['owgr_rank']}",
            "facts": (f"{name} is ranked #{r['datagolf_rank']} in the DataGolf rankings (a skill-based rating) "
                      f"but #{r['owgr_rank']} in the Official World Golf Ranking: a gap of {gap} places. "
                      f"Primary tour: {r.get('primary_tour', 'unknown')}."),
            "summary": f"{name}: DataGolf #{r['datagolf_rank']} vs world #{r['owgr_rank']} ({gap} places)"}


def _intel_sg_leader(skills: dict, cat: str):
    rows = [r for r in skills.get("players") or [] if isinstance(r.get(cat), (int, float))]
    if not rows:
        return None
    ranked = sorted(rows, key=lambda x: x[cat], reverse=True)
    r = ranked[0]
    name = display_name(r.get("player_name", ""))
    value = f"{r[cat]:+.2f}"
    runner_up = ""
    if len(ranked) > 1:
        r2 = ranked[1]
        gap = r[cat] - r2[cat]
        runner_up = (f" Second is {display_name(r2.get('player_name', ''))} at {r2[cat]:+.2f}, "
                     f"so the lead is {gap:.2f} strokes per round.")
    return {"kind": cat, "stat": value,
            "what_it_means": f"{name.upper()}: SG {SG_LABELS[cat]} PER ROUND",
            "facts": (f"{name} leads DataGolf's current skill ratings in strokes gained {SG_PLAIN[cat]}: "
                      f"{value} strokes per round vs an average tour player.{runner_up}"),
            "summary": f"{name}: best {SG_PLAIN[cat]} in DataGolf's skill ratings ({value} per round)"}


def weekly_intel(rankings: dict, skills: dict, week: int) -> dict:
    """This week's Intel Drop, rotating through INTEL_ROTATION by ISO week; falls through to the next
    angle if one has no usable data. Returns {"kind", "stat", "what_it_means", "facts", "summary"}."""
    for i in range(len(INTEL_ROTATION)):
        kind = INTEL_ROTATION[(week + i) % len(INTEL_ROTATION)]
        pick = _intel_underrated(rankings) if kind == "underrated" else _intel_sg_leader(skills, kind)
        if pick:
            return pick
    raise PicksError("no usable data in rankings or skill ratings for an Intel Drop")



# ---- Tuesday preview carousel ----

SKILL_KEYS = {"OTT": "sg_ott", "APP": "sg_app", "ARG": "sg_arg", "PUTT": "sg_putt"}
SKILL_NAMES = {"OTT": "off the tee", "APP": "approach", "ARG": "around the green", "PUTT": "putting"}


def format_odds(decimal_odds) -> str:
    """Median decimal odds -> fractional-style '29-1' (short prices keep a decimal: '0.8-1')."""
    if not decimal_odds:
        return "—"
    frac = float(decimal_odds) - 1
    return f"{frac:.0f}-1" if frac >= 1.5 else f"{frac:.1f}-1"


def skill_profile(skills: dict, dg_id) -> dict:
    """{"OTT": ("+0.45", "#12"), ...} for one player; rank among everyone in DataGolf's skill ratings."""
    rows = skills.get("players") or []
    me = next((r for r in rows if r.get("dg_id") == dg_id), None)
    if not me:
        return {}
    out = {}
    for key, field in SKILL_KEYS.items():
        value = me.get(field)
        if not isinstance(value, (int, float)):
            continue
        rank = 1 + sum(1 for r in rows if isinstance(r.get(field), (int, float)) and r[field] > value)
        out[key] = (f"{value:+.2f}", f"#{rank}")
    return out


def _win_probs(preds: dict, model: str) -> dict:
    out = {}
    for row in preds.get(model) or []:
        p = _prob(row.get("win"))
        if p is not None:
            out[row.get("dg_id")] = (display_name(row.get("player_name", "")), p)
    return out


def course_fit_boost(preds: dict, n: int = 5) -> list:
    """Players DataGolf's course history & fit model likes most vs its baseline model:
    [(name, "+1.8 pts", boost)] in win-probability percentage points. [] if both models aren't there."""
    base, fit = _win_probs(preds, "baseline"), _win_probs(preds, "baseline_history_fit")
    if not base or not fit:
        return []
    boosts = [(fit[i][0], (fit[i][1] - base[i][1]) * 100) for i in fit if i in base]
    boosts = [b for b in sorted(boosts, key=lambda x: x[1], reverse=True) if b[1] >= 0.05][:n]
    return [(name, f"+{pts:.1f} pts", pts) for name, pts in boosts]


def favorites(preds: dict, n: int = 5) -> list:
    """Top n model win chances: [(name, "18.5%")]."""
    rows = []
    for row in _model_rows(preds):
        p = _prob(row.get("win"))
        if p is not None:
            rows.append((display_name(row.get("player_name", "")), p))
    return [(name, f"{p:.1%}") for name, p in sorted(rows, key=lambda x: x[1], reverse=True)[:n]]


def pick_facts(slot: str, pick: dict, event_name: str, profile: dict) -> str:
    """Plain-text facts for one pick slide's marker note (every number shown on the slide)."""
    books = f"{pick['market_win']:.1%}" if pick.get("market_win") else "no consensus price"
    lines = [f"{pick['name']} is our {slot.upper()} pick for {event_name}.",
             f"DataGolf's model win chance: {pick['model_win']:.1%}. Sportsbooks' consensus: {books}"
             + (f" (median odds {format_odds(pick.get('median_odds'))})." if pick.get("median_odds") else "."),
             f"Why: {pick['why']}."]
    if profile:
        lines.append("Strokes gained per round (world rank): " + "; ".join(
            f"{SKILL_NAMES[k]} {v} ({r})" for k, (v, r) in profile.items()))
    role = {"win": "the most likely winner", "value": "priced too long by the books",
            "fade": "priced too short by the books — we're against him", "sleeper": "a longshot with a real chance"}
    lines.append(f"Role: {role[slot]}.")
    return "\n".join(lines)
