"""
Sistema de métricas y observabilidad para AutoBot.
Usa Prometheus para tracking de performance y uso.
"""

from prometheus_client import Counter, Histogram, Gauge, Info
import time
from contextlib import contextmanager

# ========== CONTADORES ==========

# Conversaciones
chat_requests_total = Counter(
    'gqm_chat_requests_total',
    'Total de mensajes recibidos por el agente',
    ['empresa_id']
)

chat_errors_total = Counter(
    'gqm_chat_errors_total',
    'Total de errores en el procesamiento de mensajes',
    ['error_type']
)

# Tools
tool_calls_total = Counter(
    'gqm_tool_calls_total',
    'Total de llamadas a tools',
    ['tool_name']
)

tool_errors_total = Counter(
    'gqm_tool_errors_total',
    'Total de errores en tools',
    ['tool_name', 'error_type']
)

# API calls
api_calls_total = Counter(
    'gqm_api_calls_total',
    'Total de llamadas a APIs externas',
    ['endpoint', 'status']
)

# HTTP layer (/api/chat)
HTTP_REQUESTS = Counter(
    'gqm_http_requests_total',
    'Total de requests al endpoint /api/chat por resultado',
    ['status'],  # success | timeout | error
)

# Cache del agente (por empresa)
AGENT_CACHE = Counter(
    'gqm_agent_cache_total',
    'Hits y misses del cache de agente por empresa',
    ['result'],  # hit | miss
)

# Cache de búsqueda de productos
SEARCH_CACHE = Counter(
    'gqm_search_cache_total',
    'Resultados del cache de búsqueda de productos',
    ['result'],  # hit | miss | circuit_open
)

# Tokens LLM
LLM_TOKENS = Counter(
    'gqm_llm_tokens_total',
    'Total de tokens consumidos',
    ['type'],  # input | output | total
)

LLM_TOKENS_BY_EMPRESA = Counter(
    'gqm_llm_tokens_by_empresa_total',
    'Tokens consumidos por empresa',
    ['empresa_id', 'type'],  # input | output | total
)

# Degradación de servicios (fallback silencioso)
degradation_total = Counter(
    'gqm_availability_degradation_total',
    'Veces que un servicio degradó por fallo y se usó fallback',
    ['service', 'reason'],
    # service: kia_rag | checkpointer
    # reason: circuit_open | redis_unavailable | import_missing
)

# ========== HISTOGRAMAS (LATENCIA) ==========

HTTP_DURATION = Histogram(
    'gqm_http_duration_seconds',
    'Latencia total del endpoint /api/chat (incluye LLM y tools)',
    buckets=[0.25, 0.5, 1, 2.5, 5, 10, 20, 30, 60, 90, 120],
)

chat_response_duration_seconds = Histogram(
    'gqm_chat_response_duration_seconds',
    'Tiempo de respuesta del chat en segundos',
    ['status'],
    buckets=(0.1, 0.5, 1.0, 2.0, 5.0, 10.0, 30.0, 60.0, 90.0)
)

tool_execution_duration_seconds = Histogram(
    'gqm_tool_execution_duration_seconds',
    'Tiempo de ejecución de tools en segundos',
    ['tool_name'],
    buckets=(0.1, 0.5, 1.0, 2.0, 5.0, 10.0, 20.0, 30.0)
)

api_call_duration_seconds = Histogram(
    'gqm_api_call_duration_seconds',
    'Tiempo de llamadas a API en segundos',
    ['endpoint'],
    buckets=(0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0)
)

llm_call_duration_seconds = Histogram(
    'gqm_llm_call_duration_seconds',
    'Tiempo de llamadas al LLM en segundos',
    ['status'],
    buckets=(0.5, 1.0, 2.0, 5.0, 10.0, 20.0, 30.0, 60.0, 90.0)
)

# ========== GAUGES (ESTADO ACTUAL) ==========

cache_entries = Gauge(
    'gqm_cache_entries',
    'Número de entradas en cache',
    ['cache_type']
)

