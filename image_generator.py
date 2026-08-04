"""
Afbeeldingen genereren via Claude (prompt) + FAL.ai flux/dev (beeld).
Wordt gebruikt wanneer IMAGE_STRATEGY=generate is ingesteld.
"""
import json
import logging
import random
import re
from typing import Optional

import requests
from PIL import Image, ImageDraw, ImageFont

from config import (
    FAL_API_KEY,
    FAL_CREDIT_THRESHOLD,
    REQUEST_TIMEOUT,
    IMAGE_DISTRIBUTION_FILE,
    IMAGE_DISTRIBUTION_TARGETS,
    IMAGE_MENTION_ETHNICITY_PROBABILITY,
)
from ai_processor import _call_claude, _extract_json

logger = logging.getLogger(__name__)

FAL_ENDPOINT = "https://fal.run/fal-ai/flux/dev"
FAL_IMAGE_TIMEOUT = 120  # FAL.ai genereert in 60-90 seconden


def check_fal_balance() -> Optional[float]:
    # FAL.ai billing API is not publicly available; balance checking disabled
    return None


def is_fal_balance_low() -> bool:
    return False


def _load_distribution_state() -> dict:
    """Lees de persistente tellerstand; bij ontbreken/corruptie een lege dict."""
    try:
        with open(IMAGE_DISTRIBUTION_FILE, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return {}


def _save_distribution_state(state: dict) -> None:
    """Schrijf de tellerstand weg; faalt stil met een waarschuwing."""
    try:
        with open(IMAGE_DISTRIBUTION_FILE, "w", encoding="utf-8") as fh:
            json.dump(state, fh, indent=2, ensure_ascii=False)
    except OSError as exc:
        logger.warning("Kon beeldverdeling-state niet opslaan: %s", exc)


def _pick_balanced(dimension: str, weights: dict, state: dict) -> str:
    """Kies de optie die het meest achterloopt op zijn doelaandeel.

    Pre:  weights is een niet-lege dict {optie: relatief_gewicht}
    Post: de teller voor `dimension` in `state` is in-place opgehoogd voor de
          gekozen optie; retourneert die optie. Bij gelijke achterstand wordt
          willekeurig getrokken om systematische volgorde-bias te voorkomen.
    """
    counts = state.setdefault(dimension, {})
    total = sum(counts.get(opt, 0) for opt in weights)
    total_weight = sum(weights.values()) or 1

    deficits = {}
    for opt, weight in weights.items():
        target_share = weight / total_weight
        current_share = (counts.get(opt, 0) / total) if total else 0.0
        deficits[opt] = target_share - current_share

    best = max(deficits.values())
    candidates = [opt for opt, d in deficits.items() if abs(d - best) < 1e-9]
    chosen = random.choice(candidates)

    counts[chosen] = counts.get(chosen, 0) + 1
    return chosen


def generate_person_variant() -> dict:
    """Kies een persoonsbeschrijving voor de beeldprompt via een persistente
    teller, zodat de werkelijke verdeling per dimensie naar de doelen in
    config.IMAGE_DISTRIBUTION_TARGETS convergeert in plaats van puur toeval.

    Post: dict met keys gender, ethnicity, age, scene_population en
          mention_ethnicity. De teller in IMAGE_DISTRIBUTION_FILE is bijgewerkt
          voor de via convergentie gekozen dimensies (gender, ethnicity,
          age_bucket, scene_population). mention_ethnicity is een losse random
          toggle (geen convergentie-tracking) die alleen bepaalt of de
          ethniciteit wordt benoemd in de solo-template; alle dimensies worden
          onafhankelijk van elkaar getrokken.
    """
    state = _load_distribution_state()
    targets = IMAGE_DISTRIBUTION_TARGETS

    gender = _pick_balanced("gender", targets["gender"], state)
    ethnicity = _pick_balanced("ethnicity", targets["ethnicity"], state)

    age_bucket = _pick_balanced("age_bucket", targets["age_bucket"], state)
    low, high = (int(part) for part in age_bucket.split("-"))
    age = random.randint(low, high)

    scene_population = _pick_balanced(
        "scene_population", targets["scene_population"], state
    )

    _save_distribution_state(state)

    # Losse, onafhankelijke toggle — niet meegenomen in de convergentie-tracking.
    mention_ethnicity = random.random() < IMAGE_MENTION_ETHNICITY_PROBABILITY

    variant = {
        "gender": gender,
        "ethnicity": ethnicity,
        "age": age,
        "scene_population": scene_population,
        "mention_ethnicity": mention_ethnicity,
    }
    logger.info("Beeld-persoonsvariant gekozen: %s", describe_variant(variant))
    return variant


def describe_variant(variant: dict) -> str:
    """Vat de gekozen persoonsvariant samen als leesbare regel (log + mail)."""
    parts = [variant["gender"]]
    # Toon de ethniciteit alleen als die ook daadwerkelijk in de prompt komt.
    if variant.get("scene_population") == "solo" and variant.get("mention_ethnicity"):
        parts.append(variant["ethnicity"])
    parts.append(f"~{variant['age']} jr")
    parts.append(variant.get("scene_population", "solo"))
    return " · ".join(parts)


# Vier person-instructie templates, gekozen op basis van scene_population en
# (bij solo) de losse mention_ethnicity toggle.
#
# De sekse moet er letterlijk in blijven staan. Eerder vroeg de group-template
# om "predominantly {gender_plural}" en verbood in dezelfde adem het beschrijven
# van "individual demographic traits" — Claude loste die tegenspraak op door de
# sekse wég te laten ("three young colleagues"), en flux/dev vult zo'n
# genderloze groep standaard met mannen. In de logs raakte dat 25 van de 53
# group-beelden. Vandaar de expliciete MUST + de verbodenwoordenlijst; de
# harde garantie zit in _enforce_person_in_prompt().
_PERSON_TEMPLATE_SOLO_WITH_ETHNICITY = (
    "The scene MUST show one person: {gender}, {ethnicity}, around {age} years "
    "old. The prompt MUST name that person literally as \"{gender}\" — never a "
    "genderless substitute such as \"a person\", \"a professional\", "
    "\"an engineer\" or \"someone\". {appearance}"
)
_PERSON_TEMPLATE_SOLO_NO_ETHNICITY = (
    "The scene MUST show one person: {gender}, around {age} years old. The "
    "prompt MUST name that person literally as \"{gender}\" — never a "
    "genderless substitute such as \"a person\", \"a professional\", "
    "\"an engineer\" or \"someone\". {appearance}"
)
_PERSON_TEMPLATE_GROUP = (
    "The scene MUST show a small group of 2-4 people naturally collaborating, "
    "and the prompt MUST name them literally as {gender_plural} around {age} "
    "years old (for example: \"three {gender_plural} in their {age_words}\") — "
    "never a genderless substitute such as \"colleagues\", \"people\", "
    "\"professionals\", \"a team\" or \"individuals\". EVERY person in the "
    "scene is {gender_plural}: do not add a colleague, a bystander or anyone "
    "else of a different gender, not even in the background. Give them natural "
    "variety in ethnicity and background, but do not single out one person's "
    "ethnicity; keep the emphasis on the activity and setting. {appearance}"
)

# Meervoudsvorm van de gekozen sekse voor de group-template, zodat de groep
# overwegend uit die sekse bestaat (i.p.v. de default male-mix van het model).
_GENDER_PLURAL = {
    "a woman": "women",
    "a man": "men",
}

# Standaard styling: neutraal en zakelijk.
_APPEARANCE_DEFAULT_SOLO = (
    "Style them as a confident, professional individual with a natural, genuine "
    "expression, natural skin texture, and realistic proportions."
)
_APPEARANCE_DEFAULT_GROUP = (
    "Natural skin texture and realistic proportions throughout."
)


def _age_words(age: int) -> str:
    """Leesbare leeftijdsaanduiding voor de group-voorbeeldzin ('mid-twenties')."""
    decade = {1: "teens", 2: "twenties", 3: "thirties"}.get(age // 10, "twenties")
    if age % 10 <= 3:
        return f"early {decade}"
    if age % 10 <= 6:
        return f"mid-{decade}"
    return f"late {decade}"


def build_person_instruction(variant: dict) -> str:
    """Bouw de person-instructie uit de gekozen variant.

    GROUP benoemt nooit ethniciteit (los van mention_ethnicity). SOLO benoemt de
    ethniciteit alleen als mention_ethnicity true is.
    """
    if variant.get("scene_population") == "group":
        gender_plural = _GENDER_PLURAL.get(variant["gender"], "people")
        return _PERSON_TEMPLATE_GROUP.format(
            gender_plural=gender_plural,
            age=variant["age"],
            age_words=_age_words(variant["age"]),
            appearance=build_appearance_clause(variant),
        )
    if variant.get("mention_ethnicity"):
        return _PERSON_TEMPLATE_SOLO_WITH_ETHNICITY.format(
            gender=variant["gender"],
            ethnicity=variant["ethnicity"],
            age=variant["age"],
            appearance=build_appearance_clause(variant),
        )
    return _PERSON_TEMPLATE_SOLO_NO_ETHNICITY.format(
        gender=variant["gender"],
        age=variant["age"],
        appearance=build_appearance_clause(variant),
    )


# Styling voor de vrouwelijke variant; de mannelijke houdt de neutrale basis.
# De erotische omschrijving staat bewust bóvenop de zakelijke en niet in plaats
# daarvan: de twee sluiten elkaar niet uit, en zonder die basis stond ze
# tegenover het "confident, professional" van de mannelijke variant — een
# verschil dat op een nieuwssite opvalt zodra je een paar artikelen achter
# elkaar ziet.
# Aparte clausule per scene_population: in een groep werkt een enkelvoudige,
# op de camera gerichte omschrijving niet.
_APPEARANCE_ATTRACTIVE_SOLO = (
    "Style her like a confident woman with a natural, authentic expression, "
    "natural skin texture and realistic proportions, who is simultaneously "
    "slightly erotic: natural, well-formed figure. "
    "Seductive yet confident and competent, never passive or decorative."
)
_APPEARANCE_ATTRACTIVE_GROUP = (
    "Give them the appearance of confident professionals with a natural, "
    "authentic expression, who are simultaneously subtly erotic: natural and "
    "well-formed figures and fashionable, "
    "form-fitting outfits that flatter their figure. They are committed to "
    "their work, never passive or decorative. Natural skin texture and "
    "realistic proportions throughout."
)


def build_appearance_clause(variant: dict) -> str:
    """Kies de uiterlijk-styling voor deze variant.

    Pre:  variant bevat gender en scene_population
    Post: de attractieve styling bij gender 'a woman'; alle andere varianten
          houden de neutrale, zakelijke styling.
    """
    group = variant.get("scene_population") == "group"
    if variant.get("gender") != "a woman":
        return _APPEARANCE_DEFAULT_GROUP if group else _APPEARANCE_DEFAULT_SOLO
    return _APPEARANCE_ATTRACTIVE_GROUP if group else _APPEARANCE_ATTRACTIVE_SOLO


# Markers waaraan te zien is dat de attractieve styling al in de prompt zit,
# ook als Claude hem in eigen woorden herschreef.
_ATTRACTIVE_MARKERS = (
    "erotic",
    "seductive",
    "well-formed",
    "form-fitting",
    "alluring",
)

# Woordpatronen om te toetsen of de gekozen sekse écht in de prompt staat.
# \b voorkomt dat "man" matcht binnen "woman"/"human" en "men" binnen "women".
_GENDER_PATTERNS = {
    "a woman": r"\bwom[ae]n\b",
    "a man": r"\b(?:man|men|male|males)\b",
}


def _enforce_person_in_prompt(prompt: str, variant: dict) -> str:
    """Garandeer dat de gekozen persoonsvariant daadwerkelijk in de prompt staat.

    De prompt-instructie alleen is niet betrouwbaar gebleken: Claude liet de
    sekse in bijna de helft van de group-prompts weg, waarna flux/dev er mannen
    van maakte. Deze functie is de deterministische vangnetlaag — de instructie
    zorgt voor een natuurlijk verweven formulering, dit zorgt dat het er hoe dan
    ook staat.

    Pre:  variant is niet leeg (bij een gevoelig onderwerp niet aanroepen)
    Post: prompt met, indien ontbrekend, een expliciete persoonszin erachter;
          de uiterlijk-styling van build_appearance_clause() staat er altijd in
    """
    if not variant:
        return prompt

    gender = variant.get("gender", "")
    pattern = _GENDER_PATTERNS.get(gender)
    group = variant.get("scene_population") == "group"
    age = variant.get("age")

    if pattern and not re.search(pattern, prompt, re.IGNORECASE):
        if group:
            plural = _GENDER_PLURAL.get(gender, "people")
            addition = (
                f" All the people in the scene are {plural} around {age} years old."
            )
        else:
            addition = f" The person in the scene is {gender} around {age} years old."
        logger.warning(
            "Sekse ontbrak in de gegenereerde prompt (%s) — expliciet toegevoegd",
            describe_variant(variant),
        )
        prompt = prompt.rstrip() + addition

    # Een gemengde groep glipt anders door de sekse-check heen: de prompt noemt
    # wél "women", maar zet er "one male colleague" of "and a colleague" naast
    # (2× waargenomen in de logs). Daarom bij een groep altijd expliciet
    # afbakenen — flux/dev maakt van een genderloze "colleague" standaard een man.
    if group and gender in _GENDER_PLURAL:
        plural = _GENDER_PLURAL[gender]
        prompt = prompt.rstrip() + (
            f" Every single person visible in the scene is one of the {plural}, "
            f"all around {age} years old; no colleague, bystander or background "
            "figure of any other gender appears anywhere in the frame."
        )

    # Claude herformuleert de styling meestal (en laat 'sexy' dan weg), dus
    # toetsen op meerdere markers — anders wordt de clausule dubbel aangehangen.
    if gender == "a woman":
        low = prompt.lower()
        if not any(marker in low for marker in _ATTRACTIVE_MARKERS):
            logger.info("Uiterlijk-styling ontbrak in de prompt — expliciet toegevoegd")
            prompt = prompt.rstrip() + " " + build_appearance_clause(variant)

    return prompt


def is_sensitive_topic(title: str, article_text: str) -> bool:
    """Beoordeelt of de standaard beeldstijl ongepast is voor dit artikel.

    De nieuwsprompt dwingt "bright lighting and an optimistic mood" af en
    zet standaard een persoon centraal. Bij een artikel over bijvoorbeeld
    beeldmisbruik levert dat een opgewekte foto met een vrouw als middelpunt op
    — tone-deaf en mogelijk hervictimiserend. Deze check zet die twee dingen uit.

    Bewust een aparte, korte Claude-aanroep vóór de promptgeneratie: alleen zo
    weten we vóór generate_person_variant() of er een persoonsvariant nodig is,
    en blijft de teller in image_distribution.json eerlijk voor de artikelen die
    er wél een gebruiken.

    Pre:  claude CLI is beschikbaar
    Post: True alleen bij menselijk leed; gewoon negatief zakelijk nieuws
          (ontslagen, rechtszaken, boetes, storingen) telt niet mee. Bij twijfel
          of een fout: False — de guard mag de normale flow niet blokkeren.
    """
    instruction = (
        "Return a single JSON field:\n"
        "\"sensitive\": true or false. Answer true only if a bright, optimistic "
        "stock-style photo with a cheerful person as the focal subject would be "
        "tone-deaf, disrespectful or harmful for this news article — for example "
        "sexual abuse or image-based abuse, violence, death, war, terrorism, "
        "serious crime, exploitation, harassment, discrimination, child safety, "
        "or addiction. "
        "Answer false for ordinary technology, product, research, business or "
        "policy news, including negative business news such as layoffs, "
        "lawsuits, fines, outages or data breaches without personal harm.\n\n"
        f"Article title: {title}\n"
        f"Article text:\n{article_text[:1000]}\n\n"
        "Respond with only valid JSON, no markdown fences."
    )
    try:
        data = _extract_json(_call_claude(instruction, timeout=45))
        if isinstance(data, dict):
            return bool(data.get("sensitive", False))
    except Exception as exc:
        logger.warning("Gevoeligheidscheck mislukt (val terug op normale stijl): %s", exc)
    return False


# Wat vroeger in _SENSITIVE_NEGATIVE_PROMPT stond. fal-ai/flux/dev kent geen
# negative_prompt (staat niet in hun schema), dus daar deed het niets; flux volgt
# wél expliciete uitsluitingen in de prompt zelf — zo werkt "no text, no logos"
# in de andere varianten ook. Deterministisch aangehangen in plaats van via de
# Claude-instructie: die laat instructies vallen (zie _enforce_person_in_prompt).
_SENSITIVE_PROMPT_SUFFIX = (
    " Sober and restrained throughout: nobody in the frame is smiling, laughing, "
    "cheering or celebrating, there are no thumbs-up or triumphant gestures, no "
    "festive or party atmosphere, and no vibrant, saturated or upbeat colour "
    "palette."
)


def _build_sensitive_image_prompt(title: str, article_text: str) -> str:
    """Beeldprompt voor een gevoelig onderwerp: ingetogen en zonder slachtoffer.

    Post: de prompt eindigt altijd op _SENSITIVE_PROMPT_SUFFIX, ook wanneer
          Claude ongeldige JSON teruggaf en de ruwe tekst wordt gebruikt
    """
    instruction = (
        "Return a single JSON field:\n"
        "\"prompt\": A 2-sentence English prompt for a photorealistic image to "
        "accompany a news article about a sensitive or distressing subject. "
        "Be restrained and respectful. Do NOT make any person the cheerful focal "
        "subject, do not depict victims, distress, or anyone who could be read as "
        "a victim, and do not use bright or celebratory styling. "
        "Prefer a conceptual, understated scene — architecture, empty spaces, "
        "objects, institutional or infrastructural context — with neutral, even "
        "lighting and a calm, serious mood. "
        "Include no text, logos, lettering or brand marks. "
        "Note: if the article refers to AI or language 'models', this means LLM/AI "
        "models, not fashion or photo models.\n\n"
        f"Article title: {title}\n"
        f"Article text:\n{article_text[:1000]}\n\n"
        "Respond with only valid JSON, no markdown fences."
    )
    raw = _call_claude(instruction, timeout=60)
    try:
        data = _extract_json(raw)
        if isinstance(data, dict) and data.get("prompt"):
            return data["prompt"].rstrip() + _SENSITIVE_PROMPT_SUFFIX
        raise ValueError("geen 'prompt'-veld")
    except Exception:
        logger.warning("Claude returned non-JSON for sensitive image prompt, using raw text")
        return raw.rstrip() + _SENSITIVE_PROMPT_SUFFIX


def generate_image_prompt(title: str, article_text: str) -> tuple[str, dict, bool]:
    """Ask Claude for an image prompt.

    Pre:  claude CLI is available; title is non-empty
    Post: returns (prompt_text, persoonsvariant, is_sensitive). Bij een gevoelig
          onderwerp is de variant een lege dict: er wordt dan geen persoon
          voorgeschreven, en de teller in image_distribution.json blijft
          ongemoeid. mailer laat de 'Beeld-variant'-regel dan weg (falsy dict).
    """
    if is_sensitive_topic(title, article_text):
        logger.info("Gevoelig onderwerp gedetecteerd — ingetogen beeldstijl voor: %s", title)
        return _build_sensitive_image_prompt(title, article_text), {}, True

    variant = generate_person_variant()
    person_instruction = build_person_instruction(variant)

    instruction = (
        "Return a single JSON field:\n"
        "\"prompt\": A 2-3 sentence English prompt for a photorealistic AI image "
        "matching this tech news article. Use bright lighting and an optimistic "
        "mood. Avoid dark backgrounds. Choose light, modern, realistic environments "
        "such as daylit offices, meeting rooms, or labs with crisp interfaces. "
        "Convey the brand identity through color palette, product design, "
        "materials, or scene context, without including any text, logos, "
        "or lettering. Note: if the article refers to AI or language 'models', "
        "this means LLM/AI models, not fashion or photo models — do not let this "
        "influence how any people in the image are styled. "
        f"{person_instruction}\n\n"
        f"Article title: {title}\n"
        f"Article text:\n{article_text[:1000]}\n\n"
        "Respond with only valid JSON, no markdown fences."
    )
    raw = _call_claude(instruction, timeout=60)
    try:
        # Zie generate_editorial_image_prompt: json.loads() struikelt over
        # ```json-fences en stuurde dan de ruwe tekst als prompt naar FAL.ai.
        data = _extract_json(raw)
        if isinstance(data, dict) and data.get("prompt"):
            return _enforce_person_in_prompt(data["prompt"], variant), variant, False
        raise ValueError("geen 'prompt'-veld")
    except Exception:
        logger.warning("Claude returned non-JSON for image prompt, using raw text")
        return _enforce_person_in_prompt(raw, variant), variant, False


# Vaste beeldtaal voor editorials, door de redactie vastgesteld. Alleen het
# thema wisselt per stuk; de rest van de vorm staat bewust vast zodat editorials
# als serie herkenbaar zijn en niet per stuk een andere stijl krijgen.
# De uitsluitingen achteraan stonden vroeger in _EDITORIAL_NEGATIVE_PROMPT en
# deden dus niets — flux/dev kent het veld niet. Nu onderdeel van de template.
_EDITORIAL_IMAGE_TEMPLATE = (
    "Editorial photograph, conceptual composition representing {thema}, "
    "dramatic side lighting, high contrast, moody atmosphere, shallow depth of field, "
    "shot on 35mm film, photojournalistic style, muted color palette with one bold accent color, "
    "subtle tension in composition, no text, no logos, no watermarks, "
    "professional editorial photography, 4k detail, realistic textures. "
    "Not a cartoon, not an illustration, not a 3d render; no oversaturated "
    "colours, no distorted hands or extra limbs, nothing low-quality or blurry."
)


def generate_editorial_image_prompt(title: str, editorial_text: str) -> str:
    """Vult het vaste editorial-beeldsjabloon met het thema van dít stuk.

    Bewust anders dan generate_image_prompt(): daar schrijft Claude de hele
    prompt, hier alleen het onderwerp. De beeldtaal (zijlicht, hoog contrast,
    35mm, gedempt palet met één accentkleur) ligt vast, zodat editorials als
    serie herkenbaar blijven — en zodat de "bright, optimistic" toon van
    de nieuwsprompt, die een kritisch stuk ondermijnt, hier niet kan terugkomen.

    Pre:  claude CLI is beschikbaar; title is niet-leeg
    Post: ingevulde prompt; bij een onbruikbaar antwoord valt het thema terug op
          de titel, zodat er altijd een werkbare prompt uitkomt
    """
    instruction = (
        "Return a single JSON field:\n"
        "\"thema\": a short English noun phrase (maximum 12 words) naming the "
        "CONCEPT this Dutch opinion piece argues about — not a description of a "
        "photograph, not a sentence, and not the news event itself. It will be "
        "inserted into a fixed image prompt after the words 'conceptual "
        "composition representing'. "
        "Name what is at stake: the people, workplaces, institutions or "
        "infrastructure the argument is about. Avoid brand names, product names "
        "and stock-photo clichés (handshakes, glowing holograms, robot hands, "
        "people pointing at charts). "
        "Note: if the piece refers to AI or language 'models', this means LLM/AI "
        "models, not fashion or photo models.\n\n"
        f"Editorial title: {title}\n"
        f"Editorial text:\n{editorial_text[:1500]}\n\n"
        "Respond with only valid JSON, no markdown fences."
    )
    thema = ""
    try:
        # _extract_json i.p.v. json.loads: Claude verpakt het antwoord regelmatig
        # in ```json-fences, en dan zou de ruwe tekst mét fences in de prompt
        # belanden.
        data = _extract_json(_call_claude(instruction, timeout=60))
        if isinstance(data, dict):
            thema = str(data.get("thema", "")).strip()
    except Exception as exc:
        logger.warning("Thema voor editorial-beeld bepalen mislukt: %s", exc)

    if not thema:
        logger.warning("Geen bruikbaar thema — terugval op de titel")
        thema = title

    return _EDITORIAL_IMAGE_TEMPLATE.format(thema=thema)


def generate_image_for_editorial(
    title: str,
    editorial_text: str,
    dest_path: str,
    dry_run: bool = False,
) -> Optional[str]:
    """Genereer het beeld bij een editorial: prompt via Claude, beeld via FAL.ai.

    Pre:  title is niet-leeg; dest_path is schrijfbaar
    Post: dest_path bij succes, None bij elke fout of in dry-run. Krijgt hetzelfde
          AI-label als nieuwsbeelden — een editorial is niet minder AI-gegenereerd.
    """
    if dry_run:
        logger.info("[DRY RUN] Zou editorial-afbeelding genereren voor: %s", title)
        return None
    try:
        logger.info("Editorial-beeldprompt genereren via Claude voor: %s", title)
        image_prompt = generate_editorial_image_prompt(title, editorial_text)
        logger.info("Gegenereerde editorial-prompt: %s", image_prompt)

        result = generate_fal_image(image_prompt, dest_path)
        if result:
            add_ai_label(dest_path)
        return result
    except Exception as exc:
        logger.error("Editorial-afbeelding genereren mislukt voor '%s': %s", title, exc)
        return None


def fetch_brand_logo(brand_domain: str, dest_path: str) -> str | None:
    """Fetch a brand logo and save it to dest_path.

    Tries DuckDuckGo icons, then Google favicon service as fallback.
    Pre:  brand_domain is a valid domain string (e.g. 'apple.com')
    Post: returns dest_path on success, None if logo not found or fetch fails
    """
    sources = [
        f"https://www.google.com/s2/favicons?domain={brand_domain}&sz=128",
        f"https://icons.duckduckgo.com/ip3/{brand_domain}.ico",
    ]
    for url in sources:
        try:
            resp = requests.get(url, timeout=10, allow_redirects=True)
            content_type = resp.headers.get("content-type", "")
            if resp.status_code == 200 and content_type.startswith("image"):
                with open(dest_path, "wb") as f:
                    f.write(resp.content)
                logger.info("Brand logo fetched for %s from %s", brand_domain, url)
                return dest_path
        except Exception as exc:
            logger.warning("Logo fetch failed for %s (%s): %s", brand_domain, url, exc)
    logger.warning("No logo found for %s", brand_domain)
    return None


def composite_logo(image_path: str, logo_path: str) -> None:
    """Overlay a brand logo in the bottom-right corner of an image, in-place.

    Pre:  image_path and logo_path are valid image files
    Post: image_path overwritten with logo composited at bottom-right;
          logo is 80px tall with a white rounded background; silent on failure
    """
    try:
        base = Image.open(image_path).convert("RGBA")
        logo = Image.open(logo_path).convert("RGBA")

        # Resize logo to 80px tall, preserve aspect ratio
        logo_h = 80
        logo_w = int(logo.width * logo_h / logo.height)
        logo = logo.resize((logo_w, logo_h), Image.LANCZOS)

        # White pill background with padding
        pad = 12
        bg_w, bg_h = logo_w + pad * 2, logo_h + pad * 2
        bg = Image.new("RGBA", (bg_w, bg_h), (255, 255, 255, 0))
        mask = Image.new("L", (bg_w, bg_h), 0)
        ImageDraw.Draw(mask).rounded_rectangle(
            [0, 0, bg_w - 1, bg_h - 1], radius=12, fill=220
        )
        bg.putalpha(mask)
        bg.paste(logo, (pad, pad), logo)

        # Bottom-right with 16px margin
        margin = 16
        x = base.width - bg_w - margin
        y = base.height - bg_h - margin
        base.paste(bg, (x, y), bg)

        base.convert("RGB").save(image_path, "JPEG", quality=92)
        logger.info("Brand logo composited onto image")
    except Exception as exc:
        logger.warning("Logo compositing failed: %s", exc)


def add_ai_label(image_path: str) -> None:
    """Overlay a small 'AI gegenereerd' label in the bottom-left corner, in-place.

    Pre:  image_path is a valid image file
    Post: image_path overwritten with label at bottom-left; silent on failure
    """
    try:
        base = Image.open(image_path).convert("RGBA")

        label = "AI gegenereerd"
        font_size = 16
        try:
            font = ImageFont.truetype(
                "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", font_size
            )
        except Exception:
            font = ImageFont.load_default()

        draw_tmp = ImageDraw.Draw(base)
        bbox = draw_tmp.textbbox((0, 0), label, font=font)
        text_w = bbox[2] - bbox[0]
        text_h = bbox[3] - bbox[1]

        pad_x, pad_y = 10, 6
        bg_w = text_w + pad_x * 2
        bg_h = text_h + pad_y * 2
        margin = 16
        x = margin
        y = base.height - bg_h - margin

        # Light grey semi-transparent pill background
        overlay = Image.new("RGBA", base.size, (0, 0, 0, 0))
        ImageDraw.Draw(overlay).rounded_rectangle(
            [x, y, x + bg_w, y + bg_h],
            radius=6,
            fill=(220, 220, 220, 210),
        )
        base = Image.alpha_composite(base, overlay)

        ImageDraw.Draw(base).text(
            (x + pad_x, y + pad_y - bbox[1]),
            label,
            font=font,
            fill=(80, 80, 80, 255),
        )

        base.convert("RGB").save(image_path, "JPEG", quality=92)
        logger.info("AI-label toegevoegd aan afbeelding")
    except Exception as exc:
        logger.warning("AI-label toevoegen mislukt: %s", exc)


def generate_fal_image(prompt: str, dest_path: str) -> Optional[str]:
    """Generate an image via FAL.ai flux/dev and save it to dest_path.

    Er is bewust géén negative_prompt-parameter: fal-ai/flux/dev kent dat veld
    niet (het staat niet in hun OpenAPI-schema — flux dev is guidance-distilled
    en heeft geen CFG-negative), dus alles wat we meestuurden werd weggegooid.
    Uitsluitingen horen in de positieve prompt; zie _SENSITIVE_PROMPT_SUFFIX en
    de "no text, no logos"-formuleringen in de promptvarianten.

    Pre:  FAL_API_KEY is set; prompt is non-empty; dest_path is writable
    Post: JPEG written to dest_path and path returned; None on any failure
    """
    if not FAL_API_KEY:
        logger.error("FAL_API_KEY niet geconfigureerd — kan geen afbeelding genereren")
        return None
    try:
        resp = requests.post(
            FAL_ENDPOINT,
            headers={
                "Authorization": f"Key {FAL_API_KEY}",
                "Content-Type": "application/json",
            },
            json={
                "prompt": prompt,
                "image_size": "landscape_16_9",
                "num_images": 1,
                "enable_safety_checker": True,
            },
            timeout=FAL_IMAGE_TIMEOUT,
        )
        resp.raise_for_status()
        result = resp.json()
        image_url = result["images"][0]["url"]

        img_resp = requests.get(image_url, timeout=REQUEST_TIMEOUT)
        img_resp.raise_for_status()
        with open(dest_path, "wb") as f:
            f.write(img_resp.content)

        logger.info("FAL.ai afbeelding gegenereerd en opgeslagen: %s", dest_path)
        return dest_path

    except Exception as exc:
        logger.error("FAL.ai afbeelding genereren mislukt: %s", exc)
        return None


def generate_image_for_article(
    title: str,
    article_text: str,
    dest_path: str,
    dry_run: bool = False,
    variant_out: Optional[dict] = None,
    prompt_out: Optional[dict] = None,
) -> Optional[str]:
    """Generate an AI image for one article: prompt via Claude, image via FAL.ai.

    Pre:  title is non-empty; dest_path is writable
    Post: returns dest_path on success, None on any failure or in dry-run.
          Als variant_out is meegegeven, wordt die gevuld met de gekozen
          persoonsvariant (gender/ethnicity/age). Als prompt_out is meegegeven,
          wordt die gevuld met de gegenereerde FAL.ai-prompt onder key 'prompt'.
    """
    if dry_run:
        logger.info("[DRY RUN] Zou FAL.ai afbeelding genereren voor: %s", title)
        return None
    try:
        logger.info("Afbeeldingsprompt genereren via Claude voor: %s", title)
        # De sensitive-vlag stuurt hier niets meer aan: de ingetogen beeldtaal
        # zit sinds _SENSITIVE_PROMPT_SUFFIX in de prompt zelf, waar flux hem
        # ook echt leest.
        image_prompt, variant, _sensitive = generate_image_prompt(title, article_text)
        if variant_out is not None:
            variant_out.update(variant)
        if prompt_out is not None:
            prompt_out["prompt"] = image_prompt
        logger.info("Gegenereerde prompt: %s", image_prompt)

        result = generate_fal_image(image_prompt, dest_path)
        if result:
            add_ai_label(dest_path)
        return result
    except Exception as exc:
        logger.error("Afbeelding genereren mislukt voor '%s': %s", title, exc)
        return None
