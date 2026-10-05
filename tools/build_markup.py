"""Builds templates/*.svg (Marked-Up Scorecard): printed clubhouse scorecard + red Sharpie layer, 1080x1350 (4:5).

Edit this file, then run `python tools/build_markup.py`; don't hand-edit the SVGs.
data-fit="<px>" marks the max width of a text slot: render.py shrinks the font to fit.
data-score marks a to-par score: render.py swaps its fill to ink unless it is under par.
"""
import os, sys
sys.path.insert(0, os.path.dirname(__file__))
from marker import circle, box, squiggle

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "templates")
os.makedirs(OUT, exist_ok=True)

PAPER, INK, RULE, PENCIL, GREEN, RED, SHARPIE = "#F2ECDC", "#1E2A22", "#CFC5AC", "#6E6857", "#2E5A3A", "#B3322B", "#D3261F"
W, H = 1080, 1350
MK = 'fill="none" stroke="%s" stroke-width="7" stroke-linecap="round" stroke-linejoin="round"' % SHARPIE


def head(name):
    return (f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" viewBox="0 0 {W} {H}">\n'
            f'<!-- Scratch Sheet · style: MARKED-UP SCORECARD · {name}. paper {PAPER} · ink {INK} · rule {RULE} · pencil {PENCIL} · green {GREEN} · printed red {RED} · sharpie {SHARPIE} -->\n'
            f'<rect width="{W}" height="{H}" fill="{PAPER}"/>\n'
            f'<rect x="36" y="36" width="1008" height="1278" fill="none" stroke="{INK}" stroke-width="3"/>\n')


def frame_inner():
    return f'<rect x="48" y="48" width="984" height="1254" fill="none" stroke="{INK}" stroke-width="1"/>\n'


def footer():
    return (f'<text x="96" y="1262" font-family="Barlow Condensed SemiBold" font-size="26" fill="{PENCIL}" letter-spacing="4">ATTEST</text>\n'
            f'<line x1="186" y1="1264" x2="560" y2="1264" stroke="{PENCIL}" stroke-width="1.5"/>\n'
            f'<text x="214" y="1252" font-family="Permanent Marker" font-size="44" fill="{SHARPIE}" transform="rotate(-4 214 1252)">SS</text>\n'
            f'<text x="984" y="1262" font-family="Barlow SemiBold" font-size="30" fill="{GREEN}" text-anchor="end">@TheScratchSheet</text>\n</svg>\n')


def hole_strip(y, first=1):
    pars = [4, 4, 3, 5, 4, 3, 4, 5, 4]
    s = [f'<g font-family="Barlow Condensed SemiBold" text-anchor="middle">',
         f'<rect x="96" y="{y}" width="888" height="96" fill="none" stroke="{INK}" stroke-width="2"/>',
         f'<rect x="96" y="{y}" width="888" height="48" fill="{GREEN}"/>',
         f'<text x="148" y="{y+34}" font-size="24" fill="{PAPER}" letter-spacing="3">HOLE</text>',
         f'<text x="148" y="{y+82}" font-size="24" fill="{INK}" letter-spacing="3">PAR</text>']
    for i in range(9):
        x = 200 + i * 87
        s.append(f'<line x1="{x}" y1="{y}" x2="{x}" y2="{y+96}" stroke="{RULE}" stroke-width="2"/>')
        s.append(f'<text x="{x+43}" y="{y+35}" font-size="28" fill="{PAPER}">{first+i}</text>')
        s.append(f'<text x="{x+43}" y="{y+83}" font-size="28" fill="{INK}">{pars[i]}</text>')
    s.append('</g>')
    return "\n".join(s) + "\n"


def topline(label):
    return (f'<text x="96" y="146" font-family="Barlow SemiBold" font-size="28" fill="{RED}" letter-spacing="8">{label}</text>\n'
            f'<text x="984" y="146" font-family="Barlow SemiBold" font-size="22" fill="{GREEN}" letter-spacing="6" text-anchor="end">THE SCRATCH SHEET</text>\n'
            f'<line x1="96" y1="176" x2="984" y2="176" stroke="{INK}" stroke-width="2"/>\n')


