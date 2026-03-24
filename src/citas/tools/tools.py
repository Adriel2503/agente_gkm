"""
Tools internas del agente de citas comerciales.
Estas tools son usadas por el LLM a través de function calling,
NO están expuestas directamente al orquestador.

Versión mejorada con logging, métricas, validación y runtime context (LangChain 1.2+).
"""

from typing import Any
from langchain.tools import tool, ToolRuntime
from pydantic import ValidationError

from ..services.scheduling import ScheduleValidator, ScheduleRecommender, confirm_booking
from ..services.busqueda_productos import buscar_productos_servicios, format_productos_para_respuesta
from ..services.busqueda_kia import buscar_modelos_kia, format_kia_resultados
from ..logger import get_logger
from ..metrics import track_tool_execution, record_tool_validation_error
from .validation import BookingData, format_validation_error, validate_date_format

logger = get_logger(__name__)


def _require_context(runtime: ToolRuntime | None, tool_name: str):
    """
    Extrae el AgentContext del runtime. No usa fallback id_empresa=1:
    en multi-tenant filtrar datos entre empresas sería un fallo silencioso grave.
    Raises:
        RuntimeError: si runtime o runtime.context no está disponible.
    """
    ctx = getattr(runtime, "context", None) if runtime is not None else None
    if ctx is None:
        logger.warning(
            "[TOOL:%s] runtime.context es None — operación rechazada. "
            "No se usa fallback para evitar filtrado entre empresas.",
            tool_name,
        )
        raise RuntimeError(f"[{tool_name}] AgentContext no disponible")
    return ctx


def _check_required_config(checks: dict[str, Any], tool_name: str) -> str | None:
    """
    Valida que los campos requeridos del orquestador no sean None.
    Retorna mensaje de error si falta alguno, None si todo OK.

    Args:
        checks: dict {etiqueta_legible: valor} — cada valor None se reporta como faltante.
        tool_name: nombre de la tool (para logging y métricas).
    """
    missing = [label for label, value in checks.items() if value is None]
    if missing:
        logger.error("[TOOL:%s] Configuración incompleta del orquestador: %s", tool_name, missing)
        record_tool_validation_error(tool_name)
        return (
            f"No puedo procesar la solicitud: falta configurar {' y '.join(missing)}. "
            "Por favor contacta al administrador."
        )
    return None


@tool
async def check_availability(
    date: str,
    time: str | None = None,
    runtime: ToolRuntime = None
) -> str:
    """
    Consulta horarios disponibles para una cita/reunión y fecha (y opcionalmente hora).

    Usa esta herramienta cuando el cliente pregunte por disponibilidad
    o cuando necesites verificar si una fecha/hora específica está libre.

    Si el cliente indicó una hora concreta (ej. "a las 2pm", "a las 14:00"), pásala en time
    para consultar disponibilidad exacta de ese slot (CONSULTAR_DISPONIBILIDAD).
    Si no pasas time, se devuelven sugerencias para hoy/mañana (SUGERIR_HORARIOS).
    Sin time, las sugerencias solo están disponibles para hoy y mañana; para otras fechas el cliente debe indicar también la hora.

    Args:
        date: Fecha en formato ISO (YYYY-MM-DD)
        time: Hora opcional en formato HH:MM AM/PM (ej. "2:00 PM") o 24h. Si el cliente dijo una hora concreta, pásala aquí.
        runtime: Runtime context automático (inyectado por LangChain)

    Returns:
        Texto con horarios disponibles o sugerencias para esa fecha/hora

    Examples:
        >>> await check_availability("2026-01-27")
        "Horarios sugeridos: Lunes 27/01 - 09:00 AM, 10:00 AM, 02:00 PM..."
        >>> await check_availability("2026-01-31", "2:00 PM")
        "El 2026-01-31 a las 2:00 PM está disponible. ¿Confirmamos la cita?"
    """
    logger.debug("[TOOL] check_availability - Fecha: %s, Hora: %s", date, time or "no indicada")

    is_valid, error = validate_date_format(date)
    if not is_valid:
        return error

    try:
        # Obtener configuración del runtime context — falla si no hay contexto
        ctx = _require_context(runtime, "check_availability")
        id_empresa = ctx.id_empresa
        duracion_cita_minutos = ctx.duracion_cita_minutos
        slots = ctx.slots
        agendar_usuario = ctx.agendar_usuario
        agendar_sucursal = ctx.agendar_sucursal

        missing = _check_required_config({
            "duración de cita": duracion_cita_minutos,
            "capacidad de slots": slots,
        }, "check_availability")
        if missing:
            return missing

        with track_tool_execution("check_availability"):
            recommender = ScheduleRecommender(
                id_empresa=id_empresa,
                duracion_cita_minutos=duracion_cita_minutos,
                slots=slots,
                agendar_usuario=agendar_usuario,
                agendar_sucursal=agendar_sucursal,
            )
            recommendations = await recommender.recommendation(
                fecha_solicitada=date,
                hora_solicitada=time.strip() if time and time.strip() else None,
            )
            
            if recommendations and recommendations.get("text"):
                logger.debug("[TOOL] check_availability - Recomendaciones obtenidas")
                return recommendations["text"]
            else:
                logger.warning("[TOOL] check_availability - Sin recomendaciones, usando fallback")
                return f"Horarios disponibles para el {date}. Consulta directamente para más detalles."

    except Exception as e:
        logger.error("[TOOL] check_availability - Error: %s", e, exc_info=True)
        return "No pude consultar disponibilidad ahora. Indica una fecha y hora y la verifico, o intenta en un momento."


