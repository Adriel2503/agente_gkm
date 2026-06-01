"""
Caches y locks para el agente.

Contiene:
  - TTLCache de agentes compilados (_agent_cache)
  - Locks por cache_key para evitar thundering herd (_agent_cache_locks)
  - Locks por (phone, id_bitrix) para serializar requests concurrentes del mismo lead (_session_locks)
  - Funciones de limpieza periódica de locks huérfanos

No importa de infra/ para evitar dependencias circulares.
"""

import asyncio
from typing import Any

from cachetools import TTLCache

from ... import config as app_config
from ...logger import get_logger

logger = get_logger(__name__)

# Cache de agentes compilados: clave = (id_empresa, phone, id_bitrix).
# Un agente por lead, porque el system prompt incluye <lead_identity>
# renderizada con nombre/marca/modelo/version/sucursal/correo/id_bitrix específicos de cada persona.
# id_bitrix en la key evita que el mismo phone con diferente lead de Vitrix reutilice un prompt incorrecto.
# TTL default 60 min. Si id_bitrix es None, leads sin ID del mismo phone comparten cache.
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

# Cache de textos de prompt por (id_empresa, version). Evita re-bajar el texto
# (~23 KB) de Redis en cada mensaje: solo se baja cuando cambia la versión.
_prompt_text_cache: TTLCache = TTLCache(
    maxsize=app_config.AGENT_CACHE_MAXSIZE,
    ttl=app_config.AGENT_CACHE_TTL_MINUTES * 60,
)

# Lock por (id_empresa, version) para serializar la bajada del texto desde Redis
# (evita que varios mensajes de la misma empresa bajen el mismo texto a la vez).
_prompt_text_locks: dict[tuple, asyncio.Lock] = {}

# Un lock por cache_key para evitar thundering herd al crear el agente por primera vez.
# Crece con cada id_empresa nuevo; se limpia cuando supera _LOCKS_CLEANUP_THRESHOLD.
_agent_cache_locks: dict[tuple, asyncio.Lock] = {}
_LOCKS_CLEANUP_THRESHOLD = int(app_config.AGENT_CACHE_MAXSIZE * 1.5)  # 1.5x cache maxsize

# Un lock por (phone, id_bitrix) para serializar requests concurrentes del mismo lead.
# Evita que dos mensajes del mismo lead ejecuten agent.ainvoke sobre el mismo
# thread_id del checkpointer en paralelo. Mismo phone con distinto id_bitrix usa
# locks distintos (los thread_ids también son distintos → sin riesgo de race).
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
# Operaciones del prompt text cache
# ---------------------------------------------------------------------------

def get_cached_prompt_text(id_empresa: int, version: str) -> str | None:
    """Retorna el texto de prompt cacheado para (id_empresa, version), o None."""
    return _prompt_text_cache.get((id_empresa, version))


def cache_prompt_text(id_empresa: int, version: str, text: str) -> None:
    """Cachea el texto de prompt bajo (id_empresa, version)."""
    _prompt_text_cache[(id_empresa, version)] = text


def acquire_prompt_text_lock(id_empresa: int, version: str) -> asyncio.Lock:
    """Lock para serializar la bajada del texto desde Redis por (id_empresa, version)."""
    return _prompt_text_locks.setdefault((id_empresa, version), asyncio.Lock())


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

def acquire_session_lock(phone: str, id_bitrix: str | None) -> asyncio.Lock:
    """
    Retorna el lock para un (phone, id_bitrix), creándolo si no existe.
    Ejecuta limpieza de session locks huérfanos si se supera el threshold.
    """
    session_id = f"{phone}_{id_bitrix}"
    _cleanup_stale_session_locks(session_id)
    return _session_locks.setdefault(session_id, asyncio.Lock())


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


def _cleanup_stale_session_locks(current_session_id: str) -> None:
    """
    Elimina locks de _session_locks que no están en uso.
    Solo se ejecuta si el dict supera _SESSION_LOCKS_CLEANUP_THRESHOLD.
    En multiempresa muchas sesiones acumulan; esto evita crecimiento indefinido.

    Un lock es huérfano simplemente si no está bloqueado en este momento.
    Las sesiones WhatsApp son permanentes por contacto.
    """
    if len(_session_locks) <= _SESSION_LOCKS_CLEANUP_THRESHOLD:
        return
    removed_session_ids: list[str] = []
    for sid in list(_session_locks.keys()):
        if sid == current_session_id:
            continue
        lock = _session_locks.get(sid)
        if lock is not None and not lock.locked():
            del _session_locks[sid]
            removed_session_ids.append(sid)
    if removed_session_ids:
        logger.debug(
            "[CACHE] Limpieza de session locks: %s eliminados - session_ids=%s",
            len(removed_session_ids), removed_session_ids[:10],
        )


__all__ = [
    "get_cached_agent",
    "cache_agent",
    "agent_cache_size",
    "agent_cache_ttl",
    "acquire_agent_lock",
    "release_agent_lock",
    "acquire_session_lock",
    "get_cached_prompt_text",
    "cache_prompt_text",
    "acquire_prompt_text_lock",
]
