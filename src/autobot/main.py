"""
Servidor HTTP del agente automotriz AutoBot.
Expone POST /api/chat compatible con el gateway Go.

Versión mejorada con logging, métricas y observabilidad.
"""

import asyncio
import json
import logging
import time
import uuid
from contextlib import asynccontextmanager

import uvicorn
from fastapi import APIRouter, FastAPI
from fastapi.responses import JSONResponse
from prometheus_client import make_asgi_app

from . import config as app_config, __version__
from .agent import process_message, init_checkpointer, close_checkpointer
from .logger import setup_logging, get_logger, trace_id
from .metrics import initialize_agent_info, HTTP_REQUESTS, HTTP_DURATION
from .infra import close_http_client
from .tools.tools import AGENT_TOOLS
from .config import get_health_issues
from .schemas import ChatRequest, ChatResponse, AckResponse, CallbackRequest
from .infra.http_client import get_client

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
    title="AutoBot",
    description="Agente automotriz multi-tenant",
    version=__version__,
)

# Endpoint de métricas para Prometheus
app.mount("/metrics", make_asgi_app())


# ---------------------------------------------------------------------------
# Callback OpenAPI (documentación del webhook saliente)
# ---------------------------------------------------------------------------

_callback_router = APIRouter()

@_callback_router.post(
    "{$callback_url}",
    response_model=CallbackRequest,
    summary="Webhook saliente con la respuesta del agente",
)
async def agent_callback(body: CallbackRequest):
    """
    El agente envía este payload al CALLBACK_URL configurado
    una vez que termina de procesar el mensaje.
    """
    ...  # pragma: no cover


# ---------------------------------------------------------------------------
# Endpoint principal
# ---------------------------------------------------------------------------

@app.post("/api/chat", response_model=AckResponse, callbacks=_callback_router.routes)
async def chat(req: ChatRequest) -> AckResponse:
    """
    Recibe mensaje y responde 200 inmediatamente.
    El agente procesa en background y envía el resultado al CALLBACK_URL.
    """
    trace_id.set(uuid.uuid4().hex[:8])
    logger.info("[HTTP] Mensaje recibido - Session: %s, Empresa: %s, Length: %s chars", req.phone, req.id_empresa, len(req.question))
    logger.info("[HTTP] JSON entrada:\n%s", json.dumps(req.model_dump(), indent=2, ensure_ascii=False))
    logger.debug("[HTTP] Message: %s...", req.question[:100])

    asyncio.create_task(_process_and_callback(req))
    return AckResponse()


async def _process_and_callback(req: ChatRequest) -> None:
    """Procesa el mensaje con el agente y envía la respuesta al CALLBACK_URL."""
    _start = time.perf_counter()
    _http_status = "success"

    try:
        reply, url = await asyncio.wait_for(
            process_message(
                message=req.question,
                phone=req.phone,
                id_empresa=req.id_empresa,
                config=req.config,
            ),
            timeout=app_config.CHAT_TIMEOUT,
        )

    except asyncio.TimeoutError:
        _http_status = "timeout"
        reply = f"La solicitud tardó más de {app_config.CHAT_TIMEOUT}s. Por favor, intenta de nuevo."
        url = None
        logger.error("[HTTP] Timeout en process_message (CHAT_TIMEOUT=%s)", app_config.CHAT_TIMEOUT)

    except ValueError as e:
        _http_status = "error"
        reply = f"Error de configuración: {str(e)}"
        url = None
        logger.error("[HTTP] %s", reply)

    except asyncio.CancelledError:
        _http_status = None  # No contar requests abortados externamente
        raise

    except Exception as e:
        _http_status = "error"
        reply = f"Error procesando mensaje: {str(e)}"
        url = None
        logger.error("[HTTP] %s", reply, exc_info=True)

    finally:
        if _http_status is not None:
            HTTP_REQUESTS.labels(status=_http_status).inc()
            HTTP_DURATION.observe(time.perf_counter() - _start)

    # Enviar respuesta al callback
    if not app_config.CALLBACK_URL:
        logger.error("[CALLBACK] CALLBACK_URL no configurada - Session: %s, respuesta descartada", req.phone)
        return

    payload = ChatResponse(message=reply, url=url, phone=req.phone, id_empresa=req.id_empresa, id_chat=req.id_chat, phone_number_id=req.phone_number_id)
    callback_data = payload.model_dump()
    logger.info("[CALLBACK] JSON salida:\n%s", json.dumps(callback_data, indent=2, ensure_ascii=False))
    try:
        client = get_client()
        response = await client.post(app_config.CALLBACK_URL, json=callback_data)
        response.raise_for_status()
        logger.info("[CALLBACK] Respuesta enviada - Session: %s, Status: %s", req.phone, response.status_code)
    except Exception as e:
        logger.error("[CALLBACK] Error enviando respuesta - Session: %s | %s", req.phone, e, exc_info=True)


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
        content={"status": status, "agent": "autobot", "version": __version__, "issues": issues},
    )


# ---------------------------------------------------------------------------
# Entrypoint
# ---------------------------------------------------------------------------

def main():
    logger.info("=" * 60)
    logger.info("AUTOBOT v%s", __version__)
    logger.info("=" * 60)
    logger.info("Host:       %s:%s", app_config.SERVER_HOST, app_config.SERVER_PORT)
    logger.info("Modelo:     %s (temp=%.1f, max_tokens=%s)", app_config.OPENAI_MODEL, app_config.OPENAI_TEMPERATURE, app_config.MAX_TOKENS)
    logger.info("Timeouts:   LLM=%ss | API=%ss | Chat=%ss", app_config.OPENAI_TIMEOUT, app_config.API_TIMEOUT, app_config.CHAT_TIMEOUT)
    logger.info("Cache:      agente=%smin | búsqueda=%smin | historial=%s msgs", app_config.AGENT_CACHE_TTL_MINUTES, app_config.SEARCH_CACHE_TTL_MINUTES, app_config.MAX_MESSAGES_HISTORY)
    logger.info("Resiliencia: CB threshold=%s fallos | reset=%ss", app_config.CB_THRESHOLD, app_config.CB_RESET_TTL)
    logger.info("Checkpointer: %s", "Redis (%s)" % app_config.REDIS_URL if app_config.REDIS_URL else "InMemorySaver")
    logger.info("Timezone:   %s", app_config.TIMEZONE)
    logger.info("Log Level:  %s", app_config.LOG_LEVEL)
    logger.info("-" * 60)
    logger.info("POST /api/chat  → Callback: %s", app_config.CALLBACK_URL or "(no configurada)")
    logger.info("GET  /health    GET  /metrics")
    logger.info("Tools (%s):", len(AGENT_TOOLS))
    for t in AGENT_TOOLS:
        logger.info("  - %s", t.name)
    logger.info("=" * 60)

    # Filtrar logs de healthcheck exitosos (200) — los 503 sí se loguean
    class _HealthLogFilter(logging.Filter):
        def filter(self, record: logging.LogRecord) -> bool:
            msg = record.getMessage()
            return not ('"GET /health' in msg and "200" in msg)

    logging.getLogger("uvicorn.access").addFilter(_HealthLogFilter())

    uvicorn.run(
        app,
        host=app_config.SERVER_HOST,
        port=app_config.SERVER_PORT,
    )


if __name__ == "__main__":
    main()
