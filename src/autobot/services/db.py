"""
Conexión async a PostgreSQL (automotriz-api).
Pool compartido, inicializado en el lifespan de FastAPI.
"""

import asyncpg

from .. import config as app_config
from ..logger import get_logger

logger = get_logger(__name__)

_pool: asyncpg.Pool | None = None


async def init_db() -> None:
    """Crea el pool de conexiones a PostgreSQL."""
    global _pool
    if not app_config.DATABASE_URL:
        logger.warning("[DB] DATABASE_URL vacío — chatbot_config no disponible")
        return
    _pool = await asyncpg.create_pool(
        app_config.DATABASE_URL,
        min_size=2,
        max_size=10,
        command_timeout=10,
        ssl="require",
    )
    logger.info("[DB] Pool PostgreSQL conectado")


async def close_db() -> None:
    """Cierra el pool de conexiones."""
    global _pool
    if _pool is not None:
        await _pool.close()
        _pool = None
        logger.info("[DB] Pool PostgreSQL cerrado")


def get_pool() -> asyncpg.Pool | None:
    """Retorna el pool de conexiones o None si no está configurado."""
    return _pool