@tool
async def create_booking(
    date: str,
    time: str,
    customer_name: str,
    customer_contact: str,
    runtime: ToolRuntime = None
) -> str:
    """
    Crea una nueva cita (evento en calendario) con validación y confirmación real.

    Usa esta herramienta SOLO cuando tengas TODOS los datos necesarios:
    - Fecha (YYYY-MM-DD), Hora (HH:MM AM/PM)
    - Nombre completo del cliente, Email del cliente (customer_contact)

    Solo invocar después de confirmar con el cliente fecha, hora, nombre y correo.

    La herramienta validará el horario y creará el evento en ws_calendario (CREAR_EVENTO).
    La respuesta puede incluir enlace de videollamada (Google Meet) o mensaje de cita confirmada.

    Args:
        date: Fecha de la cita (YYYY-MM-DD)
        time: Hora de la cita (HH:MM AM/PM)
        customer_name: Nombre completo del cliente
        customer_contact: Email del cliente (ej: cliente@ejemplo.com)
        runtime: Runtime context automático (inyectado por LangChain)

    Returns:
        Mensaje de confirmación, detalles (fecha, hora, nombre) y, si aplica,
        enlace de videollamada o aviso de "cita confirmada"; o mensaje de error

    Examples:
        >>> await create_booking("2026-01-27", "02:00 PM", "Juan Pérez", "cliente@ejemplo.com")
        "Evento agregado correctamente. Detalles: ... La reunión será por videollamada. Enlace: https://meet.google.com/..."
    """
    logger.debug("[TOOL] create_booking - %s %s | %s", date, time, customer_name)
    logger.info("[create_booking] Tool en uso: create_booking")

    is_valid, error = validate_date_format(date)
    if not is_valid:
        return error

    try:
        # Obtener configuración del runtime context — falla si no hay contexto
        ctx = _require_context(runtime, "create_booking")
        id_empresa = ctx.id_empresa
        duracion_cita_minutos = ctx.duracion_cita_minutos
        slots = ctx.slots
        agendar_usuario = ctx.agendar_usuario
        agendar_sucursal = ctx.agendar_sucursal
        phone = ctx.phone
        usuario_id = ctx.usuario_id
        correo_usuario = ctx.correo_usuario

        missing = _check_required_config({
            "duración de cita": duracion_cita_minutos,
            "capacidad de slots": slots,
            "ID de usuario/vendedor": usuario_id,
            "correo del usuario/vendedor": correo_usuario,
        }, "create_booking")
        if missing:
            return missing

        with track_tool_execution("create_booking"):
            # 1. VALIDAR datos de entrada y normalizar (email lowercase, nombre title-case)
            logger.debug("[TOOL] create_booking - Validando datos de entrada")
            try:
                bd = BookingData(
                    date=date,
                    time=time,
                    customer_name=customer_name,
                    customer_contact=customer_contact,
                )
            except ValidationError as e:
                error = format_validation_error(e)
                logger.warning("[TOOL] create_booking - Datos inválidos: %s", error)
                record_tool_validation_error("create_booking")
                return f"Datos inválidos: {error}\n\nPor favor verifica la información."

            # 2. VALIDAR horario con ScheduleValidator
            logger.debug("[TOOL] create_booking - Validando horario")
            validator = ScheduleValidator(
                id_empresa=id_empresa,
                duracion_cita_minutos=duracion_cita_minutos,
                slots=slots,
                agendar_usuario=agendar_usuario,
                agendar_sucursal=agendar_sucursal,
                log_create_booking_apis=True,
            )

            validation = await validator.validate(date, time)
            logger.debug("[TOOL] create_booking - Validación: %s", validation)

            if not validation["valid"]:
                logger.warning("[TOOL] create_booking - Horario no válido: %s", validation["error"])
                return f"{validation['error']}\n\nPor favor elige otra fecha u hora."

            # 3. Crear evento en ws_calendario (CREAR_EVENTO)
            logger.debug("[TOOL] create_booking - Creando evento en API")
            booking_result = await confirm_booking(
                usuario_id=usuario_id,
                phone=phone,
                nombre_completo=bd.customer_name,
                correo_cliente=bd.customer_contact,
                fecha=date,
                hora=time,
                agendar_usuario=agendar_usuario,
                duracion_cita_minutos=duracion_cita_minutos,
                correo_usuario=correo_usuario,
                log_create_booking_apis=True,
            )
            
            logger.debug("[TOOL] create_booking - Resultado: %s", booking_result)

            if booking_result["success"]:
                api_message = booking_result.get("message") or "Evento creado correctamente"
                logger.info("[TOOL] create_booking - Éxito")
                lines = [
                    api_message,
                    "",
                    "Detalles:",
                    f"• Fecha: {date}",
                    f"• Hora: {time}",
                    f"• Nombre: {bd.customer_name}",
                    "",
                ]
                if booking_result.get("google_meet_link"):
                    lines.append(f"La reunión será por videollamada. Enlace: {booking_result['google_meet_link']}")
                elif booking_result.get("google_calendar_synced") is False:
                    lines.append("Tu cita está confirmada. No se pudo generar el enlace de videollamada; te contactaremos con los detalles.")
                lines.append("")
                lines.append("¡Te esperamos!")
                return "\n".join(lines)
            else:
                error_msg = booking_result.get("error") or booking_result.get("message") or "No se pudo confirmar la cita"
                logger.warning("[TOOL] create_booking - Fallo: %s", error_msg)
                return f"{error_msg}\n\nPor favor intenta nuevamente."
    
    except Exception as e:
        logger.error("[TOOL] create_booking - Error inesperado: %s", e, exc_info=True)
        return f"Error inesperado al crear la cita: {str(e)}\n\nPor favor intenta nuevamente."


