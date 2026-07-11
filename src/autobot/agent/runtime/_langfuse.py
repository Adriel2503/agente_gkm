"""
Integración opcional con Langfuse para traces LangChain/LangGraph.

El import del SDK es lazy: config.py carga .env antes de que este módulo cree
el CallbackHandler, evitando inicializar Langfuse con credenciales vacías.
"""

from __future__ import annotations

from typing import Any, Literal

from ... import config as app_config
from ...logger import get_logger

logger = get_logger(__name__)

_LANGFUSE_CLIENT: Any | None = None


LangfuseState = Literal[
    "disabled",
    "missing_env",
    "import_missing",
    "client_initialized",
    "attached",
]


def _init_langfuse_client() -> Any:
    global _LANGFUSE_CLIENT
    if _LANGFUSE_CLIENT is not None:
        return _LANGFUSE_CLIENT

    from langfuse import Langfuse

    _LANGFUSE_CLIENT = Langfuse(
        public_key=app_config.LANGFUSE_PUBLIC_KEY,
        secret_key=app_config.LANGFUSE_SECRET_KEY,
        base_url=app_config.LANGFUSE_BASE_URL,
    )
    logger.info(
        "[LANGFUSE] client_initialized host=%s",
        app_config.LANGFUSE_BASE_URL,
    )
    return _LANGFUSE_CLIENT


def get_langfuse_callback_handler() -> tuple[Any | None, LangfuseState]:
    """Retorna un CallbackHandler de Langfuse si la integración está habilitada."""
    if not app_config.LANGFUSE_ENABLED:
        return None, "disabled"

    missing = [
        name
        for name, value in (
            ("LANGFUSE_PUBLIC_KEY", app_config.LANGFUSE_PUBLIC_KEY),
            ("LANGFUSE_SECRET_KEY", app_config.LANGFUSE_SECRET_KEY),
            ("LANGFUSE_BASE_URL", app_config.LANGFUSE_BASE_URL),
        )
        if not value
    ]
    if missing:
        logger.warning(
            "[LANGFUSE] Integración habilitada pero faltan variables: %s",
            ", ".join(missing),
        )
        return None, "missing_env"

    try:
        from langfuse.langchain import CallbackHandler
    except ImportError:
        logger.warning("[LANGFUSE] SDK no instalado; traces deshabilitados")
        return None, "import_missing"

    _init_langfuse_client()
    return CallbackHandler(), "attached"


def build_langfuse_config(
    *,
    id_empresa: int,
    phone: str,
    id_bitrix: str | None,
    nombre: str | None,
    marca: str | None,
    modelo: str | None,
    version: str | None,
    sucursal: str | None,
    correo: str | None,
) -> tuple[dict[str, Any], LangfuseState, Any | None]:
    """Construye config LangChain con callbacks y metadata de Langfuse."""
    handler, state = get_langfuse_callback_handler()
    if handler is None:
        return {}, state, None

    session_id = f"{id_empresa}_{phone}_{id_bitrix}"
    logger.info(
        "[LANGFUSE] attached session_id=%s user_id=%s empresa=%s model=%s fields=%s",
        session_id,
        phone,
        id_empresa,
        app_config.OPENAI_MODEL,
        "phone,id_bitrix,nombre,correo,marca,modelo,version,sucursal",
    )
    return {
        "callbacks": [handler],
        "metadata": {
            "langfuse_session_id": session_id,
            "langfuse_user_id": phone,
            "id_empresa": id_empresa,
            "phone": phone,
            "id_bitrix": id_bitrix,
            "nombre": nombre,
            "correo": correo,
            "marca": marca,
            "modelo": modelo,
            "version": version,
            "sucursal": sucursal,
            "openai_model": app_config.OPENAI_MODEL,
        },
        "tags": [
            "autobot",
            f"empresa:{id_empresa}",
            f"model:{app_config.OPENAI_MODEL}",
        ],
    }, state, handler


__all__ = ["build_langfuse_config", "get_langfuse_callback_handler", "LangfuseState"]
