"""
post_meta.py — posts to a Facebook Page and an Instagram Business/Creator
account via the Meta Graph API. Free to use; the cost is time, not money.
While the app is in Development mode, Page posts it makes are visible only to people
with a role on the app (admins/testers), not the public. Going public needs Live mode,
and that needs App Review (~10 days) for pages_manage_posts / instagram_content_publish.
Start the review early; test end to end as the app admin while it's pending.

Setup:
  1. developers.facebook.com -> create an App (type: Business)
  2. Add the Page and Instagram products
  3. Generate a long-lived Page Access Token (Graph API Explorer, then exchange
     for long-lived via /oauth/access_token) with these permissions:
     pages_show_list, pages_read_engagement, pages_manage_posts,
     instagram_basic, instagram_content_publish
  4. Find your IG Business Account ID (linked to the Page) via
     GET /{page-id}?fields=instagram_business_account
  5. Env vars: META_PAGE_ID, META_PAGE_ACCESS_TOKEN, META_IG_USER_ID

Check the token without posting:  python -m distribute.post_meta --check

Important: Instagram's API requires image_url to be a public URL, not a file
upload — use distribute/image_host.py to get one before calling post_to_instagram.
"""
import os
import sys

import requests

GRAPH_VERSION = "v25.0"  # v21.0 is removed 21 Jan 2027; v25.0 is available until 29 Jul 2028
PAGE_ID = os.environ.get("META_PAGE_ID")
ACCESS_TOKEN = os.environ.get("META_PAGE_ACCESS_TOKEN")
IG_USER_ID = os.environ.get("META_IG_USER_ID")


