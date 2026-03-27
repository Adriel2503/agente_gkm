"""
Singleton LLM y checkpointer LangGraph para el agente.

Inicialización lazy del modelo (get_model) igual que get_client en http_client.py.
El checkpointer se crea en init_checkpointer() (async, llamado desde lifespan).
"""

from __future__ import annotations

from typing import Any

from langchain.chat_models import init_chat_model
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer

from ... import config as app_config
from ...logger import get_logger
from ...metrics import DEGRADATION

logger = get_logger(__name__)

# ---------------------------------------------------------------------------
# Checkpointer LangGraph (singleton, inicializado en init_checkpointer)
# ---------------------------------------------------------------------------

_checkpointer: Any = None

# Modelo LLM: singleton compartido por todas las empresas.
_model = None


def _make_memory_saver() -> InMemorySaver:
    """Crea InMemorySaver con allowlist para CitaStructuredResponse (path msgpack)."""
    return InMemorySaver(
        serde=JsonPlusSerializer(
            allowed_msgpack_modules=[("autobot.agent.content", "CitaStructuredResponse")]
        )
    )


async def init_checkpointer() -> None:
    """
    Inicializa el checkpointer LangGraph.

    Si REDIS_URL está configurado, intenta AsyncRedisSaver con serialización
    JSON (JsonPlusRedisSerializer). Si Redis no está disponible o el paquete
    no está instalado, cae a InMemorySaver como fallback.

    Debe llamarse una sola vez al arrancar la app (FastAPI lifespan).
    """
    global _checkpointer

    if not app_config.REDIS_URL:
        _checkpointer = _make_memory_saver()
        logger.info("[LLM] Checkpointer: InMemorySaver (REDIS_URL vacío)")
        return

    try:
        from langgraph.checkpoint.redis.aio import AsyncRedisSaver
        from langgraph.checkpoint.redis.jsonplus_redis import (
            JsonPlusRedisSerializer,
        )

        ttl_hours = app_config.REDIS_CHECKPOINT_TTL_HOURS
        ttl_config = {"default_ttl": ttl_hours * 60} if ttl_hours > 0 else None

        saver = AsyncRedisSaver(redis_url=app_config.REDIS_URL, ttl=ttl_config)
        saver.serde = JsonPlusRedisSerializer(
            allowed_json_modules=[
                ("autobot", "agent", "content", "CitaStructuredResponse")
            ],
            allowed_msgpack_modules=[
                ("autobot.agent.content", "CitaStructuredResponse")
            ],
        )
        await saver.asetup()
        _checkpointer = saver
        _ttl_label = f"TTL={ttl_hours}h" if ttl_hours > 0 else "sin TTL"
        logger.info(
            "[LLM] Checkpointer: AsyncRedisSaver (%s, %s)",
            app_config.REDIS_URL, _ttl_label,
        )

    except ImportError:
        DEGRADATION.labels(service="checkpointer", reason="import_missing").inc()
        logger.warning(
            "[LLM] langgraph-checkpoint-redis no instalado — usando InMemorySaver"
        )
        _checkpointer = _make_memory_saver()

    except Exception as e:
        DEGRADATION.labels(service="checkpointer", reason="redis_unavailable").inc()
        logger.warning(
            "[LLM] No se pudo conectar a Redis (%s) — usando InMemorySaver", e
        )
        _checkpointer = _make_memory_saver()


def get_model():
    """
    Retorna el modelo LLM singleton, creándolo en la primera llamada.
    init_chat_model es síncrono → no hay race condition en asyncio single-thread.
    Compartido por todas las empresas: la config viene de variables de entorno.
    """
    global _model
    if _model is None:
        logger.info("[LLM] Inicializando modelo LLM: %s", app_config.OPENAI_MODEL)
        _model = init_chat_model(
            f"openai:{app_config.OPENAI_MODEL}",
            api_key=app_config.OPENAI_API_KEY,
            temperature=app_config.OPENAI_TEMPERATURE,
            max_tokens=app_config.MAX_TOKENS,
            timeout=app_config.OPENAI_TIMEOUT,
        )
    return _model


def get_checkpointer():
    """Retorna el checkpointer LangGraph singleton (InMemorySaver o AsyncRedisSaver)."""
    if _checkpointer is None:
        raise RuntimeError(
            "Checkpointer no inicializado. Llamar await init_checkpointer() primero."
        )
    return _checkpointer


async def close_checkpointer() -> None:
    """
    Cierra el checkpointer al apagar la app.
    Si es AsyncRedisSaver, cierra la conexión Redis via __aexit__.
    No-op si es InMemorySaver.
    """
    global _checkpointer

    if _checkpointer is None:
        return

    if hasattr(_checkpointer, "__aexit__"):
        try:
            await _checkpointer.__aexit__(None, None, None)
            logger.info("[LLM] AsyncRedisSaver cerrado correctamente")
        except Exception as e:
            logger.warning("[LLM] Error cerrando Redis checkpointer: %s", e)

    _checkpointer = None


__all__ = ["get_model", "get_checkpointer", "close_checkpointer", "init_checkpointer"]
