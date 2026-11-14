"""
Build a labelled synthetic test post: N comments, of which NARRATIVE_SHARE push one
coordinated narrative (same topic, shared talking points, sock-puppet-ish accounts,
bursty timing). The rest are organic, varied comments.

Every comment carries a ground-truth field `_truth` = {"is_narrative": bool, "tier": ...}
which scorer.py passes straight through, so evaluate.py can measure precision/recall.

Usage:
    python generate_test_data.py [--n 1000] [--share 0.2] [--seed 42]
                                 [--organic-from scraped_post.json] [--out test_1k.json]

--organic-from: optionally mix real scraped comments (e.g. from context_scraper.py)
into the organic pool so the "authentic" side isn't purely template-generated.

Narrative tiers (so the test isn't trivially easy):
    blatant   (40%) copy-paste + hashtags, brand-new numeric accounts, posted in bursts
    paraphrase(40%) same talking points reworded, mixed account types
    subtle    (20%) on-message but personal-sounding, older-looking accounts, spread timing
"""
from __future__ import annotations

import argparse
import json
import random
from datetime import datetime, timedelta, timezone

TZ = timezone(timedelta(hours=8))
POST_TIME = datetime(2026, 11, 13, 8, 19, tzinfo=TZ)

# ---- the narrative being pushed: edit these to simulate a different campaign --------
NARRATIVE = {
    "topic": "Pax Silica brings jobs; protesters are paid/foreign-funded",
    "hashtags": ["#YesToPaxSilica", "#JobsForFilipinos", "#PaxSilicaNow", "#ProgressPH"],
    "core_lines": [
        "Pax Silica means thousands of high-paying jobs for Filipinos",
        "These protesters are paid by foreign groups who don't want the Philippines to progress",
        "Semiconductors are the future and we cannot be left behind by Vietnam again",
        "Students should study instead of cutting classes for rallies",
        "Investment is investment, the critics just want us to stay poor",
    ],
    "openers": ["Honestly,", "Wake up people.", "Facts:", "Let's be real,", "As a parent,",
                "Simple lang:", "Sorry but", "Again,", ""],
    "closers": ["", "Period.", "Think about it.", "Support Pax Silica!", "Huwag magpaloko.",
                "Para sa bayan.", "Mabuhay ang Pilipinas!"],
    "subtle": [
        "My cousin works in a chip plant in Laguna and the pay is honestly good. More of that would help a lot of families.",
        "I get why students are worried but I remember when the call centers came in and everyone said the same thing. Jobs came anyway.",
        "Not a fan of Trump at all but this deal seems separate from him. We need the factories.",
        "Graduated engineering 2 yrs ago and still no work in my field. If Pax Silica brings fabs here I'm applying day one.",
        "Kinda feels like the rally was organized by the same groups every time. Would like to hear from actual workers.",
        "Vietnam and Malaysia got these investments years ago and look at them now. We keep missing the boat.",
    ],
}

# ---- organic pool: varied stances, topics, lengths, languages -----------------------
ORGANIC = [
    "Proud of the students for speaking up.", "Ingat kayo sa rally!", "What is Pax Silica exactly? Can someone explain?",
    "I'm torn on this. Jobs are good but what about the land issues they mentioned?",
    "The US embassy is probably taking photos lol", "Animo La Salle!", "Where's the source on the investment numbers?",
    "Respect their right to protest even if I don't agree.", "Hindi lahat ng investment ay maganda para sa atin.",
    "Saw them along Taft earlier, traffic was crazy", "My classmates were there, it was peaceful naman.",
    "Can't believe this is happening during ASEAN week", "Water use of these fabs is a real concern, look at Taiwan's droughts.",
    "Both sides have a point honestly", "lol the comments here are wild", "Who organized this?",
    "Kawawa naman yung mga magsasaka kung matuloy", "Good photos Archers Network!",
    "The government should publish the full agreement first.", "Trump visiting here is already a mess.",
    "This is why I love DLSU", "Students should be allowed to protest, that's democracy.",
    "I work in manufacturing, these jobs aren't as high paying as people think.",
    "Sana may dialogue with the admin after this", "How many people joined?", "Tag mo ko next time!",
    "My lola grew up near the proposed site, she's worried.", "Hammer and sickle flag was a bad look ngl",
    "Can we focus on the actual policy instead of attacking students?", "Pax Silica sounds like a video game",
    "Ang init pa naman kanina, sana okay lang sila", "Not everyone at the rally was a student btw",
    "Long read but worth it: the deal has some good parts and some scary parts.", "First time I've seen a walkout at DLSU",
    "The admin allowed this? Interesting.", "Following for updates", "Ano ba talaga position ng university?",
]
ORGANIC_TAILS = ["", "", "", " 😂", " 🙏", "!!", " haha", " tbh", " imo", " sana all", "..."]