# ========== INFO ==========

agent_info = Info(
    'gqm_info',
    'Información del agente AutoBot'
)

# ========== CONTEXT MANAGERS ==========

@contextmanager
def track_chat_response():
    """Context manager para trackear duración de respuestas del chat."""
    start_time = time.perf_counter()
    status = "success"
    try:
        yield
    except Exception:
        status = "error"
        raise
    finally:
        chat_response_duration_seconds.labels(status=status).observe(time.perf_counter() - start_time)


@contextmanager
def track_tool_execution(tool_name: str):
    """Context manager para trackear duración de ejecución de tools."""
    start_time = time.perf_counter()
    tool_calls_total.labels(tool_name=tool_name).inc()
    try:
        yield
    except Exception as e:
        tool_errors_total.labels(
            tool_name=tool_name,
            error_type=type(e).__name__
        ).inc()
        raise
    else:
        duration = time.perf_counter() - start_time
        tool_execution_duration_seconds.labels(tool_name=tool_name).observe(duration)


@contextmanager
def track_api_call(endpoint: str):
    """Context manager para trackear duración de llamadas a API."""
    start_time = time.perf_counter()
    status = "unknown"
    try:
        yield
        status = "success"
    except Exception as e:
        status = f"error_{type(e).__name__}"
        raise
    else:
        duration = time.perf_counter() - start_time
        api_call_duration_seconds.labels(endpoint=endpoint).observe(duration)
    finally:
        api_calls_total.labels(endpoint=endpoint, status=status).inc()


@contextmanager
def track_llm_call():
    """Context manager para trackear duración de llamadas al LLM."""
    start_time = time.perf_counter()
    status = "success"
    try:
        yield
    except Exception:
        status = "error"
        raise
    finally:
        llm_call_duration_seconds.labels(status=status).observe(time.perf_counter() - start_time)


# ========== FUNCIONES DE UTILIDAD ==========

def record_chat_error(error_type: str):
    """Registra un error en el chat."""
    chat_errors_total.labels(error_type=error_type).inc()


def record_tool_validation_error(tool_name: str):
    """Registra un rechazo por validación de datos de entrada en una tool."""
    tool_errors_total.labels(tool_name=tool_name, error_type="validation_error").inc()


def update_cache_stats(cache_type: str, count: int):
    """Actualiza estadísticas de cache."""
    cache_entries.labels(cache_type=cache_type).set(count)


def record_token_usage(empresa_id: str, input_tokens: int, output_tokens: int):
    """Registra tokens consumidos (global + por empresa)."""
    total = input_tokens + output_tokens
    LLM_TOKENS.labels(type="input").inc(input_tokens)
    LLM_TOKENS.labels(type="output").inc(output_tokens)
    LLM_TOKENS.labels(type="total").inc(total)
    LLM_TOKENS_BY_EMPRESA.labels(empresa_id=empresa_id, type="input").inc(input_tokens)
    LLM_TOKENS_BY_EMPRESA.labels(empresa_id=empresa_id, type="output").inc(output_tokens)
    LLM_TOKENS_BY_EMPRESA.labels(empresa_id=empresa_id, type="total").inc(total)


def initialize_agent_info(model: str, version: str = "1.0.0"):
    """Inicializa información del agente."""
    agent_info.info({
        'version': version,
        'model': model,
        'agent_type': 'gqm'
    })


__all__ = [
    # Tracking functions
    'track_chat_response',
    'track_tool_execution',
    'track_api_call',
    'track_llm_call',
    # Recording functions
    'record_chat_error',
    'record_tool_validation_error',
    'record_token_usage',
    'update_cache_stats',
    'initialize_agent_info',
    # Metrics (para acceso directo si necesario)
    'chat_requests_total',
    'HTTP_REQUESTS',
    'HTTP_DURATION',
    'AGENT_CACHE',
    'SEARCH_CACHE',
    'LLM_TOKENS',
    'LLM_TOKENS_BY_EMPRESA',
    'degradation_total',
]
