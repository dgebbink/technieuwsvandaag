"""
AI-verwerking via de Anthropic Claude API:
- Selecteer de 2 meest relevante artikelen
- Genereer Nederlandse samenvattingen, titels, trefwoorden en categorie
"""
import json
import logging
import re
from dataclasses import dataclass
from typing import Optional

import anthropic

from config import ANTHROPIC_API_KEY
from scraper import Article, fetch_article_text

logger = logging.getLogger(__name__)


class InsufficientCreditsError(Exception):
    """Opgegooid wanneer het Anthropic API-tegoed te laag is om een verzoek uit te voeren."""

# Beschikbare WordPress-categorieën
CATEGORIES: list[str] = [
    "Aankondiging", "AI", "AI & Innovatie", "Amazon", "API", "Apple", "ASML",
    "Bluesky", "ChatGPT", "Cloud", "Cybersecurity", "Deepseek", "DOGE", "Economie",
    "Gadgets", "Google", "Governance", "Hardware", "Hugging Face", "Innovatie",
    "Intel", "IoT", "Meta", "Microsoft", "Mistral", "Mobiele Technologie",
    "Onderzoek", "OpenAI", "Politiek", "Privacy", "Quantumcomputers", "Rechtszaak",
    "Robotica", "Smartwatch", "Social media", "Software", "Technologie", "Telecom", "Tesla",
]

MODEL = "claude-sonnet-4-6"


@dataclass
class ProcessedArticle:
    original: Article
    titel1: str
    titel2: str
    samenvatting: str
    trefwoorden: str
    categorieen: list[str]
    image_path: Optional[str] = None
    # SEO-velden
    meta_description: str = ""
    slug: str = ""
    focus_keyword: str = ""
    # Afbeelding-metadata (scrape-modus)
    image_caption: str = ""
    bron_image_url: str = ""


# ---------------------------------------------------------------------------
# JSON-extractie helper
# ---------------------------------------------------------------------------

def _extract_json(text: str) -> object:
    """Parse JSON from Claude output, stripping markdown code fences if present."""
    # pre: text contains a JSON object or array, possibly fenced
    # post: raises json.JSONDecodeError if no valid JSON found
    # Probeer JSON uit een ```json ... ``` blok te halen
    match = re.search(r"```(?:json)?\s*([\s\S]*?)\s*```", text)
    if match:
        return json.loads(match.group(1))

    # Probeer het eerste { ... } of [ ... ] blok
    match = re.search(r"(\{[\s\S]*\}|\[[\s\S]*\])", text)
    if match:
        return json.loads(match.group(1))

    # Direct parsen als laatste poging
    return json.loads(text.strip())


# ---------------------------------------------------------------------------
# Stap 2a: Artikel-selectie
# ---------------------------------------------------------------------------

def select_articles(articles: list[Article], client: anthropic.Anthropic) -> list[int]:
    """Ask Claude to pick the 2 most newsworthy articles; return 0-based indices."""
    # pre: len(articles) >= 2; client is authenticated
    # post: returns 1–2 valid indices; raises InsufficientCreditsError on low balance
    article_list = "\n".join(
        f"{i + 1}. [{_domain(a.source)}] {a.title}\n   {a.excerpt[:250]}"
        for i, a in enumerate(articles)
    )

    prompt = (
        "Je bent redacteur van een Nederlandse tech-nieuwswebsite. "
        "Hieronder een lijst met vandaag gepubliceerde tech-artikelen. "
        "Selecteer de 2 meest relevante en impactvolle artikelen voor een Nederlands publiek. "
        "Overweeg: breedte van impact, innovatie, relevantie voor consument én professional, "
        "en nieuwswaarde. "
        "Geef als output ALLEEN de nummers van de 2 gekozen artikelen als JSON array, "
        "bijv: [3, 7]\n\n"
        f"Artikelen:\n{article_list}"
    )

    try:
        message = client.messages.create(
            model=MODEL,
            max_tokens=50,
            messages=[{"role": "user", "content": prompt}],
        )
    except anthropic.APIError as exc:
        if "credit" in str(exc).lower():
            raise InsufficientCreditsError(str(exc)) from exc
        raise

    response_text = message.content[0].text  # type: ignore[union-attr]
    result = _extract_json(response_text)

    if not isinstance(result, list) or len(result) < 1:
        raise ValueError(f"Onverwacht antwoord van Claude bij selectie: {response_text}")

    # Converteer van 1-gebaseerd naar 0-gebaseerd en valideer
    indices = []
    for i in result[:2]:
        idx = int(i) - 1
        if 0 <= idx < len(articles):
            indices.append(idx)
        else:
            logger.warning("Claude gaf ongeldige index %d (max %d)", int(i), len(articles))

    return indices


