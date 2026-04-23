# AutoBot — MaravIA

Agente conversacional de IA para el sector automotriz. Atiende consultas por WhatsApp, busca modelos por similitud semantica (RAG) y guia al cliente en su proceso de compra.

**Version:** `2.5.0`
**Modelo:** `gpt-4o-mini` (configurable via `OPENAI_MODEL`)
**Puerto:** `8002`

---

## Inicio rapido

### 1. Configurar entorno

```bash
cp .env.example .env
# Editar .env con OPENAI_API_KEY, CALLBACK_URL, API_KIA_RAG_URL
```

### 2. Desarrollo local

```bash
uv sync          # Instalar dependencias
python run.py    # Iniciar servidor
```

### 3. Docker

```bash
uv lock                     # Generar lockfile (primera vez)
docker compose up -d        # Build + run
curl localhost:8002/health  # Verificar
```

---

## API Reference

### POST /api/chat

Recibe un mensaje y responde `200 OK` inmediatamente. El agente procesa en background y envia el resultado al `CALLBACK_URL`.

**Request:**
```json
{
  "question": "Que modelos KIA tienen?",
  "phone": "+506-8888-8888",
  "id_empresa": 11,
  "id_chat": 100,
  "phone_number_id": "1234567890",
  "version": "GT-Line",
  "config": {}
}
```

| Campo | Tipo | Requerido | Descripcion |
|---|---|---|---|
| `question` | string | Si | Mensaje del cliente (1-4096 chars) |
| `phone` | string | Si | Telefono WhatsApp (session ID) |
| `id_empresa` | int | Si | ID del tenant |
| `id_chat` | int | Si | ID de la conversacion |
| `phone_number_id` | string | Si | ID del numero WhatsApp |
| `version` | string | No | Version de interes del lead, si aplica |
| `config` | object | No | Configuracion del agente (GQMConfig) |

**Response inmediata:**
```json
{ "status": "ok" }
```

### Callback (webhook saliente)

El agente envia este payload al `CALLBACK_URL` cuando termina de procesar:

```json
{
  "message": "Respuesta del agente...",
  "urls": [],
  "phone": "+506-8888-8888",
  "id_empresa": 11,
  "id_chat": 100,
  "phone_number_id": "1234567890"
}
```

| Campo | Tipo | Descripcion |
|---|---|---|
| `message` | string | Respuesta del agente |
| `urls` | array[string] | URLs crudas de imagen, video o PDF |
| `phone` | string | Telefono original |
| `id_empresa` | int | ID del tenant original |
| `id_chat` | int | ID de conversacion original |
| `phone_number_id` | string | ID del numero WhatsApp original |

### GET /health

```json
{
  "status": "ok",
  "agent": "autobot",
  "version": "2.5.0",
  "issues": []
}
```

Retorna `200` si OK, `503` si degradado (API key faltante o circuit breaker abierto).

### GET /metrics

Metricas Prometheus en formato texto. Ver seccion Observabilidad.

---

## Arquitectura

```
WhatsApp -> N8N -> Orquestador -> POST /api/chat (puerto 8002)
                                       |
                                       v
                               main.py (FastAPI)
                                  |  ACK 200
                                  v
                           process_message()
                              |         |
                     _get_agent()    AgentContext
                         |               |
                   [TTLCache]      [session lock]
                         |               |
                         v               v
                    LLM gpt-4o-mini + buscar_vehiculo
                         |               |
                         |          busqueda_kia.py
                         |               |
                         |          resilient_call()
                         |            |        |
                         |     post_with_    kia_rag_cb
                         |     logging()   (circuit breaker)
                         |            |
                         |        RAG API
                         |       (/buscar)
                         v
                    CitaStructuredResponse
                      { message, urls }
                         |
                         v
                   POST CALLBACK_URL
```

---

## Tools del agente

### buscar_vehiculo

Unica tool activa. Busca modelos KIA por similitud semantica via RAG API.

```python
AGENT_TOOLS = [buscar_vehiculo]
```

