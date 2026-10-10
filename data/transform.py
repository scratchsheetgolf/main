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
import re
from datetime import date
from statistics import median

# ---- Pick rules (tune here; each pick explains itself in the returned "why") ----
VALUE_MIN_WIN_PROB = 0.015    # value pick must have >= 1.5% model win chance (no lottery tickets)
VALUE_MIN_EDGE = 1.0          # value pick: model must be ABOVE the books' no-vig price (ratio > this)
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
    """dg_id -> {'median_odds': x, 'implied': p} from the sportsbook columns (ignores DG's own column).
    'implied' has the books' margin (vig) removed: raw 1/odds across a field sums to well over 100%
    (first real run, Baycurrent 2026: every player's raw books % sat above the model's), so the raw
    numbers are scaled down proportionally until the field sums to 100%. Without this the model
    almost never looks 'above the books' and VALUE degrades to the least-bad ratio."""
    skip = {"player_name", "dg_id", "datagolf"}
    out = {}
    for row in outrights.get("odds") or []:
        prices = [float(v) for k, v in row.items()
                  if k not in skip and isinstance(v, (int, float)) and v > 1.0]
        if len(prices) >= MIN_BOOKS:
            med = median(prices)
            out[row.get("dg_id")] = {"median_odds": med, "implied": 1.0 / med}
    overround = sum(m["implied"] for m in out.values())
    if overround > 1.0:          # only ever scale down (a partly priced field can sum below 100%)
        for m in out.values():
            m["implied"] /= overround
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
    if value["model_win"] / value["market_win"] <= VALUE_MIN_EDGE:
        raise PicksError("no player has the model above the books' no-vig price; no honest VALUE pick this week")
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


def model_vs_books(pick: dict) -> str:
    """Plain statement of which side is higher, so the writer can't flip it."""
    if not pick.get("market_win"):
        return "No sportsbook consensus to compare with."
    side = "HIGHER" if pick["model_win"] > pick["market_win"] else "LOWER"
    return (f"The model is {side} than the books on him (model {pick['model_win']:.1%} vs books "
            f"{pick['market_win']:.1%}, books' margin removed).")


def picks_summary(event_name: str, picks: dict) -> str:
    """Plain-text facts for the caption writer. Only numbers computed above."""
    lines = [f"Event: {event_name}"]
    for slot in ("win", "value", "fade", "sleeper"):
        lines.append(f"{slot.upper()}: {picks[slot]['name']} ({picks[slot]['why']}). {model_vs_books(picks[slot])}")
    return "\n".join(lines)



def event_key(name) -> str:
    """Comparable event name: no accents/punctuation/case, sponsor tail dropped.
    'Open de España presented by Madrid' -> 'open de espana' (first real DP World run, 2026-10-06:
    predictions and schedule named the same event differently)."""
    import unicodedata
    text = unicodedata.normalize("NFKD", name or "").encode("ascii", "ignore").decode().lower()
    text = re.split(r"\bpresented by\b|\bpres\. by\b|\bsponsored by\b", text)[0]
    return " ".join(re.sub(r"[^a-z0-9 ]+", " ", text).split())


def short_event_name(name: str) -> str:
    """Name for cards: sponsor tail dropped, accents kept. 'Open de España presented by Madrid' -> 'Open de España'."""
    return re.split(r"\s+(?:presented by|pres\. by|sponsored by)\s+", name or "", flags=re.I)[0].strip()


def check_picks_window(event_name: str, schedule: dict, today: date = None) -> dict:
    """Picks compare DataGolf's PRE-tournament model with sportsbook odds. Once an event starts the
    books switch to live odds and the comparison is meaningless (a run during round 4 wanted to
    'fade' the 54-hole leader), so refuse after the start date. Returns the schedule entry."""
    today = today or date.today()
    target = event_key(event_name)
    candidates = [ev for ev in schedule.get("schedule") or []
                  if target and (event_key(ev.get("event_name")) == target
                                 or target in event_key(ev.get("event_name"))
                                 or (event_key(ev.get("event_name")) and event_key(ev.get("event_name")) in target))]
    if len(candidates) > 1:   # loose match hit several events: keep exact matches only
        candidates = [ev for ev in candidates if event_key(ev.get("event_name")) == target] or candidates[:0]
    for ev in candidates:
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