# ---------- leaderboard ----------
rows = [441, 591, 741, 891, 1041]
lb = [head("leaderboard"), frame_inner(),
      f'<text x="540" y="122" font-family="Barlow SemiBold" font-size="24" fill="{GREEN}" letter-spacing="7" text-anchor="middle">THE SCRATCH SHEET</text>\n',
      f'<text x="540" y="214" font-family="DM Serif Display" font-size="58" fill="{INK}" text-anchor="middle" data-fit="880">__EVENT__</text>\n',
      f'<text x="540" y="266" font-family="Barlow SemiBold" font-size="26" fill="{PENCIL}" letter-spacing="6" text-anchor="middle" data-fit="880">__ROUND__</text>\n',
      f'<rect x="80" y="310" width="920" height="56" fill="{GREEN}"/>\n',
      f'<g font-family="Barlow Condensed SemiBold" font-size="28" fill="{PAPER}" letter-spacing="4"><text x="130" y="347" text-anchor="middle">POS</text><text x="212" y="347">PLAYER</text><text x="910" y="347" text-anchor="middle">TO PAR</text></g>\n',
      f'<g stroke="{RULE}" stroke-width="2">' + "".join(f'<line x1="80" y1="{r+75}" x2="1000" y2="{r+75}"/>' for r in rows[:-1]) +
      f'<line x1="180" y1="366" x2="180" y2="1116"/><line x1="820" y1="366" x2="820" y2="1116"/></g>\n',
      f'<rect x="80" y="366" width="920" height="750" fill="none" stroke="{INK}" stroke-width="2"/>\n']
for i, r in enumerate(rows, 1):
    lb.append(f'<text x="130" y="{r+21}" font-family="DM Serif Display" font-size="60" fill="{INK}" text-anchor="middle" data-fit="92">__POS_{i}__</text>\n')
    lb.append(f'<text x="212" y="{r+18}" font-family="DM Serif Display" font-size="50" fill="{INK}" data-fit="590">__PLAYER_{i}__</text>\n')
    lb.append(f'<text x="910" y="{r+25}" font-family="Anton" font-size="72" fill="{RED}" text-anchor="middle" data-fit="140" data-score="{INK}">__SCORE_{i}__</text>\n')
lb.append(f'<path d="{circle(910, 441, 72, 56, 4)}" {MK}/>\n')
lb.append(footer())
open(f"{OUT}/leaderboard.svg", "w").write("".join(lb))

# ---------- hot take ----------
ht = [head("hot take"), frame_inner(), hole_strip(96),
      f'<text x="110" y="330" font-family="Permanent Marker" font-size="64" fill="{SHARPIE}" transform="rotate(-4 110 330)">HOT TAKE</text>\n',
      f'<path d="{circle(262, 304, 196, 66, 7, 1.12)}" {MK}/>\n',
      f'<text font-family="Anton" font-size="110" fill="{INK}" data-fit="880">'
      '<tspan x="94" y="500">__TAKE_LINE_1__</tspan><tspan x="94" y="624">__TAKE_LINE_2__</tspan>'
      '<tspan x="94" y="748">__TAKE_LINE_3__</tspan><tspan x="94" y="872">__TAKE_LINE_4__</tspan></text>\n',
      f'<g stroke="{RULE}" stroke-width="2"><line x1="96" y1="516" x2="984" y2="516"/><line x1="96" y1="640" x2="984" y2="640"/><line x1="96" y1="764" x2="984" y2="764"/><line x1="96" y1="888" x2="984" y2="888"/></g>\n',
      f'<text x="98" y="1010" font-family="Permanent Marker" font-size="30" fill="{SHARPIE}" transform="rotate(-2 98 1010)" data-fit="880">__KICKER__</text>\n',
      footer()]
open(f"{OUT}/hot_take.svg", "w").write("".join(ht))

# ---------- weekly picks ----------
prow = [405, 575, 745, 915]
wp = [head("weekly picks"), frame_inner(),
      f'<text x="540" y="122" font-family="Barlow SemiBold" font-size="24" fill="{GREEN}" letter-spacing="7" text-anchor="middle">THE SCRATCH SHEET</text>\n',
      f'<text x="540" y="214" font-family="DM Serif Display" font-size="84" fill="{INK}" text-anchor="middle">This Week\'s Card</text>\n',
      f'<text x="540" y="266" font-family="Barlow SemiBold" font-size="24" fill="{PENCIL}" letter-spacing="4" text-anchor="middle" data-fit="880">__EVENT_NAME__</text>\n',
      f'<rect x="80" y="320" width="920" height="680" fill="none" stroke="{INK}" stroke-width="2"/>\n',
      f'<g stroke="{RULE}" stroke-width="2"><line x1="80" y1="490" x2="1000" y2="490"/><line x1="80" y1="660" x2="1000" y2="660"/><line x1="80" y1="830" x2="1000" y2="830"/><line x1="330" y1="320" x2="330" y2="1000"/></g>\n']
