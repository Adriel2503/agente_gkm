# AutoBot v2.5.0 — Arquitectura

Documento de arquitectura del servicio `agent_gkm` (AutoBot). Describe el flujo
end-to-end, los componentes por carpeta, el modelo de concurrencia, la cadena de
resiliencia HTTP y las diferencias entre tools de lectura y escritura.

Stack: FastAPI 0.135 + LangChain 1.2 (`create_agent`) + LangGraph 1.0 + httpx
async + checkpointer `InMemorySaver` (default) o `AsyncRedisSaver` (opcional).

---

## 1. Flujo end-to-end

El servicio expone un único endpoint productivo (`POST /api/chat`) que responde
inmediatamente con `200 OK` y procesa el mensaje en background. La respuesta
final viaja por webhook (`CALLBACK_URL`).

```
                                 (1) inbound
   WhatsApp  ───────►  N8N orquestador  ───────►  POST /api/chat  (puerto 8002)
                                                       │
                                                       │ (2) validación Pydantic
                                                       ▼
                                              main.py · FastAPI
                                                       │
                                                       │ (3) ACK inmediato
                                                       ├──────► 200 OK { status: "ok" }
                                                       │
                                                       │ (4) BackgroundTasks
                                                       ▼
                                          agent.process_message()
                                                       │
                              ┌────────────────────────┼────────────────────────┐
                              │                        │                        │
                              ▼                        ▼                        ▼
                    _prepare_agent_context     session lock (phone)       _get_agent()
                    AgentContext (Pydantic)    serializa al mismo lead          │
                                                                                │
                                                                                ▼
                                                              ┌──────────────────────────────┐
                                                              │  TTLCache de agentes         │
                                                              │  key = (id_empresa,          │
                                                              │         phone,               │
                                                              │         id_bitrix)           │
                                                              │  asyncio.Lock por cache_key  │
                                                              │  (double-check, no stampede) │
                                                              └────────────┬─────────────────┘
                                                                           │ miss
                                                                           ▼
                                                              create_agent(LLM, tools,
                                                                           system_prompt,
                                                                           checkpointer,
                                                                           middleware)
                                                                           │
                                                                           ▼
                                                              ┌──────────────────────────────┐
                                                              │  LLM gpt-4o-mini (singleton) │
                                                              │  Checkpointer (singleton)    │
                                                              │   thread_id =                │
                                                              │   f"{empresa}_{phone}_{bx}"  │
                                                              │  message_window middleware   │
                                                              │   recorta a N msgs           │
                                                              └────────────┬─────────────────┘
                                                                           │
                                                                           ▼
                                                              agent.ainvoke(messages, ctx)
                                                                           │
                                              ┌────────────────────────────┴────────────────────────────┐
                                              │                                                         │
                                              ▼                                                         ▼
                                  tool: buscar_vehiculo (lectura)                tools de escritura (Vitrix)
                                  services/busqueda_kia.py                       services/citas_vitrix.py
                                              │                                                         │
                                              ▼                                                         ▼
                                  resilient_call()                               get_client().post() directo
                                    ├─► CB check (kia_rag_cb)                    sin retry · sin CB · sin cache
                                    ├─► post_with_logging()                      idempotencia delegada al prompt
                                    │     └─► post_with_retry() (tenacity)
                                    └─► record_success / record_failure
                                              │                                                         │
                                              ▼                                                         ▼
                                       RAG API /buscar                                       Bitrix24 (Vitrix)
                                              │                                                         │
                                              └─────────────────────┬───────────────────────────────────┘
                                                                    ▼
                                                  CitaStructuredResponse { message, urls }
                                                                    │
                                                                    ▼ (5) callback saliente
                                                  POST {CALLBACK_URL}  ───────►  N8N  ───►  WhatsApp
```

Pasos:

1. WhatsApp entrega al orquestador (N8N), que normaliza y reenvía a `/api/chat`.
2. FastAPI valida con Pydantic (`schemas.ChatRequest`) y planifica el procesamiento.
3. ACK 200 inmediato para liberar al orquestador (latencia sub-50 ms en happy path).
4. `process_message` corre en `BackgroundTasks`: lock por sesión → obtención del
   agente cacheado → invocación LLM con tools → estructura la respuesta.
