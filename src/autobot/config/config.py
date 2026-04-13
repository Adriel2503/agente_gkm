"""
Configuración del agente AutoBot (env, credenciales).
Incluye validación de tipos/valores y anotaciones para IDE y documentación.
"""

import os
from pathlib import Path

from dotenv import load_dotenv


def _find_env_path() -> Path:
    """Busca .env hacia arriba desde el módulo actual hasta encontrarlo o llegar a la raíz."""
    current = Path(__file__).resolve().parent
    for _ in range(6):
        env_file = current / ".env"
        if env_file.exists():
            return env_file
        parent = current.parent
        if parent == current:
            break
        current = parent
    return Path.cwd() / ".env"


load_dotenv(_find_env_path())

# ---------------------------------------------------------------------------
# Helpers de lectura con validación
# ---------------------------------------------------------------------------


def _get_str(key: str, default: str) -> str:
    """Obtiene variable de entorno como string."""
    return os.getenv(key, default).strip()


def _get_int(
    key: str,
    default: int,
    min_val: int | None = None,
    max_val: int | None = None,
) -> int:
    """Obtiene variable de entorno como int; valida y usa default si es inválida."""
    raw = os.getenv(key, str(default))
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return default
    if min_val is not None and value < min_val:
        return default
    if max_val is not None and value > max_val:
        return default
    return value


def _get_float(
    key: str,
    default: float,
    min_val: float | None = None,
    max_val: float | None = None,
) -> float:
    """Obtiene variable de entorno como float; valida y usa default si es inválida."""
    raw = os.getenv(key, str(default))
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return default
    if min_val is not None and value < min_val:
        return default
    if max_val is not None and value > max_val:
        return default
    return value


def _get_log_level(key: str, default: str) -> str:
    """Obtiene nivel de log; si no es válido, retorna default."""
    value = (os.getenv(key) or default).strip().upper()
    if value in ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"):
        return value
    return default.upper()

# ---------------------------------------------------------------------------
# OpenAI (modelo LLM del agente)
# ---------------------------------------------------------------------------

OPENAI_API_KEY: str = _get_str("OPENAI_API_KEY", "")
OPENAI_MODEL: str = _get_str("OPENAI_MODEL", "gpt-4o-mini")
OPENAI_TEMPERATURE: float = _get_float("OPENAI_TEMPERATURE", 0.5, min_val=0.0, max_val=2.0)

# ---------------------------------------------------------------------------
# Configuración del servidor
# ---------------------------------------------------------------------------

SERVER_HOST: str = _get_str("SERVER_HOST", "0.0.0.0")
SERVER_PORT: int = _get_int("SERVER_PORT", 8002, min_val=1, max_val=65535)

# ---------------------------------------------------------------------------
# Base de datos y Redis
# ---------------------------------------------------------------------------

DATABASE_URL: str = _get_str("DATABASE_URL", "")
REDIS_URL: str = _get_str("REDIS_URL", "")
REDIS_CHECKPOINT_TTL_HOURS: int = _get_int(
    "REDIS_CHECKPOINT_TTL_HOURS", 24, min_val=0, max_val=8760
)  # 0 = sin TTL, max 1 año

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

LOG_LEVEL: str = _get_log_level("LOG_LEVEL", "INFO")
LOG_FILE: str = _get_str("LOG_FILE", "")  # Si está vacío, no guarda en archivo

# ---------------------------------------------------------------------------
# Timeouts y límites
# ---------------------------------------------------------------------------

OPENAI_TIMEOUT: int = _get_int("OPENAI_TIMEOUT", 60, min_val=1, max_val=300)
API_TIMEOUT: int = _get_int("API_TIMEOUT", 10, min_val=1, max_val=120)
CHAT_TIMEOUT: int = _get_int("CHAT_TIMEOUT", 120, min_val=30, max_val=300)
MAX_TOKENS: int = _get_int("MAX_TOKENS", 2048, min_val=1, max_val=128000)

# Retry HTTP (aplica a todos los servicios de lectura vía post_with_retry)
HTTP_RETRY_ATTEMPTS: int = _get_int("HTTP_RETRY_ATTEMPTS", 3, min_val=1, max_val=10)
HTTP_RETRY_WAIT_MIN: int = _get_int("HTTP_RETRY_WAIT_MIN", 1, min_val=0, max_val=30)
HTTP_RETRY_WAIT_MAX: int = _get_int("HTTP_RETRY_WAIT_MAX", 4, min_val=1, max_val=60)

# ---------------------------------------------------------------------------
# Circuit breaker (threshold fallos → abierto; reset tras TTL segundos)
# ---------------------------------------------------------------------------
CB_THRESHOLD: int = _get_int("CB_THRESHOLD", 3, min_val=1, max_val=20)
CB_RESET_TTL: int = _get_int("CB_RESET_TTL", 300, min_val=60, max_val=3600)
CB_MAX_KEYS: int = _get_int("CB_MAX_KEYS", 500, min_val=50, max_val=10000)

# ---------------------------------------------------------------------------
# HTTP connection pool
# ---------------------------------------------------------------------------
HTTP_MAX_CONNECTIONS: int = _get_int("HTTP_MAX_CONNECTIONS", 50, min_val=10, max_val=500)
HTTP_MAX_KEEPALIVE: int = _get_int("HTTP_MAX_KEEPALIVE", 20, min_val=5, max_val=200)

# ---------------------------------------------------------------------------
# Cache
# ---------------------------------------------------------------------------

MAX_MESSAGES_HISTORY: int = _get_int(
    "MAX_MESSAGES_HISTORY", 20, min_val=4, max_val=200
)
AGENT_CACHE_TTL_MINUTES: int = _get_int(
    "AGENT_CACHE_TTL_MINUTES", 60, min_val=5, max_val=1440
)
AGENT_CACHE_MAXSIZE: int = _get_int("AGENT_CACHE_MAXSIZE", 500, min_val=10, max_val=5000)
SEARCH_CACHE_TTL_MINUTES: int = _get_int(
    "SEARCH_CACHE_TTL_MINUTES", 15, min_val=1, max_val=60
)
SEARCH_CACHE_MAXSIZE: int = _get_int("SEARCH_CACHE_MAXSIZE", 2000, min_val=10, max_val=10000)

# ---------------------------------------------------------------------------
# API KIA RAG (búsqueda semántica de modelos)
# ---------------------------------------------------------------------------

API_KIA_RAG_URL: str = _get_str(
    "API_KIA_RAG_URL",
    "http://localhost:8000/buscar",
)

# ---------------------------------------------------------------------------
# API Vitrix
# ---------------------------------------------------------------------------

APIKEY_VITRIX: str = _get_str("APIKEY_VITRIX", "")
VITRIX_API_URL: str = _get_str(
    "VITRIX_API_URL",
    "https://b24.guruxdev.com/qm/b24handlers/v2/index.php",
)

# ---------------------------------------------------------------------------
# Callback (URL donde se envía la respuesta del agente en modo async)
# ---------------------------------------------------------------------------

CALLBACK_URL: str = _get_str("CALLBACK_URL", "")

# ---------------------------------------------------------------------------
# Zona horaria (fecha/hora en prompts y validación)
# ---------------------------------------------------------------------------

TIMEZONE: str = _get_str("TIMEZONE", "America/Lima")
