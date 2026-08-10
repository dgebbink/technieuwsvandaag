"""
Gedeelde abstractie voor beeldgeneratie-providers.

De pijplijn kent maar één werkwoord — "maak een beeld bij deze prompt" — en mag
niet weten wélke dienst dat doet. Alles wat per provider verschilt (endpoint,
auth-header, payload-vorm, hoe de bytes terugkomen) zit achter deze interface.
"""
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import ClassVar, Optional


class ImageProviderError(RuntimeError):
    """Configuratiefout in de beeldprovider (onbekende naam, ontbrekende key).

    Bewust een aparte fout en geen stille terugval: een verkeerd gezette
    IMAGE_PROVIDER of een vergeten API-key moet luid stuklopen, niet ongemerkt
    op de andere provider (of op géén beeld) uitkomen.
    """


@dataclass(frozen=True)
class ImageOptions:
    """Providerneutrale opties bij een beeldopdracht.

    `aspect_ratio` staat als verhouding ("16:9") en niet als providerterm, omdat
    FAL.ai en Gemini daar elk een eigen woord voor hebben — de vertaling naar
    `landscape_16_9` respectievelijk `16:9` is het werk van de provider zelf.
    """

    aspect_ratio: str = "16:9"


class ImageProvider(ABC):
    """Eén methode: prompt in, bestandspad uit."""

    name: ClassVar[str]
    #: Naam van de env-var met de API-key, voor de foutmelding bij ontbreken.
    api_key_env: ClassVar[str]
    #: Providerspecifieke aanvulling die achter elke prompt wordt geplakt, voor
    #: eigenaardigheden van dít model. Bewust hier en niet in image_generator:
    #: alleen de provider weet wat zijn model verkeerd doet, en zo blijven de
    #: prompts van de andere providers letterlijk ongewijzigd.
    prompt_suffix: ClassVar[str] = ""

    @abstractmethod
    def generate_image(
        self,
        prompt: str,
        dest_path: str,
        options: Optional[ImageOptions] = None,
    ) -> Optional[str]:
        """Genereer een beeld bij `prompt` en schrijf het naar `dest_path`.

        Pre:  de API-key van deze provider is aanwezig (gecontroleerd bij het
              aanmaken van de provider); prompt is niet-leeg; dest_path is
              schrijfbaar
        Post: dest_path bij succes, None bij elke fout tijdens het genereren.
              Een mislukte generatie mag de pijplijn niet stoppen — het artikel
              gaat dan zonder beeld verder, precies zoals voorheen.
        """
