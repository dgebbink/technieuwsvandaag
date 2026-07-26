"""
AI-verwerking via de Claude Code CLI:
- Selecteer het 1 meest relevante artikel
- Genereer Nederlandse samenvattingen, titels, trefwoorden en categorie
"""
import json
import logging
import random
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
    # Gekozen persoonsvariant voor de gegenereerde afbeelding (generate-modus)
    image_variant: Optional[dict] = None
    # Prompt waarmee de FAL.ai-afbeelding gegenereerd is (generate-modus)
    image_prompt: str = ""
    # Gerandomiseerd woordaantal waarmee de samenvatting is opgevraagd (300-1000)
    target_words: int = 0
    # Instagram-velden: korte kop voor op het beeld, de losse hook-tekst (voor
    # hergebruik in de dagdigest-caption) en de volledig geassembleerde caption
    ig_kop: str = ""
    ig_tekst: str = ""
    ig_caption: str = ""


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
    # Ensure HOME points to dgebbink's home so claude finds its auth config,
    # even when the process was started with HOME=/root (e.g. via supervisord).
    env = os.environ.copy()
    env["HOME"] = "/home/dgebbink"
    if os.geteuid() == 0:
        # --dangerously-skip-permissions is blocked for root; run as dgebbink
        cmd = ["su", "-s", "/bin/sh", "dgebbink", "-c",
               f"{claude} -p {subprocess.list2cmdline([prompt])} --dangerously-skip-permissions"]
    else:
        cmd = [claude, "-p", prompt, "--dangerously-skip-permissions"]
    result = subprocess.run(
        cmd, capture_output=True, text=True, timeout=timeout, env=env,
    )
    if result.returncode != 0:
        err = result.stderr.strip()
        if "credit" in err.lower():
            raise InsufficientCreditsError(err)
        raise RuntimeError(f"claude CLI mislukt (exit {result.returncode}): {err}")
    output = result.stdout.strip()
    if not output:
        raise RuntimeError("claude CLI gaf lege response (exit 0 maar geen stdout)")
    return output


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
        response = _call_claude(prompt, timeout=120)
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


def is_similar_to_recently_posted(
    title: str,
    threshold: float = 0.6,
    days_back: int = 7,
) -> bool:
    """Checks if a similar article was already posted in the last `days_back` days.
    Pre:  title is a non-empty string; threshold in (0, 1); days_back >= 1
    Post: True if a title with >threshold word overlap was posted recently
    """
    if not POSTED_TITLES_FILE.exists():
        return False

    from datetime import timedelta
    cutoff = date.today() - timedelta(days=days_back - 1)
    title_words = set(title.lower().split())

    with open(POSTED_TITLES_FILE, "r", encoding="utf-8") as f:
        for line in f:
            parts = line.split("|", 2)
            if len(parts) < 3:
                continue
            try:
                post_date = date.fromisoformat(parts[0])
            except ValueError:
                continue
            if post_date < cutoff:
                continue
            posted_title = parts[2].strip().lower()
            posted_words = set(posted_title.split())
            combined = title_words | posted_words
            if not combined:
                continue
            ratio = len(title_words & posted_words) / len(combined)
            if ratio > threshold:
                logger.info(
                    "Vergelijkbaar artikel recent gepost (%s, overlap %.0f%%): %s",
                    parts[0],
                    ratio * 100,
                    posted_title,
                )
                return True
    return False


def _title_overlap_ratio(a: str, b: str) -> float:
    """Jaccard word-overlap of two titles (case-insensitive). 0.0 when either empty."""
    wa, wb = set(a.lower().split()), set(b.lower().split())
    combined = wa | wb
    return len(wa & wb) / len(combined) if combined else 0.0


