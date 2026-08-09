#!/usr/bin/env python3
"""
Wekelijkse, beargumenteerde uitbreiding van sources.txt.

Twee kandidaat-kanalen:
  1. Domeinen waar meerdere bestaande bronnen deze week naar linken vanuit
     hun artikelen (cross-linking tussen gerenommeerde bronnen is een
     signaal van gezag).
  2. Een korte Claude-suggestie van gerenommeerde tech-nieuwssites die nog
     niet in de lijst staan.

Elke kandidaat moet daarna een check doorstaan voordat hij wordt
toegevoegd: bereikbaar, geen bekende niet-nieuwssite (denylist), en een
Claude-oordeel dat het een gerenommeerde, redactioneel gedreven
tech-nieuwsbron is (met taal NL/EN). Toevoegingen worden gelogd in
source_discovery_log.txt zodat ze achteraf te controleren/terug te draaien
zijn.

Gebruik:
  venv/bin/python3 source_discovery.py            # echte run
  venv/bin/python3 source_discovery.py --dry-run   # alleen loggen, niets aanpassen
"""
import argparse
import logging
import random
import sys
from collections import Counter
from datetime import date
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse

import feedparser
from bs4 import BeautifulSoup

from ai_processor import _call_claude, _extract_json
from config import BASE_DIR, REQUEST_TIMEOUT, SOURCES_FILE
from scraper import (
    USER_AGENT,
    _make_session,
    extract_outbound_domains,
    load_sources,
    scrape_all_sources,
    try_rss_feed,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)-8s] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger(__name__)

DISCOVERY_LOG_FILE = BASE_DIR / "source_discovery_log.txt"

# Cross-linking-drempel: minstens dit aantal verschillende bestaande bronnen
# moet er deze week naar hetzelfde nieuwe domein hebben gelinkt
MIN_LINKING_SOURCES = 2

# Max. aantal artikelen waarvan de pagina echt wordt gefetcht voor
# link-analyse (blijft een steekproef, geen volledige herscrape)
MAX_ARTICLES_FOR_LINK_SCAN = 60

# Bekende niet-nieuwssites die genegeerd worden, ook als ze vaak gelinkt
# worden (social/embed/infra-domeinen, geen tech-nieuwsbron)
DOMAIN_DENYLIST = {
    "twitter.com", "x.com", "facebook.com", "instagram.com", "linkedin.com",
    "youtube.com", "youtu.be", "reddit.com", "github.com", "wikipedia.org",
    "google.com", "goo.gl", "bit.ly", "t.co", "amazon.com", "amzn.to",
    "apple.com", "play.google.com", "apps.apple.com", "medium.com",
    "wordpress.com", "wp.com", "feedburner.com", "doubleclick.net",
    "googlesyndication.com", "google-analytics.com", "gstatic.com",
    "cloudflare.com", "schema.org", "creativecommons.org", "w3.org",
    "mailchimp.com", "patreon.com", "discord.com", "discord.gg",
    "tiktok.com", "pinterest.com", "spotify.com",
}


def _normalise_domain(text: str) -> str:
    """Strip scheme/path/www. from a URL or bare domain string."""
    text = text.strip()
    if not text.startswith("http"):
        text = "https://" + text
    return urlparse(text).netloc.lower().removeprefix("www.")


def _existing_domains() -> set[str]:
    """Return the set of normalised domains already in sources.txt."""
    return {_normalise_domain(website) for website, _rss, _lang in load_sources()}


def find_crosslinked_candidates(existing: set[str]) -> Counter:
    """Scan a week of articles from existing sources for outbound links to
    domains not yet in sources.txt.
    Pre:  existing is the current set of normalised source domains
    Post: returns Counter {candidate_domain: aantal verschillende bronnen die linkten}
    """
    logger.info("Artikelen ophalen (7 dagen) voor cross-link analyse...")
    articles = scrape_all_sources(lookback_days=7, max_articles=None)
    if len(articles) > MAX_ARTICLES_FOR_LINK_SCAN:
        articles = random.sample(articles, MAX_ARTICLES_FOR_LINK_SCAN)
    logger.info("%d artikel(en) worden gescand op uitgaande links", len(articles))

    session = _make_session()
    linked_by: dict[str, set[str]] = {}

    for article in articles:
        source_domain = _normalise_domain(article.source)
        for domain in extract_outbound_domains(article.url, session):
            if domain in existing or domain in DOMAIN_DENYLIST:
                continue
            linked_by.setdefault(domain, set()).add(source_domain)

    return Counter({d: len(srcs) for d, srcs in linked_by.items()})


