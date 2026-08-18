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
    # Provider die het beeld daadwerkelijk maakte (webgemini/nanobanana/fal);
    # met een terugvalketen is dat lang niet altijd de primaire.
    image_provider: str = ""
    # Pad naar de bewaarde kopie zónder AI-label (voor de bewegende Reel)
    image_unlabeled: str = ""
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


def _neutral_cwd() -> str:
    """Werkmap voor de claude CLI, bewust buiten élke CLAUDE.md.

    De CLI zoekt CLAUDE.md-bestanden vanaf zijn werkmap omhoog en plakt ze
    integraal in de context van élke aanroep. Draaide hij in de projectmap, dan
    kostte dat ~19.200 tokens per aanroep (gemeten 2026-08-18: 35.957 tokens
    context in de projectmap tegen 16.733 hier) aan projectinstructies die met
    de vraag niets te maken hebben — bij ~22 aanroepen per dag ruim 400.000
    tokens. Dat de samenvatter bovendien eerst de beeldpromptregels van dit
    project las was op zichzelf al ongewenst.

    Dus: een map buiten de projectboom én buiten /home/dgebbink (waar de
    algemene CLAUDE.md staat), zodat de zoektocht naar boven niets vindt.
    Neveneffect: de sessielogs van de bot komen nu onder
    ~/.claude/projects/-tmp-tnv-claude-cwd/ te staan, los van de interactieve
    sessies in de projectmap.
    """
    path = Path("/tmp/tnv-claude-cwd")
    path.mkdir(exist_ok=True)
    # Expliciet openzetten, niet via mkdir(mode=...): die wordt door de umask
    # gemaskeerd, en de map wordt door twee gebruikers gedeeld — cron draait als
    # dgebbink, de approval-server als root (die daarna naar dgebbink su't).
    # Wie hem als eerste aanmaakt mag de ander niet buitensluiten.
    try:
        path.chmod(0o777)
    except OSError:
        pass  # bestaat al met goede rechten, of van een andere eigenaar
    return str(path)


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
        # stdin dichtzetten is niet optioneel: zonder open terminal wacht de CLI
        # 3 s op stdin, waarschuwt "no stdin data received" en eindigt met exit 1.
        # Dat sloeg toe zodra de aanroep uit een daemon kwam (approval-server via
        # supervisord) of uit een losgekoppeld achtergrondproces — cron gaf zelf
        # al /dev/null mee, vandaar dat de nachtelijke runs wél liepen en dit
        # lang onzichtbaar bleef.
        cmd, capture_output=True, text=True, timeout=timeout, env=env,
        stdin=subprocess.DEVNULL, cwd=_neutral_cwd(),
    )
    if result.returncode != 0:
        err = result.stderr.strip()
        # De CLI meldt niet alles op stderr: bij een usage-/rate-limit kwam er
        # exit 1 met een léég stderr terug, waardoor de log alleen "exit 1" zei
        # en de oorzaak nergens stond (2026-08-10, hele run zonder artikelen).
        # Daarom stdout erbij pakken als stderr niets prijsgeeft.
        if not err:
            err = result.stdout.strip()[:500] or "(geen output op stderr of stdout)"
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
# Redactionele triage
# ---------------------------------------------------------------------------

# Hoeveel kandidaten de triage maximaal terugkrijgt. Er wordt er één
# gepubliceerd; de rest is reserve voor als process_article() faalt.
_TRIAGE_MAX_RANKED = 5


