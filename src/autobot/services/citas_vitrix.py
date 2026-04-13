"""
Servicio de escritura en CRM Vitrix (sobre Bitrix24).
Crea tasks para que el equipo humano las procese (ej. confirmar citas).

NOTA: Tool de ESCRITURA — sin retry, sin circuit breaker, sin cache.
La idempotencia se delega al prompt del agente (controla no llamar duplicado
usando el historial de conversación).
"""

from datetime import datetime
from zoneinfo import ZoneInfo

import httpx

from .. import config as app_config
from ..infra import get_client
from ..logger import get_logger
from ..metrics import track_api_call

logger = get_logger(__name__)

_ACTION = "task"
_TITLE = "Confirmar Cita - IA"
_DEADLINE_FMT = "%Y-%m-%d %H:%M:%S"


async def crear_task_confirmar_cita(id_bitrix: str, description: str) -> dict:
    """
    POST directo a Vitrix para crear task "Confirmar Cita - IA".

    Args:
        id_bitrix: ID del lead en Bitrix/Vitrix (string, se castea a int).
        description: Detalles de la cita (modelo, sucursal, fecha elegida, etc.).

    Returns:
        {"success": bool, "task_id": int | None, "error": str | None}
    """
    try:
        lead_id = int(id_bitrix)
    except (TypeError, ValueError):
        logger.warning("[VITRIX] id_bitrix inválido — valor=%r", id_bitrix)
        return {"success": False, "task_id": None, "error": "id_bitrix inválido"}

    deadline = datetime.now(ZoneInfo(app_config.TIMEZONE)).strftime(_DEADLINE_FMT)

    payload = {
        "action": _ACTION,
        "api_key": app_config.APIKEY_VITRIX,
        "ID": lead_id,
        "TITLE": _TITLE,
        "DESCRIPTION": description,
        "DEADLINE": deadline,
    }

    logger.info(
        "[VITRIX] POST crear task — lead_id=%s deadline=%s desc_preview=%s",
        lead_id, deadline, description[:80],
    )

    try:
        with track_api_call("vitrix_crear_task"):
            response = await get_client().post(app_config.VITRIX_API_URL, json=payload)
            response.raise_for_status()
            data = response.json()
    except httpx.HTTPStatusError as e:
        logger.error(
            "[VITRIX] HTTP %s — lead_id=%s body=%s",
            e.response.status_code, lead_id, e.response.text[:300],
            exc_info=True,
        )
        return {"success": False, "task_id": None, "error": f"HTTP {e.response.status_code}"}
    except httpx.TransportError as e:
        logger.error("[VITRIX] TransportError — lead_id=%s err=%s", lead_id, e, exc_info=True)
        return {"success": False, "task_id": None, "error": f"transport: {e}"}
    except Exception as e:
        logger.error("[VITRIX] Error inesperado — lead_id=%s err=%s", lead_id, e, exc_info=True)
        return {"success": False, "task_id": None, "error": str(e)}

    success = bool(data.get("success"))
    task_id = data.get("task_id")
    request_id = data.get("request_id")
    time_ms = data.get("time_ms")

    if success:
        logger.info(
            "[VITRIX] Task creada — task_id=%s lead_id=%s request_id=%s time_ms=%s",
            task_id, lead_id, request_id, time_ms,
        )
        return {"success": True, "task_id": task_id, "error": None}

    error_msg = data.get("error") or "desconocido"
    logger.warning(
        "[VITRIX] API rechazó — error=%s lead_id=%s request_id=%s time_ms=%s",
        error_msg, lead_id, request_id, time_ms,
    )
    return {"success": False, "task_id": None, "error": error_msg}


__all__ = ["crear_task_confirmar_cita"]
