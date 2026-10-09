"""
music.py — background music for Instagram Reels.

The clips are 9-second, loudness-normalised cuts of Meta Sound Collection tracks (licensed for
use on Facebook/Instagram only; sources and terms in Mike's local audio/LICENSES.md). This repo is
public and the license doesn't allow redistribution, so the clips are committed ENCRYPTED
(brand/audio/*.m4a.enc, AES-256-CBC via openssl, key in the AUDIO_KEY secret). Without the key,
or if anything fails, pick_clip() returns None and the post goes out as a plain image instead.

Add a clip:  openssl enc -aes-256-cbc -pbkdf2 -iter 200000 -salt -pass env:AUDIO_KEY \
             -in clip.m4a -out brand/audio/<name>.m4a.enc
"""
import datetime
import os
import subprocess
import tempfile

AUDIO_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "brand", "audio")


def clips() -> list:
    if not os.path.isdir(AUDIO_DIR):
        return []
    return sorted(os.path.join(AUDIO_DIR, f) for f in os.listdir(AUDIO_DIR) if f.endswith(".m4a.enc"))


def pick_clip(now: datetime.datetime = None) -> str:
    """Decrypts one clip to a temp file and returns its path, or None (no key / no clips / failure).
    Rotates by hour so back-to-back posts don't share a track."""
    key, pool = os.environ.get("AUDIO_KEY"), clips()
    if not key or not pool:
        return None
    now = now or datetime.datetime.now(datetime.timezone.utc)
    src = pool[(now.timetuple().tm_yday * 24 + now.hour) % len(pool)]
    out = os.path.join(tempfile.mkdtemp(), os.path.basename(src)[:-len(".enc")])
    r = subprocess.run(["openssl", "enc", "-d", "-aes-256-cbc", "-pbkdf2", "-iter", "200000",
                        "-pass", "env:AUDIO_KEY", "-in", src, "-out", out], capture_output=True)
    return out if r.returncode == 0 and os.path.getsize(out) > 0 else None
