"""
AI-verwerking via de Claude Code CLI:
- Selecteer het 1 meest relevante artikel
- Genereer Nederlandse samenvattingen, titels, trefwoorden en categorie
"""
import json
import logging
import re
import shutil
import subprocess
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Optional

from scraper import Article, fetch_article_text

POSTED_TITLES_FILE = Path(__file__).parent / "posted_titles.txt"

logger = logging.getLogger(__name__)


class InsufficientCreditsError(Exception):
    """Opgegooid wanneer het Claude-tegoed te laag is om een verzoek uit te voeren."""

# Beschikbare WordPress-categorieën
CATEGORIES: list[str] = [
    "Aankondiging", "AI", "AI & Innovatie", "Amazon", "API", "Apple", "ASML",
    "Bluesky", "ChatGPT", "Cloud", "Cybersecurity", "Deepseek", "DOGE", "Economie",
    "Gadgets", "Google", "Governance", "Hardware", "Hugging Face", "Innovatie",
    "Intel", "IoT", "Meta", "Microsoft", "Mistral", "Mobiele Technologie",
    "Onderzoek", "OpenAI", "Politiek", "Privacy", "Quantumcomputers", "Rechtszaak",
    "Robotica", "Smartwatch", "Social media", "Software", "Technologie", "Telecom", "Tesla",
]

@dataclass
class ProcessedArticle:
    original: Article
    titel: str
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
# Claude CLI helpers
# ---------------------------------------------------------------------------

_CLAUDE_KNOWN_PATHS = [
    Path("/home/dgebbink/.local/bin/claude"),
    Path("/usr/local/bin/claude"),
]

_CLAUDE_FALLBACK_DIRS = [
    Path("/home/dgebbink/.cursor-server"),
    Path("/opt"),
]


def _find_claude() -> Optional[str]:
    if path := shutil.which("claude"):
        return path
    # Exact paths first — avoids picking up internal extension binaries
    for p in _CLAUDE_KNOWN_PATHS:
        if p.is_file() and p.stat().st_mode & 0o111:
            return str(p)
    # Directory scan as last resort
    for base in _CLAUDE_FALLBACK_DIRS:
        try:
            for p in sorted(base.rglob("claude"), reverse=True):
                if p.is_file() and p.stat().st_mode & 0o111:
                    return str(p)
        except PermissionError:
            continue
    return None


def _check_claude_cli() -> None:
    if _find_claude() is None:
        raise RuntimeError(
            "De 'claude' CLI is niet beschikbaar in PATH. "
            "Installeer Claude Code via: npm install -g @anthropic-ai/claude-code"
        )


def _call_claude(prompt: str, timeout: int = 90) -> str:
    """Invoke the claude CLI with the given prompt; return stdout."""
    import os
    claude = _find_claude() or "claude"
    if os.geteuid() == 0:
        # --dangerously-skip-permissions is blocked for root; run as dgebbink
        cmd = ["su", "-s", "/bin/sh", "dgebbink", "-c",
               f"{claude} -p {subprocess.list2cmdline([prompt])} --dangerously-skip-permissions"]
    else:
        cmd = [claude, "-p", prompt, "--dangerously-skip-permissions"]
    result = subprocess.run(
        cmd, capture_output=True, text=True, timeout=timeout,
    )
    if result.returncode != 0:
        err = result.stderr.strip()
        if "credit" in err.lower():
            raise InsufficientCreditsError(err)
        raise RuntimeError(f"claude CLI mislukt (exit {result.returncode}): {err}")
    return result.stdout.strip()


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
# Deduplicatie helpers
# ---------------------------------------------------------------------------

