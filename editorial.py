#!/usr/bin/env python3
"""
Editorial: opiniërend redactiestuk over een actueel tech-onderwerp.

Kiest zelf een onderwerp uit de artikelen die de afgelopen dagen op de site
verschenen, schrijft er een editorial over en zet die als DRAFT in WordPress.
Publiceren gebeurt pas na een klik op Publiceer in de mail — een stuk met een
expliciet standpunt hoort niet ongelezen live te gaan.

Gebruik:
  venv/bin/python3 editorial.py            # echte run (draft + mail)
  venv/bin/python3 editorial.py --dry-run   # alleen tonen, niets aanmaken
"""
import argparse
import json
import logging
import sys

from ai_processor import _call_claude, _check_claude_cli, _extract_json
from approval_store import create_editorial_tokens
from config import (
    BASE_DIR,
    EDITORIAL_CANDIDATES,
    EDITORIAL_CATEGORY,
    EDITORIAL_TOKEN_TTL_HOURS,
    ENABLE_EDITORIAL,
)
from mailer import send_editorial_email
from wordpress_client import (
    create_editorial_draft,
    fetch_recent_published,
    update_featured_image,
)

_TMP_DIR = BASE_DIR / "tmp"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)-8s] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger(__name__)

# Claude wordt hier via de CLI aangeroepen (net als in ai_processor en
# source_discovery), niet via de API — vandaar systeem- en gebruikersdeel in
# één prompt in plaats van een aparte system-parameter.
_SYSTEM_PROMPT = """\
Je bent de redactie van TechNieuwsVandaag, een Nederlandse tech-nieuwssite.
Je schrijft een scherpe, opiniërende editorial over een actueel tech-onderwerp.

STEM EN TOON:
- Je schrijft als "wij" (de redactie), nooit als individuele auteur met naam
- Scherp en opiniërend: durf een standpunt in te nemen, wees niet voorzichtig of vaag
- Kritisch waar nodig, maar onderbouwd — geen ongefundeerde uitspraken
- Nederlands, toegankelijk maar met autoriteit

STRUCTUUR:
- Lengte: 150-250 woorden, geen woord te veel
- Opening: geen samenvatting van het nieuws, maar direct de scherpe invalshoek of stelling
- Midden: 1-2 argumenten die het standpunt onderbouwen, gebaseerd op de aangeleverde context
- Slot: EXPLICIET standpunt of conclusie. Nooit eindigen met "de toekomst zal het uitwijzen"
  of "het is aan de lezer" — altijd een duidelijke uitspraak waar de redactie voor staat

ONDERWERPKEUZE (als er meerdere opties zijn):
- Kies het onderwerp met de meeste discussie- of duidingswaarde, niet per se het grootste nieuws
- Vermijd onderwerpen die al uitgebreid als nieuwsartikel op de site staan zonder nieuwe invalshoek

NUANCE:
- Ligt het onderwerp politiek of maatschappelijk gevoelig (privacywetgeving,
  AI-regelgeving, surveillance, arbeidsmarkt), verwerk dan één serieus
  tegenargument voor je je conclusie trekt. Het standpunt blijft expliciet —
  het stuk mag scherp zijn, maar niet eenzijdig.

OUTPUT FORMAAT:
Antwoord ALLEEN met valide JSON, geen markdown-fences, geen preambule:
{
  "titel": "Pakkende titel, geen clickbait maar wel scherp",
  "inhoud": "De volledige editorial tekst, in Nederlands, met '\\n\\n' tussen alinea's",
  "standpunt_samenvatting": "Één zin: wat is de kernconclusie van deze editorial",
  "onderwerp_tags": ["tag1", "tag2"]
}"""


_REVISIE_PROMPT = """\
Hieronder staat een eerder door jou geschreven editorial, plus commentaar van de
hoofdredacteur. Herschrijf de editorial en verwerk dat commentaar.

BELANGRIJK:
- Het commentaar is leidend. Volg het op, ook als je het eerdere stuk beter vond.
- Behoud stem, toon, structuur en lengte-eis (150-250 woorden) uit je instructie.
- Het slot blijft een expliciet standpunt — nooit afzwakken tot "de tijd zal het leren".
- Herschrijf het hele stuk; lever geen dagboek van wijzigingen.

HUIDIGE EDITORIAL
Titel: {titel}

{inhoud}

COMMENTAAR VAN DE HOOFDREDACTEUR
{commentaar}"""


def revise_editorial(titel: str, inhoud: str, commentaar: str) -> dict | None:
    """Herschrijft een bestaande editorial op basis van redactiecommentaar.

    Pre:  titel/inhoud zijn de huidige versie, commentaar is niet-leeg
    Post: zelfde dict-vorm als _generate() (titel/inhoud/standpunt_samenvatting/
          onderwerp_tags), of None bij een fout — nooit raisen
    """
    prompt = (
        f"{_SYSTEM_PROMPT}\n\n"
        + _REVISIE_PROMPT.format(titel=titel, inhoud=inhoud, commentaar=commentaar.strip())
    )
    return _call_and_parse(prompt)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--dry-run", action="store_true", help="Toon de editorial, maak niets aan")
    return parser.parse_args()


def _build_prompt(kandidaten: list[dict]) -> str:
    """Zet de kandidatenlijst om in de volledige prompt.

    Pre:  kandidaten is niet-leeg, elk dict heeft 'title' en 'excerpt'
    Post: prompt met systeeminstructie + genummerde kandidatenlijst
    """
    regels = []
    for i, kandidaat in enumerate(kandidaten, 1):
        regels.append(
            f"{i}. {kandidaat['title']}\n"
            f"   {kandidaat.get('excerpt', '').strip()}\n"
            f"   Bron: {kandidaat.get('link', '—')}"
        )
    return (
        f"{_SYSTEM_PROMPT}\n\n"
        "Hier zijn de kandidaat-onderwerpen van de afgelopen dagen "
        "(titel + korte samenvatting + bron):\n\n"
        + "\n\n".join(regels)
        + "\n\nSchrijf een editorial over het meest geschikte onderwerp uit deze lijst."
    )


