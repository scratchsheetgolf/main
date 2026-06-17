# The Scratch Sheet — Automation Pipeline

Fully automated golf content: data in, branded posts and newsletter out.
Built to run on GitHub Actions (free) with minimal recurring cost.

## What's here

```
templates/      the 5 SVG templates, cleaned up with unique __TOKEN__ placeholders
render.py       fills templates with real data, rasterizes to PNG (tested, works)
data/
  datagolf.py   DataGolf API client (schedule, predictions, live stats/odds)
  state.py      persists snapshot data across runs (for detecting changes)
content/
  content.py    Claude API calls that write hot takes, captions, stat callouts
config/
  brand_voice.md   <- EDIT THIS, it's loaded into every content-generation prompt
distribute/
  image_host.py  gets rendered PNGs onto a public URL (required by IG/FB)
  post_x.py      X/Twitter posting
  post_meta.py   Facebook Page + Instagram posting
  post_tiktok.py TikTok photo posting
newsletter/
  newsletter.py  compiles weekly digest HTML, sends via Mailchimp
pipeline.py      orchestration — decides what to generate and when
workflows/       GitHub Actions YAML (copy into .github/workflows/ in your repo)
```

## Status right now

- **render.py is tested and works** — verified against all 5 templates with
  mock data, including special characters. This part is done.
- Everything touching live accounts (DataGolf, X, Meta, TikTok, Mailchimp,
  Anthropic) is built and structurally complete but **untested against real
  credentials**, since none exist yet. Each module's `__main__` block tells
  you what's missing when you run it directly.
- `pipeline.py`'s DataGolf field names (`player_name`, `current_score`, etc.)
  are best guesses from their docs, not verified against a live response.
  First thing to do once you have a key: call each `data.datagolf` function,
  print the raw JSON, fix field names in `pipeline.py` if they differ.

## Testing before going public

Both `picks` and `live` support `--dry-run` — runs against real DataGolf/Claude
API data and prints exactly what would be posted, without touching X, Meta,
TikTok, or the public image host. Use this first, always:

```
python pipeline.py live --tour pga --dry-run
python pipeline.py picks --tour pga --dry-run
```

This is how you catch wrong DataGolf field names, broken renders, or off-voice
content before any of it is public. Once a dry run looks right, drop the flag
(or trigger the real workflow) to actually post.

`--event-tag "#USOpen"` (or via the workflow_dispatch input in the Actions
tab) appends a real event hashtag to captions during majors, when search
volume around that tag is genuinely elevated. Leave blank for normal weeks —
this isn't a default-on hashtag, it's a deliberate per-event choice.

## Setup, roughly in order

1. **GitHub repo** — create one, push this code, move the `workflows/*.yml`
   files into `.github/workflows/` (GitHub only recognizes them there).
2. **DataGolf** — subscribe to Scratch Plus ($30/mo) at datagolf.com/subscribe,
   grab your API key.
3. **Anthropic API key** — for the content generation calls (separate from
   your claude.ai account; console.anthropic.com).
4. **Brand voice** — fill in `config/brand_voice.md` with real examples.
   This matters more than any other single piece for whether the Hot Take
   posts actually sound like you.
5. **X/Twitter** — developer.x.com, create a Project + App, generate user
   context keys/tokens (read+write). Pay-per-use billing, no monthly minimum.
6. **Meta (Facebook + Instagram)** — developers.facebook.com, create a
   Business app, add Page + Instagram products, generate a long-lived Page
   token. Submit for App Review (~10 days) to go live publicly — you can
   test against your own accounts immediately as the app admin while you wait.
7. **TikTok** — developers.tiktok.com, register an app, request Content
   Posting API access. This one's slow (audit process, budget weeks) and
   posts stay private-only until the audit clears. Start this one first
   since it's the bottleneck.
8. **Mailchimp** — free account to start (250 contacts/500 sends per month),
   create an Audience, generate an API key. Upgrade to Essentials (~$13/mo)
   once you outgrow the free cap.
9. **Add all the above as GitHub repo secrets** (Settings → Secrets and
   variables → Actions) — the workflow YAML files already reference the
   right names.
10. **Test with `workflow_dispatch`** — every workflow can be triggered
    manually from the Actions tab before trusting it on a schedule.

## Cost estimate

DataGolf $30/mo is the only fixed cost. X posting runs a few cents per post
(pay-per-use). Meta and TikTok are free (just dev-time for approval).
Mailchimp free tier covers early list sizes. GitHub Actions is free at this
volume. Realistically ~$30-35/month all-in until the newsletter list outgrows
Mailchimp's free tier.

## What still needs your input / decisions

- Brand voice (config/brand_voice.md) — placeholder until you fill it in.
- Exact pick/value/fade/sleeper selection logic in `run_pretournament_picks`
  — currently a placeholder; needs real logic against DataGolf's prediction
  data (e.g. win-odds rank for "win", a value threshold for "value", etc.).
- "Big moment" detection thresholds for Live Alert (eagle? albatross? lead
  change only?) — currently only triggers on lead change, you'll likely want
  more triggers once you see what the live data actually looks like round to round.
- Posting cadence/volume — how many leaderboard posts per round is too many?
  Worth deciding before this goes live so the accounts don't get spammy.
