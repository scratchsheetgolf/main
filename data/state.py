"""
state.py — GitHub Actions runners start fresh every time, so to detect
*changes* between polls (a new leader, an eagle since last check) we need
to remember what we saw last time. Cheapest option: commit a small JSON
file back to the repo after each run. No database needed at this volume.

One file per tour (data/_last_snapshot.json for the PGA Tour,
data/_last_snapshot_<tour>.json for the others), so tours running the same
week never overwrite each other's leader / standings / saved picks. Their
workflow jobs can finish at the same moment, so save() rebases onto the
latest main and retries the push instead of silently losing the update.
"""
import json
import os
import subprocess
import sys

STATE_DIR = os.path.join(os.path.dirname(__file__), "..", "data")


def state_path(tour: str = "pga") -> str:
    name = "_last_snapshot.json" if tour in (None, "", "pga") else f"_last_snapshot_{tour}.json"
    return os.path.join(STATE_DIR, name)


def load(tour: str = "pga") -> dict:
    path = state_path(tour)
    if not os.path.exists(path):
        return {}
    with open(path, "r") as f:
        return json.load(f)


def save(snapshot: dict, commit: bool = True, tour: str = "pga") -> None:
    path = state_path(tour)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        json.dump(snapshot, f, indent=2)
    if not commit:
        return
    subprocess.run(["git", "add", path], check=False)
    subprocess.run(["git", "commit", "-m", f"update {tour or 'pga'} snapshot state"], check=False)
    for attempt in range(3):
        pulled = subprocess.run(["git", "pull", "--rebase", "--autostash"], check=False)
        pushed = subprocess.run(["git", "push"], check=False)
        if getattr(pushed, "returncode", 0) == 0 and getattr(pulled, "returncode", 0) == 0:
            return
    print(f"state.py: could not push {tour} state after 3 attempts; it will be stale next run", file=sys.stderr)
