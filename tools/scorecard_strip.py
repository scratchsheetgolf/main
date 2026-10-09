"""Live-alert scorecard strip: HOLE / PAR / SCORE rows for the nine the player is on, with the standard
scoring marks in red Sharpie (birdie = circle, eagle or better = double circle, bogey = square,
double = two squares, triple+ = three). Holes we don't know stay blank; nothing is guessed."""
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))
from marker import box, circle  # noqa: E402

PAPER, INK, RULE, GREEN, SHARPIE = "#F2ECDC", "#1E2A22", "#CFC5AC", "#2E5A3A", "#D3261F"


def strip(y: int, holes: list, pars: list, rel: list) -> str:
    """holes: 9 hole numbers; pars: 9 ints or None; rel: 9 scores vs par (int) or None (unknown/not yet)."""
    s = ['<g font-family="Barlow Condensed SemiBold" text-anchor="middle">',
         f'<rect x="96" y="{y}" width="888" height="144" fill="none" stroke="{INK}" stroke-width="2"/>',
         f'<rect x="96" y="{y}" width="888" height="48" fill="{GREEN}"/>',
         f'<line x1="96" y1="{y + 96}" x2="984" y2="{y + 96}" stroke="{RULE}" stroke-width="2"/>',
         f'<text x="148" y="{y + 34}" font-size="24" fill="{PAPER}" letter-spacing="3">HOLE</text>',
         f'<text x="148" y="{y + 82}" font-size="24" fill="{INK}" letter-spacing="3">PAR</text>',
         f'<text x="148" y="{y + 130}" font-size="24" fill="{INK}" letter-spacing="3">SCORE</text>']
    marks = []
    for i in range(9):
        x = 200 + i * 87
        cx, cy = x + 43, y + 120
        s.append(f'<line x1="{x}" y1="{y}" x2="{x}" y2="{y + 144}" stroke="{RULE}" stroke-width="2"/>')
        s.append(f'<text x="{cx}" y="{y + 35}" font-size="28" fill="{PAPER}">{holes[i]}</text>')
        par, r = pars[i], rel[i]
        if par is not None:
            s.append(f'<text x="{cx}" y="{y + 83}" font-size="28" fill="{INK}">{par}</text>')
        if r is None:
            continue
        shown = par + r if par is not None else (f"{r:+d}" if r else "E")
        s.append(f'<text x="{cx}" y="{y + 131}" font-size="32" fill="{INK}">{shown}</text>')
        seed = 11 + i * 7
        if r <= -1:                                   # birdie: one ring; eagle or better: two
            for k in range(min(-r, 3)):
                marks.append(circle(cx, cy - 1, 25 + 7 * k, 20 + 6 * k, seed=seed + k))
        elif r >= 1:                                  # bogey: one box; double: two; triple+: three
            for k in range(min(r, 3)):
                pad = 6 * k
                marks.append(box(cx - 24 - pad, cy - 21 - pad, 48 + 2 * pad, 40 + 2 * pad, seed=seed + k))
    s.append('</g>')
    s += [f'<path d="{d}" fill="none" stroke="{SHARPIE}" stroke-width="4.5" stroke-linecap="round" '
          f'stroke-linejoin="round" opacity="0.92"/>' for d in marks]
    return "\n".join(s) + "\n"


def nine_for(end_hole, thru) -> list:
    """The nine the player is on: started on the 10th (end_hole 9) and under 9 holes played -> 10-18,
    and so on. Returns 9 hole numbers."""
    start = 10 if str(end_hole) == "9" else 1
    played = int(thru or 0)
    first = start if played <= 9 else (1 if start == 10 else 10)
    return list(range(first, first + 9))
