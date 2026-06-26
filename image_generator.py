"""
Afbeeldingen genereren via Claude (prompt) + FAL.ai flux/dev (beeld).
Wordt gebruikt wanneer IMAGE_STRATEGY=generate is ingesteld.
"""
import json
import logging
import random
from typing import Optional

import requests
from PIL import Image, ImageDraw, ImageFont

from config import (
    FAL_API_KEY,
    FAL_CREDIT_THRESHOLD,
    REQUEST_TIMEOUT,
    IMAGE_DISTRIBUTION_FILE,
    IMAGE_DISTRIBUTION_TARGETS,
)
from ai_processor import _call_claude

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

    Post: dict met keys gender, ethnicity, body_type, age en hair_description
          (hair_description is None tenzij gender "a woman" is). De teller in
          IMAGE_DISTRIBUTION_FILE is bijgewerkt voor de gekozen opties.
    """
    state = _load_distribution_state()
    targets = IMAGE_DISTRIBUTION_TARGETS

    gender = _pick_balanced("gender", targets["gender"], state)
    ethnicity = _pick_balanced("ethnicity", targets["ethnicity"], state)

    # "Latina" is vrouwelijk gegenderd: forceer vrouw en corrigeer de teller
    # zodat die de werkelijke output blijft weerspiegelen.
    if ethnicity == "Latina" and gender != "a woman":
        state["gender"][gender] -= 1
        state["gender"]["a woman"] = state["gender"].get("a woman", 0) + 1
        gender = "a woman"

    body_type = _pick_balanced("body_type", targets["body_type"], state)

    age_bucket = _pick_balanced("age_bucket", targets["age_bucket"], state)
    low, high = (int(part) for part in age_bucket.split("-"))
    age = random.randint(low, high)

    hair_description = None
    if gender == "a woman":
        hair_description = _pick_balanced("hair", targets["hair"], state)

    _save_distribution_state(state)

    variant = {
        "gender": gender,
        "ethnicity": ethnicity,
        "body_type": body_type,
        "age": age,
        "hair_description": hair_description,
    }
    logger.info("Beeld-persoonsvariant gekozen: %s", describe_variant(variant))
    return variant


def describe_variant(variant: dict) -> str:
    """Vat de gekozen persoonsvariant samen als leesbare regel (log + mail)."""
    parts = [
        variant["gender"],
        variant["ethnicity"],
        f"~{variant['age']} jr",
        variant["body_type"],
    ]
    if variant.get("hair_description"):
        parts.append(variant["hair_description"])
    return " · ".join(parts)


def generate_image_prompt(title: str, article_text: str) -> tuple[str, dict]:
    """Ask Claude for an image prompt.

    Pre:  claude CLI is available; title is non-empty
    Post: returns (prompt_text, gekozen persoonsvariant)
    """
    variant = generate_person_variant()
    hair_part = (
        ", " + variant["hair_description"] if variant["hair_description"] else ""
    )
    person_instruction = (
        "If a person appears in the scene, show {gender}, {ethnicity}, around "
        "{age} years old, with {body_type}{hair_part}, in an active, central "
        "role. Use natural skin texture and realistic proportions."
    ).format(
        gender=variant["gender"],
        ethnicity=variant["ethnicity"],
        age=variant["age"],
        body_type=variant["body_type"],
        hair_part=hair_part,
    )

    instruction = (
        "Return a single JSON field:\n"
        "\"prompt\": A 2-sentence English prompt for a photorealistic AI image "
        "matching this tech news article. Use bright, warm lighting and an optimistic "
        "mood. Avoid dark backgrounds. Choose light, modern environments: daylit "
        "offices, crisp interfaces, futuristic but accessible settings. "
        "Convey the brand identity through the color palette, product design, "
        "materials, or the associated scene, without including any text, logos, "
        "or lettering. "
        f"{person_instruction}\n\n"
        f"Article title: {title}\n"
        f"Article text:\n{article_text[:1000]}\n\n"
        "Respond with only valid JSON, no markdown fences."
    )
    raw = _call_claude(instruction, timeout=60)
    try:
        data = json.loads(raw)
        return data.get("prompt", raw), variant
    except Exception:
        logger.warning("Claude returned non-JSON for image prompt, using raw text")
        return raw, variant


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
                "negative_prompt": (
                    "logo, text, letters, words, brand name, watermark, "
                    "typography, signage, written text, label, caption"
                ),
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
) -> Optional[str]:
    """Generate an AI image for one article: prompt via Claude, image via FAL.ai.

    Pre:  title is non-empty; dest_path is writable
    Post: returns dest_path on success, None on any failure or in dry-run.
          Als variant_out is meegegeven, wordt die gevuld met de gekozen
          persoonsvariant (gender/ethnicity/body_type/age/hair_description).
    """
    if dry_run:
        logger.info("[DRY RUN] Zou FAL.ai afbeelding genereren voor: %s", title)
        return None
    try:
        logger.info("Afbeeldingsprompt genereren via Claude voor: %s", title)
        image_prompt, variant = generate_image_prompt(title, article_text)
        if variant_out is not None:
            variant_out.update(variant)
        logger.info("Gegenereerde prompt: %s", image_prompt)

        result = generate_fal_image(image_prompt, dest_path)
        if result:
            add_ai_label(dest_path)
        return result
    except Exception as exc:
        logger.error("Afbeelding genereren mislukt voor '%s': %s", title, exc)
        return None
