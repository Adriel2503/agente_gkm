"""
Tools internas del agente comercial.
Estas tools son usadas por el LLM a través de function calling,
NO están expuestas directamente al orquestador.
"""

import time

from langchain.tools import tool, ToolRuntime

from ..services.busqueda_kia import buscar_vehiculo_rag, format_kia_resultados
from ..services.citas_vitrix import (
    actualizar_lead_y_crear_task,
    actualizar_lead_llamada_y_crear_task,
    marcar_lead_desistido,
)
from ..logger import get_logger
from ..metrics import track_tool_execution, record_tool_validation_error

logger = get_logger(__name__)


def _context_snapshot(runtime: ToolRuntime | None) -> dict:
    """Extrae un snapshot rastreable del runtime.context para logs."""
    if not runtime or not runtime.context:
        return {}
    ctx = runtime.context
    return {
        "phone": getattr(ctx, "phone", None),
        "id_empresa": getattr(ctx, "id_empresa", None),
        "id_bitrix": getattr(ctx, "id_bitrix", None),
        "nombre": getattr(ctx, "nombre", None),
        "marca": getattr(ctx, "marca", None),
        "modelo": getattr(ctx, "modelo", None),
        "sucursal": getattr(ctx, "sucursal", None),
        "event_previo": getattr(ctx, "event", None),
    }


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
    ctx_snap = _context_snapshot(runtime)
    logger.info("[TOOL:buscar_vehiculo] INPUT query=%r context=%s", query, ctx_snap)
    start = time.perf_counter()

    if not query or not query.strip():
        record_tool_validation_error("buscar_vehiculo")
        logger.warning("[TOOL:buscar_vehiculo] VALIDATION_FAIL query vacía")
        return "Necesito un término de búsqueda para buscar modelos KIA."

    try:
        with track_tool_execution("buscar_vehiculo"):
            result = await buscar_vehiculo_rag(query=query, log_apis=True)

        elapsed_ms = int((time.perf_counter() - start) * 1000)

        if not result["success"]:
            logger.warning("[TOOL:buscar_vehiculo] RAG_ERROR query=%r duration_ms=%d error=%s", query, elapsed_ms, result.get("error"))
            return "No pude buscar los modelos en este momento. Intenta nuevamente."

        resultados = result.get("resultados", [])
        if not resultados:
            mensaje = result.get("mensaje")
            logger.info("[TOOL:buscar_vehiculo] RAG_EMPTY query=%r duration_ms=%d mensaje=%r", query, elapsed_ms, mensaje)
            if isinstance(mensaje, str) and mensaje.strip():
                return mensaje.strip()
            return "No encontré vehículos en esa búsqueda. Intentá con otro modelo o marca para ayudarte mejor."

        modelos_ids = [r.get("modelo") or r.get("id") for r in resultados]
        logger.info("[TOOL:buscar_vehiculo] RAG_OK query=%r duration_ms=%d count=%d modelos=%s", query, elapsed_ms, len(resultados), modelos_ids)
        return format_kia_resultados(resultados)

    except Exception as e:
        elapsed_ms = int((time.perf_counter() - start) * 1000)
        logger.error("[TOOL:buscar_vehiculo] ERROR query=%r duration_ms=%d err=%s", query, elapsed_ms, e, exc_info=True)
        return "Error al buscar modelos KIA. Intenta nuevamente."


