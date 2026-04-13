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
_system_template = _jinja_env.get_template("gqm_system.j2")


def _now_peru() -> datetime:
    """Fecha y hora actual en Perú (America/Lima)."""
    return datetime.now(_ZONA_PERU)


async def build_gqm_system_prompt(
    id_empresa: int,
    config: GQMConfig | None,
    nombre: str | None = None,
    marca: str | None = None,
    modelo: str | None = None,
    id_bitrix: str | None = None,
) -> str:
    """
    Construye el system prompt del agente.

    Args:
        id_empresa: ID de la empresa (tenant key).
        config: GQMConfig opcional validado por Pydantic.

    Returns:
        System prompt renderizado.
    """
    variables = config.model_dump(exclude_none=True) if config else {}
    variables["id_empresa"] = id_empresa
    variables["nombre"] = nombre
    variables["marca"] = marca
    variables["modelo"] = modelo
    variables["id_bitrix"] = id_bitrix

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

    # -----------------------------------------------------------------------
    # Inyección de datos dinámicos al prompt (desactivado)
    #
    # Para inyectar datos de negocio (horarios, FAQs, catálogo, etc.):
    #
    # 1. Crear services/prompt_data/ con funciones async que obtengan los datos.
    #    Cada fetcher retorna str o estructura simple.
    #    Ejemplo:
    #        async def fetch_horario(id_empresa: int) -> str: ...
    #        async def fetch_faqs(id_chatbot: int) -> str: ...
    #
    # 2. Importar los fetchers aquí arriba.
    #
    # 3. Llamarlos en paralelo con asyncio.gather:
    #        results = await asyncio.gather(
    #            fetch_horario(id_empresa),
    #            fetch_faqs(config.id_chatbot if config else None),
    #            return_exceptions=True,
    #        )
    #        variables["horario"] = results[0] if not isinstance(results[0], Exception) else ""
    #        variables["faqs"] = results[1] if not isinstance(results[1], Exception) else ""
    #
    # 4. Usar {{ horario }} y {{ faqs }} en gqm_system.j2
    # -----------------------------------------------------------------------

    return _system_template.render(**variables)


__all__ = ["build_gqm_system_prompt"]
