"""
Integración opcional con Langfuse para traces LangChain/LangGraph.

El import del SDK es lazy: config.py carga .env antes de que este módulo cree
el CallbackHandler, evitando inicializar Langfuse con credenciales vacías.
"""

from __future__ import annotations

import os
from typing import Any

from ... import config as app_config
from ...logger import get_logger

logger = get_logger(__name__)


def get_langfuse_callback_handler() -> Any | None:
    """Retorna un CallbackHandler de Langfuse si la integración está habilitada."""
    if not app_config.LANGFUSE_ENABLED:
        return None

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
        return None

    os.environ.setdefault("LANGFUSE_HOST", app_config.LANGFUSE_BASE_URL)

    try:
        from langfuse.langchain import CallbackHandler
    except ImportError:
        logger.warning("[LANGFUSE] SDK no instalado; traces deshabilitados")
        return None

    return CallbackHandler(public_key=app_config.LANGFUSE_PUBLIC_KEY)


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
) -> dict[str, Any]:
    """Construye config LangChain con callbacks y metadata de Langfuse."""
    handler = get_langfuse_callback_handler()
    if handler is None:
        return {}

    session_id = f"{id_empresa}_{phone}_{id_bitrix}"
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
    }


__all__ = ["build_langfuse_config", "get_langfuse_callback_handler"]
