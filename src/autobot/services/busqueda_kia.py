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
        logger.info("[buscar_kia] Cache hit para: %s", query)
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
        logger.info("[buscar_kia] %d resultado(s) para: %s", len(result["resultados"]), query)
        return result

    except RuntimeError:
        SEARCH_CACHE.labels(result="circuit_open").inc()
        DEGRADATION.labels(service="kia_rag", reason="circuit_open").inc()
        logger.warning("[buscar_kia] Circuit breaker abierto — RAG API no disponible")
        return {"success": False, "resultados": [], "error": "Servicio no disponible temporalmente"}
    except Exception as e:
        logger.error("[buscar_kia] Error: %s", e, exc_info=True)
        return {"success": False, "resultados": [], "error": str(e)}


# Campos que van en el encabezado (no se repiten en los grupos)
_HEADER_FIELDS: set[str] = {"marca", "modelo", "anio_modelo", "detalle_version", "version_gama", "precio_lista_usd"}

_FIELD_GROUPS: dict[str, set[str]] = {
    "Identificación": {"id", "variante", "tipo_carroceria", "tipo_de_gama"},
    "Descripción": {"introduccion", "descripcion_modelo", "caracteristicas_generales"},
    "Precio y garantía": {"garantia", "paquete_mantenimiento"},
    "Motor y rendimiento": {"motor_cilindros", "cilindrada_combustible_tipo", "potencia", "torque", "transmision", "traccion", "autonomia_ev"},
    "Dimensiones": {"dimensiones", "distancia_entre_ejes", "tamanio_aros", "capacidad_maletera", "numero_asientos"},
    "Exterior": {"colores_disponibles", "faros_delanteros_led", "faros_posteriores_led", "faros_neblineros", "rieles_techo"},
    "Interior y confort": {"material_asientos", "asientos_electricos", "volante_forrado_cuero", "aire_acondicionado", "sunroof", "cargador_inalambrico", "maletero_inteligente", "espejos_electricos", "alzavidrios_electricos"},
    "Tecnología": {"radio_tactil", "conectividad_radio", "panel_instrumentos", "tipo_llave", "camara_retroceso", "sensores_estacionamiento"},
    "Seguridad": {"airbags", "sistema_frenos", "tipo_frenos", "freno_estacionamiento", "monitor_punto_ciego", "fca", "bca", "lka", "rcca", "lfa", "control_crucero"},
    "Suspensión": {"suspension_delantera", "suspension_posterior", "llanta_repuesto"},
    "Mantenimiento": {"primer_servicio_mantenimiento", "frecuencia_mantenimiento"},
}

_ALL_GROUPED: set[str] = _HEADER_FIELDS | set().union(*_FIELD_GROUPS.values())


def format_kia_resultados(resultados: list[dict]) -> str:
    """Formatea los resultados del RAG para que el LLM los presente bien.

    Encabezado con marca, modelo, año, versión, gama y precio.
    Luego TODOS los campos agrupados por categoría. Si la API agrega campos
    nuevos, aparecen automáticamente en el grupo "Otros".
    """
    if not resultados:
        return "No encontré modelos que coincidan con tu búsqueda."

    lineas = []
    for r in resultados:
        # Encabezado: marca + modelo + año
        marca = r.get("marca", "N/A")
        modelo = r.get("modelo", "N/A")
        anio = r.get("anio_modelo", "")
        lineas.append(f"=== {marca} {modelo} {anio} ===")

        # Subencabezado: versión | gama | precio
        sub = []
        if r.get("detalle_version"):
            sub.append(r["detalle_version"])
        if r.get("version_gama"):
            sub.append(r["version_gama"])
        if r.get("precio_lista_usd"):
            sub.append(r["precio_lista_usd"])
        if sub:
            lineas.append(" | ".join(sub))

        # Grupos de campos
        for group_name, fields in _FIELD_GROUPS.items():
            group_lines = []
            for key in fields:
                value = r.get(key)
                if value is not None and value != "" and value != "N/A":
                    group_lines.append(f"  {key}: {value}")
            if group_lines:
                lineas.append(f"[{group_name}]")
                lineas.extend(sorted(group_lines))

        # Campos no agrupados (nuevos de la API)
        otros = []
        for key, value in r.items():
            if key not in _ALL_GROUPED and value is not None and value != "" and value != "N/A":
                otros.append(f"  {key}: {value}")
        if otros:
            lineas.append("[Otros]")
            lineas.extend(sorted(otros))

        lineas.append("")

    return "\n".join(lineas).strip()