# compositional organic comments: lead-in + subject + take, so 800 comments are mostly unique
ORG_LEAD = ["", "", "Hmm", "Ok so", "Grabe", "Real talk", "Lowkey", "Tbh", "Wait", "Ngl", "As an alumni,",
            "From someone in Cavite,", "Just saw this,", "Hot take:", "Genuine question,", "Idk but", "My dad says"]
ORG_SUBJ = ["the walkout", "the rally", "Pax Silica", "the US deal", "this whole thing", "the DLSU admin", "the ASEAN summit",
            "Trump's visit", "the semiconductor plan", "the farmers' issue", "the student council", "the news coverage",
            "the land conversion part", "the protest photos", "the traffic on Taft", "these comments"]
ORG_TAKE = ["is getting blown out of proportion", "deserves more attention", "needs a proper public hearing",
            "makes me nervous for the province", "is more complicated than people think", "was handled pretty well",
            "honestly confuses me", "should be explained better by the government", "is giving 2016 vibes",
            "won't change anything sadly", "is exactly why I stopped reading FB news", "has me worried about water supply",
            "is why we need better civic education", "could go either way", "is a good thing if done transparently",
            "feels rushed", "isn't what the headline makes it sound like", "reminds me of the Clark expansion debate"]


def organic_text(rng: random.Random) -> str:
    r = rng.random()
    if r < 0.2:  # short stock reactions — real duplicates do happen
        return rng.choice(ORGANIC) + rng.choice(ORGANIC_TAILS)
    text = f"{rng.choice(ORG_LEAD)} {rng.choice(ORG_SUBJ)} {rng.choice(ORG_TAKE)}".strip()
    text = text[0].upper() + text[1:] + rng.choice([".", "", "!", "?", " lol", ".."])
    if rng.random() < 0.3:
        text += " " + rng.choice(ORGANIC)
    return text + rng.choice(ORGANIC_TAILS)

FIRST = ["Maria", "Jose", "Ana", "Mark", "John", "Kristine", "Paolo", "Bea", "Carlo", "Jasmine", "Miguel",
         "Rhea", "Angelo", "Patricia", "Jerome", "Camille", "Rafael", "Nicole", "Ken", "Liza", "Joshua", "Mae"]
LAST = ["Santos", "Reyes", "Cruz", "Bautista", "Garcia", "Mendoza", "Torres", "Ramos", "Aquino", "Flores",
        "Villanueva", "Castillo", "DelaCruz", "Navarro", "Lim", "Tan", "Salazar", "Domingo", "Pascual"]


def make_author(rng: random.Random, kind: str, used: set[str]) -> dict:
    """kind: organic | puppet_new | puppet_mixed | puppet_aged"""
    while True:
        name = f"{rng.choice(FIRST)} {rng.choice(LAST)}"
        if kind != "organic" and rng.random() < 0.3:
            name += f" {rng.randint(10, 99)}"
        elif rng.random() < 0.5:  # middle initial / second name keeps 1k+ names unique
            name = name.replace(" ", f" {rng.choice(FIRST)[0]}. ", 1) if rng.random() < 0.6 \
                else name.replace(" ", f" {rng.choice(FIRST)} ", 1)
        if name not in used:
            used.add(name)
            break
    slug = name.replace(" ", "-")
    if kind == "puppet_new" or (kind == "puppet_mixed" and rng.random() < 0.5):
        url = f"https://www.facebook.com/people/{slug}/{rng.choice(['6155', '6156', '6157'])}{rng.randint(10**9, 10**10 - 1)}/"
    elif kind == "puppet_mixed":
        url = f"https://www.facebook.com/{slug.lower().replace('-', '.')}.{rng.choice([2024, 2025, 2026])}"
    else:  # organic / aged: mix of plain vanity, old numeric ids, and private pfbid
        r = rng.random()
        if r < 0.45:
            url = f"https://www.facebook.com/{slug.lower().replace('-', '.')}{rng.choice(['', '', str(rng.randint(1, 99))])}"
        elif r < 0.65:
            url = f"https://www.facebook.com/people/{slug}/{rng.choice(['1000', '1000', '5000'])}{rng.randint(10**10, 10**11 - 1)}/"
        else:
            url = f"https://www.facebook.com/people/{slug}/pfbid0{''.join(rng.choices('abcdefghijkLMNOPQ123456789', k=40))}/"
    return {"id": name, "name": name, "profile_url": url}


def ts(dt: datetime) -> str:
    return dt.isoformat(timespec="seconds")