def triage_articles(
    articles: list[Article],
    recent_published: list[dict],
    max_ranked: int = _TRIAGE_MAX_RANKED,
) -> Optional[list[int]]:
    """Eén redactionele beoordeling van de hele kandidatenlijst.

    Dit was tot 2026-08-18 vier losse Claude-aanroepen — tech-filter,
    semantische deduplicatie, selectie en de duplicate-topic-check achteraf —
    die alle vier vrijwel dezelfde lijst meestuurden. Elke aanroep van de CLI
    kost tienduizenden tokens aan vaste context vóór de vraag zelf begint, dus
    het aantal aanroepen weegt veel zwaarder dan de lengte van de lijst. Het
    zijn bovendien geen tegenstrijdige opdrachten maar drie opeenvolgende
    filters op één lijst, wat één redacteur in één keer doet.

    Bijkomend voordeel: de duplicaat-toets zat vroeger ná process_article(), de
    duurste aanroep van de run. Een gekozen artikel dat op een recente
    publicatie leek, was dus al volledig samengevat voordat het werd
    weggegooid. Nu valt het af vóórdat er iets aan besteed wordt.

    Pre:  articles is niet-leeg; recent_published is een lijst dicts met
          'title' en 'excerpt' (nieuwste eerst, mag leeg zijn)
    Post: 0-gebaseerde indices in articles, belangrijkste eerst, max max_ranked.
          [] is een geldig oordeel: niets hiervan is publicabel, dan publiceert
          de run niets. None betekent dat de aanroep mislukte — de caller valt
          dan terug op de ongefilterde lijst, zodat de triage de pijplijn nooit
          blokkeert.
    """
    if not articles:
        return []

    numbered = "\n".join(
        f"{i + 1}. [{_domain(a.source)}] [{getattr(a, 'source_lang', 'EN')}] {a.title}\n"
        f"   {a.excerpt[:200]}"
        for i, a in enumerate(articles)
    )
    recent = "\n".join(
        f"- {art.get('title', '')} — {art.get('excerpt', '')[:150]}"
        for art in recent_published
    ) or "(nog niets gepubliceerd)"

    prompt = (
        "Je bent eindredacteur van een Nederlandse TECH-nieuwswebsite en bepaalt "
        "welk artikel vandaag gepubliceerd wordt. Loop de drie stappen hieronder "
        "in één keer door.\n\n"
        "STAP 1 — schrap wat geen tech-nieuws is.\n"
        "WEL tech: software, hardware, AI, internet, telecom, chips, "
        "cybersecurity, privacy en regulering van technologie, ruimtevaart- en "
        "wetenschapstechniek, gaming-technologie, en het zakelijke nieuws van "
        "technologiebedrijven (overnames, cijfers, rechtszaken, personeel).\n"
        "NIET tech: muziek, film, tv, celebrity's, sport, algemene politiek, "
        "economie zonder tech-invalshoek, lifestyle, gezondheid, misdaad en "
        "cultuur.\n"
        "Twijfelgeval: de technologie moet de KERN van het verhaal zijn, niet "
        "het decor. Een muzikant die een album aankondigt in een podcast is GEEN "
        "tech-nieuws, ook niet als de podcast van een techbedrijf is. Een "
        "artikel over de aanbevelingsalgoritmes van een streamingdienst is dat "
        "WEL. Bij twijfel: weglaten.\n\n"
        "STAP 2 — schrap duplicaten en wat we al brachten.\n"
        "Behandelen meerdere kandidaten hetzelfde nieuwsfeit of dezelfde "
        "aankondiging (ook anders verwoord of uit een andere bron), houd dan "
        "alleen de beste over — bij voorkeur een NL-bron boven EN, het meest "
        "gedetailleerde excerpt, de meest gezaghebbende bron.\n"
        "Schrap daarnaast elke kandidaat die hetzelfde onderwerp of nieuwsfeit "
        "behandelt als een van de RECENT GEPUBLICEERDE artikelen onderaan. "
        "Zelfde bedrijf + product + type nieuws telt als duplicaat; ook wanneer "
        "kandidaat en recent artikel over dezelfde hoofdpersoon of organisatie "
        "gaan, tenzij het nieuwsfeit duidelijk en wezenlijk anders is. Een écht "
        "ander aspect van een breed thema (ander product, andere onderneming, "
        "andere invalshoek) telt NIET als duplicaat.\n\n"
        "STAP 3 — rangschik wat overblijft, belangrijkste eerst.\n"
        "Weeg: breedte van impact, innovatie, relevantie voor consument én "
        "professional, en nieuwswaarde. Weeg [NL]- versus [EN]-bronnen op het "
        "ONDERWERP van het artikel, niet op de bron zelf:\n"
        "- Betreft het nieuws specifiek Nederland (een Nederlandse uitvinding of "
        "onderneming, impact op de Nederlandse economie of markt), geef dan een "
        "lichte voorkeur aan [NL]-bronnen — gewicht 1.3x t.o.v. [EN] bij "
        "vergelijkbare nieuwswaarde.\n"
        "- Betreft het juist algemeen/internationaal tech-nieuws zonder "
        "specifieke Nederland-link, geef dan een lichte voorkeur aan "
        "[EN]-bronnen — gewicht 1.3x t.o.v. [NL]. Reden: TechNieuwsVandaag wil "
        "dat soort nieuws als EERSTE in het Nederlands brengen, en Nederlandse "
        "bronnen berichten daar doorgaans pas later over.\n\n"
        f"KANDIDATEN:\n{numbered}\n\n"
        f"RECENT GEPUBLICEERD (niet opnieuw brengen):\n{recent}\n\n"
        "Geef ALLEEN een JSON array met de nummers van de kandidaten die stap 1 "
        f"en 2 overleven, in de volgorde van stap 3, maximaal {max_ranked} "
        "nummers. Bijvoorbeeld: [12, 4, 27]. Overleeft geen enkele kandidaat, "
        "geef dan []. Geen uitleg."
    )

    try:
        response = _call_claude(prompt, timeout=180)
    except InsufficientCreditsError:
        raise  # doorsturen naar aanroeper voor urgente melding
    except Exception as exc:
        logger.warning("Triage mislukt: %s — alle kandidaten behouden", exc)
        return None

    match = re.search(r"\[[\d,\s]*\]", response)
    if not match:
        logger.warning("Triage: geen JSON-array in antwoord — alle kandidaten behouden")
        return None

    try:
        numbers = json.loads(match.group())
    except json.JSONDecodeError as exc:
        logger.warning("Triage: array onparseerbaar (%s) — alle kandidaten behouden", exc)
        return None

    ranked: list[int] = []
    for raw in numbers:
        idx = int(raw) - 1
        if 0 <= idx < len(articles):
            if idx not in ranked:
                ranked.append(idx)
        else:
            logger.warning("Triage gaf ongeldig nummer %s (max %d)", raw, len(articles))

    dropped = len(articles) - len(ranked)
    if ranked:
        logger.info(
            "Triage: %d van %d kandidaten over na tech-filter/deduplicatie; "
            "eerste keuze: %s",
            len(ranked), len(articles), articles[ranked[0]].title,
        )
    else:
        logger.warning(
            "Triage: geen enkele van de %d kandidaten is publicabel tech-nieuws "
            "— deze run publiceert niets", len(articles),
        )
    logger.info("Triage liet %d kandidaat/kandidaten vallen", dropped)

    return ranked[:max_ranked]


