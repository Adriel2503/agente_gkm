"""
Modelos Pydantic del agente.
Define el contrato HTTP (request/response) y la configuración tipada.
"""

from pydantic import BaseModel, Field, field_validator


class GQMConfig(BaseModel):
    """
    Configuración específica del agente.
    El orquestador envía estos campos en el JSON de entrada (dentro de "config").
    Cada campo se pasa como variable a gqm_system.j2 via build_gqm_system_prompt().
    """

    # --- Campos para tools (AgentContext) — agregar aquí ---
    # Se mapean automáticamente al AgentContext (context.py) si el nombre coincide.
    # Ejemplo:
    #     duracion_cita_minutos: int | None = None
    #     slots: int | None = None

    # --- Campos para prompts (Jinja2 template) — agregar aquí ---
    # Se pasan como variables al template. Usar {{ nombre_campo }} en gqm_system.j2.
    # Ejemplo:
    #     personalidad: str = "amable, profesional y eficiente"
    #     nombre_bot: str | None = None

    # --- Validators (limpiar/normalizar datos del orquestador) ---
    # Ejemplo: campo con default si viene vacío:
    #     @field_validator("personalidad", mode="before")
    #     @classmethod
    #     def default_personalidad(cls, v: object) -> str:
    #         if not v or (isinstance(v, str) and not v.strip()):
    #             return "amable, profesional y eficiente"
    #         return v
    #
    # Ejemplo: convertir string vacío a None:
    #     @field_validator("nombre_bot", "frase_saludo", mode="before")
    #     @classmethod
    #     def empty_str_to_none(cls, v: object) -> object:
    #         if isinstance(v, str) and not v.strip():
    #             return None
    #         return v

    model_config = {"extra": "ignore"}


class ChatRequest(BaseModel):
    """Request base para agentes. Extender según necesidad."""

    question: str = Field(..., min_length=1, max_length=4096)
    phone: str = Field(..., min_length=1, max_length=30)
    id_empresa: int
    id_chat: int
    phone_number_id: str
    # --- Identidad del lead (provistos por el orquestador) ---
    id_bitrix: str | None = None
    nombre: str | None = None
    marca: str | None = None
    modelo: str | None = None
    version: str | None = None
    sucursal: str | None = None
    correo: str | None = None
    # --- agregar campos universales aquí ---
    config: GQMConfig | None = None

    model_config = {"extra": "ignore"}


class AckResponse(BaseModel):
    """Respuesta inmediata de /api/chat."""
    status: str = "ok"


class ChatResponse(BaseModel):
    """Payload enviado al callback con la respuesta del agente (message + urls)."""
    message: str
    urls: list[str] = Field(default_factory=list)
    phone: str
    id_empresa: int
    id_chat: int
    phone_number_id: str

    @field_validator("urls", mode="before")
    @classmethod
    def normalize_urls(cls, value: object) -> list[str]:
        if value is None:
            return []
        if isinstance(value, str):
            cleaned = value.strip()
            return [cleaned] if cleaned else []
        if isinstance(value, (list, tuple)):
            normalized: list[str] = []
            for item in value:
                if item is None:
                    continue
                cleaned = str(item).strip()
                if cleaned:
                    normalized.append(cleaned)
            return normalized
        return value

    model_config = {"extra": "ignore"}


# Alias para documentación OpenAPI (callbacks)
CallbackRequest = ChatResponse
CallbackRequest.__doc__ = "Payload que el agente envía al CALLBACK_URL con la respuesta procesada (message + urls)."
