"""
state.py — GitHub Actions runners start fresh every time, so to detect
*changes* between polls (a new leader, an eagle since last check) we need
to remember what we saw last time. Cheapest option: commit a small JSON
file back to the repo after each run. No database needed at this volume.
"""
import json
import os
import subprocess

STATE_PATH = os.path.join(os.path.dirname(__file__), "..", "data", "_last_snapshot.json")


def load() -> dict:
    if not os.path.exists(STATE_PATH):
        return {}
    with open(STATE_PATH, "r") as f:
        return json.load(f)


def save(snapshot: dict, commit: bool = True) -> None:
    with open(STATE_PATH, "w") as f:
        json.dump(snapshot, f, indent=2)
    if commit:
        subprocess.run(["git", "add", STATE_PATH], check=False)
        subprocess.run(["git", "commit", "-m", "update live snapshot state"], check=False)
        subprocess.run(["git", "push"], check=False)
