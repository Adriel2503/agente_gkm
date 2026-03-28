"""
Tools internas del agente comercial.
Estas tools son usadas por el LLM a través de function calling,
NO están expuestas directamente al orquestador.
"""

from langchain.tools import tool, ToolRuntime

from ..services.busqueda_kia import buscar_modelos_kia, format_kia_resultados
from ..logger import get_logger
from ..metrics import track_tool_execution, record_tool_validation_error

logger = get_logger(__name__)


@tool
async def search_kia_modelos(
    query: str,
    runtime: ToolRuntime = None
) -> str:
    """
    Busca modelos de autos KIA por similitud semántica en el catálogo.
    Úsala SIEMPRE que el cliente pregunte por modelos, precios, cuotas,
    colores disponibles, fichas técnicas o cualquier dato de los vehículos KIA.

    Devuelve hasta 3 modelos con información completa agrupada por secciones:
    Identificación, Descripción, Precio y garantía, Motor y rendimiento,
    Dimensiones, Exterior, Interior y confort, Tecnología, Seguridad,
    Suspensión y Mantenimiento.

    Args:
        query: Texto libre de búsqueda (ej: "auto familiar económico", "New Picanto", "SUV menos de 40 mil")

    Returns:
        Información completa de los modelos KIA más relevantes para la búsqueda
    """
    logger.info("[search_kia_modelos] Tool en uso: search_kia_modelos, query=%s", query)

    if not query or not query.strip():
        record_tool_validation_error("search_kia_modelos")
        return "Necesito un término de búsqueda para buscar modelos KIA."

    try:
        with track_tool_execution("search_kia_modelos"):
            result = await buscar_modelos_kia(query=query, log_apis=True)

        if not result["success"]:
            logger.warning("[TOOL] search_kia_modelos - Error: %s", result.get("error"))
            return "No pude buscar los modelos en este momento. Intenta nuevamente."

        resultados = result.get("resultados", [])
        if not resultados:
            return f"No encontré modelos KIA que coincidan con '{query}'. Prueba con otros términos."

        logger.debug("[TOOL] search_kia_modelos - %d resultado(s)", len(resultados))
        return format_kia_resultados(resultados)

    except Exception as e:
        logger.error("[TOOL] search_kia_modelos - Error: %s", e, exc_info=True)
        return "Error al buscar modelos KIA. Intenta nuevamente."


# Lista de todas las tools disponibles para el agente
AGENT_TOOLS = [search_kia_modelos]

__all__ = ["search_kia_modelos", "AGENT_TOOLS"]