5. El resultado se entrega por webhook al `CALLBACK_URL`. El llamador original
   nunca espera al LLM.

---

## 2. Componentes por carpeta

### 2.1 `src/autobot/main.py`

FastAPI app. Responsabilidades:

- `lifespan`: warm-up del cliente httpx, log de configuración, cierre limpio
  (`close_http_client`).
- `POST /api/chat`: valida `ChatRequest`, mide métrica `gqm_http_*`, delega a
  `BackgroundTasks` y retorna `{ "status": "ok" }`.
- `GET /health`: chequea API key y `circuit_breaker.any_open()`; devuelve 503 si
  está degradado.
- `GET /metrics`: exposición Prometheus.

### 2.2 `src/autobot/agent/`

| Archivo | Responsabilidad |
|---|---|
| `agent.py` | `process_message()` orquesta el ciclo completo (contexto, lock, agente, invocación, callback). `_get_agent()` resuelve el agente del cache o lo compila bajo lock. |
| `context.py` | `AgentContext` (Pydantic) tipa los datos del lead inyectados al runtime de la tool. `_prepare_agent_context()` lo construye desde el request. |
| `content.py` | `CitaStructuredResponse` (modelo de salida estructurada). `_build_content()` arma el payload final `{ message, urls }` para el callback. |

### 2.3 `src/autobot/agent/runtime/`

| Archivo | Responsabilidad |
|---|---|
| `_llm.py` | Singletons del `ChatOpenAI` y del checkpointer (`InMemorySaver` o `AsyncRedisSaver`). Construye también el config base por `thread_id`. |
| `_cache.py` | `_LoggingTTLCache` de agentes compilados, `_agent_cache_locks` (por cache_key) y `_session_locks` (por phone). Limpieza periódica de locks huérfanos. |
| `middleware.py` | `message_window`: recorta el historial a `MAX_MESSAGES_HISTORY` antes de invocar al LLM. No modifica el checkpointer (la historia completa se preserva). |

### 2.4 `src/autobot/agent/prompts/`

- `__init__.py`: `build_gqm_system_prompt()` calcula fecha/hora (TZ configurable)
  y renderiza el template Jinja2.
- `gqm_system.j2`: prompt principal con `<lead_identity>`, reglas de tratamiento,
  catálogo, contrato de tools y guardarrailes.
- Variantes: `gqm_system - voz.j2` (canal voz), `gqm_system_orig.j2` (baseline).

### 2.5 `src/autobot/tools/tools.py`

Catálogo `AGENT_TOOLS` consumido por `create_agent`. Todas usan `ToolRuntime`
para recibir `AgentContext` tipado.

| Tool | Tipo | Servicio | Resiliencia |
|---|---|---|---|
| `buscar_vehiculo` | Lectura | `busqueda_kia.py` (RAG) | resilient_call + CB + retry |
| `agendar_cita` | Escritura | `citas_vitrix.py` | sin retry, sin CB, sin cache |
| `agendar_llamada` | Escritura | `citas_vitrix.py` | sin retry, sin CB, sin cache |
| `marcar_desistido` | Escritura | `citas_vitrix.py` | sin retry, sin CB, sin cache |

### 2.6 `src/autobot/services/`

- `busqueda_kia.py`: cliente del RAG. Llama vía `resilient_call()` y formatea
  hasta 3 resultados (`format_kia_resultados`).
- `citas_vitrix.py`: integración Bitrix24. Operaciones `actualizar_lead_y_crear_task`,
  `actualizar_lead_llamada_y_crear_task`, `marcar_lead_desistido`. Usa
  `get_client().post()` directo (sin retry).

### 2.7 `src/autobot/infra/`

- `http_client.py`: `httpx.AsyncClient` singleton con pool configurable;
  `post_with_retry` (tenacity, sólo `TransportError`); `post_with_logging`
  (request/response en DEBUG).
