"""
datagolf.py — thin client for the DataGolf API (https://datagolf.com/api-access).

Requires a Scratch Plus membership ($30/mo) for an API key. Set DATAGOLF_API_KEY
as an environment variable (GitHub Actions: store as a repo secret).

Note on real-time: DataGolf's live endpoints (in-play predictions, live stats)
refresh every 5 minutes server-side. Polling more often than that gets you
nothing extra — it just burns your rate limit (45 requests/min). Poll on a
5-minute cadence during live rounds and you've got everything they offer.
"""
import os
import requests

BASE_URL = "https://feeds.datagolf.com"
API_KEY = os.environ.get("DATAGOLF_API_KEY")
RATE_LIMIT_PER_MIN = 45  # documented limit, applies across all endpoints combined


class DataGolfError(Exception):
    pass


def _get(path: str, params: dict = None) -> dict:
    if not API_KEY:
        raise DataGolfError(
            "DATAGOLF_API_KEY not set. Get one via a Scratch Plus membership "
            "at https://datagolf.com/subscribe, then set it as an env var / repo secret."
        )
    params = dict(params or {})
    params["key"] = API_KEY
    params.setdefault("file_format", "json")
    resp = requests.get(f"{BASE_URL}/{path}", params=params, timeout=20)
    if resp.status_code == 429:
        raise DataGolfError("Rate limited (45 req/min cap) — back off and retry.")
    resp.raise_for_status()
    return resp.json()


# ---- General use ----

def get_schedule(tour: str = "all", season: str = None, upcoming_only: bool = False) -> dict:
    params = {"tour": tour, "upcoming_only": "yes" if upcoming_only else "no"}
    if season:
        params["season"] = season
    return _get("get-schedule", params)


def get_field_updates(tour: str = "pga") -> dict:
    """Tee times, WDs, Monday qualifiers for this week's field."""
    return _get("field-updates", {"tour": tour})


def get_player_list() -> dict:
    return _get("get-player-list")


# ---- Pre-tournament / rankings ----

def get_dg_rankings() -> dict:
    return _get("preds/get-dg-rankings")


def get_pre_tournament_predictions(tour: str = "pga") -> dict:
    return _get("preds/pre-tournament", {"tour": tour})


def get_skill_ratings(display: str = "value") -> dict:
    return _get("preds/skill-ratings", {"display": display})


# ---- Live (use during active rounds, poll every 5 min) ----

def get_live_in_play(tour: str = "pga") -> dict:
    """Live updating finish probabilities — closest thing to a live leaderboard feed."""
    return _get("preds/in-play", {"tour": tour})


def get_live_tournament_stats(stats: str = "sg_total,sg_app,sg_putt", round_: str = "event_cumulative") -> dict:
    return _get("preds/live-tournament-stats", {"stats": stats, "round": round_})


def get_live_hole_stats(tour: str = "pga") -> dict:
    """Scoring averages/distributions per hole — useful for spotting a hot/cold stretch."""
    return _get("preds/live-hole-stats", {"tour": tour})


# ---- Betting tools (handy for Weekly Picks / Intel Stat content) ----

def get_outright_odds(market: str = "win", tour: str = "pga") -> dict:
    return _get("betting-tools/outrights", {"market": market, "tour": tour})


if __name__ == "__main__":
    if not API_KEY:
        print("No DATAGOLF_API_KEY set — this is expected until you subscribe. "
              "Module structure is ready; wire in the key when you have it.")
    else:
        print(get_schedule(tour="pga", upcoming_only=True))
