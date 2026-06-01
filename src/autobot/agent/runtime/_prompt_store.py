"""
Lectura del system prompt desde Redis (publicado por el backend automotriz-api).

Claves (db0, el MISMO Redis que el checkpointer de conversación):
  prompt:ver:{id_empresa} -> versión del prompt (epoch ms como string)
  prompt:txt:{id_empresa} -> texto crudo del prompt (fuente Jinja)

Si REDIS_URL no está configurado, o no hay clave, o Redis falla, las funciones
devuelven el sentinel / None para que el agente caiga al template local
gqm_system.j2 (nunca rompe).
"""

import redis.asyncio as redis_async

from ... import config as app_config
from ...logger import get_logger

logger = get_logger(__name__)

# Sentinel de versión cuando no hay prompt en Redis → el agente usa el .j2 local.
VERSION_FILE_FALLBACK = "file"

_client = None


def _get_client():
    """Cliente Redis async lazy (singleton). None si no hay REDIS_URL configurado."""
    global _client
    if _client is None and app_config.REDIS_URL:
        _client = redis_async.from_url(app_config.REDIS_URL, decode_responses=True)
        logger.info("[PROMPT_STORE] Cliente Redis del prompt inicializado")
    return _client


async def get_prompt_version(id_empresa: int) -> str:
    """
    Versión actual del prompt de la empresa (epoch ms como string).

    Devuelve VERSION_FILE_FALLBACK si no hay Redis, no hay clave o hay error,
    para que el agente use el template local gqm_system.j2.
    """
    client = _get_client()
    if client is None:
        return VERSION_FILE_FALLBACK
    try:
        ver = await client.get(f"prompt:ver:{id_empresa}")
        return ver or VERSION_FILE_FALLBACK
    except Exception as e:
        logger.warning(
            "[PROMPT_STORE] Error leyendo prompt:ver:%s - fallback a archivo: %s",
            id_empresa, e,
        )
        return VERSION_FILE_FALLBACK


async def get_prompt_text(id_empresa: int) -> str | None:
    """Texto crudo del prompt de la empresa. None si no hay Redis / clave / error."""
    client = _get_client()
    if client is None:
        return None
    try:
        return await client.get(f"prompt:txt:{id_empresa}")
    except Exception as e:
        logger.warning(
            "[PROMPT_STORE] Error leyendo prompt:txt:%s - fallback a archivo: %s",
            id_empresa, e,
        )
        return None


async def close_prompt_redis() -> None:
    """Cierra el cliente Redis del prompt (graceful shutdown)."""
    global _client
    if _client is not None:
        try:
            await _client.aclose()
        except Exception as e:
            logger.warning("[PROMPT_STORE] Error cerrando cliente Redis: %s", e)
        finally:
            _client = None


__all__ = [
    "get_prompt_version",
    "get_prompt_text",
    "close_prompt_redis",
    "VERSION_FILE_FALLBACK",
]
