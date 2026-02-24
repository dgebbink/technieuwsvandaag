"""
Afbeeldingen genereren via Claude (prompt) + FAL.ai flux/dev (beeld).
Wordt gebruikt wanneer IMAGE_STRATEGY=generate is ingesteld.
"""
import logging
from typing import Optional

import anthropic
import requests

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
    """Ask Claude for a 2-sentence English photorealistic image prompt.

    Pre:  ANTHROPIC_API_KEY is set; title is non-empty
    Post: returns a non-empty English prompt string
    """
    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    prompt = (
        "Genereer een Engelse prompt voor een fotorealistische AI-afbeelding "
        "die past bij dit tech-nieuwsartikel. "
        "Geen tekst in de afbeelding. "
        "Geen logo's of herkenbare merken. "
        "Stijl: professionele persfotografie. "
        "Max 2 zinnen.\n\n"
        f"Artikel titel: {title}\n\n"
        f"Artikel tekst:\n{article_text[:1000]}"
    )
    message = client.messages.create(
        model=MODEL,
        max_tokens=150,
        messages=[{"role": "user", "content": prompt}],
    )
    return message.content[0].text.strip()  # type: ignore[union-attr]


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
