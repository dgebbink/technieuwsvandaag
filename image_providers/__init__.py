"""
Beeldgeneratie-providers, gekozen via IMAGE_PROVIDER in .env.

    IMAGE_PROVIDER=webgemini    → gratis via de browser      (Selenium + sessie)
    IMAGE_PROVIDER=nanobanana   → Nano Banana 2 / Gemini     (GEMINI_API_KEY)
    IMAGE_PROVIDER=fal          → FAL.ai flux/dev            (FAL_API_KEY)

`IMAGE_FALLBACK_PROVIDER` wijst de provider aan die het overneemt als de eerste
geen beeld oplevert (bijvoorbeeld doordat het Gemini-budget op is). Twee soorten
falen worden daarbij uit elkaar gehouden:

- **Configuratiefout** (onbekende naam, ontbrekende API-key van de *primaire*
  provider): blijft luid stuk via `ImageProviderError`. Terugval zou hier de
  fout verbergen die je juist wilt zien, en dan draait de site maandenlang op de
  verkeerde dienst zonder dat iemand het merkt.
- **Runtime-fout** (quota op, time-out, geweigerde prompt): dán pas terugval,
  met een `WARNING` in de log zodat het zichtbaar blijft dat het gebeurde.
"""
import logging
from functools import lru_cache
from typing import Optional

from config import IMAGE_FALLBACK_PROVIDER, IMAGE_PROVIDER

from .base import ImageOptions, ImageProvider, ImageProviderError
from .fal import FalImageProvider
from .nanobanana import NanoBananaImageProvider
from .webgemini import WebGeminiImageProvider

logger = logging.getLogger(__name__)

_PROVIDERS: dict[str, type[ImageProvider]] = {
    FalImageProvider.name: FalImageProvider,
    NanoBananaImageProvider.name: NanoBananaImageProvider,
    WebGeminiImageProvider.name: WebGeminiImageProvider,
}

__all__ = [
    "FalImageProvider",
    "ImageOptions",
    "ImageProvider",
    "ImageProviderError",
    "NanoBananaImageProvider",
    "WebGeminiImageProvider",
    "get_fallback_provider",
    "get_fallback_providers",
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


@lru_cache(maxsize=None)
def get_fallback_providers(primary_name: str) -> tuple[ImageProvider, ...]:
    """De keten die het overneemt als `primary_name` geen beeld oplevert.

    `IMAGE_FALLBACK_PROVIDER` is komma-gescheiden en wordt op volgorde
    geprobeerd, zodat je van gratis naar duur kunt aflopen
    (`webgemini` → `nanobanana` → `fal`).

    Pre:  primary_name is de naam van de actieve provider
    Post: tuple met bruikbare providers, in volgorde; leeg als terugval uit
          staat of niets bruikbaar is. De primaire provider en dubbelen worden
          overgeslagen. Raises nooit: een onbruikbare schakel mag de gewone weg
          niet blokkeren — die wordt overgeslagen met een waarschuwing.
    """
    chain: list[ImageProvider] = []
    seen = {primary_name}
    for raw in (IMAGE_FALLBACK_PROVIDER or "").split(","):
        key = raw.strip().lower()
        if not key or key in seen:
            continue
        seen.add(key)
        try:
            chain.append(get_image_provider(key))
        except ImageProviderError as exc:
            logger.warning("Terugvalprovider %r overgeslagen: %s", key, exc)
    return tuple(chain)


def get_fallback_provider(primary_name: str) -> Optional[ImageProvider]:
    """Eerste schakel van de terugvalketen (of None). Voor losse aanroepen."""
    chain = get_fallback_providers(primary_name)
    return chain[0] if chain else None
