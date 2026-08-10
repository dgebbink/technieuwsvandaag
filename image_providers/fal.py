"""
FAL.ai flux/dev als beeldprovider — de oorspronkelijke implementatie, ongewijzigd
op de verhuizing naar deze klasse na.
"""
import logging
from typing import Optional

import requests

from config import FAL_API_KEY, REQUEST_TIMEOUT

from .base import ImageOptions, ImageProvider, ImageProviderError

logger = logging.getLogger(__name__)

FAL_ENDPOINT = "https://fal.run/fal-ai/flux/dev"
FAL_IMAGE_TIMEOUT = 120  # FAL.ai genereert in 60-90 seconden

# FAL.ai kent geen vrije verhouding maar een vaste set namen.
_IMAGE_SIZES = {
    "16:9": "landscape_16_9",
    "4:3": "landscape_4_3",
    "1:1": "square_hd",
    "3:4": "portrait_4_3",
    "9:16": "portrait_16_9",
}


class FalImageProvider(ImageProvider):
    """Beeld via fal-ai/flux/dev.

    Er is bewust géén negative_prompt-parameter: fal-ai/flux/dev kent dat veld
    niet (het staat niet in hun OpenAPI-schema — flux dev is guidance-distilled
    en heeft geen CFG-negative), dus alles wat we meestuurden werd weggegooid.
    Uitsluitingen horen in de positieve prompt; zie _SENSITIVE_PROMPT_SUFFIX en
    de "no text"-formuleringen in de promptvarianten in image_generator.
    """

    name = "fal"
    api_key_env = "FAL_API_KEY"

    def __init__(self, api_key: Optional[str] = None) -> None:
        self.api_key = FAL_API_KEY if api_key is None else api_key
        if not self.api_key:
            raise ImageProviderError(
                "IMAGE_PROVIDER=fal, maar FAL_API_KEY is niet gezet. "
                "Zet FAL_API_KEY in .env, of kies een andere provider met "
                "IMAGE_PROVIDER=nanobanana."
            )

    def generate_image(
        self,
        prompt: str,
        dest_path: str,
        options: Optional[ImageOptions] = None,
    ) -> Optional[str]:
        """Genereer via FAL.ai en sla op als bestand. Zie ImageProvider."""
        options = options or ImageOptions()
        image_size = _IMAGE_SIZES.get(options.aspect_ratio, "landscape_16_9")
        try:
            resp = requests.post(
                FAL_ENDPOINT,
                headers={
                    "Authorization": f"Key {self.api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "prompt": prompt,
                    "image_size": image_size,
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
