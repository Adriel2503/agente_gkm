"""
Servicio de búsqueda semántica de modelos KIA via RAG API.
Llama al endpoint /buscar del servicio FastAPI con pgvector.

Resiliencia:
  - Circuit breaker (kia_rag_cb): 3 fallos de red → abierto 5 min, auto-reset.
  - Retry: tenacity en post_with_logging → post_with_retry (TransportError, exponential backoff).
  - Logging: request/response en DEBUG via post_with_logging.
  - Métricas: track_api_call mide latencia, track_tool_execution mide duración total.
"""

from cachetools import TTLCache

from .. import config as app_config
from ..logger import get_logger
from ..metrics import track_tool_execution, track_api_call, DEGRADATION, SEARCH_CACHE
from ..infra import post_with_logging, resilient_call
from ..config import kia_rag_cb

logger = get_logger(__name__)

_search_cache: TTLCache = TTLCache(
    maxsize=app_config.SEARCH_CACHE_MAXSIZE,
    ttl=app_config.SEARCH_CACHE_TTL_MINUTES * 60,
)


async def buscar_modelos_kia(
    query: str,
    log_apis: bool = False,
) -> dict:
    """
    Busca modelos KIA por similitud semántica usando la RAG API.

    Args:
        query: Texto libre de búsqueda (ej: "auto familiar barato", "New Picanto")
        log_apis: Debug flag para logging detallado

    Returns:
        {"success": bool, "resultados": [...], "error": str | None}
    """
    cache_key = query.strip().lower()
    cached = _search_cache.get(cache_key)
    if cached is not None:
        SEARCH_CACHE.labels(result="hit").inc()
        logger.debug("[buscar_kia] Cache hit para: %s", query)
        return cached

    try:
        if log_apis:
            logger.info("[buscar_kia] Llamando RAG API con query: %s", query)

        with track_tool_execution("buscar_kia"):
            with track_api_call("kia_rag"):
                response = await resilient_call(
                    lambda: post_with_logging(app_config.API_KIA_RAG_URL, {"query": query}),
                    cb=kia_rag_cb,
                    circuit_key="global",
                    service_name="KIA_RAG",
                )

        if not response or "resultados" not in response:
            logger.warning("[buscar_kia] Respuesta inesperada: %s", response)
            return {"success": False, "resultados": [], "error": "Respuesta inesperada de la API"}

        SEARCH_CACHE.labels(result="miss").inc()
        result = {"success": True, "resultados": response["resultados"], "error": None}
        _search_cache[cache_key] = result
        logger.debug("[buscar_kia] %d resultado(s) para: %s", len(result["resultados"]), query)
        return result

    except RuntimeError:
        SEARCH_CACHE.labels(result="circuit_open").inc()
        DEGRADATION.labels(service="kia_rag", reason="circuit_open").inc()
        logger.warning("[buscar_kia] Circuit breaker abierto — RAG API no disponible")
        return {"success": False, "resultados": [], "error": "Servicio no disponible temporalmente"}
    except Exception as e:
        logger.error("[buscar_kia] Error: %s", e, exc_info=True)
        return {"success": False, "resultados": [], "error": str(e)}


def format_kia_resultados(resultados: list[dict]) -> str:
    """Formatea los resultados del RAG para que el LLM los presente bien."""
    if not resultados:
        return "No encontré modelos que coincidan con tu búsqueda."

    lineas = []
    for r in resultados:
        precio = r.get('precio_usd')
        cuota = r.get('cuota_bancaria')
        precio_str = f"${precio:,}" if precio else "N/A"
        cuota_str = f"${cuota}/mes" if cuota else "N/A"

        lineas.append(f"🚗 **{r.get('modelo', 'N/A')}** — {r.get('detalle_version', '')}")
        lineas.append(f"   Gama: {r.get('gama', 'N/A')} | Año: {r.get('año', 'N/A')}")
        lineas.append(f"   Precio: {precio_str} | Cuota desde: {cuota_str}")
        lineas.append(f"   Colores: {r.get('colores', 'N/A')}")
        if r.get("introduccion"):
            lineas.append(f"   {r['introduccion']}")
        if r.get("url_pdf"):
            lineas.append(f"   📄 Ficha técnica: {r['url_pdf']}")
        if r.get("url_video"):
            lineas.append(f"   🎥 Video: {r['url_video']}")
        lineas.append("")

    return "\n".join(lineas).strip()