def outright_leader(rows: list):
    """Display name of the sole leader, or None when the top is tied (or scores are unreadable).
    Tied players swap order between polls, so only an outright leader counts for lead changes."""
    if not rows:
        return None
    top = _score_int(rows[0].get("current_score"))
    if top is None:
        return None
    if len(rows) > 1:
        second = _score_int(rows[1].get("current_score"))
        if second is None or second <= top:
            return None
    return display_name(rows[0].get("player_name", ""))


def hot_take_caption(lines: list, kicker: str, rows: list) -> str:
    """Caption for a hot take: the card's ALL-CAPS take as a normal sentence, then the kicker.
    The kicker alone ('— one shot back is close enough...') read as a cut-off caption (2026-10-09)."""
    import re
    text = re.sub(r"\s+", " ", " ".join(l.strip() for l in lines)).strip().lower()
    # put player names back in proper case (longest first, so full names win over surnames)
    names = sorted({display_name(r.get("player_name", "")) for r in rows[:30]} - {""}, key=len, reverse=True)
    parts = {p for n in names for p in [n, *n.split()] if len(p) > 2}
    for p in sorted(parts, key=len, reverse=True):
        text = re.sub(rf"(?<![\w']){re.escape(p.lower())}(?![\w])", p, text)
    text = text[:1].upper() + text[1:]
    text = re.sub(r"(^|[.!?] )([a-z])", lambda m: m.group(1) + m.group(2).upper(), text)
    tail = re.sub(r"^[\s\u2014\u2013-]+", "", kicker or "").strip()
    tail = tail[:1].upper() + tail[1:]
    return f"{text}\n\n{tail}" if tail else text


MIN_CAPTION_CHARS = 25


def caption_problem(caption: str) -> str:
    """Code check on a caption before a real post: '' if it reads like a whole caption, else the reason.
    Catches a fragment like '— one shot back is close enough...' (Open de Espana R2, 2026-10-09)."""
    text = (caption or "").strip()
    if len(text) < MIN_CAPTION_CHARS:
        return f"caption too short ({len(text)} chars)"
    if text[0] in "—–-….,;:)" or text[0].islower():
        return f"caption starts mid-sentence ({text[:20]!r})"
    return ""


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

def _score_int(v):
    """-12 / "-12" / "+3" / "E" -> int; anything else -> None."""
    text = str(v).strip().upper() if v is not None else ""
    if text == "E":
        return 0
    try:
        return int(text.replace("+", ""))
    except ValueError:
        return None


def _thru_int(v):
    """Holes completed this round: "F"/"18" -> 18, "7"/"7*" -> 7; anything else (not started, a tee time
    like "1:20 PM", blank) -> 0. Strict on purpose: pulling digits out of a tee time ("1:20" -> 120) made a
    half-played round look finished (Open de España R1, 2026-10-08)."""
    text = str(v if v is not None else "").strip().upper().rstrip("*")
    if text == "F":
        return 18
    if text.isdigit() and 0 <= int(text) <= 18:
        return int(text)
    return 0


def live_scores(live: dict) -> dict:
    """Compact per-player state the live poll keeps between runs to spot big moments."""
    out = {}
    for rank, r in enumerate(sorted_leaderboard(live), start=1):
        score = _score_int(r.get("current_score"))
        if r.get("dg_id") is None or score is None:
            continue
        out[str(r["dg_id"])] = {"name": display_name(r.get("player_name", "")), "pos": str(r.get("current_pos", "")),
                                "score": score, "today": _score_int(r.get("today")),
                                "thru": _thru_int(r.get("thru")), "rank": rank}
    return out


# ---- Hole-by-hole, built from consecutive polls (the feed only has round totals) ----

def start_hole(row: dict) -> int:
    """10 for a back-nine starter (the feed's end_hole is 9), else 1."""
    return 10 if str(row.get("end_hole", "")).strip() == "9" else 1


