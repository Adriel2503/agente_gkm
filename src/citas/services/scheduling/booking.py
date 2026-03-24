"""
Función para crear evento en el calendario (ws_calendario.php).
Alineado con CREAR_EVENTO: usuario_id, id_prospecto, titulo, fecha_inicio, fecha_fin,
correo_cliente, correo_usuario, agendar_usuario.
"""

import json

import httpx
from typing import Any

from ...logger import get_logger
from ...metrics import track_api_call, record_booking_attempt, record_booking_success, record_booking_failure
from ... import config as app_config
from ...infra import get_client
from ...config import calendario_cb
from .time_parser import build_fecha_inicio_fin

logger = get_logger(__name__)



async def confirm_booking(
    usuario_id: int,
    phone: str,
    nombre_completo: str,
    correo_cliente: str,
    fecha: str,
    hora: str,
    agendar_usuario: int,
    duracion_cita_minutos: int,
    correo_usuario: str = "",
    log_create_booking_apis: bool = False,
) -> dict[str, Any]:
    """
    Crea un evento en el calendario (ws_calendario.php, CREAR_EVENTO).

    Args:
        usuario_id: ID del usuario (vendedor) que registra la cita
        phone: Teléfono del cliente (str). Se envía como id_prospecto al PHP.
        nombre_completo: Nombre completo del cliente
        correo_cliente: Email del cliente (correo_cliente en API)
        fecha: Fecha en formato YYYY-MM-DD
        hora: Hora en formato HH:MM AM/PM
        agendar_usuario: 1 = asignar vendedor automáticamente, 0 = no
        duracion_cita_minutos: Minutos de la cita para calcular fecha_fin
        correo_usuario: Email del usuario/vendedor (desde orquestador)

    Returns:
        Dict con: success, message, error
    """
    record_booking_attempt()

    try:
        fecha_inicio, fecha_fin = build_fecha_inicio_fin(fecha, hora, duracion_cita_minutos)
    except ValueError as e:
        logger.warning("[BOOKING] Fecha/hora inválidos: %s", e)
        record_booking_failure("invalid_datetime")
        return {
            "success": False,
            "message": "Formato de fecha u hora inválido",
            "error": str(e),
        }

    try:
        titulo = f"Reunion para el usuario: {nombre_completo}"

        payload = {
            "codOpe": "CREAR_EVENTO",
            "usuario_id": usuario_id,
            "id_prospecto": phone,
            "titulo": titulo,
            "fecha_inicio": fecha_inicio,
            "fecha_fin": fecha_fin,
            "correo_cliente": (correo_cliente or "").strip(),
            "correo_usuario": (correo_usuario or "").strip(),
            "agendar_usuario": agendar_usuario,
        }

        # Circuit breaker: si ws_calendario.php acumula 3 TransportErrors → fallo rápido
        if calendario_cb.is_open("global"):
            logger.warning("[BOOKING] Circuit abierto para ws_calendario.php — fallo rápido")
            record_booking_failure("circuit_open")
            return {
                "success": False,
                "message": "El servicio de calendario no está disponible en este momento. Por favor intenta en unos minutos.",
                "error": "circuit_open",
            }

        if log_create_booking_apis:
            logger.info("[create_booking] API 3: ws_calendario.php - CREAR_EVENTO")
            logger.info("  URL: %s", app_config.API_CALENDAR_URL)
            logger.info("  Enviado: %s", json.dumps(payload, ensure_ascii=False))
        logger.debug("[BOOKING] Creando evento: %s %s - %s", fecha, hora, nombre_completo)
        logger.debug("[BOOKING] Payload: %s", payload)
        logger.debug("[BOOKING] JSON enviado a ws_calendario.php (CREAR_EVENTO): %s", json.dumps(payload, ensure_ascii=False, indent=2))

        with track_api_call("crear_evento"):
            client = get_client()
            response = await client.post(app_config.API_CALENDAR_URL, json=payload)
            response.raise_for_status()
            data = response.json()

        if log_create_booking_apis:
            logger.info("  Respuesta: %s", json.dumps(data, ensure_ascii=False))
        logger.debug("[BOOKING] Respuesta API: %s", data)

        if data.get("success"):
            message = data.get("message") or "Evento creado correctamente"
            logger.info("[BOOKING] Evento creado - %s", message)
            calendario_cb.record_success("global")
            record_booking_success()
            result = {
                "success": True,
                "message": message,
                "error": None,
            }
            # Pasar respuesta de ws_calendario para que el agente responda al usuario
            if data.get("google_meet_link"):
                result["google_meet_link"] = data["google_meet_link"]
            result["google_calendar_synced"] = data.get("google_calendar_synced", False)
            if data.get("google_calendar_error"):
                result["google_calendar_error"] = data["google_calendar_error"]
            return result
        else:
            error_msg = data.get("message") or data.get("error") or "Error desconocido"
            logger.warning("[BOOKING] Creación fallida: %s", error_msg)
            record_booking_failure("api_error")
            return {
                "success": False,
                "message": error_msg,
                "error": error_msg,
            }

    except httpx.TimeoutException:
        # TimeoutException es subclase de RequestError — debe ir ANTES.
        # Si se invierte el orden, ambos bloques siguen llamando record_failure
        # (el CB no se rompe), pero se pierde la etiqueta de métrica "timeout"
        # y el mensaje correcto al usuario.
        calendario_cb.record_failure("global")
        logger.error("[BOOKING] Timeout al crear evento")
        record_booking_failure("timeout")
        return {
            "success": False,
            "message": "La conexión tardó demasiado tiempo",
            "error": "timeout",
        }

    except httpx.HTTPStatusError as e:
        # El servidor respondió con error HTTP → no abre el circuit (está up)
        logger.error("[BOOKING] Error HTTP %s: %s", e.response.status_code, e)
        record_booking_failure(f"http_{e.response.status_code}")
        return {
            "success": False,
            "message": f"Error del servidor ({e.response.status_code})",
            "error": str(e),
        }

    except httpx.RequestError as e:
        # Captura errores de transporte no cubiertos arriba (ej. ConnectError, ReadError).
        calendario_cb.record_failure("global")
        logger.error("[BOOKING] Error de conexión: %s", e)
        record_booking_failure("connection_error")
        return {
            "success": False,
            "message": "Error al conectar con el servidor",
            "error": str(e),
        }

    except Exception as e:
        logger.error("[BOOKING] Error inesperado: %s", e, exc_info=True)
        record_booking_failure("unknown_error")
        return {
            "success": False,
            "message": "Error inesperado al crear el evento",
            "error": str(e),
        }


__all__ = ["confirm_booking"]