def _call_and_parse(prompt: str) -> dict | None:
    """Roept Claude aan en valideert het JSON-antwoord.

    Gedeeld door de eerste generatie en de revisieronde, zodat beide dezelfde
    velden afdwingen.

    Post: None als de CLI faalt of onbruikbare JSON teruggeeft — nooit raisen,
          zodat een mislukte run de cron niet met een traceback vult.
    """
    try:
        antwoord = _call_claude(prompt, timeout=180)
    except Exception as exc:
        logger.error("Claude-aanroep mislukt: %s", exc)
        return None

    try:
        data = _extract_json(antwoord)
    except json.JSONDecodeError as exc:
        logger.error("Claude gaf geen bruikbare JSON: %s", exc)
        logger.debug("Ruwe output: %s", antwoord[:500])
        return None

    if not isinstance(data, dict):
        logger.error("Claude gaf %s in plaats van een JSON-object", type(data).__name__)
        return None

    ontbreekt = [k for k in ("titel", "inhoud") if not str(data.get(k, "")).strip()]
    if ontbreekt:
        logger.error("Editorial mist verplichte velden: %s", ", ".join(ontbreekt))
        return None

    return data


def _generate(kandidaten: list[dict]) -> dict | None:
    """Schrijft een nieuwe editorial op basis van de kandidatenlijst."""
    return _call_and_parse(_build_prompt(kandidaten))


def main() -> None:
    args = _parse_args()

    if not ENABLE_EDITORIAL and not args.dry_run:
        logger.info("ENABLE_EDITORIAL staat uit — editorial overgeslagen")
        return

    _check_claude_cli()

    kandidaten = fetch_recent_published(limit=EDITORIAL_CANDIDATES)
    if not kandidaten:
        logger.error("Geen recente artikelen opgehaald — geen kandidaten, editorial overgeslagen")
        return

    logger.info("Kandidaten (%d): %s", len(kandidaten), " | ".join(k["title"] for k in kandidaten))

    data = _generate(kandidaten)
    if not data:
        logger.error("Editorial genereren mislukt — niets aangemaakt")
        return

    titel      = data["titel"].strip()
    inhoud     = data["inhoud"].strip()
    standpunt  = str(data.get("standpunt_samenvatting", "")).strip()
    tags       = ", ".join(data.get("onderwerp_tags", []) or [])
    woorden    = len(inhoud.split())

    logger.info("Editorial: '%s' (%d woorden)", titel, woorden)
    if standpunt:
        logger.info("Standpunt: %s", standpunt)

    if args.dry_run:
        print(f"\n{'=' * 70}\n{titel}\n{'=' * 70}\n\n{inhoud}\n")
        print(f"Standpunt : {standpunt}")
        print(f"Tags      : {tags}")
        print(f"Woorden   : {woorden}\n")
        return

    post = create_editorial_draft(
        titel=titel, inhoud=inhoud, trefwoorden=tags, categorie=EDITORIAL_CATEGORY,
    )
    if not post:
        logger.error("Draft aanmaken mislukt — geen mail verstuurd")
        return

    # Beeld: zonder featured image toont het thema een grijs vak met "Geen
    # afbeelding" — op de homepage zelfs 420px hoog, want de nieuwste post krijgt
    # daar de hero-positie zonder categoriefilter. Eigen promptvariant: de
    # nieuwsprompt dwingt een optimistische sfeer af die een kritisch stuk
    # ondermijnt.
    beeld_ontbreekt = True
    try:
        from image_generator import generate_image_for_editorial  # noqa: PLC0415

        _TMP_DIR.mkdir(exist_ok=True)
        dest = str(_TMP_DIR / f"tnv_editorial_{post['id']}.jpg")
        image_path = generate_image_for_editorial(
            title=titel, editorial_text=inhoud, dest_path=dest,
        )
        if image_path:
            image_url = update_featured_image(post["id"], image_path, alt_text=titel)
            beeld_ontbreekt = not bool(image_url)
            logger.info("Editorial-beeld gezet: %s", image_url or "MISLUKT")
        else:
            logger.warning("Editorial-beeld genereren mislukt — draft blijft zonder beeld")
    except Exception as exc:
        logger.error("Editorial-beeld mislukt: %s", exc)

    publish_token, decline_token, revise_token = create_editorial_tokens(
        post["id"], titel, post["preview_url"],
        ttl_hours=EDITORIAL_TOKEN_TTL_HOURS,
        # Tekst mee in de token-meta zodat /revise weet wat er herschreven moet
        # worden zonder het uit WordPress terug te hoeven halen.
        meta={"titel": titel, "inhoud": inhoud, "revisie_ronde": 0},
    )
    send_editorial_email(
        titel=titel,
        inhoud=inhoud,
        standpunt=standpunt,
        preview_url=post["preview_url"],
        publish_token=publish_token,
        decline_token=decline_token,
        revise_token=revise_token,
        beeld_ontbreekt=beeld_ontbreekt,
    )
    logger.info(
        "Editorial staat als concept in WordPress (ID %d) — mail verstuurd, "
        "geldig %d uur", post["id"], EDITORIAL_TOKEN_TTL_HOURS,
    )


if __name__ == "__main__":
    main()