- `circuit_breaker.py`: `CircuitBreaker` genérico (CLOSED → OPEN con TTL → CLOSED).
- `_resilience.py`: `resilient_call(cb, fn, *args)` une CB + ejecución; sólo abre
  el circuit ante `TransportError` (un `success=false` de la API no lo abre).

### 2.8 `src/autobot/config/`

- `config.py`: lectura tipada de `.env` (timeouts, pool, TTLs, modelo, TZ…).
- `circuit_breakers.py`: instancias concretas (`kia_rag_cb`) parametrizadas con
  `CB_THRESHOLD` y `CB_RESET_TTL`.

### 2.9 `src/autobot/logger.py` y `metrics.py`

- `logger.py`: `get_logger()` centralizado, formato consistente, tags `[HTTP]`,
  `[CALLBACK]`, `[AGENT]`, `[TOOL]`, `[API]`, `[CB:*]`, `[LLM]`, `[CACHE]`,
  `[CMD]`, `[VITRIX:*]`.
- `metrics.py`: contadores, histogramas y gauges Prometheus; helpers
  `track_tool_execution`, `track_api_call`, `track_chat_response`.

---

## 3. Concurrencia

### 3.1 Cache de agentes (no tocar)

Diseño ya rediseñado y estable; no modificar.

| Aspecto | Valor |
|---|---|
| Estructura | `_LoggingTTLCache` (cachetools) |
| Key | `(id_empresa, phone, id_bitrix)` |
| Razón de la key | El prompt incluye `<lead_identity>` por lead. Mismo `phone` con distinto `id_bitrix` (re-lead) no debe reutilizar prompt. |
| TTL | `AGENT_CACHE_TTL_MINUTES` (default 60 min) |
| Maxsize | `AGENT_CACHE_MAXSIZE` (default 500), LRU al desbordar |
| Anti-stampede | `asyncio.Lock` por cache_key + double-check tras adquirir lock |
| Observabilidad | Log `[CACHE]` en cada eviction (TTL o LRU) |

### 3.2 Session locks

- `_session_locks[f"{phone}_{id_bitrix}"] = asyncio.Lock()` — serializa requests
  concurrentes del mismo lead. La key del lock es composite: un mismo `phone`
  con dos `id_bitrix` distintos (re-lead) tendrá dos locks independientes.
- Evita race conditions en el checkpointer (lecturas/escrituras simultáneas
  sobre el mismo `thread_id`).
- Limpieza periódica de locks huérfanos para no crecer indefinidamente.

### 3.3 thread_id (checkpointer)

```
thread_id = f"{id_empresa}_{phone}_{id_bitrix}"
```

En espejo con la cache key. Garantía: una conversación distinta (otro lead de
Vitrix sobre el mismo teléfono) tiene memoria separada.

### 3.4 message_window middleware

LangGraph middleware que recorta a `MAX_MESSAGES_HISTORY` mensajes antes de
llamar al LLM. Importante:

- No persiste el recorte. El checkpointer conserva el historial completo.
- Sirve sólo para acotar tokens del request al modelo.

---

## 4. Resiliencia (sólo lectura)

### 4.1 Cadena

```
tool buscar_vehiculo
   └─► services/busqueda_kia.buscar_vehiculo_rag
         └─► infra/_resilience.resilient_call(kia_rag_cb, fn, ...)
               ├─► kia_rag_cb.is_open(key)  →  RuntimeError (corta sin red)
               ├─► infra/http_client.post_with_logging(...)
               │     └─► post_with_retry(...)   # tenacity, exp. backoff
               ├─► on success      →  kia_rag_cb.record_success(key)
               └─► on TransportError →  kia_rag_cb.record_failure(key) + re-raise
```

### 4.2 Parámetros

| Parámetro | Default | Comentario |
|---|---|---|
| `CB_THRESHOLD` | 3 | fallos de transporte consecutivos para abrir |
| `CB_RESET_TTL` | 300 s (5 min) | tiempo en OPEN antes de volver a CLOSED |
| `HTTP_RETRY_ATTEMPTS` | 3 | sólo aplica a `TransportError` |
| `HTTP_RETRY_WAIT_MIN`/`MAX` | 1 s / 4 s | backoff exponencial (tenacity) |

