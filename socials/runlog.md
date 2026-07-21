# Social groei-run log

## 2026-07-20 — run 1 (eerste run)

**Replies:** `socials/` was leeg, geen bestanden om te verwerken.

**Instagram (follows + DM's):** technisch geblokkeerd. Instagram Graph API biedt geen
follow-endpoint (voor geen enkel accounttype), en de Messaging API staat geen cold-DM's
toe (alleen reageren binnen 24u/7d nadat de gebruiker zelf al contact zocht). Geen
unofficial workaround gebouwd (ToS-risico, ban-risico voor nieuw account met 0 volgers).
Gemeld aan Dennis per mail.

**Bluesky follows (8/8 gelukt, spacing 2-5 min):**
tweakers.net, brightnl.bsky.social, dutchcowboys.bsky.social, androidworldnl.bsky.social,
techcrunch.com, theverge.com, arstechnica.com, wired.com — allemaal bronsites uit
sources.txt met bevestigde, betrouwbare accounts (grotendeels custom-domain handles).

**Outreach mail (1 draft, geen mail direct verstuurd):**
[DRAFT] naar dennis@gebbink.nl voor tech@insiderpodcast.nl (Tech Insider Podcast,
Martijn van der Hoeden) — B2B-podcast over SaaS/AI, geen concurrent. Wacht op
goedkeuring/doorsturen door Dennis.

**Instagram DM's:** 0 (technisch niet mogelijk, zie boven).

**Groeitactiek:** gecontroleerd of technieuwsvandaag.nl socials linkt — Instagram en
Bluesky staan al in header en footer, geen actie nodig.

**Overige mails aan Dennis:**
- Technisch-blocker rapport (Instagram follow/DM).
- [IDEE] Stories/Reels-strategie: posts blijven leidend tot ~50 volgers, dan
  story-reeks toevoegen (hergebruikt bestaand beeld, geen pipeline-wijziging nodig
  behalve 9:16-formaat), Reels genoemd als grootste groei-hefboom maar apart project.

**Infrastructuur toegevoegd:** `socials/state.json` (state-bijhouding, bestond nog niet),
`socials/bluesky_actions.py` (herbruikbaar throttled follow-script).

**Problemen:** geen technische fouten buiten de Instagram-blocker hierboven.