def post_to_facebook_page(image_url: str, caption: str) -> dict:
    if not (PAGE_ID and ACCESS_TOKEN):
        raise RuntimeError("META_PAGE_ID / META_PAGE_ACCESS_TOKEN not set.")
    resp = requests.post(
        f"https://graph.facebook.com/{GRAPH_VERSION}/{PAGE_ID}/photos",
        data={"url": image_url, "caption": caption, "access_token": ACCESS_TOKEN},
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json()


def post_to_instagram(image_url: str, caption: str) -> dict:
    if not (IG_USER_ID and ACCESS_TOKEN):
        raise RuntimeError("META_IG_USER_ID / META_PAGE_ACCESS_TOKEN not set.")

    # Step 1: create a media container
    container_resp = requests.post(
        f"https://graph.facebook.com/{GRAPH_VERSION}/{IG_USER_ID}/media",
        data={"image_url": image_url, "caption": caption, "access_token": ACCESS_TOKEN},
        timeout=30,
    )
    container_resp.raise_for_status()
    creation_id = container_resp.json()["id"]

    # Step 2: wait until Instagram has fetched and processed the image (publishing straight away
    # failed with a 400 on the first live alert, 2026-10-09), then publish
    _wait_ready(creation_id, 90)
    return _publish(creation_id)


def _wait_ready(creation_id: str, wait_s: int) -> str:
    import time
    status, deadline = None, time.time() + wait_s
    while time.time() < deadline:
        status = _get(creation_id, fields="status_code").get("status_code")
        if status in ("FINISHED", "ERROR", "EXPIRED"):
            break
        time.sleep(3)
    if status != "FINISHED":
        raise RuntimeError(f"Instagram didn't finish processing (status {status})")
    return status


def _publish(creation_id: str) -> dict:
    """Publishes a finished container; returns {"id", "permalink"} so logs show where the post is."""
    pub = requests.post(f"https://graph.facebook.com/{GRAPH_VERSION}/{IG_USER_ID}/media_publish",
                        data={"creation_id": creation_id, "access_token": ACCESS_TOKEN}, timeout=30)
    if not pub.ok:
        raise RuntimeError(f"Instagram publish failed: {pub.status_code} {pub.text[:300]}")
    media_id = pub.json()["id"]
    try:
        link = _get(media_id, fields="permalink").get("permalink")
    except Exception:
        link = None
    return {"id": media_id, "permalink": link}


def recent_media(limit: int = 8) -> list:
    """What's actually on the Instagram account right now (read-only)."""
    data = _get(f"{IG_USER_ID}/media", fields="id,media_type,media_product_type,timestamp,permalink",
                limit=limit).get("data") or []
    return data


def post_reel_to_instagram(video_path: str, caption: str, publish: bool = True, wait_s: int = 240,
                           thumb_offset_ms: int = None) -> dict:
    """Uploads a Reel straight to Instagram (resumable upload: no public URL, so the licensed music
    isn't hosted anywhere public), waits for processing, then publishes it (unless publish=False,
    which leaves an unpublished container that's never shown and expires in 24 h)."""
    import time
    if not (IG_USER_ID and ACCESS_TOKEN):
        raise RuntimeError("META_IG_USER_ID / META_PAGE_ACCESS_TOKEN not set.")
    resp = requests.post(f"https://graph.facebook.com/{GRAPH_VERSION}/{IG_USER_ID}/media",
                         data={"media_type": "REELS", "upload_type": "resumable", "caption": caption,
                               "share_to_feed": "true", "access_token": ACCESS_TOKEN,
                               **({"thumb_offset": str(thumb_offset_ms)} if thumb_offset_ms is not None else {})},
                         timeout=30)
    if not resp.ok:
        raise RuntimeError(f"Reel container rejected: {resp.status_code} {resp.text[:300]}")
    creation_id = resp.json()["id"]
    with open(video_path, "rb") as f:
        data = f.read()
    up = requests.post(f"https://rupload.facebook.com/ig-api-upload/{GRAPH_VERSION}/{creation_id}",
                       headers={"Authorization": f"OAuth {ACCESS_TOKEN}", "offset": "0",
                                "file_size": str(len(data))}, data=data, timeout=120)
    if not up.ok:
        raise RuntimeError(f"Reel upload failed: {up.status_code} {up.text[:300]}")
    status = _wait_ready(creation_id, wait_s)
    if not publish:
        return {"creation_id": creation_id, "status": status, "published": False}
    return _publish(creation_id)


NEEDED_SCOPES = {"pages_show_list", "pages_read_engagement", "pages_manage_posts",
                 "instagram_basic", "instagram_content_publish"}


def _get(path: str, **params) -> dict:
    resp = requests.get(f"https://graph.facebook.com/{GRAPH_VERSION}/{path}",
                        params={**params, "access_token": ACCESS_TOKEN}, timeout=30)
    if not resp.ok:  # Meta's error body says what's wrong (expired token, missing permission, bad id)
        raise RuntimeError(f"Meta rejected GET {path}: {resp.status_code} {resp.text[:300]}")
    return resp.json()


def check(image_url: str = None, reel_path: str = None) -> dict:
    """Verifies the Meta setup WITHOUT posting: the token is a valid, non-expiring Page token with the
    publishing permissions, it belongs to META_PAGE_ID, and that Page's linked Instagram account is
    META_IG_USER_ID. Returns a report; 'problems' lists everything that would stop a post."""
    missing = [n for n, v in [("META_PAGE_ID", PAGE_ID), ("META_PAGE_ACCESS_TOKEN", ACCESS_TOKEN),
                              ("META_IG_USER_ID", IG_USER_ID)] if not v]
    if missing:
        raise RuntimeError(f"not set: {', '.join(missing)}")
    for name, value in [("META_PAGE_ID", PAGE_ID), ("META_PAGE_ACCESS_TOKEN", ACCESS_TOKEN),
                        ("META_IG_USER_ID", IG_USER_ID)]:
        if value != value.strip():
            raise RuntimeError(f"{name} has leading/trailing whitespace; re-set the secret without it")
    problems = []
    info = _get("debug_token", input_token=ACCESS_TOKEN).get("data") or {}
    scopes = set(info.get("scopes") or [])
    if not info.get("is_valid"):
        problems.append("token is not valid")
    if info.get("type") != "PAGE":
        problems.append(f"token type is {info.get('type')}, expected PAGE (use the Page token from /me/accounts)")
    if info.get("expires_at") not in (0, None):
        problems.append("token expires; exchange for a long-lived user token first, then take the Page token")
    if NEEDED_SCOPES - scopes:
        problems.append(f"missing permissions: {', '.join(sorted(NEEDED_SCOPES - scopes))}")
    if info.get("profile_id") and str(info.get("profile_id")) != str(PAGE_ID):
        problems.append(f"token is for Page {info.get('profile_id')}, not META_PAGE_ID {PAGE_ID}")
    page = _get(PAGE_ID, fields="name,instagram_business_account{id,username}")
    ig = page.get("instagram_business_account") or {}
    if not ig:
        problems.append("no Instagram Business/Creator account is linked to this Page")
    elif str(ig.get("id")) != str(IG_USER_ID):
        problems.append(f"Page's linked Instagram id is {ig.get('id')}, not META_IG_USER_ID {IG_USER_ID}")
    container = None
    if image_url and not problems:   # the real publish path minus the publish: proves Instagram can fetch
        resp = requests.post(f"https://graph.facebook.com/{GRAPH_VERSION}/{IG_USER_ID}/media",   # and accept
                             data={"image_url": image_url, "caption": "setup check (never published)",  # our image
                                   "access_token": ACCESS_TOKEN}, timeout=60)
        if not resp.ok:
            problems.append(f"Instagram rejected the test image: {resp.status_code} {resp.text[:300]}")
        else:   # an unpublished container is never shown anywhere and expires after 24 hours
            container = _get(resp.json()["id"], fields="status_code").get("status_code")
            if container == "ERROR":
                problems.append("Instagram accepted the image URL but failed to process it")
    reel = None
    if reel_path and not problems:   # the full Reel path minus the publish
        try:
            reel = post_reel_to_instagram(reel_path, "setup check (never published)", publish=False,
                                          thumb_offset_ms=500)["status"]   # same cover setting as live posts
        except Exception as e:
            problems.append(f"Reel test failed: {e}")
    return {"page_name": page.get("name"), "instagram_username": ig.get("username"),
            "instagram_test_container": container, "instagram_test_reel": reel,
            "app_id": info.get("app_id"), "token_type": info.get("type"),
            "expires": "never" if info.get("expires_at") in (0, None) else info.get("expires_at"),
            "scopes": sorted(scopes), "problems": problems}


if __name__ == "__main__":
    if "--recent" in sys.argv:
        for m in recent_media():
            print(m)
    elif "--check" in sys.argv:
        url = next((a for a in sys.argv[1:] if a.startswith("https://")), None)
        reel = next((a for a in sys.argv[1:] if a.endswith(".mp4")), None)
        result = check(url, reel)
        print(result)
        if result["problems"]:
            sys.exit("Meta setup not ready: " + "; ".join(result["problems"]))
    elif not ACCESS_TOKEN:
        print("No META_PAGE_ACCESS_TOKEN set — expected here. Module ready once App Review clears.")