### 4.3 Regla crítica

El circuit breaker **sólo se abre por `httpx.TransportError`**. Una respuesta
HTTP 200 con `success=false` del backend no cuenta como falla de transporte y
**no** abre el CB. Esto evita derribar el agente por errores funcionales
(ej. cero resultados o validación remota).

---

## 5. Tools de ESCRITURA (Vitrix)

Las tools que mutan estado en Bitrix24 (`agendar_cita`, `agendar_llamada`,
`marcar_desistido`) tienen un contrato distinto y **deliberadamente más simple**:

| Aspecto | Lectura (`buscar_vehiculo`) | Escritura (Vitrix) |
|---|---|---|
| Retry HTTP | sí (tenacity) | no |
| Circuit breaker | sí (`kia_rag_cb`) | no |
| Cache de resultados | reservado | no |
| Cliente HTTP | `post_with_logging` + `post_with_retry` | `get_client().post()` directo |
| Idempotencia | irrelevante (GET-like) | delegada al prompt y al LLM |
| Fuente de la regla | `infra/_resilience` | `docs/development.md` §5.2 |

Justificación:

- Un retry automático sobre un POST que crea una cita en Bitrix produce
  **duplicados** (la API no es idempotente por sí sola).
- El circuit breaker no aporta: la decisión de reintentar pertenece al usuario
  (vía LLM) y no a la infraestructura.
- La idempotencia se modela como **regla conversacional**: el prompt instruye al
  agente a confirmar antes de llamar y a no re-llamar tras éxito.

Si más adelante hace falta endurecer esto, el camino correcto es:

1. Idempotency key explícita (header) acordada con Vitrix.
2. CB dedicado por endpoint Vitrix con threshold distinto.
3. Tabla de auditoría local (id_bitrix → última operación + status).

---

## 6. Observabilidad transversal

- Logs estructurados con tags por subsistema. `phone_ctx` global por request
  permite correlación a lo largo de toda la traza.
- Métricas Prometheus en `/metrics` (contadores, histogramas y gauges descritos
  en el README raíz).
- Healthcheck Docker cada 5 min contra `/health`.

---

## 7. Puntos de extensión

| Necesidad | Dónde tocar |
|---|---|
| Inyectar datos dinámicos al prompt (horarios, FAQs) | `services/prompt_data/*` + `prompts/__init__.py` + variables en `.j2` |
| Nueva tool de lectura externa | nuevo `services/<x>.py` con `resilient_call` + CB en `config/circuit_breakers.py` + tool en `tools/tools.py` |
| Nueva tool de escritura | servicio sin retry/CB, instrucción de idempotencia en el prompt |
| Persistencia conversacional | configurar `REDIS_URL` (activa `AsyncRedisSaver`) |
| Otro canal (voz) | reutilizar `process_message` con prompt `gqm_system - voz.j2` |

---

## 8. Diagrama de dependencias internas

```
                main.py
                   │
                   ▼
              agent/agent.py ──► agent/context.py
                   │             agent/content.py
                   │
       ┌───────────┼──────────────┬─────────────────────┐
       ▼           ▼              ▼                     ▼
  runtime/_llm  runtime/_cache  agent/prompts      tools/tools.py
       │           │              │                     │
       │           │              │            ┌────────┴─────────┐
       │           │              │            ▼                  ▼
       │           │              │    services/busqueda_kia  services/citas_vitrix
       │           │              │            │                  │
       │           │              │            ▼                  ▼
       │           │              │       infra/_resilience   infra/http_client
       │           │              │            │                  (directo)
       │           │              │            ▼
       │           │              │       infra/circuit_breaker
       │           │              │            │
       │           │              │            ▼
       │           │              │       config/circuit_breakers
       │           │              │
       └───────────┴──────────────┴───────────► config/config.py
                                                logger.py
                                                metrics.py
```

Regla: `runtime/_cache.py` no importa de `infra/` para evitar ciclos. La
configuración (`config/`) es hoja del grafo.
