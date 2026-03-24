"""
Modelos Pydantic del agente.
Define el contrato HTTP (request/response) y la configuración tipada.
"""

from pydantic import BaseModel, Field, field_validator


class GQMConfig(BaseModel):
    """Configuración específica del agente de citas."""

    # --- Campos para tools (AgentContext) ---
    duracion_cita_minutos: int | None = None
    slots: int | None = None
    agendar_usuario: int = 1
    usuario_id: int | None = None
    correo_usuario: str | None = None
    agendar_sucursal: int = 0

    # --- Campos para prompts (Jinja2 template) ---
    personalidad: str = "amable, profesional y eficiente"
    nombre_bot: str | None = None
    frase_saludo: str | None = None
    frase_des: str | None = None
    frase_no_sabe: str | None = None
    archivo_saludo: str | None = None
    id_chatbot: int | None = None
    # --- agregar parámetros específicos del agente aquí ---

    @field_validator("agendar_usuario", "agendar_sucursal", mode="before")
    @classmethod
    def bool_to_int(cls, v: object) -> object:
        if isinstance(v, bool):
            return 1 if v else 0
        return v

    @field_validator("usuario_id", mode="before")
    @classmethod
    def coerce_usuario_id(cls, v: object) -> int | None:
        if v is None:
            return None
        try:
            return int(v)
        except (ValueError, TypeError):
            return None

    @field_validator("personalidad", mode="before")
    @classmethod
    def default_personalidad(cls, v: object) -> str:
        if not v or (isinstance(v, str) and not v.strip()):
            return "amable, profesional y eficiente"
        return v

    @field_validator("correo_usuario", mode="before")
    @classmethod
    def strip_correo(cls, v: object) -> str | None:
        if isinstance(v, str):
            return v.strip() or None
        return v

    @field_validator(
        "nombre_bot", "frase_saludo", "frase_des", "frase_no_sabe", "archivo_saludo",
        mode="before",
    )
    @classmethod
    def empty_str_to_none(cls, v: object) -> object:
        if isinstance(v, str) and not v.strip():
            return None
        return v

    model_config = {"extra": "ignore"}


class ChatRequest(BaseModel):
    """Request base para agentes. Extender según necesidad."""

    message: str = Field(..., min_length=1, max_length=4096)
    phone: str = Field(..., min_length=1, max_length=30)
    id_empresa: int
    # --- agregar campos universales aquí ---
    config: GQMConfig | None = None

    model_config = {"extra": "ignore"}


class AckResponse(BaseModel):
    """Respuesta inmediata de /api/chat."""
    status: str = "ok"


class ChatResponse(BaseModel):
    """Payload enviado al callback con la respuesta del agente."""
    reply: str
    url: str | None = None
    phone: str


# Alias para documentación OpenAPI (callbacks)
CallbackRequest = ChatResponse
CallbackRequest.__doc__ = "Payload que el agente envía al CALLBACK_URL con la respuesta procesada."
