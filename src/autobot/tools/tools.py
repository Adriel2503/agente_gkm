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
    Devuelve información completa agrupada por secciones: Identificación,
    Descripción, Precio, Motor, Dimensiones, Exterior, Interior, Tecnología,
    Seguridad, Suspensión y Mantenimiento.

    Args:
        query: Texto libre de búsqueda (ej: "auto familiar", "Picanto", "SUV barato")

    Returns:
        Información de hasta 3 modelos KIA relevantes
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


# Registro de todas las tools con su key para filtrar por empresa
TOOL_REGISTRY: dict[str, object] = {
    "search_kia_modelos": search_kia_modelos,
}

# Lista completa (fallback si no hay config en BD)
AGENT_TOOLS = list(TOOL_REGISTRY.values())


def get_tools_for_empresa(tools_config: list | None) -> list:
    """
    Filtra las tools según la config de la empresa.

    Args:
        tools_config: Lista de keys habilitadas, ej: ["search_kia_modelos"]
                      Si es None o vacía, devuelve todas las tools.

    Returns:
        Lista de tools habilitadas para esta empresa.
    """
    if not tools_config:
        return AGENT_TOOLS

    tools = []
    for key in tools_config:
        if key in TOOL_REGISTRY:
            tools.append(TOOL_REGISTRY[key])
        else:
            logger.warning("[TOOLS] Tool '%s' no encontrada en el registro", key)

    if not tools:
        logger.warning("[TOOLS] Ninguna tool válida en config, usando todas")
        return AGENT_TOOLS

    logger.debug("[TOOLS] Tools habilitadas: %s", [t.name for t in tools])
    return tools


__all__ = ["search_kia_modelos", "AGENT_TOOLS", "TOOL_REGISTRY", "get_tools_for_empresa"]
