"""
preview_cards.py — renders all five cards through render.py with three data sets,
to check layout after editing tools/build_markup.py:

  sample     normal-length content
  stress     longest realistic names/lines (should fit at full size)
  overflow   far past the budgets (auto-fit should shrink it, never clip)

SAMPLE DATA IS FAKE (picks, stats and scores are made up for layout testing).

Usage: python preview_cards.py [out_dir]   (default: output/previews)
"""
import os
import sys

import render

DATA = {
    "sample": dict(
        event="THE OPEN", round_label="ROUND 3",
        players=[("Scottie Scheffler", "-14"), ("Rory McIlroy", "-11"),
                 ("Xander Schauffele", "-10"), ("Ludvig Åberg", "-9"), ("Viktor Hovland", "-8")],
        take=["SCOTTIE IS", "BORING US INTO", "SUBMISSION AND", "WE LOVE IT."],
        kicker="— a take nobody asked for, week 11 of him winning everything",
        picks_event="THE OPEN CHAMPIONSHIP",
        picks=("Scottie Scheffler", "Tommy Fleetwood", "Jon Rahm", "Akshay Bhatia"),
        stat=("0.41", "SCHEFFLER'S SG: APPROACH THIS SEASON", "best mark on tour by a country mile"),
        live=("HOLE 17 · R4", "ALBATROSS ON", "THE PAR 5.", "...we need to talk about this round."),
    ),
    "stress": dict(
        event="ARNOLD PALMER INVITATIONAL", round_label="FINAL ROUND",
        players=[("Christiaan Bezuidenhout", "-21"), ("Matthew Fitzpatrick", "-19"),
                 ("Thorbjørn Olesen", "-18"), ("Rasmus Højgaard", "+1"), ("Byeong Hun An", "E")],
        take=["THE 16TH HOLE AT", "SAWGRASS JUST ATE", "THREE GUYS IN A ROW", "AND I CAN'T LOOK."],
        kicker="— rewatched this four times before posting, still not over it",
        picks_event="THE MEMORIAL TOURNAMENT PRESENTED BY WORKDAY",
        picks=("Christiaan Bezuidenhout", "Matthew Fitzpatrick", "Hideki Matsuyama", "Nicolai Højgaard"),
        stat=("+12.4%", "FAIRWAYS HIT VS. HIS 5-YEAR AVERAGE THIS SPRING", "and he still says he's 'working on it'"),
        live=("HOLE 18 · PLAYOFF", "HOLED OUT FROM", "THE GRANDSTAND.", "...somebody check on the guy in the front row."),
    ),
    "overflow": dict(
        event="THE MEMORIAL TOURNAMENT PRESENTED BY WORKDAY", round_label="ROUND 2 · SUSPENDED (DARKNESS)",
        players=[("Christiaan Bezuidenhout-Featherstonehaugh", "-21"), ("Matthew Fitzpatrick", "-19"),
                 ("Thorbjørn Olesen", "-18"), ("Rasmus Højgaard", "-17"), ("Byeong Hun An", "+12")],
        take=["THE ISLAND GREEN AT SAWGRASS", "JUST SWALLOWED FOUR BALLS", "IN A ROW AND HONESTLY", "I CANNOT LOOK AWAY."],
        kicker="— rewatched this nine times before posting and I am still not over it, not even close",
        picks_event="THE GENESIS SCOTTISH OPEN AT THE RENAISSANCE CLUB, NORTH BERWICK",
        picks=("Christiaan Bezuidenhout-Featherstonehaugh", "Matthew Fitzpatrick", "Hideki Matsuyama", "Nicolai Højgaard"),
        stat=("+112.4%", "FAIRWAYS HIT VS. HIS FIVE-YEAR AVERAGE THIS SPRING SWING", "and he still keeps saying he's 'working on it' in every single presser"),
        live=("HOLE 18 · SECOND PLAYOFF HOLE", "HOLED IT OUT FROM", "THE HOSPITALITY TENT.", "...somebody please check on the guy sitting in the front row."),
    ),
}


def render_set(set_name: str, out_root: str):
    d = DATA[set_name]
    out = os.path.join(out_root, set_name)
    os.makedirs(out, exist_ok=True)
    render.render_leaderboard(d["event"], d["round_label"],
                              [{"name": n, "score": s} for n, s in d["players"]],
                              os.path.join(out, "leaderboard.png"))
    render.render_hot_take(d["take"], d["kicker"], os.path.join(out, "hot_take.png"))
    render.render_weekly_picks(d["picks_event"], *d["picks"], out_path=os.path.join(out, "weekly_picks.png"))
    render.render_intel_stat(*d["stat"], out_path=os.path.join(out, "intel_stat.png"))
    render.render_live_alert(*d["live"], out_path=os.path.join(out, "live_alert.png"))


if __name__ == "__main__":
    out_root = sys.argv[1] if len(sys.argv) > 1 else os.path.join(render.OUTPUT_DIR, "previews")
    for name in DATA:
        render_set(name, out_root)
        print(f"{name}: ok")
    print(f"previews in {out_root}")
