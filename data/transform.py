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


def standings_snapshot(live: dict, top_n: int = 70) -> dict:
    """What the live poll saves to state each run, so Monday's recap has final standings even when
    historical-event-data isn't on the plan and the feed has moved on to the next event."""
    info = live.get("info") or {}
    rows = sorted_leaderboard(live)[:top_n]
    return {"event_name": info.get("event_name"), "round": info.get("current_round"),
            "event_stats": [{"dg_id": r.get("dg_id"), "player_name": r.get("player_name", ""),
                             "fin_text": str(r.get("current_pos", ""))} for r in rows]}


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
    return [{"pos": str(r.get("fin_text", "")), "name": display_name(r.get("player_name", ""))} for r in rows[:n]]


def pick_results(saved_picks: dict, event_results: dict) -> list:
    """[{'slot': 'win', 'name': ..., 'finish': 'T12' or 'not in results'}] for last week's saved picks."""
    by_id = {r.get("dg_id"): str(r.get("fin_text", "")) for r in event_results.get("event_stats") or []}
    out = []
    for slot in ("win", "value", "fade", "sleeper"):
        p = (saved_picks.get("picks") or {}).get(slot)
        if p:
            out.append({"slot": slot, "name": p["name"], "finish": by_id.get(p.get("dg_id"), "not in results")})
    return out