def hole_number(start: int, n: int) -> int:
    """The n-th hole a player plays this round, given where they started."""
    return (start - 1 + n - 1) % 18 + 1


def track_holes(prev: dict, live: dict) -> dict:
    """Per-player hole scores for the current round, learned only when exactly ONE hole was played
    between two polls (then that hole's score vs par = change in 'today'). If a poll was missed and a
    player jumped 2+ holes, those holes stay unknown: never guessed. prev/returned shape:
    {dg_id: {"round", "start", "thru", "today", "course", "rel": {"7": -1, ...}}}"""
    rnd_default = (live.get("info") or {}).get("current_round")
    out = {}
    for r in live.get("data") or []:
        dg = r.get("dg_id")
        if dg is None:
            continue
        dg = str(dg)
        rnd = r.get("round") or rnd_default
        thru, today = _thru_int(r.get("thru")), _score_int(r.get("today"))
        p = (prev or {}).get(dg)
        if not p or p.get("round") != rnd or thru < (p.get("thru") or 0):
            p = {"round": rnd, "start": start_hole(r), "thru": thru, "today": today,
                 "course": r.get("course"), "rel": {}}
        elif thru == p["thru"] + 1 and today is not None and p.get("today") is not None:
            p = {**p, "rel": {**p["rel"], str(hole_number(p["start"], thru)): today - p["today"]}}
        p = {**p, "thru": thru, "today": today}
        out[dg] = p
    return out


def pars_from_hole_stats(stats: dict, rnd=None) -> dict:
    """{course_code: {hole: par}} from DataGolf live-hole-stats (pars don't change by round)."""
    out = {}
    for c in (stats or {}).get("courses") or []:
        rounds = c.get("rounds") or []
        pick = next((x for x in rounds if x.get("round_num") == rnd), rounds[0] if rounds else {})
        holes = {str(h.get("hole")): h.get("par") for h in pick.get("holes") or [] if h.get("par")}
        if holes:
            out[str(c.get("course_code") or "")] = holes
    return out


def alert_scorecard(dg_id: str, holes: dict, pars: dict) -> dict:
    """The nine the player is on, their pars and known hole scores, for the alert card's strip.
    {"holes": [9 numbers], "pars": [int|None], "rel": [int|None]}"""
    h = (holes or {}).get(str(dg_id)) or {}
    start, thru = h.get("start", 1), h.get("thru") or 0
    first = start if thru <= 9 else (1 if start == 10 else 10)
    nine = list(range(first, first + 9))
    course_pars = (pars or {}).get(str(h.get("course") or "")) or (next(iter(pars.values())) if len(pars or {}) == 1 else {})
    rel = h.get("rel") or {}
    return {"holes": nine, "pars": [course_pars.get(str(n)) for n in nine], "rel": [rel.get(str(n)) for n in nine]}


INFERRED_HOLE_WORDS = ["eagle", "albatross", "double eagle", "ace", "hole-in-one", "hole in one", "holed out"]


