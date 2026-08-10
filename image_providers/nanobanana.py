"""
Nano Banana 2 (Gemini 3.1 Flash Image) als beeldprovider, via de Interactions
API van de Gemini Developer API.

Bewust de Interactions API (`/v1beta/interactions`) en niet het oudere
`models/<model>:generateContent`: die laatste is voor beeldgeneratie inmiddels
als legacy gemarkeerd, en alleen de Interactions API neemt de beeldverhouding
rechtstreeks aan (`response_format.aspect_ratio`). Bij generateContent zou de
16:9 uit ImageOptions niet af te dwingen zijn zonder hem in de prompt te
smokkelen, en dat doet flux/dev-gewijs precies wat we niet willen.
"""
import base64
import logging
from typing import Optional

import requests

from config import GEMINI_API_KEY, GEMINI_IMAGE_MODEL

from .base import ImageOptions, ImageProvider, ImageProviderError

logger = logging.getLogger(__name__)

GEMINI_ENDPOINT = "https://generativelanguage.googleapis.com/v1beta/interactions"
GEMINI_IMAGE_TIMEOUT = 120
# 1K/2K/4K — 2K is ruim genoeg voor een featured image en een stuk goedkoper
# dan 4K. Let op de hoofdletter K; de API weigert "2k".
GEMINI_IMAGE_SIZE = "2K"


def _error_message(resp) -> str:
    """Haal de leesbare reden uit een foutantwoord van de Gemini API.

    De reden staat in `error.message` en is lang (met doc-links erin); botweg
    afkappen op N tekens sneed uitgerekend de bruikbare staart eraf — bij een
    429 is dat `limit: 0, model: ...`, precies wat je moet weten. Daarom eerst
    het message-veld eruit vissen en pas dat afkappen, met de ruwe body als
    terugval wanneer het antwoord geen JSON is.
    """
    try:
        message = resp.json()["error"]["message"]
    except Exception:
        message = resp.text
    return " ".join(str(message).split())[:500]


def _extract_image_b64(payload: dict) -> Optional[str]:
    """Vis de base64-beeldbytes uit een Interactions-antwoord.

    De officiële voorbeelden gebruiken `interaction.output_image.data`, maar dat
    is een gemaksproperty van de SDK — over de draad staat het beeld in
    `steps[].content[]` bij het blok met `type == "image"`. We lopen dus die
    structuur af, en accepteren `output_image` als de API hem wél meestuurt.

    Post: base64-string, of None als er geen beeldblok in het antwoord zit
          (bijvoorbeeld wanneer het model alleen tekst teruggaf omdat het de
          prompt weigerde)
    """
    output_image = payload.get("output_image")
    if isinstance(output_image, dict) and output_image.get("data"):
        return output_image["data"]

    for step in payload.get("steps") or []:
        if not isinstance(step, dict):
            continue
        for block in step.get("content") or []:
            if isinstance(block, dict) and block.get("type") == "image" and block.get("data"):
                return block["data"]
    return None


class NanoBananaImageProvider(ImageProvider):
    """Beeld via Nano Banana 2 (Gemini 3.1 Flash Image)."""

    name = "nanobanana"
    api_key_env = "GEMINI_API_KEY"

    def __init__(self, api_key: Optional[str] = None, model: Optional[str] = None) -> None:
        self.api_key = GEMINI_API_KEY if api_key is None else api_key
        self.model = model or GEMINI_IMAGE_MODEL
        if not self.api_key:
            raise ImageProviderError(
                "IMAGE_PROVIDER=nanobanana, maar GEMINI_API_KEY is niet gezet. "
                "Zet GEMINI_API_KEY in .env (aanmaken via "
                "https://aistudio.google.com/apikey), of kies een andere "
                "provider met IMAGE_PROVIDER=fal."
            )

    def generate_image(
        self,
        prompt: str,
        dest_path: str,
        options: Optional[ImageOptions] = None,
    ) -> Optional[str]:
        """Genereer via Gemini en sla op als bestand. Zie ImageProvider.

        Anders dan FAL.ai levert Gemini de bytes direct base64-gecodeerd in het
        antwoord — er is dus geen tweede request naar een image-URL nodig.
        """
        options = options or ImageOptions()
        try:
            resp = requests.post(
                GEMINI_ENDPOINT,
                headers={
                    "x-goog-api-key": self.api_key,
                    "Content-Type": "application/json",
                },
                json={
                    "model": self.model,
                    "input": [{"type": "text", "text": prompt}],
                    "response_format": {
                        "type": "image",
                        "mime_type": "image/jpeg",
                        "aspect_ratio": options.aspect_ratio,
                        "image_size": GEMINI_IMAGE_SIZE,
                    },
                },
                timeout=GEMINI_IMAGE_TIMEOUT,
            )
            if resp.status_code >= 400:
                raise RuntimeError(f"HTTP {resp.status_code}: {_error_message(resp)}")

            payload = resp.json()
            image_b64 = _extract_image_b64(payload)
            if not image_b64:
                raise RuntimeError(
                    f"geen beeld in het antwoord (status={payload.get('status')!r})"
                )

            with open(dest_path, "wb") as f:
                f.write(base64.b64decode(image_b64))

            logger.info(
                "Nano Banana 2 (%s) afbeelding gegenereerd en opgeslagen: %s",
                self.model,
                dest_path,
            )
            return dest_path

        except Exception as exc:
            logger.error("Nano Banana 2 afbeelding genereren mislukt: %s", exc)
            return None
