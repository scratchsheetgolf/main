"""
content.py — generates the actual words (hot takes, captions, newsletter blurbs)
using the Claude API, grounded in real DataGolf data and the brand voice doc.

This is the only part of the pipeline that's genuinely generative rather than
data-formatting. Everything else (leaderboard numbers, stat call-outs) should
come straight from DataGolf — only feed the model real numbers, never let it
invent stats. That's both an accuracy requirement and an FTC/advertising-claims
safety net for anything that touches betting language.

How every generator works (built for a small, cheap model):
  1. ask for several OPTIONS in a fixed KEY: value format, with worked examples
  2. check each option in code: required fields, the card's real character
     limits, and that every number in it appears in the facts given
  3. if none pass, ask once more, quoting what was wrong
  4. the model may answer SKIP when the facts are too thin -> returns None
The first valid option is used; the rest come back as "alternatives" so a
person posting by hand can pick a better one.
"""
import os
import re

import anthropic

MODEL = "claude-haiku-4-5"  # $1 / $5 per MTok; fine for short, fact-fed writing.
# If hot takes read flat, try "claude-sonnet-5-5" ($2 / $10): it always thinks first, and thinking
# counts against max_tokens, so raise the max_tokens values below (to ~4000) when switching.
VOICE_DOC_PATH = os.path.join(os.path.dirname(__file__), "..", "config", "brand_voice.md")

client = anthropic.Anthropic()  # reads ANTHROPIC_API_KEY from env automatically

FALLBACK_VOICE = (
    "Sharp, opinionated, golf-savvy. Confident takes, dry humor, no hedging. "
    "Talks like a smart friend who watches every round, not a press release."
)

# Character limits = what fits the cards at full size (templates/*.svg via tools/build_markup.py).
LIMITS = {
    "hot_take": {"LINE1": 20, "LINE2": 20, "LINE3": 20, "LINE4": 20, "KICKER": 62},
    "live": {"LINE1": 16, "LINE2": 16, "REACTION": 50},
    "intel": {"STAT": 6, "MEANS": 48, "SUPPORTING": 55},
    "caption": {"CAPTION": 240},  # X allows 280; leaves room for an event hashtag
}
RECAP_MAX_WORDS = 130


class ContentError(Exception):
    """No option passed the checks, even after one retry."""


def _load_voice() -> str:
    try:
        with open(VOICE_DOC_PATH, "r", encoding="utf-8") as f:
            doc = f.read()
        if "PLACEHOLDER" in doc:
            return FALLBACK_VOICE
        return doc
    except FileNotFoundError:
        return FALLBACK_VOICE


def _generate(system: str, user: str, max_tokens: int = 300, temperature: float = 1.0) -> str:
    resp = client.messages.create(
        model=MODEL,
        max_tokens=max_tokens,
        system=system,
        messages=[{"role": "user", "content": user}],
        # anthropic>=1.0 dropped the temperature keyword; Haiku 4.5 still honours it in the request body.
        # (Opus 4.7+ rejects it; Sonnet 5/5.5 reject non-default values. Drop this line if MODEL changes.)
        extra_body={"temperature": temperature},
    )
    return "".join(b.text for b in resp.content if b.type == "text").strip()


# ---- checking ----

_NUMBER = re.compile(r"\d+(?:\.\d+)?")


def ungrounded_numbers(text: str, facts: str) -> list:
    """Numbers in text that don't appear in facts ('11th' counts as 11; '-14' as 14)."""
    allowed = set(_NUMBER.findall(facts))
    return [n for n in _NUMBER.findall(text) if n not in allowed]


# Words that assert history the facts never contain ("his first PGA Tour win" was invented on the first
# real run). Rejected unless the word appears in the facts themselves.
HISTORY_CLAIMS = ["first", "maiden", "career", "record", "records", "debut", "streak", "again",
                  "back-to-back", "historic", "history", "ever", "never", "all-time", "consecutive", "straight"]


def unsupported_claims(text: str, facts: str) -> list:
    """History/record words in text that the facts don't support."""
    low_text, low_facts = text.lower(), facts.lower()
    found = []
    for word in HISTORY_CLAIMS:
        pattern = r"(?<!\w)" + re.escape(word) + r"(?!\w)"  # "first-time" counts as "first"
        if re.search(pattern, low_text) and not re.search(pattern, low_facts):
            found.append(word)
    return found


def _parse_options(raw: str, fields: list) -> list:
    """Split 'OPTION n' blocks into dicts of the requested fields."""
    blocks = re.split(r"(?im)^\s*OPTION\s*\d*\s*:?\s*$", raw)
    options = []
    for block in blocks:
        opt = {}
        for line in block.splitlines():
            key, sep, value = line.partition(":")
            key = key.strip().upper()
            if sep and key in fields and key not in opt:
                opt[key] = value.strip()
        if opt:
            options.append(opt)
    return options