def is_similar_to_recent(
    candidate: "ProcessedArticle",
    recent_articles: list[dict],
    title_threshold: float = 0.6,
) -> tuple[bool, str]:
    """Check whether a candidate duplicates the topic of a recently published article.

    Two-stage, cost-aware:
      1. Cheap: word-overlap of the Dutch title against each recent title.
      2. Inconclusive → one short, cheap Claude call comparing the candidate
         title + first sentences against the recent titles + excerpts
         (no full body needed).

    Pre:  candidate has .titel and .samenvatting; recent_articles is a list of
          dicts with 'title' and 'excerpt' keys (newest first)
    Post: returns (is_similar, reason); reason names the matched recent title.
    """
    if not recent_articles:
        return False, ""

    # Stap 1 — goedkope titel-overlap (zelfde bedrijf + product + type nieuws
    # geeft doorgaans hoge woordoverlap in de NL-titel)
    for art in recent_articles:
        ratio = _title_overlap_ratio(candidate.titel, art.get("title", ""))
        if ratio > title_threshold:
            return True, f"titel-overlap {ratio * 100:.0f}% met recent '{art['title']}'"

    # Stap 2 — kort Claude-oordeel; titels + excerpt, dus nog steeds goedkoop
    # (geen volledige artikeltekst nodig)
    first_sentences = " ".join(candidate.samenvatting.split(". ")[:2]).strip()
    recent_titles = "\n".join(
        f"{i + 1}. {a.get('title', '')} — {a.get('excerpt', '')[:200]}"
        for i, a in enumerate(recent_articles)
    )
    prompt = (
        "Je bent eindredacteur. Bepaal of het KANDIDAAT-artikel hetzelfde "
        "onderwerp of nieuwsfeit behandelt als een van de RECENT gepubliceerde "
        "artikelen. Zelfde bedrijf + product + type nieuws telt als duplicaat; "
        "ook wanneer het KANDIDAAT en een recent artikel over dezelfde hoofdpersoon "
        "of organisatie gaan telt dat als duplicaat, tenzij het nieuwsfeit "
        "duidelijk en wezenlijk anders is. Een écht ander aspect van een breed "
        "thema (bv. ander product, andere onderneming, andere invalshoek) "
        "telt NIET als duplicaat.\n\n"
        f"KANDIDAAT:\nTitel: {candidate.titel}\n{first_sentences}\n\n"
        f"RECENT GEPUBLICEERD:\n{recent_titles}\n\n"
        'Antwoord ALLEEN met JSON: {"is_duplicate_topic": true/false, "reason": "..."}'
    )
    try:
        data = _extract_json(_call_claude(prompt, timeout=30))
        if isinstance(data, dict) and data.get("is_duplicate_topic") is True:
            return True, str(data.get("reason", "Claude markeerde als duplicaat-onderwerp"))
    except Exception as exc:
        logger.warning(
            "Duplicate-topic check via Claude mislukt: %s — niet als duplicaat behandeld",
            exc,
        )

    return False, ""


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
        "Weeg [NL]- versus [EN]-bronnen op basis van het ONDERWERP van het artikel, niet op "
        "basis van de bron zelf:\n"
        "- Betreft het nieuws specifiek Nederland (bijv. een Nederlandse uitvinding, een "
        "Nederlands bedrijf, impact op de Nederlandse economie of markt), geef dan een lichte "
        "voorkeur aan [NL]-bronnen — gewicht 1.3x t.o.v. [EN] bij vergelijkbare nieuwswaarde.\n"
        "- Betreft het nieuws juist algemeen/internationaal tech-nieuws zonder specifieke "
        "Nederland-link (bijv. een buitenlandse investering in AI, een overname tussen "
        "niet-Nederlandse bedrijven), geef dan juist een lichte voorkeur aan [EN]-bronnen — "
        "gewicht 1.3x t.o.v. [NL] bij vergelijkbare nieuwswaarde. Reden: TechNieuwsVandaag wil "
        "dat soort internationaal nieuws als EERSTE in het Nederlands brengen, en Nederlandse "
        "bronnen berichten daar doorgaans pas later over.\n"
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
# Instagram-caption assemblage
# ---------------------------------------------------------------------------