| Parametro | Tipo | Descripcion |
|---|---|---|
| `query` | string | Texto libre (ej: "SUV familiar", "New Picanto") |

Devuelve hasta 3 modelos con: nombre, version, gama, ano, precio USD, cuota mensual, colores, ficha tecnica (PDF) y video.

---

## Services

### busqueda_kia.py

Llama a la RAG API con la cadena completa de resiliencia:

```
busqueda_kia -> resilient_call()
                  |-> post_with_logging() -> post_with_retry()
                  |-> kia_rag_cb (circuit breaker)
                  + track_api_call() (metrica latencia)
```

- **Circuit breaker:** 3 fallos de red consecutivos -> abierto 5 min
- **Retry:** tenacity, exponential backoff, 3 intentos, solo TransportError
- **Logging:** request/response en DEBUG via post_with_logging

---

## Infra (resiliencia)

### http_client.py

Cliente HTTP compartido (httpx.AsyncClient singleton).

| Funcion | Proposito |
|---|---|
| `get_client()` | Lazy init del cliente con pool de conexiones |
| `close_http_client()` | Cierre limpio en lifespan |
| `post_with_retry()` | POST con retry automatico (tenacity) |
| `post_with_logging()` | Wrapper que loguea request/response en DEBUG |

### circuit_breaker.py

Circuit breaker generico con estados CLOSED -> OPEN -> (TTL) -> CLOSED.

| Metodo | Proposito |
|---|---|
| `is_open(key)` | True si el circuit esta abierto |
| `record_failure(key)` | Incrementa contador, abre si alcanza threshold |
| `record_success(key)` | Resetea contador |
| `any_open()` | Usado por /health |

Solo `record_failure()` ante `httpx.TransportError`. Respuestas `success=false` de la API no abren el circuit.

### _resilience.py

`resilient_call()` — orquesta circuit breaker + llamada HTTP:

1. CB abierto -> RuntimeError inmediato (sin red)
2. Exito -> `record_success()` -> retorna resultado
3. TransportError -> `record_failure()` -> re-raise

---

## System prompt

### Builder: `agent/prompts/__init__.py`

`build_gqm_system_prompt()` arma el prompt con Jinja2:

1. Extrae campos de GQMConfig (si hay)
2. Calcula fecha/hora actual en zona horaria configurada (TIMEZONE)
3. Renderiza `gqm_system.j2` con las variables

**Variables inyectadas al template:**

| Variable | Ejemplo |
|---|---|
| `{{ fecha_completa }}` | "27 de marzo de 2026 es viernes" |
| `{{ fecha_iso }}` | "2026-03-27" |
| `{{ hora_actual }}` | "02:30 PM" |
| `{{ id_empresa }}` | 11 |

### Inyeccion de datos dinamicos (desactivado)

Para inyectar datos de negocio (horarios, FAQs, catalogo) en el prompt:

1. Crear `services/prompt_data/` con funciones async
2. Importarlas en `prompts/__init__.py`
3. Llamarlas con `asyncio.gather` y pasar a `variables`
4. Usar `{{ variable }}` en el template .j2

Ver comentario-guia en `prompts/__init__.py` con ejemplo completo.

---

## Cache y concurrencia

### Agent cache (TTLCache)

- Key: `(id_empresa, phone, id_bitrix)` — un agente compilado por lead (el prompt incluye `<lead_identity>` con datos específicos de cada lead de Vitrix)
- TTL: `AGENT_CACHE_TTL_MINUTES` (default 60 min)
- Maxsize: `AGENT_CACHE_MAXSIZE` (default 500)
- Proteccion thundering herd: asyncio.Lock por cache_key con double-check

### Session locks

- Lock por `phone` (session WhatsApp)
- Serializa requests concurrentes del mismo usuario
- Evita race conditions en el checkpointer (InMemorySaver)

### Checkpointer (memoria conversacional)