def deduplicate_articles(
    articles: list[Article],
) -> list[Article]:
    """Removes near-duplicate articles covering the same topic.
    Pre:  articles is a list of Article objects
    Post: returns filtered list — when duplicates found,
          keeps the single highest-quality article per topic
          (prefers NL source, then most detailed excerpt)
    """
    if len(articles) <= 1:
        return articles

    numbered = "\n".join([
        f"{i+1}. [{a.source}] {a.title} — {a.excerpt[:100]}"
        for i, a in enumerate(articles)
    ])

    prompt = (
        "You are a news editor reviewing today's article candidates from multiple sources.\n\n"
        f"{numbered}\n\n"
        "Identify groups of articles that cover the SAME news event or announcement "
        "(even if worded differently or from different sources). "
        "For each duplicate group keep only the single best article — prefer: "
        "NL source over EN, most detailed excerpt, most authoritative source.\n\n"
        "Return ONLY a JSON array of article numbers to KEEP.\n"
        "Example: [1, 3, 5, 7, 9, 11]\n"
        "No explanation. Only the JSON array."
    )

    try:
        response = _call_claude(prompt, timeout=60)
        match = re.search(r'\[[\d,\s]+\]', response)
        if match:
            keep_indices = [
                i for i in json.loads(match.group())
                if 0 < i <= len(articles)
            ]
            kept = [articles[i - 1] for i in keep_indices]
            removed = len(articles) - len(kept)
            if removed > 0:
                logger.info("Deduplicatie: %d duplicaat/duplicaten verwijderd", removed)
            return kept if kept else articles
    except Exception as e:
        logger.warning("Dedup parsing mislukt: %s — originele lijst gebruikt", e)

    return articles


def is_similar_to_posted_today(
    title: str,
    threshold: float = 0.6,
) -> bool:
    """Checks if a similar article was already posted today.
    Pre:  title is a non-empty string; threshold in (0, 1)
    Post: True if a title with >threshold word overlap
          was posted today, False otherwise
    """
    if not POSTED_TITLES_FILE.exists():
        return False

    today = date.today().isoformat()
    title_words = set(title.lower().split())

    with open(POSTED_TITLES_FILE, "r", encoding="utf-8") as f:
        for line in f:
            if not line.startswith(today):
                continue
            posted_title = line.split("|", 2)[-1].strip().lower()
            posted_words = set(posted_title.split())
            combined = title_words | posted_words
            if not combined:
                continue
            ratio = len(title_words & posted_words) / len(combined)
            if ratio > threshold:
                logger.info(
                    "Vergelijkbaar artikel vandaag al gepost (overlap %.0f%%): %s",
                    ratio * 100,
                    posted_title,
                )
                return True
    return False


def save_posted_title(title: str, url: str) -> None:
    """Saves a posted article title for future dedup checks.
    Pre:  title and url are non-empty strings
    Post: line appended to posted_titles.txt: YYYY-MM-DD|url|title
    """
    with open(POSTED_TITLES_FILE, "a", encoding="utf-8") as f:
        f.write(f"{date.today().isoformat()}|{url}|{title}\n")


# ---------------------------------------------------------------------------
# Stap 2a: Artikel-selectie
# ---------------------------------------------------------------------------

def select_articles(articles: list[Article]) -> list[int]:
    """Ask Claude to pick the 1 most newsworthy article; return 0-based indices."""
    # pre: len(articles) >= 1
    # post: returns 1 valid index; raises InsufficientCreditsError on low balance
    article_list = "\n".join(
        f"{i + 1}. [{_domain(a.source)}] [{getattr(a, 'source_lang', 'EN')}] {a.title}\n   {a.excerpt[:250]}"
        for i, a in enumerate(articles)
    )

    prompt = (
        "Je bent redacteur van een Nederlandse tech-nieuwswebsite. "
        "Hieronder een lijst met vandaag gepubliceerde tech-artikelen. "
        "Selecteer het 1 meest relevante en impactvolle artikel voor een Nederlands publiek. "
        "Overweeg: breedte van impact, innovatie, relevantie voor consument én professional, "
        "en nieuwswaarde. "
        "Geef een lichte voorkeur aan artikelen van Nederlandse bronnen "
        "(gemarkeerd als [NL]) boven Engelstalige bronnen, mits de "
        "nieuwswaarde vergelijkbaar is. Geef elke NL-bron een gewicht "
        "van 1.3x ten opzichte van EN-bronnen bij gelijke relevantie. "
        "Geef als output ALLEEN het nummer van het gekozen artikel als JSON array, "
        "bijv: [3]\n\n"
        f"Artikelen:\n{article_list}"
    )

    response_text = _call_claude(prompt, timeout=30)
    result = _extract_json(response_text)

    if not isinstance(result, list) or len(result) < 1:
        raise ValueError(f"Onverwacht antwoord van Claude bij selectie: {response_text}")

    # Converteer van 1-gebaseerd naar 0-gebaseerd en valideer
    indices = []
    for i in result[:1]:
        idx = int(i) - 1
        if 0 <= idx < len(articles):
            indices.append(idx)
        else:
            logger.warning("Claude gaf ongeldige index %d (max %d)", int(i), len(articles))

    return indices


