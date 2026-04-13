"""
Lógica del agente automotriz usando LangChain 1.2+ API moderna.
Versión mejorada con logging, métricas, configuración centralizada y memoria automática.
"""

import openai

from langchain.agents import create_agent

from .runtime import (
    get_model, get_checkpointer,
    get_cached_agent, cache_agent, agent_cache_size, agent_cache_ttl,
    acquire_agent_lock, release_agent_lock, acquire_session_lock,
    message_window,
)
from ..tools.tools import AGENT_TOOLS
from ..logger import get_logger
from ..metrics import track_chat_response, track_llm_call, record_chat_error, record_token_usage, CHAT_REQUESTS, AGENT_CACHE, update_cache_stats
from .prompts import build_gqm_system_prompt
from .content import CitaStructuredResponse, _build_content
from .context import _prepare_agent_context
from ..schemas import GQMConfig

logger = get_logger(__name__)

# Mapeo de errores OpenAI: tipo → (log_level, metric_key, log_tag, mensaje_usuario)
_OPENAI_ERRORS: dict[type, tuple[str, str, str, str]] = {
    openai.AuthenticationError: ("critical", "openai_auth_error", "OpenAI-401", "No puedo procesar tu mensaje, la clave de acceso al servicio no es válida."),
    openai.RateLimitError: ("warning", "openai_rate_limit", "OpenAI-429", "Estoy recibiendo demasiadas solicitudes en este momento, por favor intenta en unos segundos."),
    openai.InternalServerError: ("error", "openai_server_error", "OpenAI-5xx", "El servicio de inteligencia artificial está presentando problemas, por favor intenta nuevamente."),
    openai.APIConnectionError: ("error", "openai_connection_error", "OpenAI-conn", "No pude conectarme al servicio de inteligencia artificial, por favor intenta nuevamente."),
    openai.BadRequestError: ("warning", "openai_bad_request", "OpenAI-400", "Tu mensaje no pudo ser procesado por el servicio, ¿puedes reformularlo?"),
}


async def _get_agent(
    id_empresa: int,
    config: GQMConfig | None,
    nombre: str | None = None,
    marca: str | None = None,
    modelo: str | None = None,
    id_bitrix: str | None = None,
):
    """
    Devuelve el agente compilado para id_empresa.

    Utiliza TTLCache para evitar recrear el cliente OpenAI, las HTTP calls
    del prompt y la compilación del grafo LangGraph en cada mensaje. El TTL
    se gobierna con AGENT_CACHE_TTL_MINUTES (default 60 min), independiente
    del horario de reuniones (sin cache propio).

    Incluye doble verificación con asyncio.Lock por cache_key para serializar
    la primera creación cuando múltiples sesiones de la misma empresa llegan
    concurrentemente (thundering herd).

    Args:
        id_empresa: ID de la empresa (tenant key).
        config: GQMConfig opcional con configuración del agente.

    Returns:
        Agente configurado con tools y checkpointer
    """
    cache_key: tuple = (id_empresa,)

    # Fast path: cache hit (sin await, atómico en asyncio single-thread)
    cached = get_cached_agent(cache_key)
    if cached is not None:
        AGENT_CACHE.labels(result="hit").inc()
        update_cache_stats("agent", agent_cache_size())
        logger.debug("[AGENT] Cache hit - id_empresa=%s", cache_key[0])
        return cached

    # Slow path: serializar creación para evitar thundering herd
    lock = acquire_agent_lock(cache_key)
    try:
        async with lock:
            # Double-check tras adquirir el lock (otra coroutine pudo haberlo creado)
            cached = get_cached_agent(cache_key)
            if cached is not None:
                AGENT_CACHE.labels(result="hit").inc()
                update_cache_stats("agent", agent_cache_size())
                logger.debug("[AGENT] Cache hit tras lock - id_empresa=%s", cache_key[0])
                return cached

            AGENT_CACHE.labels(result="miss").inc()
            logger.debug("[AGENT] Creando agente con LangChain 1.2+ API - id_empresa=%s", cache_key[0])

            # Construir system prompt usando template Jinja2 (async: carga horario y productos en paralelo)
            system_prompt = await build_gqm_system_prompt(
                id_empresa=id_empresa,
                config=config,
                nombre=nombre,
                marca=marca,
                modelo=modelo,
                id_bitrix=id_bitrix,
            )

            # Crear agente con API moderna (response_format: reply + url opcional)
            agent = create_agent(
                model=get_model(),
                tools=AGENT_TOOLS,
                system_prompt=system_prompt,
                checkpointer=get_checkpointer(),
                response_format=CitaStructuredResponse,
                middleware=[message_window],
            )

            cache_agent(cache_key, agent)
            update_cache_stats("agent", agent_cache_size())
            logger.debug(
                "[AGENT] Agente cacheado - id_empresa=%s, Tools: %s, TTL: %ss",
                cache_key[0],
                len(AGENT_TOOLS),
                agent_cache_ttl(),
            )
            return agent
    finally:
        release_agent_lock(cache_key)


