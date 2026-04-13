"""
Caches y locks para el agente.

Contiene:
  - TTLCache de agentes compilados (_agent_cache)
  - Locks por cache_key para evitar thundering herd (_agent_cache_locks)
  - Locks por phone para serializar requests concurrentes (_session_locks)
  - Funciones de limpieza periódica de locks huérfanos

No importa de infra/ para evitar dependencias circulares.
"""

import asyncio
from typing import Any

from cachetools import TTLCache

from ... import config as app_config
from ...logger import get_logger

logger = get_logger(__name__)

# Cache de agentes compilados: clave = (id_empresa, phone).
# Un agente por lead, porque el system prompt incluye <lead_identity>
# renderizada con nombre/marca/modelo/id_bitrix específicos de cada persona.
# TTL default 60 min. Los cambios de campos del lead (raros) se reflejan
# en el próximo MISS tras expirar el TTL.
class _LoggingTTLCache(TTLCache):
    """TTLCache que loggea cuando se elimina una entrada (TTL expirado o LRU por maxsize)."""

    def __delitem__(self, key, **kwargs):
        super().__delitem__(key, **kwargs)
        try:
            logger.info("[CACHE] Agente desalojado - key=%s, cache_size=%s", key, len(self))
        except Exception:
            pass  # nunca dejar que logging rompa operaciones del cache


_agent_cache: TTLCache = _LoggingTTLCache(
    maxsize=app_config.AGENT_CACHE_MAXSIZE,
    ttl=app_config.AGENT_CACHE_TTL_MINUTES * 60,
)

# Un lock por cache_key para evitar thundering herd al crear el agente por primera vez.
# Crece con cada id_empresa nuevo; se limpia cuando supera _LOCKS_CLEANUP_THRESHOLD.
_agent_cache_locks: dict[tuple, asyncio.Lock] = {}
_LOCKS_CLEANUP_THRESHOLD = int(app_config.AGENT_CACHE_MAXSIZE * 1.5)  # 1.5x cache maxsize

# Un lock por phone para serializar requests concurrentes del mismo usuario.
# Evita que dos mensajes del mismo usuario ejecuten agent.ainvoke sobre el mismo
# thread_id del checkpointer en paralelo.
# Crece con cada sesión nueva; se limpia cuando supera _SESSION_LOCKS_CLEANUP_THRESHOLD.
_session_locks: dict[str, asyncio.Lock] = {}
_SESSION_LOCKS_CLEANUP_THRESHOLD = app_config.AGENT_CACHE_MAXSIZE  # escala con el cache


# ---------------------------------------------------------------------------
# Operaciones del agent cache
# ---------------------------------------------------------------------------

def get_cached_agent(cache_key: tuple) -> Any | None:
    """Retorna el agente cacheado o None si no existe / expiró."""
    return _agent_cache.get(cache_key)


def cache_agent(cache_key: tuple, agent: Any) -> None:
    """Almacena un agente compilado en el cache."""
    _agent_cache[cache_key] = agent


def agent_cache_ttl() -> int:
    """Retorna el TTL configurado del cache en segundos."""
    return int(_agent_cache.ttl)


def agent_cache_size() -> int:
    """Retorna la cantidad de agentes actualmente en cache."""
    return len(_agent_cache)


# ---------------------------------------------------------------------------
# Operaciones de agent locks (thundering herd)
# ---------------------------------------------------------------------------

def acquire_agent_lock(cache_key: tuple) -> asyncio.Lock:
    """
    Retorna el lock para un cache_key, creándolo si no existe.
    Ejecuta limpieza de locks huérfanos si se supera el threshold.
    """
    _cleanup_stale_agent_locks(cache_key)
    return _agent_cache_locks.setdefault(cache_key, asyncio.Lock())


def release_agent_lock(cache_key: tuple) -> None:
    """Elimina el lock del registro tras completar la creación del agente."""
    _agent_cache_locks.pop(cache_key, None)


# ---------------------------------------------------------------------------
# Operaciones de session locks
# ---------------------------------------------------------------------------

def acquire_session_lock(phone: str) -> asyncio.Lock:
    """
    Retorna el lock para un phone, creándolo si no existe.
    Ejecuta limpieza de session locks huérfanos si se supera el threshold.
    """
    _cleanup_stale_session_locks(phone)
    return _session_locks.setdefault(phone, asyncio.Lock())


# ---------------------------------------------------------------------------
# Limpieza interna (privada)
# ---------------------------------------------------------------------------

def _cleanup_stale_agent_locks(current_cache_key: tuple) -> None:
    """
    Elimina locks de _agent_cache_locks cuyas claves ya no están en _agent_cache.
    Solo se ejecuta si el dict supera _LOCKS_CLEANUP_THRESHOLD.
    Evita crecimiento indefinido cuando hay muchas empresas distintas.

    Un lock se considera huérfano solo si su entrada en _agent_cache ya expiró (TTL).
    Un lock no bloqueado de una empresa cuyo agente aún está en caché NO se elimina.
    """
    if len(_agent_cache_locks) <= _LOCKS_CLEANUP_THRESHOLD:
        return
    removed_keys: list = []
    for key in list(_agent_cache_locks.keys()):
        if key == current_cache_key:
            continue
        if key not in _agent_cache:
            lock = _agent_cache_locks.get(key)
            if lock is not None and not lock.locked():
                del _agent_cache_locks[key]
                removed_keys.append(key)
    if removed_keys:
        logger.debug(
            "[CACHE] Limpieza de locks huérfanos: %s eliminados - keys=%s",
            len(removed_keys), removed_keys[:10],
        )


def _cleanup_stale_session_locks(current_phone: str) -> None:
    """
    Elimina locks de _session_locks que no están en uso.
    Solo se ejecuta si el dict supera _SESSION_LOCKS_CLEANUP_THRESHOLD.
    En multiempresa muchas sesiones acumulan; esto evita crecimiento indefinido.

    Un lock es huérfano simplemente si no está bloqueado en este momento.
    Las sesiones WhatsApp son permanentes por contacto.
    """
    if len(_session_locks) <= _SESSION_LOCKS_CLEANUP_THRESHOLD:
        return
    removed_phones: list[str] = []
    for sid in list(_session_locks.keys()):
        if sid == current_phone:
            continue
        lock = _session_locks.get(sid)
        if lock is not None and not lock.locked():
            del _session_locks[sid]
            removed_phones.append(sid)
    if removed_phones:
        logger.debug(
            "[CACHE] Limpieza de session locks: %s eliminados - phones=%s",
            len(removed_phones), removed_phones[:10],
        )


__all__ = [
    "get_cached_agent",
    "cache_agent",
    "agent_cache_size",
    "agent_cache_ttl",
    "acquire_agent_lock",
    "release_agent_lock",
    "acquire_session_lock",
]
