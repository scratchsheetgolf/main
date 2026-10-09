"""Builds the round recap carousel from the live feed and saved state WITHOUT posting, and hosts the
slides like a real post. Prints the slide URLs (comma-separated) as the last line, for
`post_meta --check --carousel=...`. Usage: python tools/recap_check.py <tour> <out_dir>"""
import os
import shutil
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import pipeline  # noqa: E402
from data import datagolf, state, transform  # noqa: E402
from distribute import image_host  # noqa: E402

tour, out_dir = sys.argv[1], sys.argv[2]
live = datagolf.get_live_in_play(tour=tour)
prev = state.load(tour)
event = (live.get("info") or {}).get("event_name") or ""
same = prev.get("event_name") == event
wrap = transform.round_wrap(live, prev.get("last_picks") or {})
pars = prev.get("pars") if same and prev.get("pars") else transform.pars_from_hole_stats(
    datagolf.get_live_hole_stats(tour=tour), (live.get("info") or {}).get("current_round"))
recap = pipeline.build_round_recap(tour, event, live, wrap, prev.get("holes") if same else {}, pars)
os.makedirs(out_dir, exist_ok=True)
for p in recap["slides"]:
    shutil.copy(p, out_dir)
with open(os.path.join(out_dir, "caption.txt"), "w", encoding="utf-8") as f:
    f.write(recap["caption"] + "\n\nQA facts: " + repr(recap["facts"]) + "\n")
print(recap["caption"], file=sys.stderr)
print(",".join(image_host.publish_image(p) for p in recap["slides"]))
