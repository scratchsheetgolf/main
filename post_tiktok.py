"""
post_tiktok.py — posts a single image (or carousel) to TikTok via the
Content Posting API's photo endpoint.

IMPORTANT GOTCHA: until your app passes TikTok's audit, every post it
creates is restricted to private/self-view visibility — it will NOT be
publicly visible no matter what privacy_level you request. This is the
slowest approval of the four platforms; budget weeks, not days, and test
with private visibility in the meantime. There's no way around this, it's
a platform policy, not a bug in this code.

Setup:
  1. developers.tiktok.com -> register an app, request the
     "Content Posting API" product (this triggers the audit process)
  2. Complete OAuth to get a user access token with video.publish scope
  3. Env vars: TIKTOK_ACCESS_TOKEN

Photo URLs must be publicly accessible — use distribute/image_host.py.
"""
import os
import time
import requests

ACCESS_TOKEN = os.environ.get("TIKTOK_ACCESS_TOKEN")
BASE_URL = "https://open.tiktokapis.com/v2"


def _headers() -> dict:
    if not ACCESS_TOKEN:
        raise RuntimeError("TIKTOK_ACCESS_TOKEN not set.")
    return {"Authorization": f"Bearer {ACCESS_TOKEN}", "Content-Type": "application/json; charset=UTF-8"}


def query_creator_info() -> dict:
    """Required before posting — also tells you which privacy levels you're allowed to use."""
    resp = requests.post(f"{BASE_URL}/post/publish/creator_info/query/", headers=_headers(), timeout=20)
    resp.raise_for_status()
    return resp.json()["data"]


def post_photo(image_urls: list, title: str, description: str = "",
               privacy_level: str = "SELF_ONLY") -> dict:
    """
    image_urls: list of publicly accessible URLs (up to 35, but we're only
    using single images for these templates).
    privacy_level: 'PUBLIC_TO_EVERYONE' once audited; use 'SELF_ONLY' until then.
    """
    payload = {
        "post_info": {
            "title": title[:90],
            "description": description[:4000],
            "privacy_level": privacy_level,
        },
        "source_info": {
            "source": "PULL_FROM_URL",
            "photo_images": image_urls,
        },
        "post_mode": "DIRECT_POST",
        "media_type": "PHOTO",
    }
    resp = requests.post(f"{BASE_URL}/post/publish/content/init/", headers=_headers(), json=payload, timeout=30)
    resp.raise_for_status()
    return resp.json()["data"]  # contains publish_id


def check_status(publish_id: str) -> dict:
    resp = requests.post(
        f"{BASE_URL}/post/publish/status/fetch/", headers=_headers(),
        json={"publish_id": publish_id}, timeout=20,
    )
    resp.raise_for_status()
    return resp.json()["data"]


def post_and_wait(image_urls: list, title: str, description: str = "",
                   privacy_level: str = "SELF_ONLY", poll_seconds: int = 5, timeout_seconds: int = 60) -> dict:
    result = post_photo(image_urls, title, description, privacy_level)
    publish_id = result["publish_id"]
    elapsed = 0
    while elapsed < timeout_seconds:
        status = check_status(publish_id)
        if status.get("status") in ("PUBLISH_COMPLETE", "FAILED"):
            return status
        time.sleep(poll_seconds)
        elapsed += poll_seconds
    return {"status": "TIMED_OUT", "publish_id": publish_id}


if __name__ == "__main__":
    if not ACCESS_TOKEN:
        print("No TIKTOK_ACCESS_TOKEN set — expected here. Module ready once OAuth + audit are sorted.")
