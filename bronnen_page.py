"""
Genereert de WordPress-pagina /bronnen/ uit sources.txt.

De pagina werd met de hand bijgehouden en liep daardoor achter (augustus 2026:
21 bronnen op de pagina, 35 in sources.txt). Nu is sources.txt de enige bron van
waarheid: per domein staat de omschrijving in assets/bronnen_meta.json, en
ontbrekende domeinen laat dit script eenmalig door Claude beschrijven — daarna
zit het antwoord in de cache en kost een regeneratie geen Claude-call meer.

Gebruik:
    venv/bin/python3 bronnen_page.py            # HTML schrijven
    venv/bin/python3 bronnen_page.py --publish  # HTML schrijven + naar WordPress
    venv/bin/python3 bronnen_page.py --dry-run  # alleen tonen wat er zou wijzigen

source_discovery.py roept dit na een geslaagde toevoeging automatisch aan.
"""
import argparse
import json
import logging
import re
import sys
from datetime import date
from html import escape
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv

load_dotenv()

from ai_processor import _call_claude, _extract_json
from scraper import load_sources

logger = logging.getLogger(__name__)

ASSETS_DIR = Path(__file__).parent / "assets"
META_FILE = ASSETS_DIR / "bronnen_meta.json"
OUTPUT_FILE = ASSETS_DIR / "pagina-veel-gebruikte-bronnen.html"

# Badge-klassen per type. Onbekende types vallen terug op 'nieuwssite', zodat een
# afwijkend Claude-antwoord de opmaak niet breekt.
BADGE_CLASSES = {
    "nieuwssite": "tnv-badge-nieuwssite",
    "blog": "tnv-badge-blog",
    "aggregator": "tnv-badge-aggregator",
    "vakblad": "tnv-badge-vakblad",
}


def _domain_of(website_url: str) -> str:
    """Normaliseer een bron-URL tot een kaal domein (zonder schema/www)."""
    domain = website_url.split("://", 1)[-1].strip("/").lower()
    return domain[4:] if domain.startswith("www.") else domain


def load_meta() -> dict[str, dict]:
    if not META_FILE.exists():
        return {}
    return json.loads(META_FILE.read_text(encoding="utf-8"))