def ask_claude_for_suggestions(existing: set[str]) -> list[dict]:
    """Ask Claude for reputable tech-news domains missing from the current list.
    Post: returns list of {"domain", "reason"} dicts (best-effort; [] on failure)
    """
    domains_list = "\n".join(sorted(existing))
    prompt = (
        "Je helpt de bronnenlijst van een Nederlandse tech-nieuwswebsite "
        "(TechNieuwsVandaag) uitbreiden. Dit zijn de huidige bronnen:\n\n"
        f"{domains_list}\n\n"
        "Noem maximaal 5 gerenommeerde technologie-nieuwssites (Nederlands of "
        "internationaal) die NIET in bovenstaande lijst staan en die qua "
        "kwaliteit en gezag passen bij de bestaande lijst. Geen algemene "
        "nieuwssites zonder tech-focus, geen blogs zonder redactie, geen "
        "sites die al impliciet in de lijst zitten (bv. een ander domein van "
        "hetzelfde merk).\n\n"
        'Antwoord ALLEEN met een JSON-array: '
        '[{"domain": "voorbeeld.com", "reason": "korte onderbouwing"}]'
    )
    try:
        data = _extract_json(_call_claude(prompt, timeout=60))
        if isinstance(data, list):
            return [
                d for d in data
                if isinstance(d, dict) and d.get("domain")
                and _normalise_domain(d["domain"]) not in existing
            ]
    except Exception as exc:
        logger.warning("Claude-suggesties ophalen mislukt: %s", exc)
    return []


def verify_candidate(domain: str, session) -> tuple[bool, Optional[str], str]:
    """Simple mechanical check: reachable + look for a working RSS feed.
    Post: returns (ok, rss_url_or_None, page_title)
    """
    url = "https://" + domain
    try:
        resp = session.get(url, timeout=REQUEST_TIMEOUT)
        resp.raise_for_status()
    except Exception as exc:
        logger.info("Kandidaat %s niet bereikbaar: %s", domain, exc)
        return False, None, ""

    title = ""
    try:
        soup = BeautifulSoup(resp.text, "lxml")
        if soup.title and soup.title.string:
            title = soup.title.string.strip()[:200]
    except Exception:
        pass

    feed = try_rss_feed(url)
    rss_url = getattr(feed, "href", None) if feed else None
    return True, rss_url, title


def _feed_has_entries(rss_url: str) -> bool:
    """True als rss_url een RSS-feed met minstens één item oplevert."""
    try:
        return bool(feedparser.parse(rss_url, agent=USER_AGENT).entries)
    except Exception as exc:
        logger.debug("Feed-check mislukt voor %s: %s", rss_url, exc)
        return False


def judge_reputability(candidates: list[dict]) -> list[dict]:
    """Ask Claude in one batch call whether each verified candidate is a
    reputable tech-news source, and to confirm its language.
    Pre:  candidates is a list of {"domain", "title", "reason"}
    Post: returns only entries Claude marked reputable, each with "lang" set
    """
    if not candidates:
        return []

    numbered = "\n".join(
        f"{i + 1}. {c['domain']} — paginatitel: \"{c['title']}\" — reden van voordracht: {c['reason']}"
        for i, c in enumerate(candidates)
    )
    prompt = (
        "Beoordeel per kandidaat-website of dit een GERENOMMEERDE, redactioneel "
        "gedreven technologie-nieuwssite is (geschikt als bron voor een "
        "Nederlandse tech-nieuwssite) — geen forum, geen prijsvergelijker, "
        "geen contentfarm/SEO-site, geen persbureau-feed zonder eigen redactie.\n\n"
        "Is de kandidaat een ALGEMEEN nieuwsmedium (krant, omroep, magazine) met "
        "een aparte tech-sectie, dan mag hij alleen mee met de RSS-feed van die "
        "tech-sectie — anders levert hij vooral niet-tech nieuws. Zet in dat geval "
        'die feed-URL in "tech_rss". Ken je zo\'n feed niet met zekerheid, zet dan '
        "reputable op false. Een site die al volledig over technologie gaat, "
        'laat "tech_rss" leeg.\n\n'
        f"{numbered}\n\n"
        "Antwoord ALLEEN met een JSON-array, exact één object per kandidaat, "
        'in dezelfde volgorde: '
        '[{"reputable": true/false, "lang": "NL of EN", "tech_rss": "URL of leeg"}]'
    )
    try:
        data = _extract_json(_call_claude(prompt, timeout=90))
        if isinstance(data, list) and len(data) == len(candidates):
            accepted = []
            for c, verdict in zip(candidates, data):
                if isinstance(verdict, dict) and verdict.get("reputable") is True:
                    c["lang"] = str(verdict.get("lang", "EN")).strip().upper()
                    tech_rss = str(verdict.get("tech_rss") or "").strip()
                    if tech_rss:
                        # Nooit ongezien overnemen: een verzonnen feed-URL zou de
                        # bron stil onbruikbaar maken.
                        if _feed_has_entries(tech_rss):
                            c["rss"] = tech_rss
                        else:
                            logger.warning(
                                "%s: opgegeven tech-feed %s levert niets op — overgeslagen",
                                c["domain"], tech_rss,
                            )
                            continue
                    accepted.append(c)
            return accepted
    except Exception as exc:
        logger.warning("Reputatie-beoordeling mislukt: %s — geen kandidaten toegevoegd", exc)
    return []