# ---------------------------------------------------------------------------
# Stap 2b + 2c: Samenvatting en categorie genereren
# ---------------------------------------------------------------------------

def process_article(article: Article, client: anthropic.Anthropic) -> Optional[ProcessedArticle]:
    """Generate Dutch summary, titles, keywords and categories for one article."""
    # pre: article.url is reachable; client is authenticated
    # post: returns None on any API or parse failure
    categories_str = ", ".join(CATEGORIES)

    # Haal artikeltekst op als excerpt te kort is
    artikel_tekst = article.excerpt
    if len(artikel_tekst) < 300:
        logger.info("Excerpt te kort, volledige tekst ophalen voor: %s", article.url)
        artikel_tekst = fetch_article_text(article.url) or artikel_tekst

    prompt = (
        "Je bent een ervaren auteur en je schrijft samenvattingen van artikelen voor een website "
        "die dagelijks korte berichten plaatst. "
        "Lees het artikel via de meegeleverde link aandachtig door. "
        "Vat de inhoud samen in ongeveer 300 woorden, 2 - 4 paragrafen en in foutloos Nederlands "
        "op taalniveau 2F. "
        "Zorg dat de samenvatting de kernboodschap van het artikel duidelijk overbrengt. "
        "Suggereer daarnaast twee aantrekkelijke en relevante titels van maximaal 5 - 8 woorden "
        "die nieuwsgierigheid opwekt en het artikel uitnodigend maakt voor lezers. "
        "Houd rekening met SEO "
        "- Probeer de passieve stemscore te verbeteren zodat de tekst leesbaarder wordt. "
        "- Limiteer zinnen tot maximaal 20 woorden. "
        "- Zorg dat woorden uit de titels voorkomen in de eerste paragraaf "
        "- Extraheer vervolgens de 5 belangrijke trefwoorden uit de samenvatting en geef deze "
        "weer in het volgende formaat: woord1, woord2, woord3, woord4, woord5\n\n"
        f"Artikel URL: {article.url}\n\n"
        f"Artikel tekst:\n{artikel_tekst[:3500]}\n\n"
        f"Kies maximaal 3 meest passende categorieën uit deze lijst: {categories_str}\n\n"
        "Geef je antwoord als JSON in dit exacte formaat:\n"
        "{\n"
        '  "titel1": "...",\n'
        '  "titel2": "...",\n'
        '  "samenvatting": "...",\n'
        '  "trefwoorden": "woord1, woord2, woord3, woord4, woord5",\n'
        '  "categorieen": ["cat1", "cat2"],\n'
        '  "meta_description": "...",\n'
        '  "slug": "...",\n'
        '  "focus_keyword": "..."\n'
        "}\n\n"
        "Regels voor de extra SEO-velden:\n"
        "- meta_description: max 155 tekens, bevat het focus-trefwoord, prikkelend voor de lezer\n"
        "- slug: URL-vriendelijke slug van max 5 woorden, geen lidwoorden of stopwoorden, "
        "alleen kleine letters en koppeltekens (bijv. 'openai-lanceert-nieuw-model')\n"
        "- focus_keyword: het primaire SEO-trefwoord, 1-3 woorden"
    )

    try:
        try:
            message = client.messages.create(
                model=MODEL,
                max_tokens=1500,
                messages=[{"role": "user", "content": prompt}],
            )
        except anthropic.APIError as exc:
            if "credit" in str(exc).lower():
                raise InsufficientCreditsError(str(exc)) from exc
            raise

        response_text = message.content[0].text  # type: ignore[union-attr]
        data = _extract_json(response_text)

        if not isinstance(data, dict):
            raise ValueError(f"Verwachtte een dict, kreeg: {type(data)}")

        # Valideer verplichte velden
        for field in ("titel1", "titel2", "samenvatting", "trefwoorden", "categorieen"):
            if field not in data:
                raise ValueError(f"Veld '{field}' ontbreekt in Claude-antwoord")

        # Normaliseer categorieen: accepteer zowel lijst als enkele string
        raw_cats = data["categorieen"]
        if isinstance(raw_cats, str):
            raw_cats = [raw_cats]
        categorieen = [str(c).strip() for c in raw_cats[:3] if str(c).strip()]
        if not categorieen:
            categorieen = ["Technologie"]

        # SEO-velden (optioneel — geen fout als ze ontbreken)
        meta_description = str(data.get("meta_description", "")).strip()[:155]
        slug = str(data.get("slug", "")).strip().lower()
        focus_keyword = str(data.get("focus_keyword", "")).strip()

        return ProcessedArticle(
            original=article,
            titel1=str(data["titel1"]).strip(),
            titel2=str(data["titel2"]).strip(),
            samenvatting=str(data["samenvatting"]).strip(),
            trefwoorden=str(data["trefwoorden"]).strip(),
            categorieen=categorieen,
            meta_description=meta_description,
            slug=slug,
            focus_keyword=focus_keyword,
        )

    except Exception as exc:
        logger.error("Artikel-verwerking mislukt voor '%s': %s", article.title, exc)
        return None


