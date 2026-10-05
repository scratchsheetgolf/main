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
# ---------- carousel: one slide per pick (four variants, one Sharpie mark each) ----------
def pick_mark(slot):
    if slot == "win":
        return f'<path d="{circle(540, 300, 150, 66, 41)}" {MK}/>\n<path d="{circle(540, 300, 128, 52, 42, 1.08, -1.2)}" {MK}/>\n'
    if slot == "value":
        return f'<path d="{circle(540, 300, 160, 66, 43)}" {MK}/>\n'
    if slot == "fade":
        return f'<path d="{box(400, 248, 280, 104, 44)}" {MK}/>\n'
    return f'<path d="{squiggle(410, 352, 260, 45, 9)}" {MK}/>\n'


SKILL_ROWS = [("OFF THE TEE", "OTT"), ("APPROACH", "APP"), ("AROUND THE GREEN", "ARG"), ("PUTTING", "PUTT")]
for slot, label in [("win", "WIN"), ("value", "VALUE"), ("fade", "FADE"), ("sleeper", "SLEEPER")]:
    pk = [head(f"pick {slot}"), frame_inner(), topline("THIS WEEK'S CARD"),
          f'<text x="540" y="324" font-family="Barlow Condensed SemiBold" font-size="72" fill="{INK}" letter-spacing="10" text-anchor="middle">{label}</text>\n',
          pick_mark(slot),
          f'<text x="540" y="500" font-family="DM Serif Display" font-size="92" fill="{INK}" text-anchor="middle" data-fit="880">__PLAYER__</text>\n',
          f'<text x="540" y="552" font-family="Barlow SemiBold" font-size="24" fill="{PENCIL}" letter-spacing="4" text-anchor="middle" data-fit="880">__EVENT__</text>\n',
          # odds row: three cells
          f'<rect x="96" y="596" width="888" height="150" fill="none" stroke="{INK}" stroke-width="2"/>\n',
          f'<rect x="96" y="596" width="888" height="44" fill="{GREEN}"/>\n',
          f'<g stroke="{RULE}" stroke-width="2"><line x1="392" y1="640" x2="392" y2="746"/><line x1="688" y1="640" x2="688" y2="746"/></g>\n',
          f'<g font-family="Barlow Condensed SemiBold" font-size="24" fill="{PAPER}" letter-spacing="4" text-anchor="middle">'
          '<text x="244" y="627">MODEL WIN %</text><text x="540" y="627">BOOKS WIN %</text><text x="836" y="627">BOOKS ODDS</text></g>\n',
          f'<g font-family="Anton" font-size="64" fill="{INK}" text-anchor="middle">'
          '<text x="244" y="722" data-fit="270">__MODEL_WIN__</text><text x="540" y="722" data-fit="270">__BOOKS_WIN__</text>'
          '<text x="836" y="722" data-fit="270">__ODDS__</text></g>\n',
          # skill profile: four rows
          f'<text x="96" y="806" font-family="Barlow Condensed SemiBold" font-size="26" fill="{PENCIL}" letter-spacing="4">STROKES GAINED PER ROUND · WORLD RANK</text>\n',
          f'<rect x="96" y="822" width="888" height="280" fill="none" stroke="{INK}" stroke-width="2"/>\n',
          f'<g stroke="{RULE}" stroke-width="2"><line x1="96" y1="892" x2="984" y2="892"/><line x1="96" y1="962" x2="984" y2="962"/>'
          '<line x1="96" y1="1032" x2="984" y2="1032"/><line x1="640" y1="822" x2="640" y2="1102"/><line x1="820" y1="822" x2="820" y2="1102"/></g>\n']
    for i, (name, key) in enumerate(SKILL_ROWS):
        y = 868 + i * 70
        pk.append(f'<text x="124" y="{y}" font-family="Barlow Condensed SemiBold" font-size="32" fill="{INK}" letter-spacing="3">{name}</text>\n')
        pk.append(f'<text x="730" y="{y + 2}" font-family="Anton" font-size="40" fill="{INK}" text-anchor="middle">__SG_{key}__</text>\n')
        pk.append(f'<text x="902" y="{y + 2}" font-family="Barlow Condensed SemiBold" font-size="34" fill="{RED}" text-anchor="middle">__RANK_{key}__</text>\n')
    pk.append(f'<text x="98" y="1180" font-family="Permanent Marker" font-size="32" fill="{SHARPIE}" transform="rotate(-2 98 1180)" data-fit="880">__NOTE__</text>\n')
    pk.append(footer())
    open(f"{OUT}/pick_{slot}.svg", "w").write("".join(pk))