# Vaste onderdelen van elke Instagram-caption. Bewust in code i.p.v. door
# Claude gegenereerd: de AI-disclosure mag nooit ontbreken en links in
# captions zijn niet klikbaar (vandaar link-in-bio).
_IG_LINK_LINE = "🔗 Lees het volledige artikel via de link in bio."
_IG_LINK_LINE_MEERVOUD = "🔗 Lees de volledige artikelen via de link in bio."
_IG_AI_DISCLOSURE = "🤖 Beeld gegenereerd met AI."
_IG_BASE_HASHTAGS = ["#technieuws", "#tech"]
_IG_MAX_EXTRA_HASHTAGS = 3  # totaal max 5 — meer oogt als spam
_IG_KOP_MAX_WORDS = 12
IG_CAPTION_MAX = 2200  # harde Graph API-limiet; erboven faalt de post volledig


def _build_ig_hashtags(trefwoorden: str) -> str:
    """Bouw de hashtagregel: 2 vaste tags + max 3 uit de trefwoorden.

    Pre:  trefwoorden is komma-gescheiden (mag leeg zijn)
    Post: regel als '#technieuws #tech #woord1 ...'; alleen alfanumerieke
          tags, zonder duplicaten (case-insensitief)
    """
    tags = list(_IG_BASE_HASHTAGS)
    seen = {t.lower() for t in tags}
    for kw in trefwoorden.split(","):
        tag = "#" + re.sub(r"[^\w]", "", kw.strip().lower())
        if len(tag) > 1 and tag.lower() not in seen:
            tags.append(tag)
            seen.add(tag.lower())
        if len(tags) >= len(_IG_BASE_HASHTAGS) + _IG_MAX_EXTRA_HASHTAGS:
            break
    return " ".join(tags)


def build_ig_caption(ig_tekst: str, trefwoorden: str) -> str:
    """Assembleer de volledige Instagram-caption uit de AI-tekst + vaste blokken.

    Pre:  ig_tekst is de hook + context (zonder hashtags/links/disclosure)
    Post: caption met lege regels tussen de blokken; ruim onder de
          2200-tekens-limiet van Instagram
    """
    return "\n\n".join([
        ig_tekst.strip(),
        _IG_LINK_LINE,
        _IG_AI_DISCLOSURE,
        _build_ig_hashtags(trefwoorden),
    ])


def _fallback_ig_tekst(samenvatting: str) -> str:
    """Eerste twee zinnen van de samenvatting als caption-tekst (fallback)."""
    tekst = ". ".join(samenvatting.split(". ")[:2]).strip()
    if tekst and not tekst.endswith("."):
        tekst += "."
    return tekst


def ensure_ig_fields(article: "ProcessedArticle") -> None:
    """Vul ig_kop/ig_tekst/ig_caption in-place met fallbacks als ze leeg zijn.

    Voor artikelen die niet via process_article() zijn gemaakt (oudere flows,
    adhoc/backfill) zodat queue_instagram_post altijd bruikbare velden krijgt.
    """
    if not article.ig_kop:
        article.ig_kop = " ".join(article.titel.split()[:_IG_KOP_MAX_WORDS])
    if not article.ig_tekst:
        article.ig_tekst = _fallback_ig_tekst(article.samenvatting)
    if not article.ig_caption:
        article.ig_caption = build_ig_caption(article.ig_tekst, article.trefwoorden)


def build_combined_ig_caption(entries: list[dict]) -> str:
    """Combineert meerdere artikel-hooks tot één Instagram-caption (dagdigest
    of wekelijkse Reel-recap — beide bundelen meerdere artikelen in 1 post).

    Pre:  entries is niet-leeg, elk dict heeft 'ig_tekst' en 'trefwoorden'
    Post: bij 1 entry gewoon de hook; bij meerdere een genummerde lijst en een
          link-regel in het meervoud. Vaste link/disclosure/hashtag-blokken
          komen er, net als bij een losse post, precies één keer bij — niet
          per artikel.
    """
    if len(entries) == 1:
        body = entries[0]["ig_tekst"].strip()
        link_line = _IG_LINK_LINE
    else:
        body = "\n\n".join(
            f"{i}. {entry['ig_tekst'].strip()}" for i, entry in enumerate(entries, 1)
        )
        link_line = _IG_LINK_LINE_MEERVOUD
    alle_trefwoorden = ", ".join(entry.get("trefwoorden", "") for entry in entries)
    return "\n\n".join([
        body,
        link_line,
        _IG_AI_DISCLOSURE,
        _build_ig_hashtags(alle_trefwoorden),
    ])