def save_meta(meta: dict[str, dict]) -> None:
    META_FILE.write_text(
        json.dumps(meta, indent=2, ensure_ascii=False, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def describe_sources(domains: list[str]) -> dict[str, dict]:
    """Laat Claude naam, beschrijving en type bepalen voor onbekende domeinen.

    Post: bevat alleen domeinen waarvoor een bruikbaar antwoord kwam; bij een
          mislukte call een lege dict (de pagina slaat die bronnen dan over).
    """
    if not domains:
        return {}

    numbered = "\n".join(f"{i + 1}. {d}" for i, d in enumerate(domains))
    prompt = (
        "Hieronder staan tech-nieuwsbronnen die een Nederlandse nieuwssite scant. "
        "Beschrijf ze voor een publieke bronnenpagina.\n\n"
        f"{numbered}\n\n"
        "Geef per bron:\n"
        '- "naam": de officiële schrijfwijze van het medium (bijv. "The Verge")\n'
        '- "beschrijving": één Nederlandse zin (max 22 woorden) over wat het medium '
        "doet en waar het bekend om staat. Neutraal en feitelijk, geen reclametaal.\n"
        '- "type": exact één van "Nieuwssite", "Blog", "Aggregator", "Vakblad"\n\n'
        "Antwoord ALLEEN met een JSON-array, exact één object per bron, in dezelfde "
        'volgorde: [{"naam": "...", "beschrijving": "...", "type": "..."}]'
    )

    try:
        data = _extract_json(_call_claude(prompt, timeout=120))
    except Exception as exc:
        logger.warning("Bronbeschrijvingen ophalen mislukt: %s", exc)
        return {}

    if not isinstance(data, list) or len(data) != len(domains):
        logger.warning("Onverwacht antwoord bij bronbeschrijvingen — %d bronnen overgeslagen",
                       len(domains))
        return {}

    described: dict[str, dict] = {}
    for domain, entry in zip(domains, data):
        if not isinstance(entry, dict):
            continue
        naam = str(entry.get("naam") or "").strip()
        beschrijving = str(entry.get("beschrijving") or "").strip()
        if not naam or not beschrijving:
            logger.warning("Onvolledige beschrijving voor %s — overgeslagen", domain)
            continue
        described[domain] = {
            "naam": naam,
            "beschrijving": beschrijving,
            "type": str(entry.get("type") or "Nieuwssite").strip().capitalize(),
        }
    return described


def _row(domain: str, info: dict) -> str:
    """Eén tabelrij. De data-label-attributen zijn wat de mobiele kaartweergave toont."""
    badge_class = BADGE_CLASSES.get(info["type"].lower(), "tnv-badge-nieuwssite")
    return (
        "      <tr>\n"
        '        <td data-label="Bron">'
        f'<a href="https://{escape(domain)}" target="_blank" rel="noopener nofollow">'
        f'<strong>{escape(info["naam"])}</strong></a>'
        f'<span class="tnv-domein">{escape(domain)}</span></td>\n'
        f'        <td data-label="Beschrijving">{escape(info["beschrijving"])}</td>\n'
        f'        <td data-label="Type"><span class="tnv-badge {badge_class}">'
        f'{escape(info["type"])}</span></td>\n'
        "      </tr>"
    )


def _table(titel: str, rows: list[str]) -> str:
    if not rows:
        return ""
    return (
        f"  <h2 class=\"tnv-kop\">{escape(titel)}</h2>\n"
        '  <table class="tnv-tabel">\n'
        "    <thead>\n"
        "      <tr><th>Bron</th><th>Beschrijving</th><th>Type</th></tr>\n"
        "    </thead>\n"
        "    <tbody>\n"
        + "\n".join(rows)
        + "\n    </tbody>\n"
        "  </table>\n"
    )


_STYLE = """  <style>
    .tnv-bronnen-pagina { max-width: 820px; }
    .tnv-intro { font-size: 1.05em; line-height: 1.75; margin-bottom: 2em; color: #444; }
    .tnv-kop { font-size: 1.25em; margin: 1.6em 0 0.6em; }
    .tnv-tabel { width: 100%; border-collapse: collapse; font-size: 0.95em; margin-bottom: 2em; }
    .tnv-tabel thead th {
      background: #1a73e8; color: #fff; text-align: left;
      padding: 10px 14px; font-weight: 600;
    }
    .tnv-tabel tbody tr:nth-child(even) { background: #f5f8ff; }
    .tnv-tabel tbody td { padding: 9px 14px; border-bottom: 1px solid #e4e8f0; vertical-align: top; }
    .tnv-tabel tbody td a { color: #1a73e8; text-decoration: none; }
    .tnv-tabel tbody td a:hover { text-decoration: underline; }
    .tnv-domein { display: block; font-size: 0.82em; color: #777; margin-top: 2px; }
    .tnv-badge {
      display: inline-block; font-size: 0.78em; padding: 2px 8px;
      border-radius: 12px; font-weight: 600; white-space: nowrap;
    }
    .tnv-badge-nieuwssite  { background: #e3f2fd; color: #0d47a1; }
    .tnv-badge-blog        { background: #fce4ec; color: #880e4f; }
    .tnv-badge-aggregator  { background: #fff8e1; color: #e65100; }
    .tnv-badge-vakblad     { background: #e8f5e9; color: #1b5e20; }
    .tnv-disclaimer {
      background: #f9f9f9; border-left: 4px solid #1a73e8;
      padding: 14px 18px; font-size: 0.9em; line-height: 1.7;
      color: #555; border-radius: 0 4px 4px 0;
    }
    .tnv-bijgewerkt { font-size: 0.85em; color: #777; margin: 0 0 2em; }

    /* Mobiel: drie kolommen worden op een telefoon onleesbaar smal (de
       beschrijving breekt dan op elk woord), dus elke rij wordt daar een kaart.
       De koprij verdwijnt: naam, beschrijving en badge spreken zonder labels
       voor zich. De data-label-attributen blijven de haakjes voor deze regels. */
    @media (max-width: 640px) {
      /* Het thema zet randen op table en td; in de kaartweergave blijft daar een
         los verticaal streepje van over, dus die worden hier teruggezet. */
      table.tnv-tabel { font-size: 1em; border: 0; }
      .tnv-tabel thead {
        position: absolute; width: 1px; height: 1px;
        overflow: hidden; clip: rect(0 0 0 0); white-space: nowrap;
      }
      .tnv-tabel, .tnv-tabel tbody, .tnv-tabel tr, .tnv-tabel td {
        display: block; width: 100%; box-sizing: border-box;
      }
      .tnv-tabel tbody tr {
        border: 1px solid #e4e8f0; border-radius: 8px;
        padding: 12px 14px; margin-bottom: 12px; background: #fff;
      }
      .tnv-tabel tbody tr:nth-child(even) { background: #f5f8ff; }
      .tnv-tabel tbody td { padding: 0; border: 0; }
      .tnv-tabel tbody td + td { margin-top: 8px; }
      .tnv-tabel tbody td[data-label="Bron"] { font-size: 1.08em; }
      .tnv-tabel tbody td[data-label="Beschrijving"] { line-height: 1.6; color: #444; }
      .tnv-domein { display: inline-block; margin: 0 0 0 6px; }
    }
  </style>
"""


_MAANDEN = ("januari", "februari", "maart", "april", "mei", "juni", "juli",
            "augustus", "september", "oktober", "november", "december")


def _dutch_date() -> str:
    """Datum als '9 augustus 2026' — niet via strftime, dat volgt de systeemlocale."""
    vandaag = date.today()
    return f"{vandaag.day} {_MAANDEN[vandaag.month - 1]} {vandaag.year}"


def build_html(meta: dict[str, dict]) -> str:
    """Render de volledige pagina-inhoud uit sources.txt + metadata."""
    nl_rows, en_rows, missing = [], [], []
    seen: set[str] = set()

    for website, _rss, lang in load_sources():
        domain = _domain_of(website)
        if domain in seen:
            continue
        seen.add(domain)

        info = meta.get(domain)
        if not info:
            missing.append(domain)
            continue

        (nl_rows if lang == "NL" else en_rows).append(_row(domain, info))

    if missing:
        logger.warning("Geen beschrijving voor %d bron(nen), niet op de pagina: %s",
                       len(missing), ", ".join(missing))

    total = len(nl_rows) + len(en_rows)
    parts = [
        "<!--\n"
        "  GEGENEREERD BESTAND — niet met de hand bewerken.\n"
        "  Bron: sources.txt + assets/bronnen_meta.json\n"
        "  Opnieuw genereren: venv/bin/python3 bronnen_page.py --publish\n"
        "-->\n",
        '<div class="tnv-bronnen-pagina">\n',
        '  <p class="tnv-intro">\n'
        "    TechNieuwsVandaag selecteert dagelijks de meest relevante "
        "technologienieuws&shy;berichten\n"
        f"    uit een vaste set van {total} betrouwbare, internationaal erkende bronnen.\n"
        "    Onderstaand overzicht laat zien welke websites worden gescand en "
        "waarom ze zijn gekozen.\n"
        "  </p>\n",
        _STYLE,
        _table("Nederlandse bronnen", nl_rows),
        _table("Internationale bronnen", en_rows),
        f'  <p class="tnv-bijgewerkt">Laatst bijgewerkt: {_dutch_date()}.</p>\n',
        '  <div class="tnv-disclaimer">\n'
        "    <strong>Journalistieke onafhankelijkheid</strong><br>\n"
        "    TechNieuwsVandaag neemt geen content over van bovenstaande bronnen.\n"
        "    Artikelen zijn AI-gegenereerde Nederlandstalige <em>samenvattingen</em> "
        "op basis van\n"
        "    openbaar beschikbare RSS-feeds en webpagina's.\n"
        "    Originele auteurs en uitgevers behouden alle rechten op hun werk.\n"
        "    Elk artikel op TechNieuwsVandaag bevat een directe link naar de "
        "bronpublicatie.\n"
        "  </div>\n",
        "</div>\n",
    ]
    html = "\n".join(p for p in parts if p)

    # Geen lege regels in de uitvoer. WordPress' wpautop maakt van elke lege regel
    # een alineagrens en doet dat óók binnen <style>: er belandde een letterlijke
    # </p><p> midden in de CSS, waarna de browser de rest van het blok oversloeg —
    # inclusief de mobiele media-query, die daardoor live niet werkte terwijl hij
    # in de HTML stond. Zie ook de blokkanteling hieronder in _STYLE.
    return re.sub(r"\n[ \t]*\n+", "\n", html)


def regenerate(publish: bool = False, dry_run: bool = False) -> bool:
    """Werk metadata bij, schrijf de HTML en publiceer optioneel.

    Post: True als de pagina is geschreven (of zou worden geschreven bij dry-run).
    """
    meta = load_meta()
    known = set(meta)
    domains = []
    for website, _rss, _lang in load_sources():
        d = _domain_of(website)
        if d not in known and d not in domains:
            domains.append(d)

    if domains:
        logger.info("%d nieuwe bron(nen) zonder beschrijving: %s",
                    len(domains), ", ".join(domains))
        if dry_run:
            logger.info("[DRY RUN] Zou Claude om beschrijvingen vragen")
        else:
            described = describe_sources(domains)
            if described:
                meta.update(described)
                save_meta(meta)
                logger.info("%d beschrijving(en) toegevoegd aan %s",
                            len(described), META_FILE.name)

    html = build_html(meta)

    if dry_run:
        logger.info("[DRY RUN] Pagina niet geschreven (%d tekens)", len(html))
        return True

    OUTPUT_FILE.write_text(html, encoding="utf-8")
    logger.info("Pagina geschreven: %s", OUTPUT_FILE)

    if publish:
        from publish_pages import PAGES, publish_page  # noqa: PLC0415
        page_def = next(p for p in PAGES if p["slug"] == "bronnen")
        publish_page(page_def)

    return True


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Genereer de bronnenpagina uit sources.txt")
    parser.add_argument("--publish", action="store_true", help="ook naar WordPress publiceren")
    parser.add_argument("--dry-run", action="store_true", help="niets schrijven of publiceren")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)-8s] %(message)s",
        handlers=[logging.StreamHandler(sys.stdout)],
    )

    regenerate(publish=args.publish, dry_run=args.dry_run)
    return 0


if __name__ == "__main__":
    sys.exit(main())
