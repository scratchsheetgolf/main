"""
post_x.py — posts an image + caption to X/Twitter.

Setup: create a Project + App at developer.x.com, generate a User Context
access token (read+write), set these env vars:
  X_API_KEY, X_API_SECRET, X_ACCESS_TOKEN, X_ACCESS_SECRET

Cost note: as of 2026 X moved to pay-per-use pricing for new developer
accounts — roughly $0.015 per post created (more if it contains a URL, so
don't put links in these posts; put them in the caption-free Hot Take /
Leaderboard posts and save links for the newsletter). At normal posting
volume for one content account this runs a few dollars a month.
"""
import os
import tweepy

API_KEY = os.environ.get("X_API_KEY")
API_SECRET = os.environ.get("X_API_SECRET")
ACCESS_TOKEN = os.environ.get("X_ACCESS_TOKEN")
ACCESS_SECRET = os.environ.get("X_ACCESS_SECRET")


def _client() -> tweepy.Client:
    return tweepy.Client(
        consumer_key=API_KEY, consumer_secret=API_SECRET,
        access_token=ACCESS_TOKEN, access_token_secret=ACCESS_SECRET,
    )


def _api_v1() -> tweepy.API:
    # media upload still goes through the v1.1 endpoint even with v2 client
    auth = tweepy.OAuth1UserHandler(API_KEY, API_SECRET, ACCESS_TOKEN, ACCESS_SECRET)
    return tweepy.API(auth)


def post_image(image_path: str, caption: str) -> dict:
    if not all([API_KEY, API_SECRET, ACCESS_TOKEN, ACCESS_SECRET]):
        raise RuntimeError("X API credentials not fully set (X_API_KEY/SECRET, X_ACCESS_TOKEN/SECRET).")

    media = _api_v1().media_upload(image_path)
    resp = _client().create_tweet(text=caption, media_ids=[media.media_id])
    return {"id": resp.data["id"], "text": resp.data["text"]}


if __name__ == "__main__":
    if not API_KEY:
        print("No X credentials set — expected here. Module ready for production secrets.")