def fit_ig_entries(entries: list[dict], max_items: int) -> list[dict]:
    """Grootste voorloop van entries waarvan de gecombineerde caption past.

    Instagram weigert een post volledig ("The caption was too long") zodra de
    caption over IG_CAPTION_MAX gaat; ~9 artikelhooks halen dat al. Callers
    moeten dus vooraf snoeien i.p.v. achteraf een 400 opvangen.

    Pre:  entries is niet-leeg, max_items >= 1
    Post: niet-lege sublist (max max_items lang) waarvan
          build_combined_ig_caption() <= IG_CAPTION_MAX is; bij één entry die
          op zichzelf al te lang is wordt die toch teruggegeven — snoeien
          helpt daar niet meer.
    """
    for count in range(min(len(entries), max_items), 1, -1):
        if len(build_combined_ig_caption(entries[:count])) <= IG_CAPTION_MAX:
            return entries[:count]
    return entries[:1]


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

    # Gevarieerde artikellengte i.p.v. een vaste ~300 woorden: voorkomt dunne,
    # uniforme content (relevant voor AdSense-review) en maakt de in-article
    # ad-drempel (>500 woorden) daadwerkelijk af en toe actief.
    target_words = random.randint(300, 1000)
    paragraph_range = "2 - 4" if target_words < 500 else "4 - 7"

    prompt = (
        "Je bent een ervaren auteur en je schrijft artikelen voor een website "
        "die dagelijks nieuwsberichten plaatst. "
        "Lees het artikel via de meegeleverde link aandachtig door. "
        f"Vat de inhoud samen in ongeveer {target_words} woorden, {paragraph_range} paragrafen "
        "en in foutloos Nederlands op taalniveau 2F. "
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
        '  "focus_keyword": "...",\n'
        '  "ig_kop": "...",\n'
        '  "ig_tekst": "..."\n'
        "}\n\n"
        "Regels voor de extra SEO-velden:\n"
        "- meta_description: max 155 tekens, bevat het focus-trefwoord, prikkelend voor de lezer\n"
        "- slug: URL-vriendelijke slug van max 5 woorden, geen lidwoorden of stopwoorden, "
        "alleen kleine letters en koppeltekens (bijv. 'openai-lanceert-nieuw-model')\n"
        "- focus_keyword: het primaire SEO-trefwoord, 1-3 woorden\n\n"
        "Regels voor de Instagram-velden:\n"
        "- ig_kop: korte kop die óp de afbeelding komt te staan, max 9 woorden, "
        "actieve vorm, feitelijk en krachtig, geen punt aan het eind, geen "
        "aanhalingstekens, geen clickbait\n"
        "- ig_tekst: 2 à 3 zinnen voor de Instagram-caption. De eerste zin is de "
        "hook (max 125 tekens, sterk maar geen clickbait — dit is wat lezers zien "
        "vóór de 'meer'-knop), daarna 1 à 2 zinnen context. Geen hashtags, geen "
        "URL's, geen emoji's"
    )

    import time
    last_exc: Exception = RuntimeError("onbekende fout")
    for attempt in range(1, 4):
        try:
            # Timeout ruim boven 240s: bij target_words richting de 1000 duurt
            # generatie langer en liep de eerste poging anders vaak tegen de
            # oude 240s-limiet aan.
            response_text = _call_claude(prompt, timeout=360)
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

            titel = str(data["titel"]).strip()
            samenvatting = str(data["samenvatting"]).strip()
            trefwoorden = str(data["trefwoorden"]).strip()

            # Instagram-velden (optioneel — fallback op titel/samenvatting).
            # De kop wordt hard afgekapt op woorden: het beeld verkleint zelf
            # het font, maar boven ~12 woorden wordt de balk te vol.
            ig_kop = str(data.get("ig_kop", "")).strip().rstrip(".") or titel
            ig_kop = " ".join(ig_kop.split()[:_IG_KOP_MAX_WORDS])
            ig_tekst = str(data.get("ig_tekst", "")).strip() or _fallback_ig_tekst(samenvatting)
            ig_caption = build_ig_caption(ig_tekst, trefwoorden)

            return ProcessedArticle(
                original=article,
                titel=titel,
                samenvatting=samenvatting,
                trefwoorden=trefwoorden,
                categorieen=categorieen,
                meta_description=meta_description,
                slug=slug,
                focus_keyword=focus_keyword,
                target_words=target_words,
                ig_kop=ig_kop,
                ig_tekst=ig_tekst,
                ig_caption=ig_caption,
            )

        except (RuntimeError, subprocess.TimeoutExpired, json.JSONDecodeError, ValueError) as exc:
            last_exc = exc
            logger.warning("Artikel-verwerking poging %d/3 mislukt voor '%s': %s", attempt, article.title, exc)
            if attempt < 3:
                time.sleep(15 * attempt)

    logger.error("Artikel-verwerking mislukt na 3 pogingen voor '%s': %s", article.title, last_exc)
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
    articles = [a for a in articles if not is_similar_to_recently_posted(a.title)]
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

    # Bouw kandidatenlijst: geselecteerde index eerst, daarna de rest als fallback
    fallback_indices = [i for i in range(len(articles)) if i not in selected_indices[:1]]
    candidate_indices = selected_indices[:1] + fallback_indices

    # Laatste 10 gepubliceerde artikelen ophalen voor de duplicate-topic check.
    # Up-to-date op selectiemoment: binnen één run publiceert er niets tussen
    # selectie en publicatie, dus dit is gelijk aan de stand bij publiceren.
    from wordpress_client import fetch_recent_published  # noqa: PLC0415
    recent_published = fetch_recent_published(limit=10)
    logger.info("Duplicate-topic check tegen laatste %d gepubliceerde artikel(en)",
                len(recent_published))

    MAX_DUP_ATTEMPTS = 5
    processed: list[ProcessedArticle] = []
    first_processed: Optional[ProcessedArticle] = None  # fallback: nooit niets publiceren
    dup_attempts = 0

    for idx in candidate_indices:
        if not (0 <= idx < len(articles)):
            logger.warning("Index %d buiten bereik (max %d)", idx, len(articles) - 1)
            continue

        article = articles[idx]
        lang = getattr(article, "source_lang", "EN")
        logger.info("Artikel verwerken [%s]: %s", lang, article.title)
        result = process_article(article)
        if not result:
            logger.warning("Artikel mislukt, probeer volgende kandidaat")
            continue

        if first_processed is None:
            first_processed = result  # bewaar eerste kandidaat als laatste redmiddel

        dup_attempts += 1
        is_dup, reason = is_similar_to_recent(result, recent_published)
        if is_dup:
            logger.info(
                "skipped_duplicate_topic: '%s' overgeslagen (poging %d/%d) — %s",
                result.titel, dup_attempts, MAX_DUP_ATTEMPTS, reason,
            )
            if dup_attempts >= MAX_DUP_ATTEMPTS:
                logger.warning(
                    "Na %d pogingen geen niet-gelijkend artikel gevonden — "
                    "publiceer toch oorspronkelijke kandidaat: '%s'",
                    dup_attempts, first_processed.titel,
                )
                processed.append(first_processed)
                break
            continue

        logger.info(
            "Geen overlap met recente publicaties — gekozen voor publicatie: '%s'",
            result.titel,
        )
        processed.append(result)
        break  # 1 niet-gelijkend artikel verwerkt, klaar

    # Alle beschikbare kandidaten leken op recente publicaties (minder dan
    # MAX_DUP_ATTEMPTS kandidaten): publiceer toch de eerste om niets-publiceren
    # te voorkomen.
    if not processed and first_processed is not None:
        logger.warning(
            "Alle %d kandidaten leken op recente publicaties — "
            "publiceer toch oorspronkelijke kandidaat: '%s'",
            dup_attempts, first_processed.titel,
        )
        processed.append(first_processed)

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