def detect_moments(live: dict, prev_scores: dict, already_sent=()) -> list:
    """Big moments since the last poll, best first. Each: {key, kind, name, hole_moment, facts, inferred}.
    The in-play feed has totals per player, not hole-by-hole scores, so a single-hole move is INFERRED
    from two polls with exactly one hole played between them; the card never names the hole or calls it
    an eagle/ace (we don't know the par). Kinds:
      big_hole  2+ under on one hole, player inside the top 30
      charge    6+ under for the round, inside the top 10
      collapse  top 3 at the last poll, now 3+ over for the round"""
    info = live.get("info") or {}
    rnd = info.get("current_round") or "?"
    now = live_scores(live)
    leader = next(iter(sorted(now.values(), key=lambda x: x["rank"])), None)
    moments = []
    for dg, cur in now.items():
        prev = (prev_scores or {}).get(dg)
        base = {"dg_id": dg, "score": cur["score"], "pos": cur["pos"], "thru": cur["thru"], "round": rnd}
        where = f"now {cur['pos']} at {format_to_par(cur['score'])}"
        lead_line = (f"Leader: {leader['name']} at {format_to_par(leader['score'])}."
                     if leader and leader["name"] != cur["name"] else "He leads the tournament.")
        if prev and cur["thru"] - prev["thru"] == 1 and prev["score"] - cur["score"] >= 2 and cur["rank"] <= 30:
            drop = prev["score"] - cur["score"]
            moments.append({**base,
                "key": f"hole:{dg}:{rnd}:{cur['thru']}", "kind": "big_hole", "priority": 3 if drop >= 3 else 2, "drop": drop,
                "prev_score": prev["score"], "prev_thru": prev["thru"],
                "caption": (f"{cur['name']} goes {drop} under on one hole (round {rnd}, thru {cur['thru']}). "
                            f"Now {cur['pos']} at {format_to_par(cur['score'])}."),
                "name": cur["name"], "hole_moment": f"ROUND {rnd} · THRU {cur['thru']}", "inferred": True,
                "facts": (f"{cur['name']} just played one hole in {drop} under par: his total went from "
                          f"{format_to_par(prev['score'])} to {format_to_par(cur['score'])} with one more hole "
                          f"completed (thru {cur['thru']}, round {rnd}), {where}. {lead_line} The feed doesn't say "
                          "which hole it was or its par, so do NOT name the hole and do NOT call it an eagle, ace, "
                          "albatross or hole-in-one; say how many under he went on one hole.")})
        today = cur["today"]
        if today is not None and today <= -6 and cur["rank"] <= 10 and cur["thru"] < 18:
            moments.append({**base,
                "key": f"charge:{dg}:{rnd}", "kind": "charge", "priority": 1, "inferred": False, "today": today,
                "caption": (f"{cur['name']} is {format_to_par(today)} through {cur['thru']} in round {rnd}. "
                            f"Now {cur['pos']} at {format_to_par(cur['score'])}."),
                "name": cur["name"], "hole_moment": f"ROUND {rnd} · THRU {cur['thru']}",
                "facts": (f"{cur['name']} is {format_to_par(today)} for round {rnd} through {cur['thru']} holes, "
                          f"{where}. {lead_line}")})
        if prev and prev["rank"] <= 3 and today is not None and today >= 3:
            moments.append({**base,
                "key": f"collapse:{dg}:{rnd}", "kind": "collapse", "priority": 1, "inferred": False,
                "prev_pos": prev["pos"], "today": today,
                "caption": (f"{cur['name']} was {prev['pos']} and is {format_to_par(today)} for round {rnd} "
                            f"through {cur['thru']}. Now {cur['pos']} at {format_to_par(cur['score'])}."),
                "name": cur["name"], "hole_moment": f"ROUND {rnd} · THRU {cur['thru']}",
                "facts": (f"{cur['name']} was {prev['pos']} at the last check but is {format_to_par(today)} for "
                          f"round {rnd} through {cur['thru']} holes, {where}. {lead_line}")})
    moments = [m for m in moments if m["key"] not in set(already_sent)]
    return sorted(moments, key=lambda m: (-m["priority"], now[m["key"].split(":")[1]]["rank"]))


def final_caption(event_name: str, top5: list) -> str:
    """Data-only caption for the final leaderboard (used when posting automatically)."""
    if not top5:
        return f"{event_name}: final."
    win = top5[0]
    rest = "; ".join(f"{p['pos']} {p['name']} {p['score']}" for p in top5[1:])
    return f"{event_name} final: {win['name']} wins at {win['score']}." + (f" Then: {rest}." if rest else "")


def live_caption(event_name: str, rnd, top5: list) -> str:
    """Data-only caption for an hourly leaderboard: who leads and by how much, nothing else. Used when
    QA rejects the AI caption, so the board still posts (the España boards were all held, 2026-10-09)."""
    scores = [(p, _score_int(p["score"])) for p in top5 if _score_int(p["score"]) is not None]
    if not scores:
        return f"{event_name}, round {rnd}: live leaderboard."
    best = scores[0][1]
    leaders = [p["name"] for p, sc in scores if sc == best]
    chasers = [(p["name"], sc) for p, sc in scores if sc != best]
    head = f"{event_name}, round {rnd}: "
    if len(leaders) == 1:
        head += f"{leaders[0]} leads at {top5[0]['score']}"
    else:
        head += f"{', '.join(leaders[:-1])} and {leaders[-1]} share the lead at {top5[0]['score']}"
    if chasers:
        nxt = chasers[0][1]
        names = [n for n, sc in chasers if sc == nxt]
        gap = nxt - best
        head += (f", {gap} shot{'s' if gap != 1 else ''} clear of " if len(leaders) == 1 else f", {gap} ahead of ") \
            + (" and ".join(names) if len(names) <= 2 else f"{len(names)} players") + f" ({format_to_par(nxt)})"
    return head + "."


