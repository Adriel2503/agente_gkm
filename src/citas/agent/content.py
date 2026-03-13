"""
Schema de respuesta estructurada y parsing de contenido multimodal.
"""

import re

from pydantic import BaseModel, Field


class CitaStructuredResponse(BaseModel):
    """Schema para response_format del agente. Siempre devuelve reply; url opcional."""

    reply: str = Field(
        description="Respuesta al cliente que escribe. Nunca dejar vacío."
    )
    url: str | None = Field(
        default=None,
        description="URL opcional de archivo adjunto. null si no aplica.",
    )


_IMAGE_URL_RE = re.compile(
    r"https?://\S+\.(?:jpg|jpeg|png|gif|webp)(?:\?\S*)?",
    re.IGNORECASE,
)
_MAX_IMAGES = 10  # límite de OpenAI Vision


def _build_content(message: str) -> str | list[dict]:
    """
    Devuelve string si no hay URLs de imagen (Caso 1),
    o lista de bloques OpenAI Vision si las hay (Casos 2-5).

    Casos:
      1. Solo texto         -> str
      2. Solo 1 URL         -> [{image_url}]
      3. Texto + 1 URL      -> [{text}, {image_url}]
      4. Solo N URLs        -> [{image_url}, ...]
      5. Texto + N URLs     -> [{text}, {image_url}, ...]
    """
    urls = _IMAGE_URL_RE.findall(message)
    if not urls:
        return message  # Caso 1: sin cambio

    urls = urls[:_MAX_IMAGES]
    text = _IMAGE_URL_RE.sub("", message).strip()

    blocks: list[dict] = []
    if text:
        blocks.append({"type": "text", "text": text})
    for url in urls:
        blocks.append({"type": "image_url", "image_url": {"url": url}})
    return blocks