def add_to_sources_file(accepted: list[dict], dry_run: bool = False) -> None:
    """Append accepted domains to sources.txt in the correct NL/EN section.
    Pre:  accepted entries have 'domain', 'lang' ('NL'/'EN'), optional 'rss'
    Post: sources.txt updated in place (unless dry_run); each addition logged
          to source_discovery_log.txt
    """
    if not accepted:
        logger.info("Geen kandidaten die de checks doorstonden.")
        return

    def _line_for(c: dict) -> str:
        return f"{c['domain']}|{c['rss']}" if c.get("rss") else c["domain"]

    if not dry_run:
        lines = SOURCES_FILE.read_text(encoding="utf-8").splitlines()
        en_marker_idx = next(
            (i for i, l in enumerate(lines) if l.strip().upper() == "// EN"), len(lines)
        )

        for c in accepted:
            if c["lang"] == "NL":
                lines.insert(en_marker_idx, _line_for(c))
                en_marker_idx += 1
            else:
                lines.append(_line_for(c))

        SOURCES_FILE.write_text("\n".join(lines) + "\n", encoding="utf-8")
        logger.info("sources.txt bijgewerkt met %d nieuwe bron(nen)", len(accepted))
    else:
        logger.info("[DRY RUN] Zou toevoegen: %s",
                     [(c["domain"], c["lang"]) for c in accepted])

    with open(DISCOVERY_LOG_FILE, "a", encoding="utf-8") as f:
        for c in accepted:
            prefix = "DRY-RUN|" if dry_run else ""
            f.write(
                f"{date.today().isoformat()}|{c['domain']}|{c['lang']}|"
                f"{prefix}{c.get('reason', '')}\n"
            )


def main() -> None:
    parser = argparse.ArgumentParser(description="Bronnenlijst wekelijks beargumenteerd uitbreiden")
    parser.add_argument("--dry-run", action="store_true", help="Alleen loggen, niets aanpassen")
    args = parser.parse_args()

    existing = _existing_domains()
    logger.info("%d bestaande bron-domeinen geladen", len(existing))

    crosslink_counts = find_crosslinked_candidates(existing)
    crosslinked = [
        {"domain": d, "reason": f"gelinkt door {n} bestaande bron(nen) deze week"}
        for d, n in crosslink_counts.items() if n >= MIN_LINKING_SOURCES
    ]
    logger.info("%d cross-linked kandidaat/kandidaten (>= %d bronnen)",
                len(crosslinked), MIN_LINKING_SOURCES)

    claude_suggestions = ask_claude_for_suggestions(existing)
    claude_candidates = [
        {"domain": _normalise_domain(s["domain"]), "reason": s.get("reason", "Claude-suggestie")}
        for s in claude_suggestions
    ]
    logger.info("%d Claude-suggestie(s)", len(claude_candidates))

    # Dedupliceren (cross-link kandidaat + Claude-suggestie voor zelfde domein)
    merged: dict[str, dict] = {}
    for c in crosslinked + claude_candidates:
        merged.setdefault(c["domain"], c)
    candidates = list(merged.values())

    if not candidates:
        logger.info("Geen kandidaten gevonden deze week.")
        return

    session = _make_session()
    verified = []
    for c in candidates:
        ok, rss_url, title = verify_candidate(c["domain"], session)
        if ok:
            c["title"] = title
            c["rss"] = rss_url
            verified.append(c)
    logger.info("%d/%d kandidaten bereikbaar", len(verified), len(candidates))

    accepted = judge_reputability(verified)
    logger.info("%d kandidaat/kandidaten geaccepteerd na reputatie-check", len(accepted))

    add_to_sources_file(accepted, dry_run=args.dry_run)

    if accepted:
        # De bronnenpagina hoort bij sources.txt: liep die met de hand achter, dan
        # klopte de publieke verantwoording niet meer (aug. 2026: 21 vermeld, 35 in
        # gebruik). Faalt dit, dan blijft de bron gewoon staan — alleen de pagina
        # is dan even oud, en `bronnen_page.py --publish` haalt dat handmatig in.
        try:
            from bronnen_page import regenerate  # noqa: PLC0415
            regenerate(publish=True, dry_run=args.dry_run)
        except Exception as exc:
            logger.error("Bronnenpagina bijwerken mislukt: %s", exc)


if __name__ == "__main__":
    main()
