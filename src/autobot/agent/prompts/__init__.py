"""
Prompts del agente. Builder del system prompt.
"""

import asyncio
from pathlib import Path
from datetime import datetime
from zoneinfo import ZoneInfo

from jinja2 import Environment, FileSystemLoader, select_autoescape

from ... import config as app_config
from ...logger import get_logger
from ...schemas import GQMConfig
from ...services.chatbot_config import (
    get_chatbot_config,
    get_empresa_timezone,
    get_sucursales_por_marca,
    get_faqs,
    get_marcas,
)

logger = get_logger(__name__)

_TEMPLATES_DIR = Path(__file__).resolve().parent

_DIAS_ESPANOL = ["lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"]
_MESES_ESPANOL = [
    "enero", "febrero", "marzo", "abril", "mayo", "junio",
    "julio", "agosto", "septiembre", "octubre", "noviembre", "diciembre",
]

_jinja_env = Environment(
    loader=FileSystemLoader(str(_TEMPLATES_DIR)),
    autoescape=select_autoescape(disabled_extensions=()),
)
_system_template = _jinja_env.get_template("gqm_system.j2")


def _fecha_hora(tz_name: str) -> dict[str, str]:
    """Genera variables de fecha/hora para el timezone dado."""
    zona = ZoneInfo(tz_name)
    now = datetime.now(zona)
    dia_nombre = _DIAS_ESPANOL[now.weekday()]
    mes_nombre = _MESES_ESPANOL[now.month - 1]
    return {
        "fecha_iso": now.strftime("%Y-%m-%d"),
        "hora_actual": now.strftime("%I:%M %p"),
        "fecha_completa": f"{now.day} de {mes_nombre} de {now.year} es {dia_nombre}",
    }


async def build_gqm_system_prompt(
    id_empresa: int,
    config: GQMConfig | None,
) -> str:
    """
    Construye el system prompt del agente.

    Intenta leer chatbot_config de la BD. Si no hay config en BD,
    usa el template .j2 local como fallback.
    """
    # Traer config de BD y datos dinámicos en paralelo
    db_config, timezone, sucursales, faqs, marcas = await asyncio.gather(
        get_chatbot_config(id_empresa),
        get_empresa_timezone(id_empresa),
        get_sucursales_por_marca(id_empresa),
        get_faqs(id_empresa),
        get_marcas(id_empresa),
    )

    # Si hay prompt_sistema en BD, usarlo directamente
    if db_config and db_config.get("prompt_sistema"):
        logger.info("[PROMPT] Usando prompt de BD - id_empresa=%s, bot=%s", id_empresa, db_config.get("nombre_bot"))

        fecha = _fecha_hora(timezone)
        logger.info(
            "[AGENT] Fecha usada en prompt - Hoy: %s, Hora: %s, Para API: %s",
            fecha["fecha_completa"], fecha["hora_actual"], fecha["fecha_iso"],
        )

        # Renderizar el prompt de BD como template Jinja2
        template = _jinja_env.from_string(db_config["prompt_sistema"])
        variables = {
            **(config.model_dump(exclude_none=True) if config else {}),
            "id_empresa": id_empresa,
            "nombre_bot": db_config.get("nombre_bot", "Asistente"),
            "personalidad": db_config.get("personalidad", ""),
            "mensaje_bienvenida": db_config.get("mensaje_bienvenida", ""),
            "sucursales": sucursales,
            "faqs": faqs,
            "marcas": marcas,
            "timezone": timezone,
            **fecha,
        }
        return template.render(**variables)

    # Fallback: template .j2 local (comportamiento original)
    logger.info("[PROMPT] Usando template .j2 local (sin config en BD) - id_empresa=%s", id_empresa)

    variables = config.model_dump(exclude_none=True) if config else {}
    variables["id_empresa"] = id_empresa

    fecha = _fecha_hora(app_config.TIMEZONE)
    variables.update(fecha)
    logger.info(
        "[AGENT] Fecha usada en prompt - Hoy: %s, Hora: %s, Para API: %s",
        fecha["fecha_completa"], fecha["hora_actual"], fecha["fecha_iso"],
    )

    return _system_template.render(**variables)


__all__ = ["build_gqm_system_prompt"]
