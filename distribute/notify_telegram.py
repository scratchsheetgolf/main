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


def quiet_now(now=None) -> bool:
    """True during Mike's quiet hours, when live drafts are held (not pinged). Repo variables:
    QUIET_TZ (default America/Los_Angeles), QUIET_START/QUIET_END hours (default 22 -> 7)."""
    import datetime
    from zoneinfo import ZoneInfo
    tz = ZoneInfo(os.environ.get("QUIET_TZ") or "America/Los_Angeles")
    start, end = int(os.environ.get("QUIET_START") or 22), int(os.environ.get("QUIET_END") or 7)
    hour = (now or datetime.datetime.now(tz)).astimezone(tz).hour
    return (hour >= start or hour < end) if start > end else (start <= hour < end)


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
        if r.ok:
            return "sent"
        try:   # Telegram's short reason ("Bad Request: chat not found"); token stripped in case it's ever echoed
            reason = str(r.json().get("description", ""))[:120].replace(token, "[token]")
        except Exception:
            reason = ""
        return f"FAILED: Telegram {r.status_code}" + (f" ({reason})" if reason else "")
    except Exception as e:
        return f"FAILED: {type(e).__name__}"


def send_text(text: str) -> str:
    return _call("sendMessage", {"text": text[:4096]})


def send_file(path: str, caption: str = "", respect_quiet: bool = False) -> str:
    """Photo for .png/.jpg, video for .mp4, document otherwise. Long captions go as a follow-up message.
    respect_quiet: skip during quiet hours (live drafts; the next leaderboard after quiet hours pings)."""
    if respect_quiet and quiet_now():
        return "held (quiet hours)"
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