INACTIVE_POS = ("CUT", "WD", "DQ", "MDF", "DNS")


def round_complete(live: dict) -> bool:
    """True once every player still in the event has finished the current round (thru 18 / F)."""
    active = [r for r in live.get("data") or [] if str(r.get("current_pos", "")).upper() not in INACTIVE_POS]
    return bool(active) and all(_thru_int(r.get("thru")) >= 18 for r in active)


def round_wrap(live: dict, saved_picks: dict, event_tag: str = "") -> dict:
    """Data-only 'After Round N' content: leader, our four picks' standing, low round of the day.
    saved_picks = state["last_picks"] for this event. Every word comes from the feed (safe to auto-post)."""
    info = live.get("info") or {}
    rnd = info.get("current_round")
    rows = sorted_leaderboard(live)
    by_id = {str(r.get("dg_id")): r for r in rows}
    lead = rows[0]
    leader, leader_score = display_name(lead.get("player_name", "")), format_to_par(lead.get("current_score"))
    lead_phrase = f"{leader} leads at {leader_score}"
    tied = [display_name(r.get("player_name", "")) for r in rows
            if _score_int(r.get("current_score")) is not None
            and _score_int(r.get("current_score")) == _score_int(lead.get("current_score"))]
    if len(tied) > 1:   # "Bridgeman leads" when Mitchell is level got the PGA R2 wrap held by QA (2026-10-09)
        last_names = [n.split()[-1] for n in tied]
        leader = " & ".join(last_names) if len(tied) == 2 else (
            ", ".join(last_names) if len(tied) == 3 else f"{len(tied)}-way tie")
        lead_phrase = (f"{', '.join(tied[:-1])} and {tied[-1]} share the lead at {leader_score}" if len(tied) <= 3
                       else f"{len(tied)} players share the lead at {leader_score}")
    picks, standing = [], {}
    for slot in ("win", "value", "fade", "sleeper"):
        pk = (saved_picks.get("picks") or {}).get(slot) or {}
        r = by_id.get(str(pk.get("dg_id")))
        pos = str(r.get("current_pos", "")) if r else "—"
        score = format_to_par(r.get("current_score")) if r else "—"
        picks.append((pk.get("name", ""), pos, score))
        standing[slot] = (pk.get("name", ""), pos, _finish_rank(pos) if r else 10_000)
    active = [r for r in rows if str(r.get("current_pos", "")).upper() not in INACTIVE_POS
              and _score_int(r.get("today")) is not None]
    low = min(active, key=lambda r: _score_int(r.get("today"))) if active else None
    last = lambda name: name.split()[-1] if name else ""
    f_name, f_pos, f_rank = standing["fade"]
    w_name, w_pos, w_rank = standing["win"]
    if f_rank <= 3:
        note = f"our fade is {'leading' if f_rank == 1 else f_pos}. we'll own that sunday."
    elif w_rank <= 5:
        note = f"the model's pick is right in it at {w_pos}."
    elif any(rank <= 10 for _, _, rank in standing.values()):
        slot, (name, pos, _) = min(standing.items(), key=lambda kv: kv[1][2])
        note = f"best of the card: {last(name)} at {pos}."
    else:
        note = f"rough day for the card. {max(4 - int(rnd or 0), 0)} rounds to fix it."
    card = " · ".join(f"{slot.upper()} {last(n)} {p}" for slot, (n, p, _) in zip(("win", "value", "fade", "sleeper"),
                                                                                 [(a, b, c) for a, b, c in picks]))
    caption = f"{info.get('event_name')} after round {rnd}: {lead_phrase}. Our card: {card}."
    if low:
        extra = f" Low round: {display_name(low.get('player_name', ''))} {format_to_par(_score_int(low.get('today')))}."
        if len(caption) + len(extra) <= 265:
            caption += extra
    if event_tag:
        caption += f" {event_tag}"
    return {"round": rnd, "title": f"After Round {rnd}", "leader": leader, "leader_score": leader_score,
            "lead_phrase": lead_phrase, "picks": picks, "note": note, "caption": caption,
            "low": ({"name": display_name(low.get("player_name", "")), "today": _score_int(low.get("today")),
                     "dg_id": str(low.get("dg_id"))} if low else None)}


