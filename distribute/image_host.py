"""
image_host.py — Instagram and Facebook's Graph API require a publicly
reachable image URL, not a file upload. GitHub Actions runners don't expose
anything publicly by default, so this commits rendered PNGs to a dedicated
'media' branch of this same repo and returns the raw.githubusercontent.com
URL, which IS publicly fetchable. Free, no extra service needed.

Requires: the GitHub Actions workflow checks out with a token that has push
access (the default GITHUB_TOKEN works fine for this within the same repo).
"""
import os
import subprocess
import time

REPO_SLUG = os.environ.get("GITHUB_REPOSITORY", "your-username/scratch-sheet")  # set automatically in Actions
MEDIA_BRANCH = "media"


def publish_image(local_path: str) -> str:
    """Commits local_path to the media branch and returns its public raw URL."""
    filename = f"{int(time.time())}_{os.path.basename(local_path)}"
    dest_rel_path = f"posts/{filename}"

    # Lightweight approach: use git worktree so we don't disturb the main checkout.
    worktree_dir = "/tmp/media_worktree"
    subprocess.run(["git", "fetch", "origin", MEDIA_BRANCH], check=False)
    exists = subprocess.run(
        ["git", "show-ref", "--verify", f"refs/remotes/origin/{MEDIA_BRANCH}"],
        capture_output=True,
    ).returncode == 0

    if exists:
        subprocess.run(["git", "worktree", "add", worktree_dir, MEDIA_BRANCH], check=True)
    else:
        subprocess.run(["git", "worktree", "add", "-b", MEDIA_BRANCH, worktree_dir], check=True)

    os.makedirs(os.path.join(worktree_dir, "posts"), exist_ok=True)
    dest_full_path = os.path.join(worktree_dir, dest_rel_path)
    with open(local_path, "rb") as src, open(dest_full_path, "wb") as dst:
        dst.write(src.read())

    subprocess.run(["git", "add", dest_rel_path], cwd=worktree_dir, check=True)
    subprocess.run(["git", "commit", "-m", f"add {filename}"], cwd=worktree_dir, check=True)
    subprocess.run(["git", "push", "origin", MEDIA_BRANCH], cwd=worktree_dir, check=True)
    subprocess.run(["git", "worktree", "remove", worktree_dir, "--force"], check=False)

    return f"https://raw.githubusercontent.com/{REPO_SLUG}/{MEDIA_BRANCH}/{dest_rel_path}"


if __name__ == "__main__":
    print("This module needs to run inside a git repo with push access (i.e. inside the GitHub Action).")
