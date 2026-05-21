"""
Servicio de escritura en CRM Vitrix (sobre Bitrix24).
Actualiza el lead con los campos del flujo comercial (action=edit) y en paralelo
crea una task "Confirmar Cita - IA" (action=task) para el equipo humano.

NOTA: Tool de ESCRITURA — sin retry, sin circuit breaker, sin cache.
La idempotencia se delega al prompt del agente.
"""

import asyncio
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import httpx
import unicodedata

from .. import config as app_config
from ..infra import get_client
from ..logger import get_logger
from ..metrics import track_api_call

logger = get_logger(__name__)

_ACTION = "edit"
_TASK_ACTION = "task"
_TASK_TITLE = "Llamar - confirmar cita IA"
_TASK_DESCRIPTION = "-"
_TASK_DEADLINE_FMT = "%Y-%m-%d %H:%M:%S"
_STATUS_BOT = "Sesión Bot Completada Exitosamente"
_CITA_VALUE = _TASK_TITLE
_LLAMADA_LABEL = "Llamar - Cliente interesado IA"
_DEFAULT_VALUE = "N.A"
_DESISTIDO_REASON = "No respondió a la IA"
_DESISTIDO_STATUS_ID = "Desistido"


def _clean_str(value: str | None) -> str | None:
    if value is None:
        return _DEFAULT_VALUE
    text = value.strip()
    return text or _DEFAULT_VALUE


def _normalize_yes_no(value: object) -> str | None:
    if value is None:
        return _DEFAULT_VALUE
    if isinstance(value, bool):
        return "SÍ" if value else "NO"
    text = str(value).strip()
    if not text:
        return _DEFAULT_VALUE

    normalized = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii").lower()
    normalized = " ".join(normalized.split())

    affirmative_phrases = (
        "si",
        "s",
        "yes",
        "y",
        "true",
        "1",
        "claro",
        "si quiero",
        "quiero financiamiento",
        "requiero financiamiento",
        "quiero financiar",
        "si, requiero financiamiento",
        "si claro",
        "si, claro",
        "afirmativo",
        "con financiamiento",
    )
    negative_phrases = (
        "no",
        "n",
        "false",
        "0",
        "no gracias",
        "no, gracias",
        "contado",
        "compra de contado",
        "sin financiamiento",
        "no requiero financiamiento",
        "prefiero contado",
        "sin credito",
        "sin crédito",
    )

    if normalized in affirmative_phrases or any(phrase in normalized for phrase in ("si ", "si,", "si.", "quiero financiamiento", "requiero financiamiento", "quiero financiar")):
        return "SÍ"
    if normalized in negative_phrases or any(phrase in normalized for phrase in ("no ", "no,", "no.", "compra de contado", "sin financiamiento", "prefiero contado")):
        return "NO"
    return _DEFAULT_VALUE


def _build_payload(
    lead_id: int,
    financing_required: object | None = None,
    corporate_agreement: str | None = None,
    trade_in_vehicle: object | None = None,
    used_vehicle_brand: str | None = None,
    used_vehicle_model: str | None = None,
    used_vehicle_year: str | None = None,
    used_vehicle_mileage: str | None = None,
    purchase_expectation: str | None = None,
    budget_description: str | None = None,
    appointment_datetime: str | None = None,
    resumen: str | None = None,
) -> dict[str, object]:
    payload: dict[str, object] = {
        "action": _ACTION,
        "api_key": app_config.APIKEY_VITRIX,
        "ID": lead_id,
        "UF_CRM_1774974891": _STATUS_BOT,
        "UF_CRM_1728502747862": _CITA_VALUE,
        "STATUS_ID": "Asignado a Vendedor (Sin Contacto)",
    }

    mapped_values: dict[str, object] = {
        "UF_CRM_1653688826": _normalize_yes_no(financing_required),
        "UF_CRM_1774975288": _clean_str(corporate_agreement),
        "UF_CRM_1653599755": _normalize_yes_no(trade_in_vehicle),
        "UF_CRM_1653605302623": _clean_str(used_vehicle_brand),
        "UF_CRM_1653605318164": _clean_str(used_vehicle_model),
        "UF_CRM_1534532283": _clean_str(used_vehicle_year),
        "UF_CRM_1534533297": _clean_str(used_vehicle_mileage),
        "UF_CRM_1721064189466": _clean_str(purchase_expectation),
        "SOURCE_DESCRIPTION": f"Presupuesto: {_clean_str(budget_description)} | Cita: {_clean_str(appointment_datetime)} | Resumen: {_clean_str(resumen)}",
    }

    for key, value in mapped_values.items():
        payload[key] = value

    return payload