def _problems(opt: dict, limits: dict, facts: str) -> list:
    out = []
    for field, limit in limits.items():
        value = opt.get(field, "")
        if not value:
            out.append(f"{field} missing")
        elif len(value) > limit:
            out.append(f"{field} is {len(value)} characters (max {limit}): {value!r}")
    text = " ".join(opt.values())
    invented = ungrounded_numbers(text, facts)
    if invented:
        out.append(f"uses numbers not in the facts: {', '.join(invented)}")
    claims = unsupported_claims(text, facts)
    if claims:
        out.append(f"claims history the facts don't support ({', '.join(claims)}): no firsts, records, streaks or career talk")
    return out


def _ask(task: str, system: str, facts: str, limits: dict, n: int, temperature: float, max_tokens: int):
    """Returns a list of valid options (best first), or None if the model chose SKIP."""
    fields = list(limits)
    fmt = "\n".join(f"{f}: ..." for f in fields)
    instructions = (
        f"{system}\n\n"
        f"Write {n} different options. Output EXACTLY this, nothing else:\n"
        + "\n".join(f"OPTION {i + 1}\n{fmt}" for i in range(n))
        + "\n\nHard limits (characters, counting spaces): "
        + ", ".join(f"{f} <= {lim}" for f, lim in limits.items())
        + ".\nUse only numbers that appear in the FACTS. Don't claim anything about history the FACTS "
          "don't state: no firsts, records, streaks, career or 'again'. Compare players only using numbers "
          "in the FACTS (no 'nobody's close' unless the numbers show it). If the facts are too thin for a "
          "good one, output just: SKIP"
    )
    user = f"FACTS:\n{facts}"
    feedback = ""
    for attempt in range(2):
        raw = _generate(instructions, user + feedback, max_tokens=max_tokens, temperature=temperature)
        if raw.strip().upper().startswith("SKIP"):
            return None
        options = _parse_options(raw, fields)
        valid = [o for o in options if not _problems(o, limits, facts)]
        if valid:
            return valid
        issues = [p for o in options for p in _problems(o, limits, facts)] or ["no OPTION blocks in the expected format"]
        feedback = ("\n\nYour last answer was rejected:\n- " + "\n- ".join(issues[:8])
                    + "\nTry again, fixing those problems.")
    raise ContentError(f"{task}: no option passed the checks after a retry ({'; '.join(issues[:4])})")


# ---- worked examples (from config/brand_voice.md, in the exact output format) ----

HOT_TAKE_EXAMPLES = """GOOD EXAMPLES (style only — their facts are made up, never reuse them):
LINE1: SCOTTIE'S MAKING
LINE2: THIS LOOK
LINE3: STUPID EASY
LINE4: AGAIN.
KICKER: — not a hot take, just facts at this point

LINE1: BRYSON HIT DRIVER
LINE2: OFF THE DECK ON 17
LINE3: ...AND NEARLY
LINE4: HOLED IT.
KICKER: — rewatched this four times before posting

LINE1: RORY 3-PUTTED
LINE2: FROM 12 FEET.
LINE3: WE'VE ALL BEEN
LINE4: THERE, RORY.
KICKER: — sympathy follow for every weekend golfer reading this

LINE1: THIS COURSE IS
LINE2: EATING THE FIELD
LINE3: ALIVE AND IT'S
LINE4: KIND OF GLORIOUS.
KICKER: — half the field under 70, the other half in therapy

BAD (don't do this):
- "BRYSON HIT DRIVER OFF THE DECK" with no outcome: a setup with no punchline. State what happened.
- A personal-reaction take with a "just facts" kicker: take and kicker must agree.
- Hedging ("some might say..."): commit to the take."""


def _brand_system(role: str) -> str:
    return f"You write {role} for a golf content brand called The Scratch Sheet.\n\nBRAND VOICE:\n{_load_voice()}"


# ---- generators ----

def generate_hot_take(context: str, n: int = 3):
    """
    context: plain-text facts about what's happening (e.g. "Scottie Scheffler has taken the lead,
    passing Rory McIlroy."). Returns {"lines": [4], "kicker": str, "alternatives": [...]},
    or None if the model judged the facts too thin.
    """
    system = (
        _brand_system("hot-take social cards") + "\n\n" + HOT_TAKE_EXAMPLES + "\n\n"
        "Each option is ONE take: four short ALL-CAPS lines that read as one sentence (they're set "
        "huge in a condensed display font), then a normal-case KICKER one-liner starting with '— '. "
        "If it describes a shot or moment, state the outcome. The KICKER must agree with the take."
    )
    options = _ask("hot take", system, context, LIMITS["hot_take"], n, temperature=1.0, max_tokens=700)
    if options is None:
        return None
    to_take = lambda o: {"lines": [o["LINE1"], o["LINE2"], o["LINE3"], o["LINE4"]], "kicker": o["KICKER"]}
    best, *rest = [to_take(o) for o in options]
    return {**best, "alternatives": rest}