# ---------------------------------------------------------------------------
# Deduplicatie helpers
# ---------------------------------------------------------------------------

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


def similar_to_recent_titles(
    candidate: "ProcessedArticle",
    recent_articles: list[dict],
    title_threshold: float = 0.6,
) -> tuple[bool, str]:
    """Goedkoop vangnet: woordoverlap van de NL-titel met recente publicaties.

    Het semantische oordeel hierover zit sinds 2026-08-18 in
    triage_articles(), dat duplicaten van recente publicaties al vóór de
    samenvatting wegstreept. Wat hier overblijft is de gratis, lokale toets die
    er vroeger als eerste trap vóór zat: zelfde bedrijf + product + type nieuws
    geeft doorgaans hoge woordoverlap in de Nederlandse titel. Kost geen
    Claude-aanroep, dus hij blijft staan als tweede paar ogen op de kop die
    Claude uiteindelijk schreef — die kende de triage nog niet.

    Pre:  candidate heeft .titel; recent_articles is een lijst dicts met 'title'
    Post: (is_similar, reden); reden noemt de recente titel waarmee het botst
    """
    for art in recent_articles:
        ratio = _title_overlap_ratio(candidate.titel, art.get("title", ""))
        if ratio > title_threshold:
            return True, f"titel-overlap {ratio * 100:.0f}% met recent '{art['title']}'"
    return False, ""


def save_posted_title(title: str, url: str) -> None:
    """Saves a posted article title for future dedup checks.
    Pre:  title and url are non-empty strings
    Post: line appended to posted_titles.txt: YYYY-MM-DD|url|title
    """
    with open(POSTED_TITLES_FILE, "a", encoding="utf-8") as f:
        f.write(f"{date.today().isoformat()}|{url}|{title}\n")


# ---------------------------------------------------------------------------
# Instagram-caption assemblage
# ---------------------------------------------------------------------------

# Vaste onderdelen van elke Instagram-caption. Bewust in code i.p.v. door
# Claude gegenereerd: links in captions zijn niet klikbaar (vandaar
# link-in-bio). Geen AI-disclosure-regel: compose_instagram_image() zet al een
# zichtbaar 'AI-gegenereerd'-label op het beeld zelf én XMP-metadata waarmee
# Meta het officiele AI-info-label toont — in de caption is het dubbelop.
_IG_LINK_LINE = "🔗 Lees het volledige artikel via de link in bio."
_IG_LINK_LINE_MEERVOUD = "🔗 Lees de volledige artikelen via de link in bio."
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

# Vanaf hoeveel tekens brontekst we de samenvatting zonder eigen onderzoek
# laten schrijven. Onder deze grens halen we het artikel eerst zelf op; lukt
# ook dat niet, dan mag Claude de link alsnog openen.
_VOLLEDIGE_TEKST_DREMPEL = 1200


