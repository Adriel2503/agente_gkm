"""
Tools internas del agente comercial.
Estas tools son usadas por el LLM a través de function calling,
NO están expuestas directamente al orquestador.
"""

from langchain.tools import tool, ToolRuntime

from ..services.busqueda_kia import buscar_modelos_kia, format_kia_resultados
from ..services.citas_vitrix import crear_task_confirmar_cita
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


@tool
async def agendar_cita(
    description: str,
    runtime: ToolRuntime = None,
) -> str:
    """
    Registra la cita agendada del lead en el CRM con el historial del
    perfilamiento, para que el asesor humano la procese.

    Args:
        description: Bloque Q&A del perfilamiento (8 preguntas canónicas
            del flujo) en formato "P: ...\\nR: ..." por línea. Ver
            <tools> del system prompt para el formato completo y reglas
            de uso.

    Returns:
        Mensaje corto indicando si la cita quedó registrada o no.
    """
    logger.info("[agendar_cita] Tool en uso: agendar_cita")

    if not description or not description.strip():
        record_tool_validation_error("agendar_cita")
        return "Necesito los detalles de la cita para registrarla."

    id_bitrix = getattr(runtime.context, "id_bitrix", None) if runtime else None
    if not id_bitrix:
        logger.warning("[agendar_cita] id_bitrix ausente en el contexto")
        return "No puedo registrar la cita en este momento."

    try:
        with track_tool_execution("agendar_cita"):
            result = await crear_task_confirmar_cita(id_bitrix, description.strip())
    except Exception as e:
        logger.error("[agendar_cita] Error inesperado: %s", e, exc_info=True)
        return "No pude registrar la cita en este momento. Intenta nuevamente."

    if result["success"]:
        return "Cita registrada correctamente."

    return "No pude registrar la cita en este momento. Intenta nuevamente."


# Lista de todas las tools disponibles para el agente
AGENT_TOOLS = [search_kia_modelos, agendar_cita]

__all__ = ["search_kia_modelos", "agendar_cita", "AGENT_TOOLS"]
