"""
notify_telegram.py — sends DRAFTS to Mike's phone so he can post them by hand while
auto-posting is off. Uses a dedicated Telegram bot (NOT the trading bot).

Secrets (GitHub repo secrets, set by Mike; never in code or chat):
  TELEGRAM_DRAFTS_TOKEN    bot token from @BotFather
  TELEGRAM_DRAFTS_CHAT_ID  Mike's own chat id (from @userinfobot)
If either is missing, every function returns "skipped" and does nothing. Never raises:
a failed ping must not fail the run that produced the draft.
"""
import os

import requests

API = "https://api.telegram.org/bot{token}/{method}"
CAPTION_MAX = 1024   # Telegram's limit for media captions


def _creds():
    return os.environ.get("TELEGRAM_DRAFTS_TOKEN"), os.environ.get("TELEGRAM_DRAFTS_CHAT_ID")


def enabled() -> bool:
    token, chat = _creds()
    return bool(token and chat)


def _call(method: str, data: dict, files: dict = None) -> str:
    token, chat = _creds()
    if not (token and chat):
        return "skipped (TELEGRAM_DRAFTS_TOKEN / TELEGRAM_DRAFTS_CHAT_ID not set)"
    try:
        r = requests.post(API.format(token=token, method=method), data={"chat_id": chat, **data},
                          files=files, timeout=60)
        return "sent" if r.ok else f"FAILED: Telegram {r.status_code}"   # no body: it could echo the token
    except Exception as e:
        return f"FAILED: {type(e).__name__}"


def send_text(text: str) -> str:
    return _call("sendMessage", {"text": text[:4096]})


def send_file(path: str, caption: str = "") -> str:
    """Photo for .png/.jpg, video for .mp4, document otherwise. Long captions go as a follow-up message."""
    ext = os.path.splitext(path)[1].lower()
    method, field = (("sendPhoto", "photo") if ext in (".png", ".jpg", ".jpeg")
                     else ("sendVideo", "video") if ext == ".mp4" else ("sendDocument", "document"))
    short = caption if len(caption) <= CAPTION_MAX else ""
    with open(path, "rb") as f:
        status = _call(method, {"caption": short}, files={field: f})
    if caption and not short:
        send_text(caption)
    return status


def send_album(paths: list, caption: str = "") -> str:
    """Up to 10 photos as one album (Instagram carousel order)."""
    import json
    paths = paths[:10]
    if not paths:
        return "skipped (no files)"
    media, files = [], {}
    for i, p in enumerate(paths):
        files[f"f{i}"] = open(p, "rb")
        media.append({"type": "photo", "media": f"attach://f{i}"})
    try:
        status = _call("sendMediaGroup", {"media": json.dumps(media)}, files=files)
    finally:
        for f in files.values():
            f.close()
    if caption:
        send_text(caption)
    return status