# ---------- carousel: generic top-5 list ----------
lrows = [456, 576, 696, 816, 936]
sl = [head("stat list"), frame_inner(), topline("THIS WEEK'S CARD"),
      f'<text x="540" y="268" font-family="DM Serif Display" font-size="76" fill="{INK}" text-anchor="middle" data-fit="880">__TITLE__</text>\n',
      f'<text x="540" y="320" font-family="Barlow SemiBold" font-size="24" fill="{PENCIL}" letter-spacing="3" text-anchor="middle" data-fit="880">__SUBTITLE__</text>\n',
      f'<rect x="80" y="352" width="920" height="54" fill="{GREEN}"/>\n',
      f'<g font-family="Barlow Condensed SemiBold" font-size="26" fill="{PAPER}" letter-spacing="4"><text x="130" y="388" text-anchor="middle">POS</text>'
      f'<text x="212" y="388">PLAYER</text><text x="890" y="388" text-anchor="middle" data-fit="200">__HEADER__</text></g>\n',
      f'<g stroke="{RULE}" stroke-width="2">' + "".join(f'<line x1="80" y1="{r + 60}" x2="1000" y2="{r + 60}"/>' for r in lrows[:-1]) +
      f'<line x1="180" y1="406" x2="180" y2="996"/><line x1="780" y1="406" x2="780" y2="996"/></g>\n',
      f'<rect x="80" y="406" width="920" height="590" fill="none" stroke="{INK}" stroke-width="2"/>\n']
for i, r in enumerate(lrows, 1):
    sl.append(f'<text x="130" y="{r + 20}" font-family="DM Serif Display" font-size="52" fill="{INK}" text-anchor="middle">{i}</text>\n')
    sl.append(f'<text x="212" y="{r + 18}" font-family="DM Serif Display" font-size="46" fill="{INK}" data-fit="550">__NAME_{i}__</text>\n')
    sl.append(f'<text x="890" y="{r + 22}" font-family="Anton" font-size="56" fill="{RED}" text-anchor="middle" data-fit="200">__VALUE_{i}__</text>\n')
sl.append(f'<path d="{circle(890, 456, 92, 50, 46)}" {MK}/>\n')
sl.append(f'<text x="98" y="1090" font-family="Permanent Marker" font-size="30" fill="{SHARPIE}" transform="rotate(-2 98 1090)" data-fit="880">__NOTE__</text>\n')
sl.append(footer())
open(f"{OUT}/stat_list.svg", "w").write("".join(sl))

# ---------- carousel: closing slide ----------
cl = [head("closer"), frame_inner(), hole_strip(96, 10),
      f'<text x="540" y="560" font-family="Anton" font-size="200" fill="{INK}" text-anchor="middle">RECEIPTS</text>\n',
      f'<text x="540" y="760" font-family="Anton" font-size="200" fill="{INK}" text-anchor="middle">SUNDAY.</text>\n',
      f'<path d="{squiggle(250, 800, 580, 47, 7)}" {MK}/>\n',
      f'<text x="540" y="890" font-family="Barlow SemiBold" font-size="26" fill="{PENCIL}" letter-spacing="4" text-anchor="middle" data-fit="880">__EVENT__</text>\n',
      f'<text x="540" y="990" font-family="Permanent Marker" font-size="38" fill="{SHARPIE}" text-anchor="middle" transform="rotate(-2 540 990)">win or lose, we post how these did</text>\n',
      footer()]
