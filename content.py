"""
content.py — generates the actual words (hot takes, captions, newsletter blurbs)
using the Claude API, grounded in real DataGolf data and the brand voice doc.

This is the only part of the pipeline that's genuinely generative rather than
data-formatting. Everything else (leaderboard numbers, stat call-outs) should
come straight from DataGolf — only feed the model real numbers, never let it
invent stats. That's both an accuracy requirement and an FTC/advertising-claims
safety net for anything that touches betting language.
"""
import os
import anthropic

MODEL = "claude-sonnet-4-6"
VOICE_DOC_PATH = os.path.join(os.path.dirname(__file__), "..", "config", "brand_voice.md")

client = anthropic.Anthropic()  # reads ANTHROPIC_API_KEY from env automatically

FALLBACK_VOICE = (
    "Sharp, opinionated, golf-savvy. Confident takes, dry humor, no hedging. "
    "Talks like a smart friend who watches every round, not a press release."
)


def _load_voice() -> str:
    try:
        with open(VOICE_DOC_PATH, "r", encoding="utf-8") as f:
            doc = f.read()
        if "PLACEHOLDER" in doc:
            return FALLBACK_VOICE
        return doc
    except FileNotFoundError:
        return FALLBACK_VOICE


def _generate(system: str, user: str, max_tokens: int = 300) -> str:
    resp = client.messages.create(
        model=MODEL,
        max_tokens=max_tokens,
        system=system,
        messages=[{"role": "user", "content": user}],
    )
    return resp.content[0].text.strip()


def generate_hot_take(context: str) -> dict:
    """
    context: a plain-text summary of what's happening (e.g. "Scottie Scheffler
    has won 4 of his last 6 starts and currently leads by 3 after 54 holes").

    Returns {"lines": [4 strings], "kicker": str} sized for the Hot Take template.
    """
    voice = _load_voice()
    system = (
        f"You write social posts for a golf content brand called The Scratch Sheet.\n\n"
        f"BRAND VOICE:\n{voice}\n\n"
        "You will be given real, factual context about current golf events. Write ONE "
        "punchy hot take based only on that context — never invent stats or scores not "
        "given to you. Output EXACTLY in this format, nothing else:\n"
        "LINE1: ...\nLINE2: ...\nLINE3: ...\nLINE4: ...\nKICKER: ...\n\n"
        "Each LINE should be short (2-5 words, ALL CAPS reads well since it's set in a big "
        "bold display font) and the four lines together form one complete thought/sentence. "
        "If the take describes a specific shot or moment, it MUST state the outcome/result, "
        "not just the action — 'Bryson hit driver off the deck' is an incomplete setup with "
        "no punchline; 'Bryson hit driver off the deck and nearly holed it' is a complete take. "
        "KICKER is a short italic one-liner underneath, normal case. The KICKER must logically "
        "agree with the LINE content: if the take is a personal reaction/opinion, the kicker "
        "should be a personal aside (not 'just facts'); if the take is itself a stated fact or "
        "stat, the kicker can lean into that. Don't let them contradict each other."
    )
    raw = _generate(system, context, max_tokens=200)
    lines, kicker = [], ""
    for line in raw.splitlines():
        if line.startswith("LINE"):
            lines.append(line.split(":", 1)[1].strip())
        elif line.startswith("KICKER:"):
            kicker = line.split(":", 1)[1].strip()
    return {"lines": lines[:4], "kicker": kicker}


def generate_live_reaction(moment_description: str) -> dict:
    """For the Live Alert template — short reaction to something that just happened."""
    voice = _load_voice()
    system = (
        f"You write live reaction posts for a golf content brand called The Scratch Sheet.\n\n"
        f"BRAND VOICE:\n{voice}\n\n"
        "You'll be given a factual description of something that just happened in a live "
        "round. Output EXACTLY:\nLINE1: ...\nLINE2: ...\nREACTION: ...\n\n"
        "LINE1/LINE2 together describe what happened, short and punchy (these render huge "
        "and bold — think headline, not sentence). REACTION is a short italic one-liner, "
        "your genuine in-the-moment reaction."
    )
    raw = _generate(system, moment_description, max_tokens=150)
    out = {"event_line_1": "", "event_line_2": "", "reaction": ""}
    for line in raw.splitlines():
        if line.startswith("LINE1:"):
            out["event_line_1"] = line.split(":", 1)[1].strip()
        elif line.startswith("LINE2:"):
            out["event_line_2"] = line.split(":", 1)[1].strip()
        elif line.startswith("REACTION:"):
            out["reaction"] = line.split(":", 1)[1].strip()
    return out


def generate_intel_caption(stat_context: str) -> dict:
    """For the Intel Stat template — turns a raw stat into a headline + supporting line."""
    voice = _load_voice()
    system = (
        f"You write data-driven golf content for a brand called The Scratch Sheet.\n\n"
        f"BRAND VOICE:\n{voice}\n\n"
        "You'll be given one real stat. Output EXACTLY:\nSTAT: ...\nMEANS: ...\nSUPPORTING: ...\n\n"
        "STAT is the number itself, formatted for display (e.g. '0.41' or '6-for-6'). "
        "MEANS is a short bold headline explaining what it is (ALL CAPS reads well, <8 words). "
        "SUPPORTING is one short italic line of color/context."
    )
    raw = _generate(system, stat_context, max_tokens=150)
    out = {"stat": "", "what_it_means": "", "supporting_line": ""}
    for line in raw.splitlines():
        if line.startswith("STAT:"):
            out["stat"] = line.split(":", 1)[1].strip()
        elif line.startswith("MEANS:"):
            out["what_it_means"] = line.split(":", 1)[1].strip()
        elif line.startswith("SUPPORTING:"):
            out["supporting_line"] = line.split(":", 1)[1].strip()
    return out


def generate_social_caption(post_type: str, image_summary: str, event_tag: str = "") -> str:
    """Short caption text to accompany the image when posting to each platform.

    event_tag: an optional real event hashtag (e.g. "#USOpen") to append during
    majors/big events when search volume is genuinely elevated. This isn't the
    "no hashtag spam" rule being violated — a correct, relevant event tag during
    the actual event is accurate tagging, not spam. Leave blank for normal weeks.
    """
    voice = _load_voice()
    system = (
        f"You write captions to accompany golf content images for The Scratch Sheet.\n\n"
        f"BRAND VOICE:\n{voice}\n\nKeep it under 2 sentences. No hashtag spam (max 2 "
        "relevant hashtags, only if it genuinely fits the voice). No emojis unless the "
        "voice doc explicitly calls for them."
    )
    user = f"Post type: {post_type}\nWhat the image shows: {image_summary}"
    caption = _generate(system, user, max_tokens=100)
    if event_tag:
        caption = f"{caption} {event_tag}"
    return caption


if __name__ == "__main__":
    if not os.environ.get("ANTHROPIC_API_KEY"):
        print("No ANTHROPIC_API_KEY set in this environment — expected here, "
              "module structure is ready. Set it as a GitHub Actions secret in production.")
    else:
        print(generate_hot_take("Scottie Scheffler has won 4 of his last 6 PGA Tour starts."))
