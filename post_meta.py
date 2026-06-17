"""
post_meta.py — posts to a Facebook Page and an Instagram Business/Creator
account via the Meta Graph API. Free to use; the cost is time, not money —
Meta requires App Review (~10 days) before a non-admin/tester can use these
permissions in production. While waiting, you (as the app's admin) can test
against your own Page/IG account immediately.

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

Important: Instagram's API requires image_url to be a public URL, not a file
upload — use distribute/image_host.py to get one before calling post_to_instagram.
"""
import os
import requests

GRAPH_VERSION = "v21.0"
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

    # Step 2: publish it
    publish_resp = requests.post(
        f"https://graph.facebook.com/{GRAPH_VERSION}/{IG_USER_ID}/media_publish",
        data={"creation_id": creation_id, "access_token": ACCESS_TOKEN},
        timeout=30,
    )
    publish_resp.raise_for_status()
    return publish_resp.json()


if __name__ == "__main__":
    if not ACCESS_TOKEN:
        print("No META_PAGE_ACCESS_TOKEN set — expected here. Module ready once App Review clears.")
