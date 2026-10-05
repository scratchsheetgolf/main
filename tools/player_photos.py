"""Player photo library for the playing-card carousel slides: brand/players/.

Each photo is stored pre-cropped to the card's photo window (700:572, saved at 2x = 1400x1144)
in natural color; the brand-color treatment is applied at render time. brand/players/players.json
records, per player: name, the Wikimedia Commons source page, photographer, license, license URL,
the crop used, and the date added. Only openly licensed photos (CC BY / CC BY-SA / CC0 / public
domain) go in, each checked by eye to be the right person (name matching alone once picked a
politician who shares a golfer's name).

Add one:  python tools/player_photos.py add PHOTO.jpg --name "Scottie Scheffler" --source-page URL \\
              --artist "Bryan Berlin" --license "CC BY-SA 4.0" [--license-url URL] [--crop X0 Y0 W]
          (crop = left, top, width as fractions of the photo; height follows the window ratio)
"""
import argparse
import datetime
import json
import os
import re
import unicodedata

from PIL import Image

LIB_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "brand", "players")
MANIFEST = os.path.join(LIB_DIR, "players.json")
WINDOW_W, WINDOW_H = 1400, 1144          # 2x the card's 700x572 photo window
FREE_LICENSE = re.compile(r"^(CC BY(-SA)? [0-9.]+|CC0( 1\.0)?|Public domain)$", re.I)


def slug(name: str) -> str:
    """'Ludvig Åberg' -> 'ludvig-aberg'; 'J.J. Spaun' -> 'j-j-spaun'. Shared with pipeline lookups."""
    ascii_name = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", "-", ascii_name.lower()).strip("-")


def load() -> dict:
    if not os.path.exists(MANIFEST):
        return {}
    with open(MANIFEST, encoding="utf-8") as f:
        return json.load(f)


def lookup(name: str):
    """(photo path, entry) for a player, or (None, None) if there's no approved photo."""
    entry = load().get(slug(name))
    if not entry:
        return None, None
    path = os.path.join(LIB_DIR, entry["image"])
    return (path, entry) if os.path.exists(path) else (None, None)


def credit(entry: dict) -> str:
    return f"Photo: {entry['artist']} / {entry['license']}"


def add(src: str, name: str, source_page: str, artist: str, license_name: str, license_url: str = "",
        crop=(0.0, 0.02, 1.0)) -> dict:
    if not FREE_LICENSE.match(license_name):
        raise ValueError(f"{license_name!r} isn't an open license we accept")
    im = Image.open(src).convert("RGB")
    w, h = im.size
    x0, y0, fw = crop
    cw = int(w * fw)
    ch = int(cw * WINDOW_H / WINDOW_W)
    left, top = int(w * x0), int(h * y0)
    if top + ch > h:
        top = max(0, h - ch)
    im = im.crop((left, top, left + cw, top + ch)).resize((WINDOW_W, WINDOW_H), Image.LANCZOS)
    os.makedirs(LIB_DIR, exist_ok=True)
    key = slug(name)
    image = f"{key}.jpg"
    im.save(os.path.join(LIB_DIR, image), "JPEG", quality=84, optimize=True)
    manifest = load()
    manifest[key] = {"name": name, "image": image, "source_page": source_page, "artist": artist,
                     "license": license_name, "license_url": license_url, "crop": list(crop),
                     "source_pixels_across": cw, "added": datetime.date.today().isoformat()}
    with open(MANIFEST, "w", encoding="utf-8") as f:
        json.dump(dict(sorted(manifest.items())), f, indent=1, ensure_ascii=False)
        f.write("\n")
    return manifest[key]


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("add")
    a.add_argument("photo")
    a.add_argument("--name", required=True)
    a.add_argument("--source-page", required=True)
    a.add_argument("--artist", required=True)
    a.add_argument("--license", required=True)
    a.add_argument("--license-url", default="")
    a.add_argument("--crop", nargs=3, type=float, default=[0.0, 0.02, 1.0])
    sub.add_parser("list")
    args = ap.parse_args()
    if args.cmd == "add":
        print(add(args.photo, args.name, args.source_page, args.artist, args.license, args.license_url, tuple(args.crop)))
    else:
        for k, v in load().items():
            print(f"{v['name']:<24} {credit(v)}")
