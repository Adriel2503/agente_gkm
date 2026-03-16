"""
Servicio de búsqueda semántica de modelos KIA via RAG API.
Llama al endpoint /buscar del servicio FastAPI con pgvector.
"""

from .. import config as app_config
from ..logger import get_logger
from ..metrics import track_tool_execution
from ..infra.http_client import post_with_retry

logger = get_logger(__name__)


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
    try:
        if log_apis:
            logger.info("[buscar_kia] Llamando RAG API con query: %s", query)

        with track_tool_execution("buscar_kia"):
            response = await post_with_retry(
                url=app_config.API_KIA_RAG_URL,
                payload={"query": query},
            )

        if not response or "resultados" not in response:
            logger.warning("[buscar_kia] Respuesta inesperada: %s", response)
            return {"success": False, "resultados": [], "error": "Respuesta inesperada de la API"}

        result = {"success": True, "resultados": response["resultados"], "error": None}
        logger.debug("[buscar_kia] %d resultado(s) para: %s", len(result["resultados"]), query)
        return result

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
