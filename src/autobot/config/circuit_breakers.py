"""
Instancias de CircuitBreaker para las APIs externas.

Cada API tiene su propio CB con partición por key (id_empresa, "global", etc.).
Para agregar una nueva API, crear una instancia aquí con _register() y usarla en el servicio.

La clase CircuitBreaker vive en infra/ (infraestructura genérica).
Las instancias viven aquí (configuración de negocio).
"""

from ..infra import CircuitBreaker
from . import CB_THRESHOLD, CB_RESET_TTL, CB_MAX_KEYS

# ---------------------------------------------------------------------------
# Registry para /health
# ---------------------------------------------------------------------------

_registry: list[CircuitBreaker] = []


def _register(cb: CircuitBreaker) -> CircuitBreaker:
    """Registra un CB en el registro global. Retorna el mismo CB para uso inline."""
    _registry.append(cb)
    return cb


def get_health_issues() -> list[str]:
    """
    Retorna lista de CBs abiertos en formato '{name}_degraded'.
    Usado por /health para reportar degradación sin enumerar los CBs individualmente.
    Agregar un nuevo CB solo requiere usar _register() — /health se actualiza solo.
    """
    return [f"{cb.name}_degraded" for cb in _registry if cb.any_open()]


# ---------------------------------------------------------------------------
# Instancias — agregar nuevos circuit breakers aquí con _register()
#
# Para proteger una nueva API externa:
#
# 1. Crear la instancia aquí:
#        mi_api_cb: CircuitBreaker = _register(CircuitBreaker(
#            name="mi_api",
#            threshold=CB_THRESHOLD,
#            reset_ttl=CB_RESET_TTL,
#            max_keys=CB_MAX_KEYS,
#        ))
#
# 2. Exportarla en __all__ y en config/__init__.py
#
# 3. Usarla en el servicio con resilient_call (infra/_resilience.py):
#        from ..config import mi_api_cb
#        data = await resilient_call(
#            lambda: post_with_logging(url, payload),
#            cb=mi_api_cb,
#            circuit_key=id_empresa,  # o "global" si no es multi-tenant
#            service_name="MI_API",
#        )
#
# 4. /health reportará "mi_api_degraded" automáticamente si el CB se abre.
# ---------------------------------------------------------------------------

# Key fija "global": la RAG API es un servicio interno compartido.
kia_rag_cb: CircuitBreaker = _register(CircuitBreaker(
    name="kia_rag_api",
    threshold=CB_THRESHOLD,
    reset_ttl=CB_RESET_TTL,
    max_keys=CB_MAX_KEYS,
))

__all__ = [
    "kia_rag_cb",
    "get_health_issues",
]
