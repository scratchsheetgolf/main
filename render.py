"""
render.py — fills SVG templates with real content and rasterizes to PNG.

Each function below maps a content dict to a finished image. Token names
match the __TOKEN__ placeholders in templates/*.svg exactly. If you add a
field, add it to the placeholder dict AND the SVG token, or it'll silently
stay as literal text in the output.
"""
import os
import cairosvg

TEMPLATE_DIR = os.path.join(os.path.dirname(__file__), "templates")
OUTPUT_DIR = os.path.join(os.path.dirname(__file__), "output")
IMG_SIZE = 1080


def _fill_and_render(template_filename: str, tokens: dict, out_path: str) -> str:
    """Loads an SVG template, replaces __TOKEN__ placeholders, rasterizes to PNG."""
    path = os.path.join(TEMPLATE_DIR, template_filename)
    with open(path, "r", encoding="utf-8") as f:
        svg = f.read()

    for key, value in tokens.items():
        token = f"__{key.upper()}__"
        if token not in svg:
            raise ValueError(f"Token {token} not found in {template_filename} — check spelling.")
        svg = svg.replace(token, _escape_xml(str(value)))

    # catch any tokens that were left unfilled (would render as literal "__X__" text)
    import re
    leftover = re.findall(r"__[A-Z0-9_]+__", svg)
    if leftover:
        raise ValueError(f"Unfilled tokens in {template_filename}: {set(leftover)}")

    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    cairosvg.svg2png(bytestring=svg.encode("utf-8"), write_to=out_path,
                      output_width=IMG_SIZE, output_height=IMG_SIZE)
    return out_path


def _escape_xml(text: str) -> str:
    return (text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))


def render_leaderboard(event: str, round_label: str, players: list, out_path: str) -> str:
    """players: list of up to 5 dicts like {"name": "Scottie Scheffler", "score": "-12"}"""
    players = (players + [{"name": "", "score": ""}] * 5)[:5]
    tokens = {"event": event, "round": round_label}
    for i, p in enumerate(players, start=1):
        tokens[f"player_{i}"] = p.get("name", "")
        tokens[f"score_{i}"] = p.get("score", "")
    return _fill_and_render("leaderboard.svg", tokens, out_path)


def render_hot_take(lines: list, kicker: str, out_path: str) -> str:
    """lines: up to 4 strings, one per line of the big bold quote block."""
    lines = (lines + [""] * 4)[:4]
    tokens = {f"take_line_{i+1}": line for i, line in enumerate(lines)}
    tokens["kicker"] = kicker
    return _fill_and_render("hot_take.svg", tokens, out_path)


def render_weekly_picks(event_name: str, win: str, value: str, fade: str, sleeper: str, out_path: str) -> str:
    tokens = {
        "event_name": event_name, "win_player": win, "value_player": value,
        "fade_player": fade, "sleeper_player": sleeper,
    }
    return _fill_and_render("weekly_picks.svg", tokens, out_path)


def render_intel_stat(stat: str, what_it_means: str, supporting_line: str, out_path: str) -> str:
    tokens = {"stat": stat, "what_it_means": what_it_means, "supporting_line": supporting_line}
    return _fill_and_render("intel_stat.svg", tokens, out_path)


def render_live_alert(hole_moment: str, event_line_1: str, event_line_2: str, reaction: str, out_path: str) -> str:
    tokens = {
        "hole_moment": hole_moment, "event_line_1": event_line_1,
        "event_line_2": event_line_2, "reaction": reaction,
    }
    return _fill_and_render("live_alert.svg", tokens, out_path)


if __name__ == "__main__":
    # smoke test with mock data
    render_leaderboard(
        event="THE OPEN", round_label="ROUND 3",
        players=[
            {"name": "Scottie Scheffler", "score": "-14"},
            {"name": "Rory McIlroy", "score": "-11"},
            {"name": "Xander Schauffele", "score": "-10"},
            {"name": "Ludvig Åberg", "score": "-9"},
            {"name": "Viktor Hovland", "score": "-8"},
        ],
        out_path=os.path.join(OUTPUT_DIR, "test_leaderboard.png"),
    )
    render_hot_take(
        lines=["SCOTTIE IS", "BORING US INTO", "SUBMISSION AND", "WE LOVE IT."],
        kicker="— a take nobody asked for, week 11 of him winning everything",
        out_path=os.path.join(OUTPUT_DIR, "test_hot_take.png"),
    )
    render_weekly_picks(
        event_name="THE OPEN CHAMPIONSHIP",
        win="Scottie Scheffler", value="Tommy Fleetwood",
        fade="Jon Rahm", sleeper="Akshay Bhatia",
        out_path=os.path.join(OUTPUT_DIR, "test_weekly_picks.png"),
    )
    render_intel_stat(
        stat="0.41", what_it_means="SCHEFFLER'S SG: APPROACH THIS SEASON",
        supporting_line="best mark on tour by a country mile",
        out_path=os.path.join(OUTPUT_DIR, "test_intel_stat.png"),
    )
    render_live_alert(
        hole_moment="HOLE 17 · R4", event_line_1="ALBATROSS ON",
        event_line_2="THE PAR 5.", reaction="...we need to talk about this round.",
        out_path=os.path.join(OUTPUT_DIR, "test_live_alert.png"),
    )
    print("All 5 templates rendered successfully.")