- Default: `InMemorySaver` (sin persistencia entre restarts)
- Opcional: `AsyncRedisSaver` (si `REDIS_URL` esta configurado)
- TTL configurable: `REDIS_CHECKPOINT_TTL_HOURS` (default 24h)
- thread_id = `f"{id_empresa}_{phone}_{id_bitrix}"` (cada lead tiene su propio hilo, en espejo con la cache key)

### Message window (middleware)

Recorta historial a `MAX_MESSAGES_HISTORY` mensajes antes de enviar al LLM. No modifica el checkpointer (historial completo se preserva).

---

## Observabilidad

### Metricas Prometheus

**Contadores:**

| Metrica | Labels | Descripcion |
|---|---|---|
| `gqm_http_requests_total` | status | Requests a /api/chat |
| `gqm_chat_requests_total` | empresa_id | Mensajes recibidos |
| `gqm_chat_errors_total` | error_type | Errores de procesamiento |
| `gqm_tool_calls_total` | tool_name | Invocaciones de tools |
| `gqm_tool_errors_total` | tool_name, error_type | Errores en tools |
| `gqm_api_calls_total` | endpoint, status | Llamadas a APIs externas |
| `gqm_agent_cache_total` | result | Cache hits/misses |

**Histogramas (latencia):**

| Metrica | Descripcion |
|---|---|
| `gqm_http_duration_seconds` | Latencia total /api/chat |
| `gqm_chat_response_duration_seconds` | Tiempo de respuesta del agente |
| `gqm_tool_execution_duration_seconds` | Latencia de tools |
| `gqm_api_call_duration_seconds` | Latencia de APIs externas |
| `gqm_llm_call_duration_seconds` | Latencia del LLM |

**Gauges:** `gqm_cache_entries` (entradas en cache por tipo)
**Info:** `gqm_info` (version, modelo, tipo de agente)

### Logging tags

| Tag | Contexto |
|---|---|
| `[HTTP]` | Endpoint /api/chat |
| `[CALLBACK]` | Webhook saliente |
| `[AGENT]` | Creacion/ejecucion del agente |
| `[TOOL]` | Ejecucion de tools |
| `[API]` | Llamadas HTTP externas |
| `[CB:*]` | Eventos de circuit breaker |
| `[LLM]` | Modelo y checkpointer |
| `[CMD]` | Comandos (/clear, /restart) |

---

## Variables de entorno

| Variable | Default | Descripcion |
|---|---|---|
| **OpenAI** | | |
| `OPENAI_API_KEY` | "" | API key (requerida) |
| `OPENAI_MODEL` | "gpt-4o-mini" | Modelo LLM |
| `OPENAI_TEMPERATURE` | 0.5 | Creatividad (0.0-2.0) |
| `OPENAI_TIMEOUT` | 60 | Timeout LLM en segundos |
| `MAX_TOKENS` | 2048 | Max tokens de salida |
| **Servidor** | | |
| `SERVER_HOST` | "0.0.0.0" | Host |
| `SERVER_PORT` | 8002 | Puerto |
| `CHAT_TIMEOUT` | 120 | Timeout total del request |
| **Logging** | | |
| `LOG_LEVEL` | "INFO" | DEBUG/INFO/WARNING/ERROR/CRITICAL |
| `LOG_FILE` | "" | Archivo de log (vacio = solo stdout) |
| **HTTP** | | |
| `API_TIMEOUT` | 10 | Timeout lectura HTTP |
| `HTTP_RETRY_ATTEMPTS` | 3 | Reintentos |
| `HTTP_RETRY_WAIT_MIN` | 1 | Backoff minimo (seg) |
| `HTTP_RETRY_WAIT_MAX` | 4 | Backoff maximo (seg) |
| `HTTP_MAX_CONNECTIONS` | 50 | Pool de conexiones |
| `HTTP_MAX_KEEPALIVE` | 20 | Keep-alive |
| **Circuit breaker** | | |
| `CB_THRESHOLD` | 3 | Fallos para abrir |
| `CB_RESET_TTL` | 300 | Segundos hasta auto-reset |
| `CB_MAX_KEYS` | 500 | Keys rastreadas |
| **Cache** | | |
| `AGENT_CACHE_TTL_MINUTES` | 60 | TTL del cache de agentes |
| `AGENT_CACHE_MAXSIZE` | 500 | Max agentes cacheados |
| `SEARCH_CACHE_TTL_MINUTES` | 15 | TTL cache de busquedas (reservado) |
| `SEARCH_CACHE_MAXSIZE` | 2000 | Max busquedas cacheadas (reservado) |
| `MAX_MESSAGES_HISTORY` | 20 | Mensajes enviados al LLM |
| **Redis** | | |
| `REDIS_URL` | "" | Conexion Redis (vacio = InMemorySaver) |
| `REDIS_CHECKPOINT_TTL_HOURS` | 24 | TTL de sesiones (0 = sin TTL) |
| **APIs** | | |
| `API_KIA_RAG_URL` | "http://localhost:8000/buscar" | Endpoint RAG |
| `CALLBACK_URL` | "" | Webhook para respuestas |
| **Zona horaria** | | |
| `TIMEZONE` | "America/Lima" | Para fecha/hora en prompts |

