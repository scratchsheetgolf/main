"""
post_x.py — posts an image + caption to X/Twitter.

Setup: in the X Developer Console create an App, set User authentication settings
to Read and write (BEFORE generating the access token), then set these secrets:
  X_API_KEY (Consumer Key), X_API_SECRET (Consumer Secret),
  X_ACCESS_TOKEN, X_ACCESS_SECRET            (the Bearer Token isn't used)

Cost (X pay-per-use, checked 2026-10-04): $0.015 per post created, $0.20 if the
post contains a URL — so keep links out of these posts. Reads ~$0.005-0.01 each.

Check the credentials without posting:  python -m distribute.post_x --check
"""
import os
import sys

import requests
import tweepy
from requests_oauthlib import OAuth1

API_KEY = os.environ.get("X_API_KEY")
API_SECRET = os.environ.get("X_API_SECRET")
ACCESS_TOKEN = os.environ.get("X_ACCESS_TOKEN")
ACCESS_SECRET = os.environ.get("X_ACCESS_SECRET")

V2_MEDIA_UPLOAD = "https://api.x.com/2/media/upload"


def _require_credentials():
    if not all([API_KEY, API_SECRET, ACCESS_TOKEN, ACCESS_SECRET]):
        raise RuntimeError("X API credentials not fully set (X_API_KEY/SECRET, X_ACCESS_TOKEN/SECRET).")


def _client(**kwargs) -> tweepy.Client:
    return tweepy.Client(
        consumer_key=API_KEY, consumer_secret=API_SECRET,
        access_token=ACCESS_TOKEN, access_token_secret=ACCESS_SECRET, **kwargs,
    )


def upload_media(image_path: str) -> tuple:
    """(media_id, which endpoint worked). Tries X's v2 upload first; falls back to v1.1 via tweepy
    (tweepy 4.17 only knows v1.1, which X has been retiring)."""
    errors = []
    try:
        with open(image_path, "rb") as f:
            resp = requests.post(
                V2_MEDIA_UPLOAD,
                auth=OAuth1(API_KEY, API_SECRET, ACCESS_TOKEN, ACCESS_SECRET),
                files={"media": (os.path.basename(image_path), f, "image/png")},
                data={"media_category": "tweet_image"},
                timeout=60,
            )
        if resp.ok:
            data = resp.json().get("data") or {}
            media_id = data.get("id") or data.get("media_id_string") or data.get("media_id")
            if media_id:
                return str(media_id), "v2 /2/media/upload"
        errors.append(f"v2 upload HTTP {resp.status_code}: {resp.text[:200]}")
    except Exception as e:
        errors.append(f"v2 upload failed: {e}")
    try:
        auth = tweepy.OAuth1UserHandler(API_KEY, API_SECRET, ACCESS_TOKEN, ACCESS_SECRET)
        media = tweepy.API(auth).media_upload(image_path)
        return str(media.media_id), "v1.1 media/upload (fallback)"
    except Exception as e:
        errors.append(f"v1.1 upload failed: {e}")
    raise RuntimeError("X media upload failed on both endpoints: " + " | ".join(errors))


def post_image(image_path: str, caption: str) -> dict:
    _require_credentials()
    media_id, endpoint = upload_media(image_path)
    resp = _client().create_tweet(text=caption, media_ids=[media_id])
    return {"id": resp.data["id"], "text": resp.data["text"], "media_upload": endpoint}


def check(image_path: str = None) -> dict:
    """Verifies the credentials WITHOUT posting: who they belong to, whether the token can write,
    and (if image_path is given) that an image upload works. An uploaded-but-unused image is never
    shown anywhere; X discards it."""
    _require_credentials()
    raw = _client(return_type=requests.Response).get_me(user_auth=True)
    raw.raise_for_status()
    user = (raw.json().get("data") or {})
    access_level = raw.headers.get("x-access-level", "unknown")
    out = {"username": user.get("username"), "user_id": user.get("id"), "access_level": access_level,
           "can_post": "write" in access_level}
    if image_path:
        media_id, endpoint = upload_media(image_path)
        out.update({"test_upload_media_id": media_id, "media_upload_endpoint": endpoint})
    return out


if __name__ == "__main__":
    if "--check" in sys.argv:
        img = next((a for a in sys.argv[1:] if a.endswith(".png")), None)
        result = check(img)
        print(result)
        if not result["can_post"]:
            sys.exit("Token is read-only: set Read and write in User authentication settings, then regenerate "
                     "the Access Token and Secret and update X_ACCESS_TOKEN / X_ACCESS_SECRET.")
    elif not API_KEY:
        print("No X credentials set — expected here. Module ready for production secrets.")