@tool
async def search_productos_servicios(
    busqueda: str,
    runtime: ToolRuntime = None
) -> str:
    """
    Busca productos y servicios del catálogo por nombre o descripción.
    Úsala cuando el cliente pregunte por algo específico: precios, descripción,
    detalles de un producto o servicio concreto. Devuelve hasta 10 resultados.

    Args:
        busqueda: Término de búsqueda (ej: "NovaX", "demostración", "consultoría")
        runtime: Contexto automático (inyectado por LangChain)

    Returns:
        Texto con los productos/servicios encontrados (precio, categoría, descripción)
    """
    logger.info("[search_productos_servicios] Tool en uso: search_productos_servicios")

    try:
        # Obtener configuración del runtime context — falla si no hay contexto
        ctx = _require_context(runtime, "search_productos_servicios")
        id_empresa = ctx.id_empresa

        logger.debug(
            "[TOOL] search_productos_servicios - id_empresa=%s, busqueda=%s",
            id_empresa, busqueda,
        )
        with track_tool_execution("search_productos_servicios"):
            result = await buscar_productos_servicios(
                id_empresa=id_empresa,
                busqueda=busqueda,
                log_search_apis=True,
            )

            if not result["success"]:
                logger.debug("[TOOL] search_productos_servicios - Respuesta: error=%s", result.get("error"))
                return result.get("error", "No se pudo completar la búsqueda.")

            productos = result.get("productos", [])
            if not productos:
                logger.debug("[TOOL] search_productos_servicios - Respuesta: 0 resultados")
                return f"No encontré productos o servicios que coincidan con '{busqueda}'. Prueba con otros términos."

            logger.debug("[TOOL] search_productos_servicios - Respuesta: %s resultado(s)", len(productos))
            lineas = [f"Encontré {len(productos)} resultado(s) para '{busqueda}':\n"]

            lineas.append(format_productos_para_respuesta(productos))
            return "\n".join(lineas)

    except Exception as e:
        logger.error("[TOOL] search_productos_servicios - Error: %s", e, exc_info=True)
        return f"Error al buscar: {str(e)}. Intenta de nuevo."


@tool
async def search_kia_modelos(
    query: str,
    runtime: ToolRuntime = None
) -> str:
    """
    Busca modelos de autos KIA por similitud semántica en el catálogo.
    Úsala SIEMPRE que el cliente pregunte por modelos, precios, cuotas,
    colores disponibles, fichas técnicas o cualquier dato de los vehículos KIA.

    Devuelve hasta 3 modelos con: nombre, versión, gama, año, precio,
    cuota bancaria, colores, descripción, ficha técnica (PDF) y video.

    Args:
        query: Texto libre de búsqueda (ej: "auto familiar económico", "New Picanto", "SUV menos de 40 mil")

    Returns:
        Información de los modelos KIA más relevantes para la búsqueda
    """
    logger.info("[search_kia_modelos] Tool en uso: search_kia_modelos, query=%s", query)

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

__all__ = ["check_availability", "create_booking", "search_productos_servicios", "search_kia_modelos", "AGENT_TOOLS"]