---

## Estructura del proyecto

```
src/autobot/
  __init__.py              # Version y metadata
  main.py                  # FastAPI server, endpoints, lifespan
  schemas.py               # Pydantic models (request/response/config)
  metrics.py               # Prometheus metrics
  logger.py                # Logging centralizado

  agent/
    agent.py               # process_message(), _get_agent()
    content.py             # CitaStructuredResponse, _build_content
    context.py             # AgentContext, _prepare_agent_context
    prompts/
      __init__.py          # build_gqm_system_prompt()
      gqm_system.j2       # Template Jinja2 del prompt
    runtime/
      _llm.py             # Singleton LLM y checkpointer
      _cache.py            # TTLCache agentes, locks
      middleware.py        # message_window (trim historial)

  tools/
    tools.py               # AGENT_TOOLS = [buscar_vehiculo]

  services/
    busqueda_kia.py        # RAG API con resilient_call

  infra/
    http_client.py         # httpx AsyncClient compartido
    circuit_breaker.py     # CircuitBreaker generico
    _resilience.py         # resilient_call()

  config/
    config.py              # Variables de entorno
    circuit_breakers.py    # Instancias de CB (kia_rag_cb)
```

---

## Stack tecnologico

| Dependencia | Version | Proposito |
|---|---|---|
| FastAPI | 0.135.1 | Framework HTTP ASGI |
| uvicorn | 0.41.0 | Servidor ASGI |
| Pydantic | 2.12.5 | Validacion request/response |
| OpenAI | 2.26.0 | Error types del SDK |
| LangChain | 1.2.10 | create_agent, @tool, ToolRuntime |
| LangGraph | 1.0.10 | Grafo del agente |
| langgraph-checkpoint | 4.0.1 | InMemorySaver |
| langgraph-checkpoint-redis | 0.4.0 | AsyncRedisSaver (opcional) |
| httpx | 0.28.1 | Cliente HTTP async |
| tenacity | 9.1.4 | Retry con backoff |
| python-dotenv | 1.2.2 | Carga de .env |
| Jinja2 | 3.1.6 | Templates de prompt |
| prometheus-client | 0.24.1 | Metricas |
| cachetools | 7.0.3 | TTLCache |

---

## Docker

### Dockerfile

- Base: `python:3.12-slim`
- Package manager: `uv 0.9` (pinneado)
- **2 capas de instalacion:** deps primero (cacheables), codigo despues
- Lockfile: `uv.lock` con `--frozen` (determinista)
- Usuario: `appuser` (no root)
- Healthcheck: cada 5 min contra `/health`
- CMD: `.venv/bin/python -m autobot.main`

### compose.yaml

```yaml
services:
  autobot:
    build: .
    ports: ["8002:8002"]
    env_file: [.env]
    restart: unless-stopped
    logging:
      driver: json-file
      options: { max-size: "10m", max-file: "3" }
```
