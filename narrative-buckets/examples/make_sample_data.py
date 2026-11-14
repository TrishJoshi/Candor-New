"""Generate a small synthetic Reddit-like dataset for exercising Stage 2.

Writes examples/sample_comments.json. Includes three topics with organic
narratives, one coordinated burst (near-identical comments from fresh accounts
within minutes), and a few off-topic comments that should land in noise.
"""

from __future__ import annotations

import json
import random
from datetime import datetime, timedelta, timezone
from pathlib import Path

NARRATIVES: dict[str, list[str]] = {
    "ev_cost": [
        "EVs are still way too expensive for normal families, I can't justify 50k for a car.",
        "Electric cars only make sense if you're rich. The sticker price is insane.",
        "Even with the tax credit an EV costs far more than a used Corolla.",
        "Battery replacement costs alone make electric vehicles a bad deal financially.",
        "Nobody on a working class budget can afford a new electric car right now.",
        "The upfront cost of EVs is the real barrier, not range anxiety.",
        "Insurance on my EV is double what I paid for my gas car, total rip off.",
        "Until EVs are under 25k they will stay a luxury product.",
    ],
    "ev_charging": [
        "Public chargers near me are broken half the time, it's a nightmare on road trips.",
        "The charging network just isn't there yet outside of big cities.",
        "I waited 40 minutes for a fast charger at a highway stop last weekend.",
        "Apartment dwellers have nowhere to charge, that's the biggest problem with EVs.",
        "Charging infrastructure in rural areas is basically nonexistent.",
        "Every charging network needs a different app, it's a mess.",
        "Took my EV on a road trip and spent more time hunting for working chargers than driving.",
    ],
    "ev_climate": [
        "EVs cut lifetime emissions massively even accounting for battery production.",
        "Switching to electric cars is one of the best things we can do for the climate.",
        "Grid is getting cleaner every year so EVs only get greener over time.",
        "Tailpipe emissions in cities are killing people, electric cars fix that.",
        "Studies consistently show EVs have a lower carbon footprint than gas cars.",
        "If we're serious about climate change we need to electrify transport now.",
    ],
    "wfh_productive": [
        "I get way more done working from home without office distractions.",
        "Remote work saved me two hours of commuting a day, I'm more productive than ever.",
        "My team's output went up after we went fully remote.",
        "Open offices kill focus. At home I can actually do deep work.",
        "Working remotely lets me structure my day around when I'm most productive.",
        "Productivity data from my company shows remote engineers ship more.",
    ],
    "wfh_culture": [
        "Junior employees miss out on mentorship when everyone is remote.",
        "Office culture matters, you can't build trust over Zoom.",
        "I learned more in one month in the office than a year of remote work.",
        "Spontaneous hallway conversations are where the real collaboration happens.",
        "Remote work is isolating, I barely know my coworkers anymore.",
        "New hires really struggle to integrate into a fully remote team.",
    ],
    "housing_zoning": [
        "Zoning laws that ban apartments are the main reason rent is so high.",
        "We need to legalize building more housing, single family zoning is the problem.",
        "Housing is expensive because cities make it illegal to build enough homes.",
        "Upzoning near transit would bring rents down within a few years.",
        "NIMBYs blocking every new development are why young people can't afford homes.",
        "Abolish parking minimums and density limits and housing gets cheaper.",
    ],
}

# Coordinated burst: lightly varied talking point pushed by fresh accounts.
ASTROTURF = [
    "Wake up people, EVs are a scam pushed by elites to control where you can drive.",
    "EVs are a total scam pushed by the elites so they can control where you drive.",
    "Electric cars are an elite scam designed to control where you are allowed to drive.",
    "Wake up, the EV push is a scam by elites to control your ability to drive.",
    "EVs are a scam. The elites want to control where you can drive. Wake up.",
    "This EV agenda is a scam from the elites to control where people can drive.",
    "Wake up people, electric vehicles are a scam by elites to control your driving.",
]

NOISE = [
    "lol",
    "Anyone know a good pizza place in Denver?",
    "My cat just knocked my coffee off the desk again.",
    "[deleted]",
    "What's the best mechanical keyboard switch for typing?",
]


def build(seed: int = 7) -> list[dict]:
    rng = random.Random(seed)
    start = datetime(2026, 11, 1, tzinfo=timezone.utc)
    rows: list[dict] = []

    def add(text: str, author: str, ts: datetime, narrative: str) -> None:
        rows.append(
            {
                "comment_id": f"c{len(rows):04d}",
                "author": author,
                "text": text,
                "created_utc": int(ts.timestamp()),
                "subreddit": "r/sample",
                "ground_truth": narrative,  # for eyeballing results only
            }
        )

    for name, texts in NARRATIVES.items():
        for text in texts:
            ts = start + timedelta(hours=rng.uniform(0, 24 * 10))
            add(text, f"user_{rng.randint(1000, 9999)}", ts, name)

    burst_start = start + timedelta(days=6, hours=14)
    for i, text in enumerate(ASTROTURF):
        add(text, f"freedom_patriot_{8800 + i}", burst_start + timedelta(minutes=3 * i), "astroturf")

    for text in NOISE:
        add(text, f"user_{rng.randint(1000, 9999)}", start + timedelta(hours=rng.uniform(0, 240)), "noise")

    rng.shuffle(rows)
    return rows


if __name__ == "__main__":
    out = Path(__file__).with_name("sample_comments.json")
    data = build()
    out.write_text(json.dumps(data, indent=2))
    print(f"Wrote {len(data)} comments to {out}")
