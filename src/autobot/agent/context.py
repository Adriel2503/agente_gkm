"""
Modelo de contexto runtime y función de preparación.
"""

from dataclasses import dataclass, fields as dc_fields
from typing import Any

from ..logger import get_logger
from ..schemas import GQMConfig

logger = get_logger(__name__)


@dataclass
class AgentContext:
    """
    Esquema de contexto runtime para el agente.
    Este contexto se inyecta en las tools que lo necesiten.
    """
    id_empresa: int
    phone: str = ""
    id_bitrix: str | None = None
    event: str | None = None  # "cita_agendada" | "callback_solicitado" | "desistido"


def _prepare_agent_context(
    id_empresa: int,
    config: GQMConfig | None,
    phone: str,
    id_bitrix: str | None = None,
) -> AgentContext:
    """
    Prepara el contexto runtime para inyectar a las tools del agente.

    Los validators de GQMConfig ya normalizaron bool→int, str→int, strip, etc.
    Solo se incluyen campos con valor no-None; el resto queda con el default del dataclass.

    Args:
        id_empresa: ID de la empresa (tenant key).
        config: GQMConfig opcional validado por Pydantic.
        phone: Teléfono del cliente (str, identificador de sesión WhatsApp).
        id_bitrix: ID del lead en Bitrix/Vitrix (para tools de escritura en CRM).

    Returns:
        AgentContext configurado.
    """
    params: dict[str, Any] = {
        "id_empresa": id_empresa,
        "phone": phone,
        "id_bitrix": id_bitrix,
    }

    if config:
        agent_fields = {f.name for f in dc_fields(AgentContext)}
        for k, v in config.model_dump(exclude_none=True).items():
            if k in agent_fields:
                params[k] = v

    return AgentContext(**params)