def _active_scored(live: dict) -> list:
    """Players still in the event with a readable total and round score."""
    return [r for r in live.get("data") or [] if str(r.get("current_pos", "")).upper() not in INACTIVE_POS
            and _score_int(r.get("current_score")) is not None and _score_int(r.get("today")) is not None]


def _ranks(scores: dict) -> dict:
    """{id: total} -> {id: position}, golf-style (ties share the better position)."""
    return {k: 1 + sum(1 for o in scores.values() if o < v) for k, v in scores.items()}


def _pos_text(rank: int, scores: dict, k) -> str:
    return ("T" if sum(1 for o in scores.values() if o == scores[k]) > 1 else "") + str(rank)


def movers(live: dict, n: int = 5) -> list:
    """Biggest climbs this round among the players still in the event: position before today
    (total minus today's score) vs now, both ranked over the same field. Round 1 has no 'before',
    so it returns []. Each: {name, dg_id, up, before, now, today}."""
    rnd = (live.get("info") or {}).get("current_round")
    if not rnd or int(rnd) < 2:
        return []
    rows = _active_scored(live)
    now = {str(r.get("dg_id")): _score_int(r.get("current_score")) for r in rows}
    before = {str(r.get("dg_id")): _score_int(r.get("current_score")) - _score_int(r.get("today")) for r in rows}
    rn, rb = _ranks(now), _ranks(before)
    out = [{"name": display_name(r.get("player_name", "")), "dg_id": k, "up": rb[k] - rn[k],
            "before": _pos_text(rb[k], before, k), "now": _pos_text(rn[k], now, k),
            "today": _score_int(r.get("today"))}
           for r in rows for k in [str(r.get("dg_id"))] if rb[k] - rn[k] > 0]
    return sorted(out, key=lambda m: (-m["up"], rn[m["dg_id"]]))[:n]


def low_rounds(live: dict, n: int = 5) -> list:
    """Today's best rounds: [(name, '-7')], lowest first."""
    rows = sorted(_active_scored(live), key=lambda r: (_score_int(r.get("today")), _score_int(r.get("current_score"))))
    return [(display_name(r.get("player_name", "")), format_to_par(_score_int(r.get("today")))) for r in rows[:n]]


ROUND_CARD_MIN_HOLES = 14   # below this many tracked holes, the scorecard slide reads as mostly blank


def round_of_day(live: dict, holes: dict, pars: dict):
    """The low round of the day with its hole-by-hole card (front + back nine), or None when we
    tracked fewer than ROUND_CARD_MIN_HOLES of its holes (missed polls leave holes blank, never guessed).
    Among players tied for the low round, the one with the most tracked holes wins."""
    rows = [r for r in _active_scored(live) if _thru_int(r.get("thru")) >= 18]
    if not rows:
        return None
    best = min(_score_int(r.get("today")) for r in rows)
    def known(r):
        return len(((holes or {}).get(str(r.get("dg_id"))) or {}).get("rel") or {})
    r = max((r for r in rows if _score_int(r.get("today")) == best), key=known)
    if known(r) < ROUND_CARD_MIN_HOLES:
        return None
    h = holes[str(r.get("dg_id"))]
    course_pars = (pars or {}).get(str(h.get("course") or "")) or (next(iter(pars.values())) if len(pars or {}) == 1 else {})
    rel = h.get("rel") or {}
    nine = lambda first: {"holes": list(range(first, first + 9)),
                          "pars": [course_pars.get(str(x)) for x in range(first, first + 9)],
                          "rel": [rel.get(str(x)) for x in range(first, first + 9)]}
    return {"name": display_name(r.get("player_name", "")), "dg_id": str(r.get("dg_id")), "today": best,
            "pos": str(r.get("current_pos", "")), "total": format_to_par(_score_int(r.get("current_score"))),
            "known": len(rel), "front": nine(1), "back": nine(10)}


