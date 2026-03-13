"""
Servidor HTTP del agente especializado en citas / reuniones.
Expone POST /api/chat compatible con el gateway Go.

Versión mejorada con logging, métricas y observabilidad.
"""

import asyncio
import logging
import time
from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from prometheus_client import make_asgi_app

from . import config as app_config, __version__
from .agent import process_cita_message, init_checkpointer, close_checkpointer
from .logger import setup_logging, get_logger
from .metrics import initialize_agent_info, HTTP_REQUESTS, HTTP_DURATION
from .infra import close_http_client
from .config import get_health_issues
from .schemas import ChatRequest, ChatResponse

# Configurar logging antes de cualquier otra cosa
log_level = getattr(logging, app_config.LOG_LEVEL.upper(), logging.INFO)
setup_logging(
    level=log_level,
    log_file=app_config.LOG_FILE if app_config.LOG_FILE else None
)

logger = get_logger(__name__)

# Inicializar información del agente para métricas
initialize_agent_info(model=app_config.OPENAI_MODEL, version=__version__)


# ---------------------------------------------------------------------------
# Lifespan (cierra el cliente HTTP compartido al apagar)
# ---------------------------------------------------------------------------

@asynccontextmanager
async def app_lifespan(app: FastAPI):
    await init_checkpointer()
    try:
        yield
    finally:
        await close_checkpointer()
        await close_http_client()


# ---------------------------------------------------------------------------
# Aplicación FastAPI
# ---------------------------------------------------------------------------

app = FastAPI(
    lifespan=app_lifespan,
    title="Agente Citas - MaravIA",
    description="Agente especializado en gestión de citas y reuniones",
    version=__version__,
)

# Endpoint de métricas para Prometheus
app.mount("/metrics", make_asgi_app())


# ---------------------------------------------------------------------------
# Endpoint principal
# ---------------------------------------------------------------------------

@app.post("/api/chat", response_model=ChatResponse)
async def chat(req: ChatRequest) -> ChatResponse:
    """
    Agente especializado en citas / reuniones.

    Recibe el mensaje del cliente y el contexto de configuración enviados
    por el gateway, y devuelve la respuesta del agente.

    El agente maneja la conversación completa de forma autónoma,
    decidiendo cuándo usar cada tool según el contexto.
    La memoria es automática gracias al checkpointer (InMemorySaver).

    Body:
        message: Mensaje del cliente que quiere agendar una cita
        session_id: ID de sesión (int, unificado con orquestador)
        id_empresa: ID de la empresa (int, requerido)
        config: Configuración opcional del bot:
            - usuario_id (int, opcional): ID del usuario/vendedor
            - correo_usuario (str, opcional): Email del usuario/vendedor
            - agendar_usuario (bool o int, opcional): 1=agendar por usuario (default: 1)
            - agendar_sucursal (bool o int, opcional): 1=agendar por sucursal (default: 0)
            - duracion_cita_minutos (int, requerido): Duración de la cita en minutos
            - slots (int, requerido): Capacidad de slots simultáneos
            - personalidad (str, opcional): Personalidad del agente

    Returns:
        JSON con campo reply: respuesta del agente
    """
    config = req.config

    logger.info("[HTTP] Mensaje recibido - Session: %s, Empresa: %s, Length: %s chars", req.session_id, req.id_empresa, len(req.message))
    logger.debug("[HTTP] Message: %s...", req.message[:100])
    logger.debug("[HTTP] Config fields: %s", config.model_fields_set if config else "None")

    _start = time.perf_counter()
    _http_status = "success"

    try:
        reply, url = await asyncio.wait_for(
            process_cita_message(
                message=req.message,
                session_id=req.session_id,
                id_empresa=req.id_empresa,
                config=config,
            ),
            timeout=app_config.CHAT_TIMEOUT,
        )

        logger.info("[HTTP] Respuesta generada - Length: %s chars", len(reply))
        logger.debug("[HTTP] Reply: %s...", reply[:200])
        return ChatResponse(reply=reply, url=url, session_id=req.session_id)

    except asyncio.TimeoutError:
        _http_status = "timeout"
        error_msg = f"La solicitud tardó más de {app_config.CHAT_TIMEOUT}s. Por favor, intenta de nuevo."
        logger.error("[HTTP] Timeout en process_cita_message (CHAT_TIMEOUT=%s)", app_config.CHAT_TIMEOUT)
        return ChatResponse(reply=error_msg, url=None, session_id=req.session_id)

    except ValueError as e:
        _http_status = "error"
        error_msg = f"Error de configuración: {str(e)}"
        logger.error("[HTTP] %s", error_msg)
        return ChatResponse(reply=error_msg, url=None, session_id=req.session_id)

    except asyncio.CancelledError:
        _http_status = None  # No contar requests abortados externamente
        raise

    except Exception as e:
        _http_status = "error"
        error_msg = f"Error procesando mensaje: {str(e)}"
        logger.error("[HTTP] %s", error_msg, exc_info=True)
        return ChatResponse(reply=error_msg, url=None, session_id=req.session_id)

    finally:
        if _http_status is not None:
            HTTP_REQUESTS.labels(status=_http_status).inc()
            HTTP_DURATION.observe(time.perf_counter() - _start)


# ---------------------------------------------------------------------------
# Health check
# ---------------------------------------------------------------------------

@app.get("/health")
async def health():
    issues = []

    if not app_config.OPENAI_API_KEY:
        issues.append("openai_api_key_missing")
    issues.extend(get_health_issues())

    status = "degraded" if issues else "ok"
    return JSONResponse(
        status_code=503 if issues else 200,
        content={"status": status, "agent": "citas", "version": __version__, "issues": issues},
    )


# ---------------------------------------------------------------------------
# Entrypoint
# ---------------------------------------------------------------------------

def main():
    logger.info("=" * 60)
    logger.info("INICIANDO AGENTE CITAS - MaravIA")
    logger.info("=" * 60)
    logger.info("Host: %s:%s", app_config.SERVER_HOST, app_config.SERVER_PORT)
    logger.info("Modelo: %s", app_config.OPENAI_MODEL)
    logger.info("Timeout LLM: %ss", app_config.OPENAI_TIMEOUT)
    logger.info("Timeout API: %ss", app_config.API_TIMEOUT)
    logger.info("Cache TTL agente:   %s min", app_config.AGENT_CACHE_TTL_MINUTES)
    logger.info("Cache TTL búsqueda: %s min", app_config.SEARCH_CACHE_TTL_MINUTES)
    logger.info("Max mensajes LLM:   %s", app_config.MAX_MESSAGES_HISTORY)
    logger.info("Timeout chat:       %ss", app_config.CHAT_TIMEOUT)
    logger.info("Timezone: %s", app_config.TIMEZONE)
    logger.info("Circuit breaker threshold: %s fallos", app_config.CB_THRESHOLD)
    logger.info("Redis checkpointer: %s", "activo" if app_config.REDIS_URL else "InMemorySaver")
    logger.info("Log Level: %s", app_config.LOG_LEVEL)
    logger.info("-" * 60)
    logger.info("Endpoint: POST /api/chat")
    logger.info("Health:   GET  /health")
    logger.info("Metrics:  GET  /metrics")
    logger.info("Tools internas del agente:")
    logger.info("- check_availability (consulta horarios)")
    logger.info("- create_booking (crea citas/eventos)")
    logger.info("- search_productos_servicios (busca productos/servicios)")
    logger.info("=" * 60)

    uvicorn.run(
        app,
        host=app_config.SERVER_HOST,
        port=app_config.SERVER_PORT,
    )


if __name__ == "__main__":
    main()