def generate_live_reaction(moment_description: str, n: int = 3):
    """For the Live Alert card. Returns {"event_line_1", "event_line_2", "reaction", "alternatives"} or None."""
    system = (
        _brand_system("live reaction cards") + "\n\n"
        "LINE1 and LINE2 together say what just happened, like a headline split over two huge lines. "
        "REACTION is your in-the-moment one-liner, normal case."
    )
    options = _ask("live reaction", system, moment_description, LIMITS["live"], n, temperature=1.0, max_tokens=500)
    if options is None:
        return None
    conv = lambda o: {"event_line_1": o["LINE1"], "event_line_2": o["LINE2"], "reaction": o["REACTION"]}
    best, *rest = [conv(o) for o in options]
    return {**best, "alternatives": rest}


def generate_intel_caption(stat_context: str, n: int = 3):
    """For the Intel Drop card. Returns {"stat", "what_it_means", "supporting_line", "alternatives"} or None."""
    system = (
        _brand_system("data-driven stat cards") + "\n\n"
        "STAT is the number itself, formatted for display (e.g. '0.41'). MEANS is a short ALL-CAPS "
        "headline saying what it is. SUPPORTING is one short line of color, normal case."
    )
    options = _ask("intel stat", system, stat_context, LIMITS["intel"], n, temperature=0.7, max_tokens=500)
    if options is None:
        return None
    conv = lambda o: {"stat": o["STAT"], "what_it_means": o["MEANS"], "supporting_line": o["SUPPORTING"]}
    best, *rest = [conv(o) for o in options]
    return {**best, "alternatives": rest}


def generate_supporting_line(facts: str, n: int = 3):
    """One short marker-style line under a stat card whose number and headline were set in code.
    Returns options (best first) or None on SKIP."""
    system = (
        _brand_system("the one-line comment under a stat card") + "\n\n"
        "The big number and the headline are already on the card. Write SUPPORTING: one short, "
        "normal-case line of color that makes a golf fan care about the stat. Don't repeat the headline."
    )
    options = _ask("supporting line", system, facts, {"SUPPORTING": LIMITS["intel"]["SUPPORTING"]}, n,
                   temperature=0.9, max_tokens=400)
    return None if options is None else [o["SUPPORTING"] for o in options]


def generate_caption_options(post_type: str, image_summary: str, event_tag: str = "", n: int = 3):
    """Caption options to post alongside a card, best first; None if the model chose SKIP.

    event_tag: an optional real event hashtag (e.g. "#USOpen") to append during
    majors/big events when search volume is genuinely elevated. This isn't the
    "no hashtag spam" rule being violated — a correct, relevant event tag during
    the actual event is accurate tagging, not spam. Leave blank for normal weeks.
    """
    system = (
        _brand_system("captions for social cards") + "\n\n"
        f"The post is a {post_type} card. Each CAPTION is under 2 sentences. No hashtags (one may be "
        "added separately), no emojis unless the voice doc calls for them, no engagement bait."
    )
    options = _ask("caption", system, image_summary, LIMITS["caption"], n, temperature=0.8, max_tokens=500)
    if options is None:
        return None
    return [f"{o['CAPTION']} {event_tag}".strip() for o in options]


def generate_social_caption(post_type: str, image_summary: str, event_tag: str = "") -> str:
    """Single best caption (kept for callers that want one string). Raises ContentError on SKIP."""
    options = generate_caption_options(post_type, image_summary, event_tag)
    if not options:
        raise ContentError("caption: model chose SKIP")
    return options[0]


def generate_newsletter_recap(facts: str) -> str:
    """Two short paragraphs recapping last week, written ONLY from the facts given.
    facts: plain text built by pipeline.py from DataGolf results (winner, top 5, how our picks finished).
    Returns plain text; the caller escapes it into HTML. Tables of numbers are built in code, not here."""
    system = (
        _brand_system("the weekly recap in the newsletter") + "\n\n"
        f"Write two short paragraphs, {RECAP_MAX_WORDS} words at most in total: what happened, then an "
        "honest line on our picks — own the misses as confidently as the hits. Use ONLY the facts given: "
        "no scores, stats, shots or storylines that aren't in them. Use names (players, course, event) exactly "
        "as written in the facts. Plain text, no headings, no markdown."
    )
    feedback = ""
    for _ in range(2):
        text = _generate(system, f"FACTS:\n{facts}{feedback}", max_tokens=400, temperature=0.3)
        issues = []
        if len(text.split()) > RECAP_MAX_WORDS:
            issues.append(f"{len(text.split())} words (max {RECAP_MAX_WORDS})")
        invented = ungrounded_numbers(text, facts)
        if invented:
            issues.append(f"uses numbers not in the facts: {', '.join(invented)}")
        claims = unsupported_claims(text, facts)
        if claims:
            issues.append(f"claims history the facts don't support ({', '.join(claims)})")
        if not issues:
            return text
        feedback = "\n\nYour last answer was rejected:\n- " + "\n- ".join(issues) + "\nTry again."
    raise ContentError(f"recap: failed checks after a retry ({'; '.join(issues)})")


if __name__ == "__main__":
    if not os.environ.get("ANTHROPIC_API_KEY"):
        print("No ANTHROPIC_API_KEY set in this environment — expected here, "
              "module structure is ready. Set it as a GitHub Actions secret in production.")
    else:
        print(generate_hot_take("Scottie Scheffler has won 4 of his last 6 PGA Tour starts."))