for (lab, tok), y in zip([("WIN", "WIN_PLAYER"), ("VALUE", "VALUE_PLAYER"), ("FADE", "FADE_PLAYER"), ("SLEEPER", "SLEEPER_PLAYER")], prow):
    wp.append(f'<text x="205" y="{y+16}" font-family="Barlow Condensed SemiBold" font-size="44" fill="{INK}" letter-spacing="5" text-anchor="middle">{lab}</text>\n')
    wp.append(f'<text x="362" y="{y+17}" font-family="DM Serif Display" font-size="48" fill="{INK}" data-fit="610">__{tok}__</text>\n')
wp += [f'<path d="{circle(205, 405, 92, 52, 11)}" {MK}/>\n', f'<path d="{circle(205, 405, 76, 40, 12, 1.08, -1.2)}" {MK}/>\n',
       f'<path d="{circle(205, 575, 100, 52, 13)}" {MK}/>\n',
       f'<path d="{box(126, 706, 158, 78, 14)}" {MK}/>\n',
       f'<path d="{squiggle(118, 945, 176, 15)}" {MK}/>\n',
       f'<text x="96" y="1084" font-family="Permanent Marker" font-size="32" fill="{SHARPIE}" transform="rotate(-2 96 1084)">circled twice = all in · boxed = stay away</text>\n',
       f'<text x="96" y="1150" font-family="Barlow Medium" font-size="26" fill="{PENCIL}">Full breakdown in the newsletter.</text>\n',
       footer()]
open(f"{OUT}/weekly_picks.svg", "w").write("".join(wp))

# ---------- intel stat ----------
st = [head("intel stat"), frame_inner(), topline("INTEL DROP"),
      f'<text x="540" y="640" font-family="Anton" font-size="290" fill="{INK}" text-anchor="middle" data-fit="880">__STAT__</text>\n',
      f'<path d="{squiggle(250, 700, 580, 21, 6)}" {MK}/>\n', f'<path d="{squiggle(290, 726, 500, 22, 5)}" {MK}/>\n',
      f'<text x="540" y="830" font-family="Barlow Condensed SemiBold" font-size="42" fill="{INK}" letter-spacing="1" text-anchor="middle" data-fit="880">__WHAT_IT_MEANS__</text>\n',
      f'<text x="540" y="930" font-family="Permanent Marker" font-size="34" fill="{SHARPIE}" text-anchor="middle" transform="rotate(-2 540 930)" data-fit="880">__SUPPORTING_LINE__</text>\n',
      footer()]
open(f"{OUT}/intel_stat.svg", "w").write("".join(st))

# ---------- live alert ----------
lv = [head("live alert"),
      f'<rect x="48" y="48" width="984" height="712" fill="{RED}"/>\n',
      f'<circle cx="108" cy="134" r="16" fill="{PAPER}"/>\n',
      f'<text x="140" y="160" font-family="Anton" font-size="76" fill="{PAPER}" letter-spacing="8">LIVE</text>\n',
      f'<text x="984" y="156" font-family="Anton" font-size="50" fill="{PAPER}" letter-spacing="3" text-anchor="end" data-fit="520">__HOLE_MOMENT__</text>\n',
      f'<line x1="96" y1="200" x2="984" y2="200" stroke="{PAPER}" stroke-opacity="0.4" stroke-width="2"/>\n',
      f'<text font-family="Anton" font-size="130" fill="{PAPER}" data-fit="880"><tspan x="92" y="450">__EVENT_LINE_1__</tspan><tspan x="92" y="590">__EVENT_LINE_2__</tspan></text>\n',
      frame_inner(),
      f'<text x="98" y="890" font-family="Permanent Marker" font-size="36" fill="{SHARPIE}" transform="rotate(-2 98 890)" data-fit="880">__REACTION__</text>\n',
      hole_strip(1040, 10),
      footer()]
open(f"{OUT}/live_alert.svg", "w").write("".join(lv))
print("built", sorted(os.listdir(OUT)))