async def actualizar_lead_cita(
    id_bitrix: str,
    financing_required: object | None = None,
    corporate_agreement: str | None = None,
    trade_in_vehicle: object | None = None,
    used_vehicle_brand: str | None = None,
    used_vehicle_model: str | None = None,
    used_vehicle_year: str | None = None,
    used_vehicle_mileage: str | None = None,
    purchase_expectation: str | None = None,
    budget_description: str | None = None,
    appointment_datetime: str | None = None,
    resumen: str | None = None,
) -> dict:
    """
    POST directo a Vitrix para actualizar el lead con los campos del flujo.

    Args:
        id_bitrix: ID del lead en Bitrix/Vitrix (string, se castea a int).
        financing_required: Requiere financiamiento (SÍ/NO o booleano).
        corporate_agreement: Convenio corporativo seleccionado.
        trade_in_vehicle: Entrega de vehículo en parte de pago (SÍ/NO o booleano).
        used_vehicle_brand: Marca del vehículo en parte de pago.
        used_vehicle_model: Modelo del vehículo en parte de pago.
        used_vehicle_year: Año del vehículo en parte de pago.
        used_vehicle_mileage: Kilometraje del vehículo en parte de pago.
        purchase_expectation: Expectativa de compra.
        budget_description: Presupuesto textual, por ejemplo "3000 dólares".
        appointment_datetime: Fecha y hora de la cita.
        resumen: Breve resumen de la conversación hasta el momento de agendar la cita.

    Returns:
        Dict con la respuesta normalizada de Vitrix.
    """
    try:
        lead_id = int(id_bitrix)
    except (TypeError, ValueError):
        logger.warning("[VITRIX] id_bitrix inválido — valor=%r", id_bitrix)
        return {
            "success": False,
            "lead_id": None,
            "request_id": None,
            "resolve_errors": [],
            "brand_routed": [],
            "bitrix_response": None,
            "error": "id_bitrix inválido",
            "time_ms": None,
        }

    payload = _build_payload(
        lead_id=lead_id,
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

    logger.info(
        "[VITRIX:edit] INPUT — lead_id=%s campos=%s",
        lead_id,
        sorted(k for k in payload.keys() if k not in {"action", "api_key", "ID"}),
    )
    logger.info("[VITRIX:edit] REQUEST — %s", payload)

    try:
        with track_api_call("vitrix_edit_lead"):
            response = await get_client().post(app_config.VITRIX_API_URL, json=payload)
            try:
                data = response.json()
            except ValueError:
                body_preview = response.text[:300]
                logger.error(
                    "[VITRIX:edit] RESPONSE no JSON — status=%s lead_id=%s body=%s",
                    response.status_code,
                    lead_id,
                    body_preview,
                )
                return {
                    "success": False,
                    "lead_id": lead_id,
                    "request_id": None,
                    "resolve_errors": [],
                    "brand_routed": [],
                    "bitrix_response": None,
                    "error": f"Respuesta no JSON del API (HTTP {response.status_code})",
                    "time_ms": None,
                }
    except httpx.TransportError as e:
        logger.error("[VITRIX:edit] RESPONSE TransportError — lead_id=%s err=%s", lead_id, e, exc_info=True)
        return {
            "success": False,
            "lead_id": lead_id,
            "request_id": None,
            "resolve_errors": [],
            "brand_routed": [],
            "bitrix_response": None,
            "error": f"transport: {e}",
            "time_ms": None,
        }
    except Exception as e:
        logger.error("[VITRIX:edit] RESPONSE Error inesperado — lead_id=%s err=%s", lead_id, e, exc_info=True)
        return {
            "success": False,
            "lead_id": lead_id,
            "request_id": None,
            "resolve_errors": [],
            "brand_routed": [],
            "bitrix_response": None,
            "error": str(e),
            "time_ms": None,
        }

    success = bool(data.get("success"))
    request_id = data.get("request_id")
    resolve_errors = data.get("resolve_errors") or []
    brand_routed = data.get("brand_routed") or []
    bitrix_response = data.get("bitrix_response")
    error_msg = data.get("error")
    time_ms = data.get("time_ms")

    result = {
        "success": success,
        "lead_id": data.get("lead_id", lead_id),
        "request_id": request_id,
        "resolve_errors": resolve_errors,
        "brand_routed": brand_routed,
        "bitrix_response": bitrix_response,
        "error": error_msg,
        "time_ms": time_ms,
    }

    if success:
        logger.info(
            "[VITRIX:edit] RESPONSE OK — lead_id=%s request_id=%s time_ms=%s body=%s",
            result["lead_id"],
            request_id,
            time_ms,
            data,
        )
        return result

    if resolve_errors:
        logger.warning(
            "[VITRIX:edit] RESPONSE validación rechazada — lead_id=%s request_id=%s errors=%s",
            result["lead_id"],
            request_id,
            resolve_errors,
        )
    else:
        logger.warning(
            "[VITRIX:edit] RESPONSE rechazo API — lead_id=%s request_id=%s error=%s status=%s body=%s",
            result["lead_id"],
            request_id,
            error_msg,
            response.status_code,
            data,
        )

    if not error_msg and response.status_code >= 400:
        result["error"] = f"HTTP {response.status_code}"

    return result


async def crear_task_confirmar_cita(id_bitrix: str) -> dict:
    """
    POST a Vitrix (action=task) para crear la task 'Confirmar Cita - IA'.
    Todos los campos salvo id_bitrix son hardcoded; la IA no aporta parámetros.
    """
    try:
        lead_id = int(id_bitrix)
    except (TypeError, ValueError):
        logger.warning("[VITRIX:task] id_bitrix inválido — valor=%r", id_bitrix)
        return {"success": False, "task_id": None, "error": "id_bitrix inválido"}

    deadline = (datetime.now(ZoneInfo(app_config.TIMEZONE)) + timedelta(hours=1)).strftime(_TASK_DEADLINE_FMT)
    payload = {
        "action": _TASK_ACTION,
        "api_key": app_config.APIKEY_VITRIX,
        "ID": lead_id,
        "TITLE": _TASK_TITLE,
        "DESCRIPTION": _TASK_DESCRIPTION,
        "DEADLINE": deadline,
    }

    logger.info(
        "[VITRIX:task] INPUT — lead_id=%s (hardcoded: title=%s description=%s deadline=%s)",
        lead_id,
        _TASK_TITLE,
        _TASK_DESCRIPTION,
        deadline,
    )
    logger.info("[VITRIX:task] REQUEST — %s", payload)

    try:
        with track_api_call("vitrix_crear_task"):
            response = await get_client().post(app_config.VITRIX_API_URL, json=payload)
            response.raise_for_status()
            data = response.json()
    except httpx.HTTPStatusError as e:
        logger.error(
            "[VITRIX:task] RESPONSE HTTP %s — lead_id=%s body=%s",
            e.response.status_code,
            lead_id,
            e.response.text[:300],
            exc_info=True,
        )
        return {"success": False, "task_id": None, "error": f"HTTP {e.response.status_code}"}
    except httpx.TransportError as e:
        logger.error("[VITRIX:task] RESPONSE TransportError — lead_id=%s err=%s", lead_id, e, exc_info=True)
        return {"success": False, "task_id": None, "error": f"transport: {e}"}
    except Exception as e:
        logger.error("[VITRIX:task] RESPONSE Error inesperado — lead_id=%s err=%s", lead_id, e, exc_info=True)
        return {"success": False, "task_id": None, "error": str(e)}

    logger.info("[VITRIX:task] RESPONSE — lead_id=%s body=%s", lead_id, data)
    success = bool(data.get("success"))
    task_id = data.get("task_id")
    if success:
        return {"success": True, "task_id": task_id, "error": None}
    return {"success": False, "task_id": task_id, "error": data.get("error") or "desconocido"}


async def actualizar_lead_y_crear_task(
    id_bitrix: str,
    **edit_kwargs,
) -> dict:
    """
    Ejecuta action=edit y luego, 5 segundos después en background, action=task.
    El delay garantiza que Vitrix haya procesado el edit antes de recibir la task.
    Devuelve el resultado de edit inmediatamente; la task corre fire-and-forget.
    """
    edit_result = await actualizar_lead_cita(id_bitrix=id_bitrix, **edit_kwargs)

    async def _delayed_task():
        await asyncio.sleep(5)
        task_result = await crear_task_confirmar_cita(id_bitrix=id_bitrix)
        if not task_result.get("success"):
            logger.warning("[VITRIX:task] Task no creada — %s", task_result)

    asyncio.create_task(_delayed_task())

    return edit_result


def _build_payload_llamada(
    lead_id: int,
    financing_required: object | None = None,
    corporate_agreement: str | None = None,
    trade_in_vehicle: object | None = None,
    used_vehicle_brand: str | None = None,
    used_vehicle_model: str | None = None,
    used_vehicle_year: str | None = None,
    used_vehicle_mileage: str | None = None,
    purchase_expectation: str | None = None,
    budget_description: str | None = None,
    resumen: str | None = None,
) -> dict[str, object]:
    payload: dict[str, object] = {
        "action": _ACTION,
        "api_key": app_config.APIKEY_VITRIX,
        "ID": lead_id,
        "UF_CRM_1774974891": _STATUS_BOT,
        "UF_CRM_1728502747862": _LLAMADA_LABEL,
        "STATUS_ID": "Asignado a Vendedor (Sin Contacto)",
    }

    mapped_values: dict[str, object] = {
        "UF_CRM_1653688826": _normalize_yes_no(financing_required),
        "UF_CRM_1774975288": _clean_str(corporate_agreement),
        "UF_CRM_1653599755": _normalize_yes_no(trade_in_vehicle),
        "UF_CRM_1653605302623": _clean_str(used_vehicle_brand),
        "UF_CRM_1653605318164": _clean_str(used_vehicle_model),
        "UF_CRM_1534532283": _clean_str(used_vehicle_year),
        "UF_CRM_1534533297": _clean_str(used_vehicle_mileage),
        "UF_CRM_1721064189466": _clean_str(purchase_expectation),
        "SOURCE_DESCRIPTION": f"Presupuesto: {_clean_str(budget_description)} | Resumen: {_clean_str(resumen)}",
    }

    for key, value in mapped_values.items():
        payload[key] = value

    return payload


async def actualizar_lead_llamada(
    id_bitrix: str,
    financing_required: object | None = None,
    corporate_agreement: str | None = None,
    trade_in_vehicle: object | None = None,
    used_vehicle_brand: str | None = None,
    used_vehicle_model: str | None = None,
    used_vehicle_year: str | None = None,
    used_vehicle_mileage: str | None = None,
    purchase_expectation: str | None = None,
    budget_description: str | None = None,
    resumen: str | None = None,
) -> dict:
    """
    POST directo a Vitrix para actualizar el lead en el flujo callback (sin appointment_datetime).

    Args:
        id_bitrix: ID del lead en Bitrix/Vitrix (string, se castea a int).
        financing_required: Requiere financiamiento (SÍ/NO o booleano).
        corporate_agreement: Convenio corporativo seleccionado.
        trade_in_vehicle: Entrega de vehículo en parte de pago (SÍ/NO o booleano).
        used_vehicle_brand: Marca del vehículo en parte de pago.
        used_vehicle_model: Modelo del vehículo en parte de pago.
        used_vehicle_year: Año del vehículo en parte de pago.
        used_vehicle_mileage: Kilometraje del vehículo en parte de pago.
        purchase_expectation: Expectativa de compra.
        budget_description: Presupuesto textual, por ejemplo "3000 dólares".
        resumen: Breve resumen de la conversación hasta el momento del callback.

    Returns:
        Dict con la respuesta normalizada de Vitrix.
    """
    try:
        lead_id = int(id_bitrix)
    except (TypeError, ValueError):
        logger.warning("[VITRIX] id_bitrix inválido — valor=%r", id_bitrix)
        return {
            "success": False,
            "lead_id": None,
            "request_id": None,
            "resolve_errors": [],
            "brand_routed": [],
            "bitrix_response": None,
            "error": "id_bitrix inválido",
            "time_ms": None,
        }

    payload = _build_payload_llamada(
        lead_id=lead_id,
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

    logger.info(
        "[VITRIX:edit_llamada] INPUT — lead_id=%s campos=%s",
        lead_id,
        sorted(k for k in payload.keys() if k not in {"action", "api_key", "ID"}),
    )
    logger.info("[VITRIX:edit_llamada] REQUEST — %s", payload)

    try:
        with track_api_call("vitrix_edit_llamada"):
            response = await get_client().post(app_config.VITRIX_API_URL, json=payload)
            try:
                data = response.json()
            except ValueError:
                body_preview = response.text[:300]
                logger.error(
                    "[VITRIX:edit_llamada] RESPONSE no JSON — status=%s lead_id=%s body=%s",
                    response.status_code,
                    lead_id,
                    body_preview,
                )
                return {
                    "success": False,
                    "lead_id": lead_id,
                    "request_id": None,
                    "resolve_errors": [],
                    "brand_routed": [],
                    "bitrix_response": None,
                    "error": f"Respuesta no JSON del API (HTTP {response.status_code})",
                    "time_ms": None,
                }
    except httpx.TransportError as e:
        logger.error("[VITRIX:edit_llamada] RESPONSE TransportError — lead_id=%s err=%s", lead_id, e, exc_info=True)
        return {
            "success": False,
            "lead_id": lead_id,
            "request_id": None,
            "resolve_errors": [],
            "brand_routed": [],
            "bitrix_response": None,
            "error": f"transport: {e}",
            "time_ms": None,
        }
    except Exception as e:
        logger.error("[VITRIX:edit_llamada] RESPONSE Error inesperado — lead_id=%s err=%s", lead_id, e, exc_info=True)
        return {
            "success": False,
            "lead_id": lead_id,
            "request_id": None,
            "resolve_errors": [],
            "brand_routed": [],
            "bitrix_response": None,
            "error": str(e),
            "time_ms": None,
        }

    success = bool(data.get("success"))
    request_id = data.get("request_id")
    resolve_errors = data.get("resolve_errors") or []
    brand_routed = data.get("brand_routed") or []
    bitrix_response = data.get("bitrix_response")
    error_msg = data.get("error")
    time_ms = data.get("time_ms")

    result = {
        "success": success,
        "lead_id": data.get("lead_id", lead_id),
        "request_id": request_id,
        "resolve_errors": resolve_errors,
        "brand_routed": brand_routed,
        "bitrix_response": bitrix_response,
        "error": error_msg,
        "time_ms": time_ms,
    }

    if success:
        logger.info(
            "[VITRIX:edit_llamada] RESPONSE OK — lead_id=%s request_id=%s time_ms=%s body=%s",
            result["lead_id"],
            request_id,
            time_ms,
            data,
        )
        return result

    if resolve_errors:
        logger.warning(
            "[VITRIX:edit_llamada] RESPONSE validación rechazada — lead_id=%s request_id=%s errors=%s",
            result["lead_id"],
            request_id,
            resolve_errors,
        )
    else:
        logger.warning(
            "[VITRIX:edit_llamada] RESPONSE rechazo API — lead_id=%s request_id=%s error=%s status=%s body=%s",
            result["lead_id"],
            request_id,
            error_msg,
            response.status_code,
            data,
        )

    if not error_msg and response.status_code >= 400:
        result["error"] = f"HTTP {response.status_code}"

    return result


async def crear_task_llamada(id_bitrix: str) -> dict:
    """
    POST a Vitrix (action=task) para crear la task 'Llamar - Cliente interesado IA'.
    Todos los campos salvo id_bitrix son hardcoded; la IA no aporta parámetros.
    """
    try:
        lead_id = int(id_bitrix)
    except (TypeError, ValueError):
        logger.warning("[VITRIX:task_llamada] id_bitrix inválido — valor=%r", id_bitrix)
        return {"success": False, "task_id": None, "error": "id_bitrix inválido"}

    deadline = (datetime.now(ZoneInfo(app_config.TIMEZONE)) + timedelta(hours=1)).strftime(_TASK_DEADLINE_FMT)
    payload = {
        "action": _TASK_ACTION,
        "api_key": app_config.APIKEY_VITRIX,
        "ID": lead_id,
        "TITLE": _LLAMADA_LABEL,
        "DESCRIPTION": _TASK_DESCRIPTION,
        "DEADLINE": deadline,
    }

    logger.info(
        "[VITRIX:task_llamada] INPUT — lead_id=%s (hardcoded: title=%s description=%s deadline=%s)",
        lead_id,
        _LLAMADA_LABEL,
        _TASK_DESCRIPTION,
        deadline,
    )
    logger.info("[VITRIX:task_llamada] REQUEST — %s", payload)

    try:
        with track_api_call("vitrix_crear_task_llamada"):
            response = await get_client().post(app_config.VITRIX_API_URL, json=payload)
            response.raise_for_status()
            data = response.json()
    except httpx.HTTPStatusError as e:
        logger.error(
            "[VITRIX:task_llamada] RESPONSE HTTP %s — lead_id=%s body=%s",
            e.response.status_code,
            lead_id,
            e.response.text[:300],
            exc_info=True,
        )
        return {"success": False, "task_id": None, "error": f"HTTP {e.response.status_code}"}
    except httpx.TransportError as e:
        logger.error("[VITRIX:task_llamada] RESPONSE TransportError — lead_id=%s err=%s", lead_id, e, exc_info=True)
        return {"success": False, "task_id": None, "error": f"transport: {e}"}
    except Exception as e:
        logger.error("[VITRIX:task_llamada] RESPONSE Error inesperado — lead_id=%s err=%s", lead_id, e, exc_info=True)
        return {"success": False, "task_id": None, "error": str(e)}

    logger.info("[VITRIX:task_llamada] RESPONSE — lead_id=%s body=%s", lead_id, data)
    success = bool(data.get("success"))
    task_id = data.get("task_id")
    if success:
        return {"success": True, "task_id": task_id, "error": None}
    return {"success": False, "task_id": task_id, "error": data.get("error") or "desconocido"}


async def actualizar_lead_llamada_y_crear_task(
    id_bitrix: str,
    **edit_kwargs,
) -> dict:
    """
    Ejecuta action=edit (flujo llamada) y luego, 5 segundos después en background, action=task.
    Devuelve el resultado de edit inmediatamente; la task corre fire-and-forget.
    """
    edit_result = await actualizar_lead_llamada(id_bitrix=id_bitrix, **edit_kwargs)

    async def _delayed_task():
        await asyncio.sleep(5)
        task_result = await crear_task_llamada(id_bitrix=id_bitrix)
        if not task_result.get("success"):
            logger.warning("[VITRIX:task_llamada] Task no creada — %s", task_result)

    asyncio.create_task(_delayed_task())

    return edit_result


async def marcar_lead_desistido(id_bitrix: str) -> dict:
    """
    POST directo a Vitrix (action=edit) para marcar el lead como desistido.
    Payload 100% hardcoded salvo id_bitrix.
    """
    try:
        lead_id = int(id_bitrix)
    except (TypeError, ValueError):
        logger.warning("[VITRIX:desistido] id_bitrix inválido — valor=%r", id_bitrix)
        return {
            "success": False,
            "lead_id": None,
            "request_id": None,
            "error": "id_bitrix inválido",
        }

    payload = {
        "action": _ACTION,
        "api_key": app_config.APIKEY_VITRIX,
        "ID": lead_id,
        "UF_CRM_1774974891": _STATUS_BOT,
        "UF_CRM_1559082118": _DESISTIDO_REASON,
        "STATUS_ID": _DESISTIDO_STATUS_ID,
    }

    logger.info("[VITRIX:desistido] REQUEST — %s", payload)

    try:
        with track_api_call("vitrix_desistido"):
            response = await get_client().post(app_config.VITRIX_API_URL, json=payload)
            try:
                data = response.json()
            except ValueError:
                body_preview = response.text[:300]
                logger.error(
                    "[VITRIX:desistido] RESPONSE no JSON — status=%s lead_id=%s body=%s",
                    response.status_code,
                    lead_id,
                    body_preview,
                )
                return {
                    "success": False,
                    "lead_id": lead_id,
                    "request_id": None,
                    "error": f"Respuesta no JSON del API (HTTP {response.status_code})",
                }
    except httpx.TransportError as e:
        logger.error("[VITRIX:desistido] TransportError — lead_id=%s err=%s", lead_id, e, exc_info=True)
        return {"success": False, "lead_id": lead_id, "request_id": None, "error": f"transport: {e}"}
    except Exception as e:
        logger.error("[VITRIX:desistido] Error inesperado — lead_id=%s err=%s", lead_id, e, exc_info=True)
        return {"success": False, "lead_id": lead_id, "request_id": None, "error": str(e)}

    success = bool(data.get("success"))
    result = {
        "success": success,
        "lead_id": data.get("lead_id", lead_id),
        "request_id": data.get("request_id"),
        "error": data.get("error"),
    }

    if success:
        logger.info("[VITRIX:desistido] RESPONSE OK — lead_id=%s body=%s", result["lead_id"], data)
    else:
        logger.warning("[VITRIX:desistido] RESPONSE rechazo — lead_id=%s body=%s", result["lead_id"], data)

    return result


__all__ = [
    "actualizar_lead_cita",
    "crear_task_confirmar_cita",
    "actualizar_lead_y_crear_task",
    "actualizar_lead_llamada",
    "crear_task_llamada",
    "actualizar_lead_llamada_y_crear_task",
    "marcar_lead_desistido",
]
