"""
Prompts del agente de citas. Builder del system prompt.
"""

import asyncio
from pathlib import Path
from datetime import datetime
from zoneinfo import ZoneInfo

from jinja2 import Environment, FileSystemLoader, select_autoescape

from ... import config as app_config
from ...logger import get_logger
from ...schemas import CitasConfig
from ...services.prompt_data import fetch_contexto_negocio, fetch_horario_reuniones, fetch_nombres_productos_servicios, format_nombres_para_prompt, fetch_preguntas_frecuentes

logger = get_logger(__name__)

_TEMPLATES_DIR = Path(__file__).resolve().parent
_ZONA_PERU = ZoneInfo(app_config.TIMEZONE)

_DIAS_ESPANOL = ["lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"]
_MESES_ESPANOL = [
    "enero", "febrero", "marzo", "abril", "mayo", "junio",
    "julio", "agosto", "septiembre", "octubre", "noviembre", "diciembre",
]

_jinja_env = Environment(
    loader=FileSystemLoader(str(_TEMPLATES_DIR)),
    autoescape=select_autoescape(disabled_extensions=()),
)
_citas_template = _jinja_env.get_template("gqm_system.j2")


def _now_peru() -> datetime:
    """Fecha y hora actual en Perú (America/Lima)."""
    return datetime.now(_ZONA_PERU)


async def build_gqm_system_prompt(
    id_empresa: int,
    config: CitasConfig | None,
) -> str:
    """
    Construye el system prompt del agente de citas.

    Args:
        id_empresa: ID de la empresa (tenant key).
        config: CitasConfig opcional validado por Pydantic.

    Returns:
        System prompt renderizado.
    """
    variables = config.model_dump(exclude_none=True) if config else {}
    variables["id_empresa"] = id_empresa
    variables["archivo_saludo"] = (config.archivo_saludo if config else None or "").strip()

    # Fecha y hora actual en Perú (para que el agente sepa "hoy" y "mañana")
    now = _now_peru()
    variables["fecha_iso"] = now.strftime("%Y-%m-%d")
    variables["hora_actual"] = now.strftime("%I:%M %p")
    dia_nombre = _DIAS_ESPANOL[now.weekday()]
    mes_nombre = _MESES_ESPANOL[now.month - 1]
    variables["fecha_completa"] = f"{now.day} de {mes_nombre} de {now.year} es {dia_nombre}"
    logger.info(
        "[AGENT] Fecha usada en prompt - Hoy: %s, Hora: %s, Para API: %s",
        variables["fecha_completa"],
        variables["hora_actual"],
        variables["fecha_iso"],
    )

    """
    # Cargar horario, productos/servicios, contexto de negocio y preguntas frecuentes en paralelo
    # Services deshabilitados para plantilla demo (chatbot puro)
    # Descomentar para reactivar los fetches de datos de negocio

    results = await asyncio.gather(
        fetch_horario_reuniones(id_empresa),
        fetch_nombres_productos_servicios(id_empresa),
        fetch_contexto_negocio(id_empresa),
        fetch_preguntas_frecuentes(config.id_chatbot if config else None),
        return_exceptions=True,
    )

    if isinstance(results[0], Exception):
        logger.warning("[PROMPT] horario_reuniones falló: %s - %s", type(results[0]).__name__, results[0])
    if isinstance(results[1], Exception):
        logger.warning("[PROMPT] productos_servicios falló: %s - %s", type(results[1]).__name__, results[1])
    if isinstance(results[2], Exception):
        logger.warning("[PROMPT] contexto_negocio falló: %s - %s", type(results[2]).__name__, results[2])
    if isinstance(results[3], Exception):
        logger.warning("[PROMPT] preguntas_frecuentes falló: %s - %s", type(results[3]).__name__, results[3])

    horario_atencion = results[0] if not isinstance(results[0], Exception) else "No hay horario cargado."
    prods_servs = results[1] if not isinstance(results[1], Exception) else ([], [])
    nombres_productos, nombres_servicios = prods_servs
    contexto_negocio = results[2] if not isinstance(results[2], Exception) else None
    preguntas_frecuentes_str = results[3] if not isinstance(results[3], Exception) else ""

    variables["horario_atencion"] = horario_atencion
    variables["nombres_productos"] = nombres_productos
    variables["nombres_servicios"] = nombres_servicios
    variables["lista_productos_servicios"] = format_nombres_para_prompt(nombres_productos, nombres_servicios)
    variables["contexto_negocio"] = contexto_negocio
    variables["preguntas_frecuentes"] = preguntas_frecuentes_str or ""
    """

    return _citas_template.render(**variables)


__all__ = ["build_gqm_system_prompt"]