open(f"{OUT}/closer.svg", "w").write("".join(cl))
# ---------- carousel: playing cards (true 5:7 card on the 4:5 frame) ----------
# Photo variant: __PHOTO__ is a data: URI filled by render.py (photo pre-cropped to 700x572 window,
# natural color or brand duotone). No-photo variant: the player's initials in the window.
CX, CY, CW, CH = 100, 59, 880, 1232
CPW, CPH = 700, 572
CARD_BG = "#FBF8F0"


def card_mark(slot, x, y, s=0.85):
    if slot == "W":
        return f'<path d="{circle(x, y, 30*s, 19*s, 51)}" {MK}/><path d="{circle(x, y, 22*s, 13*s, 52, 1.08, -1.2)}" {MK}/>'
    if slot == "V":
        return f'<path d="{circle(x, y, 30*s, 19*s, 53)}" {MK}/>'
    if slot == "F":
        return f'<path d="{box(x - 26*s, y - 16*s, 52*s, 32*s, 54)}" {MK}/>'
    return f'<path d="{squiggle(x - 28*s, y, 56*s, 55, 5)}" {MK}/>'


def playing_card(slot, label, photo):
    L, R, T, B = CX, CX + CW, CY, CY + CH
    mid = CX + CW / 2
    px, py = mid - CPW / 2, T + 134
    out = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" viewBox="0 0 {W} {H}">\n',
           f'<!-- Scratch Sheet · playing card · {label} · {"photo" if photo else "no photo"} -->\n',
           f'<rect width="{W}" height="{H}" fill="{PAPER}"/>\n',
           f'<rect x="{L}" y="{T}" width="{CW}" height="{CH}" rx="40" fill="{CARD_BG}" stroke="{INK}" stroke-width="4"/>\n',
           f'<rect x="{L + 22}" y="{T + 22}" width="{CW - 44}" height="{CH - 44}" rx="28" fill="none" stroke="{RED}" stroke-width="2"/>\n',
           f'<text x="{L + 52}" y="{T + 84}" font-family="Anton" font-size="46" fill="{SHARPIE}" text-anchor="middle">{slot}</text>\n',
           card_mark(slot, L + 52, T + 108) + "\n",
           f'<g transform="rotate(180 {R - 52} {B - 108})"><text x="{R - 52}" y="{B - 132}" font-family="Anton" font-size="46" '
           f'fill="{SHARPIE}" text-anchor="middle">{slot}</text>{card_mark(slot, R - 52, B - 108)}</g>\n']
    if photo:
        out += [f'<image x="{px}" y="{py}" width="{CPW}" height="{CPH}" preserveAspectRatio="xMidYMid slice" href="__PHOTO__"/>\n',
                f'<rect x="{px}" y="{py}" width="{CPW}" height="{CPH}" fill="none" stroke="{INK}" stroke-width="3"/>\n',
                f'<rect x="{px + 3}" y="{py + CPH - 30}" width="{CPW - 6}" height="27" fill="{INK}" fill-opacity="0.55"/>\n',
                f'<text x="{px + CPW - 12}" y="{py + CPH - 11}" font-family="Barlow SemiBold" font-size="15" fill="{PAPER}" '
                f'text-anchor="end" data-fit="660">__CREDIT__</text>\n']
    else:
        out += [f'<rect x="{px}" y="{py}" width="{CPW}" height="{CPH}" fill="#ECE5D3" stroke="{INK}" stroke-width="3"/>\n',
                f'<text x="{mid}" y="{py + CPH / 2 + 95}" font-family="DM Serif Display" font-size="270" fill="{INK}" '
                f'text-anchor="middle" data-fit="560">__INITIALS__</text>\n',
                f'<path d="{circle(mid, py + CPH / 2, 250, 175, 56, 1.1)}" {MK}/>\n']
    t3 = [mid - 250, mid, mid + 250]
    cols = [mid - 300, mid - 100, mid + 100, mid + 300]
    out += [f'<rect x="{px - 20}" y="{py + CPH + 18}" width="{CPW + 40}" height="92" fill="{GREEN}"/>\n',
            f'<text x="{mid}" y="{py + CPH + 82}" font-family="DM Serif Display" font-size="58" fill="{PAPER}" text-anchor="middle" data-fit="700">__PLAYER__</text>\n',
            f'<text x="{mid}" y="{py + CPH + 146}" font-family="Barlow Condensed SemiBold" font-size="28" fill="{RED}" letter-spacing="7" '
            f'text-anchor="middle" data-fit="760">{label} PICK · __EVENT__</text>\n',
            f'<g font-family="Barlow Condensed SemiBold" font-size="22" fill="{PENCIL}" letter-spacing="4" text-anchor="middle">'
            f'<text x="{t3[0]}" y="{B - 330}">MODEL WIN</text><text x="{t3[1]}" y="{B - 330}">BOOKS WIN</text><text x="{t3[2]}" y="{B - 330}">ODDS</text></g>\n',
            f'<g font-family="Anton" font-size="60" fill="{INK}" text-anchor="middle">'
            f'<text x="{t3[0]}" y="{B - 258}" data-fit="220">__MODEL_WIN__</text><text x="{t3[1]}" y="{B - 258}" data-fit="220">__BOOKS_WIN__</text>'
            f'<text x="{t3[2]}" y="{B - 258}" data-fit="220">__ODDS__</text></g>\n',
            f'<line x1="{L + 70}" y1="{B - 232}" x2="{R - 70}" y2="{B - 232}" stroke="{RULE}" stroke-width="2"/>\n',
            '<g font-family="Barlow Condensed SemiBold" font-size="23" fill="' + INK + '" letter-spacing="2" text-anchor="middle">'
            + "".join(f'<text x="{x}" y="{B - 196}">{k} __SG_{k}__</text>' for x, k in zip(cols, ["OTT", "APP", "ARG", "PUTT"])) + '</g>\n',
            '<g font-family="Barlow Condensed SemiBold" font-size="21" fill="' + RED + '" text-anchor="middle">'
            + "".join(f'<text x="{x}" y="{B - 166}">__RANK_{k}__</text>' for x, k in zip(cols, ["OTT", "APP", "ARG", "PUTT"])) + '</g>\n',
            f'<text x="{mid}" y="{B - 100}" font-family="Permanent Marker" font-size="27" fill="{SHARPIE}" text-anchor="middle" '
            f'transform="rotate(-2 {mid} {B - 100})" data-fit="700">__NOTE__</text>\n</svg>\n']
    return "".join(out)