async def process_message(
    message: str,
    phone: str,
    id_empresa: int,
    config: GQMConfig | None,
    nombre: str | None = None,
    marca: str | None = None,
    modelo: str | None = None,
    id_bitrix: str | None = None,
) -> tuple[str, str | None]:
    """
    Procesa un mensaje del cliente usando LangChain 1.2+ Agent.

    El agente tiene acceso a tools internas:
    - search_kia_modelos: Busca modelos KIA por similitud semántica

    La memoria es automática gracias al checkpointer (InMemorySaver).

    Args:
        message: Mensaje del cliente
        phone: Teléfono del cliente (str, identificador de sesión WhatsApp)
        id_empresa: ID de la empresa (tenant key)
        config: Config opcional del bot.

    Returns:
        Tupla (reply, url). url es None cuando no hay medio que adjuntar.
    """
    # Validaciones rápidas FUERA del lock (no tocan estado compartido)
    if not message or not message.strip():
        return ("No recibí tu mensaje. ¿Podrías repetirlo?", None)

    # Comandos del sistema (interceptados antes del lock y del agente)
    _cmd = message.strip().lower()
    if _cmd == "/clear":
        if phone:
            await get_checkpointer().adelete_thread(phone)
        logger.info("[CMD] /clear - Session: %s | Historial borrado", phone)
        return ("Historial limpiado. ¿En qué puedo ayudarte?", None)

    if _cmd == "/restart":
        logger.warning("[CMD] /restart - Session: %s | Comando reservado, sin acción", phone)
        return ("Este comando está reservado para administradores.", None)

    if not phone:
        raise ValueError("phone es requerido")

    # Registrar request con label de baja cardinalidad (por empresa, no por sesión)
    _empresa_id = str(id_empresa)
    CHAT_REQUESTS.labels(empresa_id=_empresa_id).inc()

    # Serializar requests concurrentes del mismo usuario para evitar condiciones
    # de carrera sobre el mismo thread_id del checkpointer (InMemorySaver).
    lock = acquire_session_lock(phone)
    async with lock:
        try:
            agent = await _get_agent(id_empresa, config, nombre, marca, modelo, id_bitrix)
        except Exception as e:
            logger.error("[AGENT] Error creando agent: %s", e, exc_info=True)
            record_chat_error("agent_creation_error")
            return ("Disculpa, tuve un problema de configuración. ¿Podrías intentar nuevamente?", None)

        agent_context = _prepare_agent_context(id_empresa, config, phone)

        # LangGraph checkpointer usa thread_id como str
        run_config = {
            "configurable": {
                "thread_id": phone
            }
        }
        try:
            logger.debug("[AGENT] Invocando agent - Session: %s, Message: %s...", phone, message[:100])

            with track_chat_response():
                with track_llm_call():
                    result = await agent.ainvoke(
                        {
                            "messages": [
                                {"role": "user", "content": _build_content(message)}
                            ]
                        },
                        config=run_config,
                        context=agent_context
                    )

            structured = result.get("structured_response")
            if isinstance(structured, CitaStructuredResponse):
                if structured.reply is None:
                    logger.warning("[AGENT] structured.reply es None - Session: %s", phone)
                    reply = "No recibí respuesta del asistente, por favor intenta nuevamente."
                elif structured.reply == "":
                    logger.warning("[AGENT] structured.reply es string vacío - Session: %s", phone)
                    reply = "El asistente envió una respuesta vacía, por favor intenta nuevamente."
                else:
                    reply = structured.reply
                url = structured.url if (structured.url and structured.url.strip()) else None
            else:
                logger.warning("[AGENT] Respuesta fuera de formato estructurado - Session: %s", phone)
                messages = result.get("messages", [])
                if messages:
                    last_message = messages[-1]
                    reply = last_message.content if hasattr(last_message, "content") else str(last_message)
                    if not reply:
                        logger.warning("[AGENT] last_message.content vacío - Session: %s", phone)
                        reply = "El asistente respondió en un formato inesperado, por favor intenta nuevamente."
                else:
                    reply = "El asistente respondió en un formato inesperado, por favor intenta nuevamente."
                url = None

            # Extraer tokens de todos los AIMessage
            _input_tokens = 0
            _output_tokens = 0
            for msg in result.get("messages", []):
                um = getattr(msg, "usage_metadata", None)
                if um:
                    _input_tokens += um.get("input_tokens", 0)
                    _output_tokens += um.get("output_tokens", 0)
            if _input_tokens or _output_tokens:
                record_token_usage(_empresa_id, _input_tokens, _output_tokens)
                logger.debug("[AGENT] Tokens — input=%s, output=%s, total=%s, empresa=%s",
                             _input_tokens, _output_tokens, _input_tokens + _output_tokens, _empresa_id)

            logger.debug("[AGENT] Respuesta generada: %s...", (reply[:200], url))

        except tuple(_OPENAI_ERRORS.keys()) as e:
            log_level, error_key, log_tag, user_msg = _OPENAI_ERRORS[type(e)]
            getattr(logger, log_level)("[AGENT][%s] Session: %s | %s", log_tag, phone, e)
            record_chat_error(error_key)
            return (user_msg, None)
        except Exception as e:
            logger.error("[AGENT] Error inesperado (%s) - Session: %s | %s", type(e).__name__, phone, e, exc_info=True)
            record_chat_error("agent_execution_error")
            return ("Disculpa, tuve un problema al procesar tu mensaje. ¿Podrías intentar nuevamente?", None)

    return (reply, url)
