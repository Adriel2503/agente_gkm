"""
Modelo de contexto runtime y función de preparación.
"""

from dataclasses import dataclass, fields as dc_fields
from typing import Any

from ..logger import get_logger
from ..schemas import CitasConfig

logger = get_logger(__name__)


@dataclass
class AgentContext:
    """
    Esquema de contexto runtime para el agente.
    Este contexto se inyecta en las tools que lo necesiten.
    """
    id_empresa: int
    duracion_cita_minutos: int | None = None  # None = no enviado por el orquestador
    slots: int | None = None  # None = no enviado por el orquestador
    agendar_usuario: int = 1  # bandera agendar_usuario (1/0) para ScheduleValidator
    usuario_id: int | None = None  # None = no enviado por el orquestador (requerido para CREAR_EVENTO)
    correo_usuario: str | None = None  # None = no enviado por el orquestador (requerido para CREAR_EVENTO)
    agendar_sucursal: int = 0
    phone: str = ""


def _prepare_agent_context(id_empresa: int, config: CitasConfig | None, phone: str) -> AgentContext:
    """
    Prepara el contexto runtime para inyectar a las tools del agente.

    Los validators de CitasConfig ya normalizaron bool→int, str→int, strip, etc.
    Solo se incluyen campos con valor no-None; el resto queda con el default del dataclass.

    Args:
        id_empresa: ID de la empresa (tenant key).
        config: CitasConfig opcional validado por Pydantic.
        phone: Teléfono del cliente (str, identificador de sesión WhatsApp).

    Returns:
        AgentContext configurado.
    """
    params: dict[str, Any] = {
        "id_empresa": id_empresa,
        "phone": phone,
    }

    if config:
        agent_fields = {f.name for f in dc_fields(AgentContext)}
        for k, v in config.model_dump(exclude_none=True).items():
            if k in agent_fields:
                params[k] = v

    return AgentContext(**params)