for slot, label in [("W", "WIN"), ("V", "VALUE"), ("F", "FADE"), ("S", "SLEEPER")]:
    for photo in (True, False):
        open(f"{OUT}/card_{label.lower()}{'' if photo else '_nophoto'}.svg", "w").write(playing_card(slot, label, photo))

# ---------- carousel: "This Week's Hand" cover background (cards composited in render.py) ----------
hd = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" viewBox="0 0 {W} {H}">\n',
      f'<rect width="{W}" height="{H}" fill="{PAPER}"/>\n',
      f'<rect x="36" y="36" width="1008" height="1278" fill="none" stroke="{INK}" stroke-width="3"/>\n',
      f'<text x="540" y="200" font-family="Anton" font-size="120" fill="{INK}" text-anchor="middle">THIS WEEK\'S HAND</text>\n',
      f'<text x="540" y="256" font-family="Barlow SemiBold" font-size="28" fill="{PENCIL}" letter-spacing="6" text-anchor="middle" data-fit="900">__EVENT__</text>\n',
      f'<text x="540" y="1250" font-family="Permanent Marker" font-size="40" fill="{SHARPIE}" text-anchor="middle" transform="rotate(-2 540 1250)">swipe for the reads</text>\n</svg>\n']
open(f"{OUT}/hand_cover.svg", "w").write("".join(hd))
print("built", sorted(os.listdir(OUT)))