# ---------------------------------------------------------------------------
# Stap 2b + 2c: Samenvatting en categorie genereren
# ---------------------------------------------------------------------------

def process_article(article: Article) -> Optional[ProcessedArticle]:
    """Generate Dutch summary, titles, keywords and categories for one article."""
    # pre: article.url is reachable
    # post: returns None on any failure
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
        "Suggereer daarnaast één aantrekkelijke en relevante titel van maximaal 5 - 8 woorden "
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
        '  "titel": "...",\n'
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
        response_text = _call_claude(prompt, timeout=120)
        data = _extract_json(response_text)

        if not isinstance(data, dict):
            raise ValueError(f"Verwachtte een dict, kreeg: {type(data)}")

        # Valideer verplichte velden
        for field in ("titel", "samenvatting", "trefwoorden", "categorieen"):
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
            titel=str(data["titel"]).strip(),
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
    """Select top 1 article and generate full Dutch processing for it."""
    # pre: claude CLI is available; len(articles) >= 1
    # post: returns at most 1 ProcessedArticle object
    _check_claude_cli()

    if len(articles) == 0:
        logger.warning("Geen artikelen beschikbaar voor verwerking")
        return []

    # Stap A: semantische deduplicatie over alle kandidaten
    logger.info("Deduplicatie: %d kandidaten controleren op duplicaten", len(articles))
    articles = deduplicate_articles(articles)
    logger.info("Na deduplicatie: %d artikel(en) over", len(articles))

    # Stap B: filter artikelen die vandaag al gepost zijn (title overlap)
    before_filter = len(articles)
    articles = [a for a in articles if not is_similar_to_posted_today(a.title)]
    filtered = before_filter - len(articles)
    if filtered:
        logger.info("%d artikel(en) gefilterd wegens overlap met vandaag al geposte titels", filtered)

    if not articles:
        logger.warning("Alle kandidaten gefilterd (duplicaten of al gepost vandaag)")
        return []

    # Stap C: Claude selecteert het beste artikel
    if len(articles) < 2:
        logger.info("Slechts %d artikel(en) beschikbaar, selectie overgeslagen", len(articles))
        selected_indices = [0]
    else:
        logger.info("AI selecteert 1 artikel uit %d kandidaten", len(articles))
        try:
            selected_indices = select_articles(articles)
            logger.info("Claude selecteerde indices: %s", selected_indices)
        except InsufficientCreditsError:
            raise  # doorsturen naar aanroeper voor urgente melding
        except Exception as exc:
            logger.error("Artikel-selectie mislukt, val terug op eerste: %s", exc)
            selected_indices = [0]

    processed: list[ProcessedArticle] = []
    for idx in selected_indices[:1]:
        if 0 <= idx < len(articles):
            article = articles[idx]
            lang = getattr(article, "source_lang", "EN")
            logger.info("Artikel verwerken [%s]: %s", lang, article.title)
            result = process_article(article)
            if result:
                processed.append(result)
        else:
            logger.warning("Index %d buiten bereik (max %d)", idx, len(articles) - 1)

    # Log NL vs EN verdeling van geselecteerde artikelen
    nl_selected = sum(1 for p in processed if getattr(p.original, "source_lang", "EN") == "NL")
    en_selected = len(processed) - nl_selected
    logger.info(
        "Geselecteerd: %d NL-artikel(en), %d EN-artikel(en)",
        nl_selected,
        en_selected,
    )

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
