"""
Beeldgeneratie-providers, gekozen via IMAGE_PROVIDER in .env.

    IMAGE_PROVIDER=fal          → FAL.ai flux/dev            (FAL_API_KEY)
    IMAGE_PROVIDER=nanobanana   → Nano Banana 2 / Gemini     (GEMINI_API_KEY)

Er is bewust geen automatische terugval van de ene provider op de andere: een
stille wissel maakt het onmogelijk om aan de beelden af te lezen welke dienst ze
maakte, en verbergt precies de configuratiefout die je wilt zien.
"""
from functools import lru_cache
from typing import Optional

from config import IMAGE_PROVIDER

from .base import ImageOptions, ImageProvider, ImageProviderError
from .fal import FalImageProvider
from .nanobanana import NanoBananaImageProvider

_PROVIDERS: dict[str, type[ImageProvider]] = {
    FalImageProvider.name: FalImageProvider,
    NanoBananaImageProvider.name: NanoBananaImageProvider,
}

__all__ = [
    "FalImageProvider",
    "ImageOptions",
    "ImageProvider",
    "ImageProviderError",
    "NanoBananaImageProvider",
    "get_image_provider",
]


@lru_cache(maxsize=None)
def get_image_provider(name: Optional[str] = None) -> ImageProvider:
    """Geef de ingestelde provider terug (standaard die uit IMAGE_PROVIDER).

    Gecachet, zodat de keuze één keer per proces wordt gemaakt en gecontroleerd
    in plaats van bij elk beeld opnieuw.

    Pre:  IMAGE_PROVIDER is 'fal' of 'nanobanana'
    Post: een gebruiksklare ImageProvider. Raises ImageProviderError bij een
          onbekende naam of een ontbrekende API-key — nooit een stille terugval.
    """
    key = (name or IMAGE_PROVIDER).strip().lower()
    provider_cls = _PROVIDERS.get(key)
    if provider_cls is None:
        raise ImageProviderError(
            f"Onbekende IMAGE_PROVIDER {key!r}. Geldige waarden: "
            f"{', '.join(sorted(_PROVIDERS))}."
        )
    return provider_cls()
