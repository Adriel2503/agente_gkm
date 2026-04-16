"""
Tools internas del agente comercial.
Estas tools son usadas por el LLM a través de function calling,
NO están expuestas directamente al orquestador.
"""

from langchain.tools import tool, ToolRuntime

from ..services.busqueda_kia import buscar_vehiculo_rag, format_kia_resultados
from ..services.citas_vitrix import actualizar_lead_y_crear_task, marcar_lead_desistido
from ..logger import get_logger
from ..metrics import track_tool_execution, record_tool_validation_error

logger = get_logger(__name__)


@tool
async def buscar_vehiculo(
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
    logger.info("[buscar_vehiculo] Tool en uso: buscar_vehiculo, query=%s", query)

    if not query or not query.strip():
        record_tool_validation_error("buscar_vehiculo")
        return "Necesito un término de búsqueda para buscar modelos KIA."

    try:
        with track_tool_execution("buscar_vehiculo"):
            result = await buscar_vehiculo_rag(query=query, log_apis=True)

        if not result["success"]:
            logger.warning("[TOOL] buscar_vehiculo - Error: %s", result.get("error"))
            return "No pude buscar los modelos en este momento. Intenta nuevamente."

        resultados = result.get("resultados", [])
        if not resultados:
            mensaje = result.get("mensaje")
            if isinstance(mensaje, str) and mensaje.strip():
                return mensaje.strip()
            return "No encontré vehículos en esa búsqueda. Intentá con otro modelo o marca para ayudarte mejor."

        logger.debug("[TOOL] buscar_vehiculo - %d resultado(s)", len(resultados))
        return format_kia_resultados(resultados)

    except Exception as e:
        logger.error("[TOOL] buscar_vehiculo - Error: %s", e, exc_info=True)
        return "Error al buscar modelos KIA. Intenta nuevamente."


@tool
async def agendar_cita(
    financing_required: str | None = None,
    corporate_agreement: str | None = None,
    trade_in_vehicle: str | None = None,
    used_vehicle_brand: str | None = None,
    used_vehicle_model: str | None = None,
    used_vehicle_year: str | None = None,
    used_vehicle_mileage: str | None = None,
    purchase_expectation: str | None = None,
    budget_description: str | None = None,
    appointment_datetime: str | None = None,
    runtime: ToolRuntime = None,
) -> str:
    """
    Actualiza el lead en CRM con los campos del flujo comercial.

    Args:
        financing_required: Requiere financiamiento (SÍ/NO).
        corporate_agreement: Convenio corporativo seleccionado.
        trade_in_vehicle: Entrega de vehículo en parte de pago (SÍ/NO).
        used_vehicle_brand: Marca del vehículo en parte de pago.
        used_vehicle_model: Modelo del vehículo en parte de pago.
        used_vehicle_year: Año del vehículo en parte de pago.
        used_vehicle_mileage: Kilometraje del vehículo en parte de pago.
        purchase_expectation: Expectativa de compra.
        budget_description: Presupuesto textual, por ejemplo "3000 dólares".
        appointment_datetime: Fecha y hora de la cita.
        Si un campo no aplica o no se conoce, usar "N.A".

    Returns:
        Mensaje corto indicando si el lead quedó actualizado o no.
    """
    logger.info("[agendar_cita] Tool en uso: agendar_cita")

    if not any(
        value is not None and str(value).strip()
        for value in (
            financing_required,
            corporate_agreement,
            trade_in_vehicle,
            used_vehicle_brand,
            used_vehicle_model,
            used_vehicle_year,
            used_vehicle_mileage,
            purchase_expectation,
            budget_description,
            appointment_datetime,
        )
    ):
        record_tool_validation_error("agendar_cita")
        return "Necesito datos del flujo para actualizar el lead."

    id_bitrix = getattr(runtime.context, "id_bitrix", None) if runtime else None
    if not id_bitrix:
        logger.warning("[agendar_cita] id_bitrix ausente en el contexto")
        return "No puedo actualizar el lead en este momento."

    try:
        with track_tool_execution("agendar_cita"):
            result = await actualizar_lead_y_crear_task(
                id_bitrix=id_bitrix,
                financing_required=financing_required,
                corporate_agreement=corporate_agreement,
                trade_in_vehicle=trade_in_vehicle,
                used_vehicle_brand=used_vehicle_brand,
                used_vehicle_model=used_vehicle_model,
                used_vehicle_year=used_vehicle_year,
                used_vehicle_mileage=used_vehicle_mileage,
                purchase_expectation=purchase_expectation,
                budget_description=budget_description,
                appointment_datetime=appointment_datetime,
            )
    except Exception as e:
        logger.error("[agendar_cita] Error inesperado: %s", e, exc_info=True)
        return "No pude actualizar el lead en este momento. Intenta nuevamente."

    if result["success"]:
        return "Lead actualizado correctamente."

    resolve_errors = result.get("resolve_errors") or []
    if resolve_errors:
        first_error = resolve_errors[0]
        field = first_error.get("field", "campo")
        message = first_error.get("message", "error de validación")
        options = first_error.get("options")
        if isinstance(options, list) and options:
            options_text = ", ".join(str(option) for option in options[:5])
            return f"No pude actualizar el lead: {field}. {message}. Opciones: {options_text}."
        return f"No pude actualizar el lead: {field}. {message}."

    error_msg = result.get("error")
    if isinstance(error_msg, str) and error_msg.strip():
        return f"No pude actualizar el lead: {error_msg.strip()}."

    return "No pude actualizar el lead en este momento. Intenta nuevamente."


@tool
async def marcar_desistido(runtime: ToolRuntime = None) -> str:
    """
    Marca el lead como 'Desistido' en el CRM. Llamar SOLO cuando el cliente:
      - No responde tras 2 intentos de reformular una pregunta clave.
      - Rechaza explícitamente agendar cita Y callback (Paso 8).
      - Pide no ser contactado más ("no me molesten", "quítenme de la lista").
    NO requiere parámetros del cliente; todo el payload es interno.
    Idempotente: una sola llamada por conversación.
    """
    logger.info("[marcar_desistido] Tool en uso: marcar_desistido")

    id_bitrix = getattr(runtime.context, "id_bitrix", None) if runtime else None
    if not id_bitrix:
        logger.warning("[marcar_desistido] id_bitrix ausente en el contexto")
        return "No puedo marcar el lead en este momento."

    try:
        with track_tool_execution("marcar_desistido"):
            result = await marcar_lead_desistido(id_bitrix=id_bitrix)
    except Exception as e:
        logger.error("[marcar_desistido] Error inesperado: %s", e, exc_info=True)
        return "No pude marcar el lead en este momento. Intenta nuevamente."

    if result["success"]:
        return "Lead marcado como desistido."

    error_msg = result.get("error")
    if isinstance(error_msg, str) and error_msg.strip():
        return f"No pude marcar el lead: {error_msg.strip()}."
    return "No pude marcar el lead en este momento."


# Lista de todas las tools disponibles para el agente
AGENT_TOOLS = [buscar_vehiculo, agendar_cita, marcar_desistido]

__all__ = ["buscar_vehiculo", "agendar_cita", "marcar_desistido", "AGENT_TOOLS"]