def recap_caption(event_name: str, rnd, wrap: dict, moved: list, best, event_tag: str = "") -> str:
    """Data-only caption for the round recap carousel."""
    parts = [f"{event_name} after round {rnd}: {wrap['lead_phrase']}."]
    if moved:
        m = moved[0]
        parts.append(f"Biggest climb: {m['name']}, up {m['up']} spots to {m['now']} ({format_to_par(m['today'])} today).")
    if best:
        parts.append(f"Round of the day: {best['name']} {format_to_par(best['today'])}.")
    elif wrap.get("low"):
        parts.append(f"Low round: {wrap['low']['name']} {format_to_par(wrap['low']['today'])}.")
    card = " · ".join(f"{slot.upper()} {n.split()[-1] if n else ''} {p}"
                      for slot, (n, p, _) in zip(("win", "value", "fade", "sleeper"), wrap["picks"]))
    parts.append(f"Our card: {card}.")
    text = " ".join(parts) + "\n\nSwipe for the full recap."
    return text + (f" {event_tag}" if event_tag else "")


def _to_par_int(score):
    if isinstance(score, (int, float)):
        return int(score)
    s = str(score or "").strip().upper()
    if s == "E":
        return 0
    try:
        return int(s)
    except ValueError:
        return None


def leader_hook(rows: list, final: bool = False) -> str:
    """Reel opening line, straight from the feed (never AI wording): 'BRIDGEMAN LEADS BY 1',
    'SMITH AND JONES SHARE THE LEAD', '3-WAY TIE AT -5', 'BRIDGEMAN WINS AT -18'. rows = sorted
    feed rows (current_pos, current_score, player_name). '' if the feed is too thin to say."""
    rows = [r for r in rows if _to_par_int(r.get("current_score")) is not None]
    if not rows:
        return ""
    lead = _to_par_int(rows[0].get("current_score"))
    at_top = [r for r in rows if _to_par_int(r.get("current_score")) == lead]
    last = lambda r: display_name(r.get("player_name", "")).split()[-1].upper()
    score = format_to_par(lead)
    if len(at_top) == 1:
        if final:
            return f"{last(at_top[0])} WINS AT {score}"
        behind = [_to_par_int(r.get("current_score")) for r in rows[1:]]
        return f"{last(at_top[0])} LEADS BY {behind[0] - lead}" if behind else f"{last(at_top[0])} LEADS AT {score}"
    if final:
        return ""   # a tie on a final board means a playoff or a data gap: let the card speak
    if len(at_top) == 2:
        return f"{last(at_top[0])} AND {last(at_top[1])} SHARE THE LEAD"
    return f"{len(at_top)}-WAY TIE AT {score}"


def moment_fallback(m: dict) -> tuple:
    """Card lines straight from the data, for when the writer fails or SKIPs."""
    last = m["name"].split()[-1].upper()
    if m["kind"] == "big_hole":
        return (f"{last} GOES", f"{m['drop']} UNDER ON ONE", "one hole, whole new leaderboard.")
    if m["kind"] == "charge":
        return (f"{last} IS", "ON A HEATER", "somebody check the scorecard.")
    return (f"{last} IS", "SLIPPING", "golf giveth, golf taketh away.")

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
             f"Why: {pick['why']}.", model_vs_books(pick)]
    if profile:
        lines.append("Strokes gained per round (world rank): " + "; ".join(
            f"{SKILL_NAMES[k]} {v} ({r})" for k, (v, r) in profile.items()))
    role = {"win": "the most likely winner", "value": "priced too long by the books",
            "fade": "priced too short by the books — we're against him", "sleeper": "a longshot with a real chance"}
    lines.append(f"Role: {role[slot]}.")
    return "\n".join(lines)
