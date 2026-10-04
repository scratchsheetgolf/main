"""Builds brand/ — The Scratch Sheet logo in the Marked-Up Scorecard style (same fonts and marks as the cards).

brand/avatar.svg / .png     1000x1000 profile image: Sharpie "SS" circled twice. Content stays
                            inside the circle crop X / Instagram / TikTok apply, and the
                            strokes are heavy enough to read at 32-48 px.
brand/wordmark.svg / .png   1500x500 (X header size): the mark + "The Scratch Sheet"

Run with the venv python so the PNGs get rendered:  python tools/build_brand.py
Needs the fonts in fonts/ installed (see render.py).
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from marker import circle, squiggle

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "brand")
os.makedirs(OUT, exist_ok=True)

PAPER, INK, GREEN, PENCIL, SHARPIE = "#F2ECDC", "#1E2A22", "#2E5A3A", "#6E6857", "#D3261F"


def mk(width):
    return f'fill="none" stroke="{SHARPIE}" stroke-width="{width}" stroke-linecap="round" stroke-linejoin="round"'


def svg(w, h, body):
    return (f'<svg xmlns="http://www.w3.org/2000/svg" width="{w}" height="{h}" viewBox="0 0 {w} {h}">\n'
            f'<rect width="{w}" height="{h}" fill="{PAPER}"/>\n{body}</svg>\n')


def mark(cx, cy, s):
    """The SS mark centred on (cx, cy); s = scale (1.0 = avatar size)."""
    return (f'<text x="{cx}" y="{cy + 145 * s:.1f}" font-family="Permanent Marker" font-size="{420 * s:.1f}" fill="{SHARPIE}" '
            f'stroke="{SHARPIE}" stroke-width="{10 * s:.1f}" stroke-linejoin="round" text-anchor="middle" '
            f'transform="rotate(-6 {cx} {cy + 35 * s:.1f})">SS</text>\n'
            f'<path d="{circle(cx, cy, 345 * s, 275 * s, 31, 1.16)}" {mk(round(30 * s, 1))}/>\n'
            f'<path d="{circle(cx, cy, 318 * s, 250 * s, 32, 0.62, 2.2)}" {mk(round(22 * s, 1))}/>\n')


files = {
    "avatar": svg(1000, 1000, mark(500, 505, 1.0)),
    "wordmark": svg(1500, 500,
        f'<line x1="120" y1="110" x2="1380" y2="110" stroke="{INK}" stroke-width="3"/>\n'
        f'<line x1="120" y1="390" x2="1380" y2="390" stroke="{INK}" stroke-width="3"/>\n'
        + mark(262, 250, 0.36) +
        f'<text x="430" y="290" font-family="DM Serif Display" font-size="112" fill="{INK}">The Scratch Sheet</text>\n'
        f'<path d="{squiggle(440, 326, 800, 36, 8)}" {mk(10)}/>\n'
        f'<text x="120" y="440" font-family="Barlow Condensed SemiBold" font-size="26" fill="{PENCIL}" letter-spacing="5">ATTEST</text>\n'
        f'<line x1="210" y1="442" x2="560" y2="442" stroke="{PENCIL}" stroke-width="1.5"/>\n'
        f'<text x="1380" y="440" font-family="Barlow SemiBold" font-size="28" fill="{GREEN}" text-anchor="end">@TheScratchSheet</text>\n'),
}

for name, content in files.items():
    open(os.path.join(OUT, f"{name}.svg"), "w").write(content)

try:
    import cairosvg
    for name in files:
        cairosvg.svg2png(url=os.path.join(OUT, f"{name}.svg"), write_to=os.path.join(OUT, f"{name}.png"))
    print("built SVG + PNG:", ", ".join(files))
except ImportError:
    print("built SVG only (run with the venv python to render PNGs):", ", ".join(files))
