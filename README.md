<h1 align="center">Candor</h1>

<p align="center"><b>We can't fix the crowd. But we can show you who's in it.</b></p>

<p align="center">
  <a href="https://candor-junction-2026.tech"><b>Try it live → candor-junction-2026.tech</b></a>
  &nbsp;·&nbsp; <a href="TECHNICAL.md">Technical documentation</a>
  &nbsp;·&nbsp; <a href="docs/screenshots/">All screenshots</a>
</p>

<p align="center"><img src="docs/screenshots/07-result-thread.png" alt="A Reddit thread seen through Candor: each comment carries its author's authenticity badge and its narrative, and the sidebar ranks the thread's narratives" width="900"></p>

---

## Read the comments first?

How many of you read the headline, skip the article, and go straight to the comments?

None? Just me?

Before we even open the link, we have already formed an opinion. Research shows that like counts, upvotes and top comments make it easier to judge a post before we have evaluated it.

## The problem

If it's about Hesburger vs McDonald's, or which Kardashian is the hottest, who cares if a million people liked it?

But some things can't be left to personal taste. When it affects your friends and your family, that's when public perception matters most. And that perception is exactly what bots, purchased accounts and coordinated campaigns are built to bend.

## The Candor approach

Instead of banning bots or flagging accounts, **we examine how narratives behave, and what kinds of accounts support them**, and we make that readily available to users and the public.

Our solution has two parts:

1. **Live platform, account and narrative authenticity analysis**, easy for anyone to digest.
2. **How these narratives shape the most powerful topics on the Internet today.**

---

## How it works

As you scroll the Internet, your feed fills with content, each piece pushing a narrative. Paste any public Reddit link into Candor, or browse threads we have already analysed.

<table>
<tr>
<td width="50%"><img src="docs/screenshots/01-analyze.png" alt="Analyze tab: paste a Reddit link or open the demo feed"></td>
<td width="50%"><img src="docs/screenshots/04-demo-feed.png" alt="Demo feed of real analysed threads"></td>
</tr>
<tr>
<td align="center"><sub>Paste any public Reddit thread: results in about a minute</sub></td>
<td align="center"><sub>Or browse the demo feed of threads analysed by the live pipeline</sub></td>
</tr>
</table>

### Stage 1 · Who is talking?

[context.dev](https://context.dev) lets us query structured data from posts and comments in real time, as news and trends develop.

An algorithm then characterises the accounts commenting under the post. We check them against behavioural patterns that match **Coordinated Inauthentic Behaviour (CIB)**, the kind shown by bots and purchased accounts. Every account is scored 0–100 on five weighted, transparent criteria:

| | Criterion | The question it asks |
|---|---|---|
| C1 | Profile | Is this an established, real person? |
| C2 | Originality | Did this person write it? |
| C3 | Coordination | Is it acting in concert with others? |
| C4 | Engagement legitimacy | Did it earn its upvotes organically? |
| C5 | Behavioural history | Does the account do this elsewhere? |

<table>
<tr>
<td width="50%"><img src="docs/screenshots/14-account-score-popover.png" alt="A flagged account's score and its five criteria"></td>
<td width="50%"><img src="docs/screenshots/11-authenticity-shield-card.png" alt="Authenticity Shield: breakdown of the thread's scored accounts"></td>
</tr>
<tr>
<td align="center"><sub>Click any badge to see why an account scored the way it did</sub></td>
<td align="center"><sub>The Authenticity Shield sums up who is in the thread</sub></td>
</tr>
</table>

### Stage 2 · What are they saying?

Using **Gemini's text embedding model**, each comment is turned into a vector, so text can be compared mathematically. **HDBSCAN** then finds dense clusters of comments that mean the same thing. **NVIDIA Nemotron** groups similar clusters into broader topics with agglomerative clustering, and explains what each group means: **the narrative**.

<table>
<tr>
<td width="50%"><img src="docs/screenshots/13-mini-dashboard.png" alt="Mini dashboard: each narrative's share and which kinds of accounts push it"></td>
<td width="50%"><img src="docs/screenshots/21-thread-narrative-map.png" alt="The thread's narrative map"></td>
</tr>
<tr>
<td align="center"><sub>Each narrative's share of the conversation, and which kinds of accounts push it</sub></td>
<td align="center"><sub>The narrative map: dots sized by upvotes, faded by author authenticity</sub></td>
</tr>
</table>

These narratives are then clustered across the Internet, building up the most relevant topics online.

<p align="center"><img src="docs/screenshots/27-dashboard-zoom-to-topic.png" alt="Dashboard: narrative map of 4,453 posts and comments, zoomed to one topic" width="900"></p>
<p align="center"><sub>The Dashboard: 4,453 posts and comments from four subreddits, grouped into 75 topics and 107 narratives</sub></p>

### Stage 3 · Showing you

The last stage is communicating what we've learned to **you**.

What we show you is **not a verdict on any person or any narrative**. It is the composition of the kinds of accounts supporting a narrative: through time, in every post, and across a channel, based on weighted, transparent criteria.

<table>
<tr>
<td width="50%"><img src="docs/screenshots/30-dashboard-inauthentic-narratives.png" alt="Narratives ranked by engagement from inauthentic-looking accounts"></td>
<td width="50%"><img src="docs/screenshots/35-narrative-weather-frame3.png" alt="Narrative weather: a time-lapse of narratives as they appear and grow"></td>
</tr>
<tr>
<td align="center"><sub>Which narratives lean on inauthentic accounts</sub></td>
<td align="center"><sub>Narrative weather: watch narratives form and swell over time</sub></td>
</tr>
</table>

Every number explains itself on hover, a built-in tutorial walks first-time visitors through each page, and it all works on a phone.

<p align="center">
  <img src="docs/screenshots/mobile/m03-result.png" alt="Result on mobile" width="200">
  &nbsp;
  <img src="docs/screenshots/mobile/m04-mini-dashboard.png" alt="Mini dashboard on mobile" width="200">
  &nbsp;
  <img src="docs/screenshots/mobile/m05-dashboard.png" alt="Dashboard on mobile" width="200">
</p>

---

## Our mission

To give a bigger view of what goes on across the Internet, and to cut through the noise. We want you to see what real people, with their own real intentions, are saying. **Empowering you with accessible transparency.**

Not just for users: Candor also provides narrative-monitoring capability to experts in politics, disaster risk reduction, health, sovereignty and legislation.

## Roadmap

| | Value now | Value later |
|---|---|---|
| **Scope** | We assess the narratives formed in every post. | With fewer resource limits, we combine these insights across an entire channel, or the entire platform, in real time. |
| **Platforms** | We are targeting Reddit. | As other social media platforms adopt Candor, we gain transparency into which platforms reliably protect their users from targeted content. |
| **Governance** | Open to the public. | Supporting governing bodies such as the European Commission (DG CONNECT) and the European External Action Service (EEAS), which address political and foreign information manipulation and interference, as they develop legislation. |

---

## Closing

Social media was built as a space to connect. Now we scroll through noise, wondering who's real and who's paid.

We can't fix the crowd. But we can show you who's in it.

<p align="center"><b>See it for yourself. See it with Candor.</b><br><a href="https://candor-junction-2026.tech">candor-junction-2026.tech</a></p>

---

<p align="center"><sub>Built at Junction 2026 with <a href="https://context.dev">context.dev</a>, Google AI Studio (Gemini), NVIDIA (Nemotron) and Hostinger.<br>How it's built, hosted and run: <a href="TECHNICAL.md">TECHNICAL.md</a> · Every feature screenshotted: <a href="docs/screenshots/">docs/screenshots</a></sub></p>
