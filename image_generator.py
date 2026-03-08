"""
Afbeeldingen genereren via Claude (prompt) + FAL.ai flux/dev (beeld).
Wordt gebruikt wanneer IMAGE_STRATEGY=generate is ingesteld.
"""
import json
import logging
from typing import Optional

import anthropic
import requests
from PIL import Image, ImageDraw

from config import ANTHROPIC_API_KEY, FAL_API_KEY, FAL_CREDIT_THRESHOLD, REQUEST_TIMEOUT

logger = logging.getLogger(__name__)

MODEL = "claude-sonnet-4-6"
FAL_ENDPOINT = "https://fal.run/fal-ai/flux/dev"
FAL_BILLING_ENDPOINT = "https://rest.alpha.fal.ai/billing/user"
FAL_IMAGE_TIMEOUT = 120  # FAL.ai genereert in 60-90 seconden


def check_fal_balance() -> Optional[float]:
    """Fetch the current FAL.ai credit balance in dollars.

    Pre:  FAL_API_KEY is set
    Post: returns balance as float, or None on any failure
    """
    if not FAL_API_KEY:
        logger.warning("FAL_API_KEY niet geconfigureerd — balanscontrole overgeslagen")
        return None
    try:
        resp = requests.get(
            FAL_BILLING_ENDPOINT,
            headers={"Authorization": f"Key {FAL_API_KEY}"},
            timeout=REQUEST_TIMEOUT,
        )
        resp.raise_for_status()
        data = resp.json()
        # Mogelijke veldnamen afhankelijk van API-versie
        balance = (
            data.get("balance")
            or data.get("credits")
            or data.get("credit_balance")
        )
        if balance is not None:
            return float(balance)
        logger.warning("FAL.ai balans-API gaf onverwacht formaat: %s", data)
        return None
    except Exception as exc:
        logger.error("FAL.ai balanscontrole mislukt: %s", exc)
        return None


def is_fal_balance_low() -> bool:
    """Return True when the FAL.ai balance is below FAL_CREDIT_THRESHOLD.

    Pre:  FAL_API_KEY and FAL_CREDIT_THRESHOLD are configured
    Post: returns False if balance cannot be determined
    """
    if FAL_CREDIT_THRESHOLD <= 0:
        return False
    balance = check_fal_balance()
    if balance is None:
        return False
    if balance < FAL_CREDIT_THRESHOLD:
        logger.warning(
            "FAL.ai tegoed laag: $%.4f (drempel: $%.2f)", balance, FAL_CREDIT_THRESHOLD
        )
        return True
    logger.info("FAL.ai tegoed: $%.4f (drempel: $%.2f)", balance, FAL_CREDIT_THRESHOLD)
    return False


def generate_image_prompt(title: str, article_text: str) -> str:
    """Ask Claude for an image prompt.

    Pre:  ANTHROPIC_API_KEY is set; title is non-empty
    Post: returns prompt_text
    """
    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    instruction = (
        "Return a single JSON field:\n"
        "\"prompt\": A 2-sentence English prompt for a photorealistic AI image "
        "matching this tech news article. Use bright, warm lighting and an optimistic "
        "mood. Avoid dark backgrounds. Choose light, modern environments: daylit "
        "offices, crisp interfaces, futuristic but accessible settings. "
        "Subtly use actual logos, text, lettering, or brand names in the image as if "
        "it were their office, building, outfit, or similar. Convey the brand identity "
        "through the color palette, product design, or the associated scene. "
        "Do not create a logo yourself.\n\n"
        f"Article title: {title}\n"
        f"Article text:\n{article_text[:1000]}\n\n"
        "Respond with only valid JSON, no markdown fences."
    )
    message = client.messages.create(
        model=MODEL,
        max_tokens=200,
        messages=[{"role": "user", "content": instruction}],
    )
    raw = message.content[0].text.strip()  # type: ignore[union-attr]
    try:
        data = json.loads(raw)
        return data.get("prompt", raw)
    except Exception:
        logger.warning("Claude returned non-JSON for image prompt, using raw text")
        return raw


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
) -> Optional[str]:
    """Generate an AI image for one article: prompt via Claude, image via FAL.ai.

    Pre:  title is non-empty; dest_path is writable
    Post: returns dest_path on success, None on any failure or in dry-run
    """
    if dry_run:
        logger.info("[DRY RUN] Zou FAL.ai afbeelding genereren voor: %s", title)
        return None
    try:
        logger.info("Afbeeldingsprompt genereren via Claude voor: %s", title)
        image_prompt = generate_image_prompt(title, article_text)
        logger.info("Gegenereerde prompt: %s", image_prompt)

        return generate_fal_image(image_prompt, dest_path)
    except Exception as exc:
        logger.error("Afbeelding genereren mislukt voor '%s': %s", title, exc)
        return None
