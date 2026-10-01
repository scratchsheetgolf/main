"""
newsletter.py — compiles the week's content into an HTML digest and sends it
via Mailchimp's Campaigns API.

Why Mailchimp over beehiiv: beehiiv's programmatic Create Post / Send API is
gated to Enterprise plans. Mailchimp's Campaigns API (create -> set content ->
send) works on the free tier (250 contacts / 500 sends/mo) and Essentials
(~$13/mo) once you outgrow that. No scheduling on free tier, but irrelevant
here since our own cron decides timing, not Mailchimp's UI.

Setup once you have an account:
  1. Mailchimp > Account > Extras > API keys -> generate one
  2. Note your server prefix from the key itself (e.g. key ending "-us21" -> us21)
  3. Create an Audience (list) in the Mailchimp UI, grab its List ID
  4. Set env vars: MAILCHIMP_API_KEY, MAILCHIMP_SERVER_PREFIX, MAILCHIMP_LIST_ID
"""
import os
import requests

API_KEY = os.environ.get("MAILCHIMP_API_KEY")
SERVER = os.environ.get("MAILCHIMP_SERVER_PREFIX")  # e.g. "us21"
LIST_ID = os.environ.get("MAILCHIMP_LIST_ID")


def _base_url() -> str:
    if not SERVER:
        raise RuntimeError("MAILCHIMP_SERVER_PREFIX not set (the suffix after the dash in your API key).")
    return f"https://{SERVER}.api.mailchimp.com/3.0"


def _auth():
    if not API_KEY:
        raise RuntimeError("MAILCHIMP_API_KEY not set.")
    return ("anystring", API_KEY)  # Mailchimp basic auth: any username, key as password


def compile_digest_html(week_label: str, sections: list) -> str:
    """
    sections: list of dicts like {"heading": "...", "body_html": "..."}
    Returns a single self-contained HTML string. Keep it simple — Mailchimp
    wraps this in its own template scaffolding (header/footer/unsubscribe link
    are added automatically and can't be removed, which is what you want for
    CAN-SPAM/GDPR compliance anyway).
    """
    blocks = "".join(
        f'<h2 style="font-family:Arial,sans-serif;color:#1A4A2E;">{s["heading"]}</h2>'
        f'<div style="font-family:Arial,sans-serif;color:#222;line-height:1.5;">{s["body_html"]}</div>'
        f'<hr style="border:none;border-top:1px solid #ddd;margin:24px 0;">'
        for s in sections
    )
    return (
        f'<div style="max-width:600px;margin:0 auto;">'
        f'<h1 style="font-family:Arial,sans-serif;color:#0D1F14;">The Scratch Sheet — {week_label}</h1>'
        f"{blocks}"
        f"</div>"
    )


def send_campaign(subject: str, html_content: str, from_name: str = "The Scratch Sheet") -> dict:
    """Creates a Mailchimp campaign, sets its content, and sends it immediately."""
    base = _base_url()
    auth = _auth()

    create_resp = requests.post(
        f"{base}/campaigns",
        auth=auth,
        json={
            "type": "regular",
            "recipients": {"list_id": LIST_ID},
            "settings": {
                "subject_line": subject,
                "title": subject,
                "from_name": from_name,
                "reply_to": os.environ.get("NEWSLETTER_REPLY_TO", "noreply@example.com"),
            },
        },
        timeout=20,
    )
    create_resp.raise_for_status()
    campaign_id = create_resp.json()["id"]

    content_resp = requests.put(
        f"{base}/campaigns/{campaign_id}/content",
        auth=auth,
        json={"html": html_content},
        timeout=20,
    )
    content_resp.raise_for_status()

    send_resp = requests.post(f"{base}/campaigns/{campaign_id}/actions/send", auth=auth, timeout=20)
    send_resp.raise_for_status()

    return {"campaign_id": campaign_id, "status": "sent"}


if __name__ == "__main__":
    html = compile_digest_html(
        week_label="The Open Championship",
        sections=[
            {"heading": "This Week's Picks", "body_html": "<p>Scheffler to win, Fleetwood for value...</p>"},
            {"heading": "Leaderboard Recap", "body_html": "<p>Scheffler closed it out at -14...</p>"},
        ],
    )
    print(html[:300], "...")
    if not API_KEY:
        print("\nNo MAILCHIMP_API_KEY set — expected here. Module ready for when you have an account.")