@tool
async def agendar_cita(
    resumen: str,
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
        resumen: Breve resumen de la conversación hasta el momento de agendar la cita.
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
    ctx_snap = _context_snapshot(runtime)
    args_snap = {
        "resumen": resumen,
        "financing_required": financing_required,
        "corporate_agreement": corporate_agreement,
        "trade_in_vehicle": trade_in_vehicle,
        "used_vehicle_brand": used_vehicle_brand,
        "used_vehicle_model": used_vehicle_model,
        "used_vehicle_year": used_vehicle_year,
        "used_vehicle_mileage": used_vehicle_mileage,
        "purchase_expectation": purchase_expectation,
        "budget_description": budget_description,
        "appointment_datetime": appointment_datetime,
    }
    logger.info("[TOOL:agendar_cita] INPUT args=%s context=%s", args_snap, ctx_snap)
    start = time.perf_counter()

    if not any(
        value is not None and str(value).strip()
        for value in (
            resumen,
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
        logger.warning("[TOOL:agendar_cita] VALIDATION_FAIL todos los campos vacíos context=%s", ctx_snap)
        return "Necesito datos del flujo para actualizar el lead."

    id_bitrix = getattr(runtime.context, "id_bitrix", None) if runtime else None
    if not id_bitrix:
        logger.warning("[TOOL:agendar_cita] NO_ID_BITRIX context=%s", ctx_snap)
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
                resumen=resumen,
            )
    except Exception as e:
        elapsed_ms = int((time.perf_counter() - start) * 1000)
        logger.error("[TOOL:agendar_cita] UNEXPECTED_ERROR id_bitrix=%s duration_ms=%d err=%s", id_bitrix, elapsed_ms, e, exc_info=True)
        return "No pude actualizar el lead en este momento. Intenta nuevamente."

    elapsed_ms = int((time.perf_counter() - start) * 1000)

    if result["success"]:
        if runtime and runtime.context:
            runtime.context.event = "cita_agendada"
            logger.info("[TOOL:agendar_cita] EVENT_SET event=cita_agendada id_bitrix=%s duration_ms=%d request_id=%s", id_bitrix, elapsed_ms, result.get("request_id"))
        return "Lead actualizado correctamente."

    resolve_errors = result.get("resolve_errors") or []
    if resolve_errors:
        first_error = resolve_errors[0]
        field = first_error.get("field", "campo")
        message = first_error.get("message", "error de validación")
        options = first_error.get("options")
        logger.warning("[TOOL:agendar_cita] VALIDATION_REJECTED id_bitrix=%s duration_ms=%d field=%s message=%s", id_bitrix, elapsed_ms, field, message)
        if isinstance(options, list) and options:
            options_text = ", ".join(str(option) for option in options[:5])
            return f"No pude actualizar el lead: {field}. {message}. Opciones: {options_text}."
        return f"No pude actualizar el lead: {field}. {message}."

    error_msg = result.get("error")
    logger.warning("[TOOL:agendar_cita] REJECTED id_bitrix=%s duration_ms=%d error=%s", id_bitrix, elapsed_ms, error_msg)
    if isinstance(error_msg, str) and error_msg.strip():
        return f"No pude actualizar el lead: {error_msg.strip()}."

    return "No pude actualizar el lead en este momento. Intenta nuevamente."


@tool
async def agendar_llamada(
    resumen: str,
    financing_required: str | None = None,
    corporate_agreement: str | None = None,
    trade_in_vehicle: str | None = None,
    used_vehicle_brand: str | None = None,
    used_vehicle_model: str | None = None,
    used_vehicle_year: str | None = None,
    used_vehicle_mileage: str | None = None,
    purchase_expectation: str | None = None,
    budget_description: str | None = None,
    runtime: ToolRuntime = None,
) -> str:
    """
    Marca el lead como 'Cliente interesado' en CRM y crea task para que el asesor llame al cliente.
    Llamar cuando el cliente prefiere callback (rama B del Paso 9) en vez de agendar visita.

    Args:
        resumen: Breve resumen de la conversación hasta el momento del callback.
        financing_required: Requiere financiamiento (SÍ/NO).
        corporate_agreement: Convenio corporativo seleccionado.
        trade_in_vehicle: Entrega de vehículo en parte de pago (SÍ/NO).
        used_vehicle_brand: Marca del vehículo en parte de pago.
        used_vehicle_model: Modelo del vehículo en parte de pago.
        used_vehicle_year: Año del vehículo en parte de pago.
        used_vehicle_mileage: Kilometraje del vehículo en parte de pago.
        purchase_expectation: Expectativa de compra.
        budget_description: Presupuesto textual, por ejemplo "3000 dólares".
        Si un campo no aplica o no se conoce, usar "N.A".

    Returns:
        Mensaje corto indicando si el lead quedó actualizado o no.
    """
    ctx_snap = _context_snapshot(runtime)
    args_snap = {
        "resumen": resumen,
        "financing_required": financing_required,
        "corporate_agreement": corporate_agreement,
        "trade_in_vehicle": trade_in_vehicle,
        "used_vehicle_brand": used_vehicle_brand,
        "used_vehicle_model": used_vehicle_model,
        "used_vehicle_year": used_vehicle_year,
        "used_vehicle_mileage": used_vehicle_mileage,
        "purchase_expectation": purchase_expectation,
        "budget_description": budget_description,
    }
    logger.info("[TOOL:agendar_llamada] INPUT args=%s context=%s", args_snap, ctx_snap)
    start = time.perf_counter()

    if not any(
        value is not None and str(value).strip()
        for value in (
            resumen,
            financing_required,
            corporate_agreement,
            trade_in_vehicle,
            used_vehicle_brand,
            used_vehicle_model,
            used_vehicle_year,
            used_vehicle_mileage,
            purchase_expectation,
            budget_description,
        )
    ):
        record_tool_validation_error("agendar_llamada")
        logger.warning("[TOOL:agendar_llamada] VALIDATION_FAIL todos los campos vacíos context=%s", ctx_snap)
        return "Necesito datos del flujo para actualizar el lead."

    id_bitrix = getattr(runtime.context, "id_bitrix", None) if runtime else None
    if not id_bitrix:
        logger.warning("[TOOL:agendar_llamada] NO_ID_BITRIX context=%s", ctx_snap)
        return "No puedo actualizar el lead en este momento."

    try:
        with track_tool_execution("agendar_llamada"):
            result = await actualizar_lead_llamada_y_crear_task(
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
                resumen=resumen,
            )
    except Exception as e:
        elapsed_ms = int((time.perf_counter() - start) * 1000)
        logger.error("[TOOL:agendar_llamada] UNEXPECTED_ERROR id_bitrix=%s duration_ms=%d err=%s", id_bitrix, elapsed_ms, e, exc_info=True)
        return "No pude actualizar el lead en este momento. Intenta nuevamente."

    elapsed_ms = int((time.perf_counter() - start) * 1000)

    if result["success"]:
        if runtime and runtime.context:
            runtime.context.event = "callback_solicitado"
            logger.info("[TOOL:agendar_llamada] EVENT_SET event=callback_solicitado id_bitrix=%s duration_ms=%d request_id=%s", id_bitrix, elapsed_ms, result.get("request_id"))
        return "Lead actualizado correctamente."

    resolve_errors = result.get("resolve_errors") or []
    if resolve_errors:
        first_error = resolve_errors[0]
        field = first_error.get("field", "campo")
        message = first_error.get("message", "error de validación")
        options = first_error.get("options")
        logger.warning("[TOOL:agendar_llamada] VALIDATION_REJECTED id_bitrix=%s duration_ms=%d field=%s message=%s", id_bitrix, elapsed_ms, field, message)
        if isinstance(options, list) and options:
            options_text = ", ".join(str(option) for option in options[:5])
            return f"No pude actualizar el lead: {field}. {message}. Opciones: {options_text}."
        return f"No pude actualizar el lead: {field}. {message}."

    error_msg = result.get("error")
    logger.warning("[TOOL:agendar_llamada] REJECTED id_bitrix=%s duration_ms=%d error=%s", id_bitrix, elapsed_ms, error_msg)
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
    ctx_snap = _context_snapshot(runtime)
    logger.info("[TOOL:marcar_desistido] INPUT context=%s", ctx_snap)
    start = time.perf_counter()

    id_bitrix = getattr(runtime.context, "id_bitrix", None) if runtime else None
    if not id_bitrix:
        logger.warning("[TOOL:marcar_desistido] NO_ID_BITRIX context=%s", ctx_snap)
        return "No puedo marcar el lead en este momento."

    try:
        with track_tool_execution("marcar_desistido"):
            result = await marcar_lead_desistido(id_bitrix=id_bitrix)
    except Exception as e:
        elapsed_ms = int((time.perf_counter() - start) * 1000)
        logger.error("[TOOL:marcar_desistido] UNEXPECTED_ERROR id_bitrix=%s duration_ms=%d err=%s", id_bitrix, elapsed_ms, e, exc_info=True)
        return "No pude marcar el lead en este momento. Intenta nuevamente."

    elapsed_ms = int((time.perf_counter() - start) * 1000)

    if result["success"]:
        if runtime and runtime.context:
            runtime.context.event = "desistido"
            logger.info("[TOOL:marcar_desistido] EVENT_SET event=desistido id_bitrix=%s duration_ms=%d request_id=%s", id_bitrix, elapsed_ms, result.get("request_id"))
        return "Lead marcado como desistido."

    error_msg = result.get("error")
    logger.warning("[TOOL:marcar_desistido] REJECTED id_bitrix=%s duration_ms=%d error=%s", id_bitrix, elapsed_ms, error_msg)
    if isinstance(error_msg, str) and error_msg.strip():
        return f"No pude marcar el lead: {error_msg.strip()}."
    return "No pude marcar el lead en este momento."


# Lista de todas las tools disponibles para el agente
AGENT_TOOLS = [buscar_vehiculo, agendar_cita, agendar_llamada, marcar_desistido]

__all__ = ["buscar_vehiculo", "agendar_cita", "agendar_llamada", "marcar_desistido", "AGENT_TOOLS"]