# ---------------------------------------------------------------------------
# Hoofd-verwerkingsfunctie
# ---------------------------------------------------------------------------

def process_articles(articles: list[Article]) -> list[ProcessedArticle]:
    """Select top 2 articles and generate full Dutch processing for each."""
    # pre: ANTHROPIC_API_KEY is set; len(articles) >= 1
    # post: returns at most 2 ProcessedArticle objects
    if not ANTHROPIC_API_KEY:
        raise ValueError("ANTHROPIC_API_KEY is niet ingesteld in .env")

    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

    if len(articles) == 0:
        logger.warning("Geen artikelen beschikbaar voor verwerking")
        return []

    if len(articles) < 2:
        logger.warning("Slechts %d artikel(en) beschikbaar, selectie overgeslagen", len(articles))
        selected_indices = list(range(len(articles)))
    else:
        logger.info("AI selecteert 2 artikelen uit %d kandidaten", len(articles))
        try:
            selected_indices = select_articles(articles, client)
            logger.info("Claude selecteerde indices: %s", selected_indices)
        except InsufficientCreditsError:
            raise  # doorsturen naar aanroeper voor urgente melding
        except Exception as exc:
            logger.error("Artikel-selectie mislukt, val terug op eerste twee: %s", exc)
            selected_indices = [0, 1]

    processed: list[ProcessedArticle] = []
    for idx in selected_indices[:2]:
        if 0 <= idx < len(articles):
            article = articles[idx]
            logger.info("Artikel verwerken: %s", article.title)
            result = process_article(article, client)
            if result:
                processed.append(result)
        else:
            logger.warning("Index %d buiten bereik (max %d)", idx, len(articles) - 1)

    return processed


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------

def _domain(url: str) -> str:
    """Return the netloc (domain) from a URL string."""
    # post: returns url unchanged on parse failure
    try:
        from urllib.parse import urlparse
        return urlparse(url).netloc or url
    except Exception:
        return url