def process_article(article: Article) -> Optional[ProcessedArticle]:
    """Generate Dutch summary, titles, keywords and categories for one article."""
    # pre: article.url is reachable
    # post: returns None on any failure
    categories_str = ", ".join(CATEGORIES)

    # Zelf ophalen kost een HTTP-request en geen tokens, dus doen we dat tot aan
    # dezelfde drempel waaronder we Claude anders zouden vragen te gaan browsen.
    # Stond op 300 tekens, waardoor een excerpt van 600 wél "genoeg" heette maar
    # te dun was om echt op te vatten.
    artikel_tekst = article.excerpt
    if len(artikel_tekst) < _VOLLEDIGE_TEKST_DREMPEL:
        logger.info("Excerpt te kort, volledige tekst ophalen voor: %s", article.url)
        artikel_tekst = fetch_article_text(article.url) or artikel_tekst

    # Hebben we genoeg tekst, dan moet Claude er ook mee wérken. De opdracht om
    # "het artikel via de link te lezen" stond hier ook als de tekst al
    # meegestuurd werd, en dan gaat de CLI zelf op onderzoek uit: gemeten
    # 2026-08-18 leidde één samenvatting tot 21 API-calls met WebFetch,
    # WebSearch én zeven Bash-aanroepen, samen ~549.000 tokens — verreweg de
    # duurste stap van de hele run, voor tekst die al in de prompt stond. Elke
    # extra beurt herhaalt bovendien de volledige context.
    # Onder de drempel is de link juist wél nodig: dan hebben we weinig meer
    # dan een kop, en is zelf ophalen de enige manier aan een samenvatting te
    # komen.
    heeft_volledige_tekst = len(artikel_tekst) >= _VOLLEDIGE_TEKST_DREMPEL
    bron_instructie = (
        "Werk uitsluitend met de artikeltekst hieronder; die is volledig genoeg. "
        "Open de link niet en zoek niet verder — de URL staat er alleen ter "
        "referentie bij. "
        if heeft_volledige_tekst
        else "De meegeleverde tekst is onvolledig: haal het artikel éénmaal op "
             "via de meegeleverde link en werk met wat je daar aantreft. Doe "
             "geen aanvullend onderzoek, geen zoekopdrachten en geen "
             "shell-commando's; is de pagina niet leesbaar, schrijf de "
             "samenvatting dan op basis van titel en meegeleverde tekst. "
    )
    if not heeft_volledige_tekst:
        logger.info(
            "Slechts %d tekens brontekst — Claude haalt het artikel zelf op",
            len(artikel_tekst),
        )

    # Gevarieerde artikellengte i.p.v. een vaste ~300 woorden: voorkomt dunne,
    # uniforme content (relevant voor AdSense-review) en maakt de in-article
    # ad-drempel (>500 woorden) daadwerkelijk af en toe actief.
    target_words = random.randint(300, 1000)
    paragraph_range = "2 - 4" if target_words < 500 else "4 - 7"

    prompt = (
        "Je bent een ervaren auteur en je schrijft artikelen voor een website "
        "die dagelijks nieuwsberichten plaatst. "
        f"{bron_instructie}"
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

    # Stap A: eerst de gratis, lokale filters — die kosten geen Claude-aanroep
    # en verkleinen de lijst die straks meegestuurd wordt.
    before_filter = len(articles)
    articles = [a for a in articles if not is_similar_to_recently_posted(a.title)]
    filtered = before_filter - len(articles)
    if filtered:
        logger.info(
            "%d artikel(en) gefilterd wegens overlap met vandaag al geposte titels",
            filtered,
        )

    if not articles:
        logger.warning("Alle kandidaten al gepost vandaag")
        return []

    # Laatste 10 gepubliceerde artikelen: gaan mee in de triage, zodat een
    # onderwerp dat we net brachten al afvalt vóór de dure samenvatting.
    # Up-to-date op selectiemoment: binnen één run publiceert er niets tussen
    # selectie en publicatie, dus dit is gelijk aan de stand bij publiceren.
    from wordpress_client import fetch_recent_published  # noqa: PLC0415
    recent_published = fetch_recent_published(limit=10)
    logger.info("Triage tegen laatste %d gepubliceerde artikel(en)",
                len(recent_published))

    # Stap B: één redactionele beoordeling — tech-filter, deduplicatie,
    # uitsluiting van wat we al brachten, en rangschikking in één aanroep.
    # Bronnen als nytimes.com en theguardian.com zijn betrouwbaar maar breed;
    # zonder de tech-toets kan een muziek- of sportartikel de selectie winnen
    # (2026-08-09: een blink-182-album haalde de site).
    logger.info("Triage: %d kandidaten beoordelen", len(articles))
    ranked = triage_articles(articles, recent_published)

    if ranked is None:
        # Mislukte aanroep mag de pijplijn niet blokkeren: val terug op de
        # ongefilterde lijst op recentheid, zoals de losse selectie vroeger
        # terugviel op het eerste artikel.
        logger.warning("Triage onbruikbaar — alle kandidaten op recentheid gebruikt")
        candidate_indices = list(range(len(articles)))
    elif not ranked:
        # Geldig oordeel: hier zit niets publicabels tussen.
        return []
    else:
        candidate_indices = ranked

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
        is_dup, reason = similar_to_recent_titles(result, recent_published)
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