def narrative_text(rng: random.Random, tier: str) -> str:
    n = NARRATIVE
    if tier == "blatant":
        line = rng.choice(n["core_lines"][:2])  # few lines -> heavy copy-paste
        tags = " ".join(rng.sample(n["hashtags"], k=rng.randint(3, 4)))
        return f"{line}! {tags}" if rng.random() < 0.8 else rng.choice(["#YesToPaxSilica", "YES to PAX SILICA!", "Jobs! #PaxSilicaNow"])
    if tier == "paraphrase":
        lines = rng.sample(n["core_lines"], k=rng.randint(1, 2))
        lines = [l if rng.random() < 0.5 else l.lower() for l in lines]
        text = f"{rng.choice(n['openers'])} {'. '.join(lines)}. {rng.choice(n['closers'])}".strip()
        return text + (f" {rng.choice(n['hashtags'])}" if rng.random() < 0.4 else "")
    return rng.choice(n["subtle"]) + rng.choice(["", "", " 🙏", " just my 2 cents"])


def build(n: int, share: float, seed: int, organic_seed: list[dict]) -> dict:
    rng = random.Random(seed)
    used: set[str] = set()
    n_narr = round(n * share)
    n_org = n - n_narr
    comments: list[dict] = []

    # --- narrative: ~1 puppet account per 3 comments, so some accounts post repeatedly in bursts
    tiers = ["blatant"] * round(n_narr * 0.4) + ["paraphrase"] * round(n_narr * 0.4)
    tiers += ["subtle"] * (n_narr - len(tiers))
    pools = {
        "blatant": [make_author(rng, "puppet_new", used) for _ in range(max(1, len(tiers) // 8))],
        "paraphrase": [make_author(rng, "puppet_mixed", used) for _ in range(max(1, len(tiers) // 6))],
        "subtle": [make_author(rng, "puppet_aged", used) for _ in range(max(1, len(tiers) // 6))],
    }
    wave_starts = [POST_TIME + timedelta(minutes=rng.randint(5, 600)) for _ in range(4)]
    for i, tier in enumerate(tiers):
        if tier == "subtle":
            t = POST_TIME + timedelta(minutes=rng.randint(10, 1400))
        else:  # coordinated waves: seconds-to-minutes apart
            t = rng.choice(wave_starts) + timedelta(seconds=rng.randint(0, 900))
        comments.append({
            "comment_id": f"n{i:05d}",
            "author": dict(rng.choice(pools[tier])),
            "message": narrative_text(rng, tier),
            "attachments": [], "reaction_count": rng.randint(0, 40),
            "created_time": ts(t), "replies": [],
            "_truth": {"is_narrative": True, "tier": tier},
        })

    # --- organic: mostly one-off authors, a few regulars, spread over ~24h
    regulars = [make_author(rng, "organic", used) for _ in range(15)]
    for i in range(n_org):
        if organic_seed and rng.random() < 0.3:
            src = rng.choice(organic_seed)
            text, attachments = src.get("message", ""), src.get("attachments", [])
        elif rng.random() < 0.03:
            text, attachments = "", [{"type": "photo", "url": "https://www.facebook.com/photo.php?fbid=0", "title": "May be a meme"}]
        else:
            text, attachments = organic_text(rng), []
        author = rng.choice(regulars) if rng.random() < 0.08 else make_author(rng, "organic", used)
        comments.append({
            "comment_id": f"o{i:05d}",
            "author": dict(author),
            "message": text, "attachments": attachments, "reaction_count": rng.randint(0, 60),
            "created_time": ts(POST_TIME + timedelta(minutes=rng.randint(1, 1440), seconds=rng.randint(0, 59))),
            "replies": [],
            "_truth": {"is_narrative": False, "tier": "organic"},
        })

    rng.shuffle(comments)
    return {
        "post_id": "synthetic_test_post",
        "permalink_url": "https://www.facebook.com/ArchersNetwork/posts/synthetic/",
        "author": {"id": "ArchersNetwork", "name": "Archers Network", "profile_url": "https://www.facebook.com/ArchersNetwork"},
        "source": {"type": "page", "id": "ArchersNetwork", "name": "Archers Network"},
        "message": "LASALLIANS WALK OUT AGAINST PAX SILICA, DONALD TRUMP (synthetic test post)",
        "attachments": [], "share_count": 0, "comment_count": n,
        "created_time": ts(POST_TIME),
        "comments": comments,
        "_test_meta": {"n": n, "narrative_share": share, "n_narrative": n_narr, "seed": seed,
                       "narrative_topic": NARRATIVE["topic"]},
    }


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=1000)
    ap.add_argument("--share", type=float, default=0.2)
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--organic-from", help="scraped post JSON whose comments seed the organic pool")
    ap.add_argument("--out", default="test_1k.json")
    a = ap.parse_args()

    seed_comments = []
    if a.organic_from:
        with open(a.organic_from, encoding="utf-8") as f:
            seed_comments = [c for c in json.load(f).get("comments", []) if c.get("message")]
    post = build(a.n, a.share, a.seed, seed_comments)
    with open(a.out, "w", encoding="utf-8") as f:
        json.dump(post, f, ensure_ascii=False, indent=2)
    m = post["_test_meta"]
    print(f"wrote {m['n']} comments ({m['n_narrative']} narrative) -> {a.out}")
