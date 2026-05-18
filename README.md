# AutoBot

Agente conversacional de IA para el sector automotriz. Atiende consultas por WhatsApp, busca modelos por similitud semántica (RAG) y guía al cliente en su proceso de compra. Integra con CRM Vitrix (Bitrix24) para registrar leads, agendar citas, callbacks y marcar desistidos.

**Versión:** `2.5.0`
**Modelo:** `gpt-4o-mini` (configurable vía `OPENAI_MODEL`)
**Puerto:** `8002`
**Stack:** FastAPI + LangChain 1.2 + LangGraph 1.0 + httpx async + Redis opcional

---

## Inicio rápido

### Local

```bash
cp .env.example .env
# Editar .env con OPENAI_API_KEY, CALLBACK_URL, API_KIA_RAG_URL, APIKEY_VITRIX, VITRIX_API_URL
uv sync
python run.py
```

### Docker

```bash
docker compose up -d
curl localhost:8002/health
```

Guía completa: [`docs/deployment.md`](docs/deployment.md).

---

## Flujo end-to-end

```
WhatsApp -> N8N -> Orquestador -> POST /api/chat (8002)
                                       |
                                       v
                                 ACK 200 + BackgroundTasks
                                       |
                                       v
                              process_message()
                                       |
                          [TTLCache (id_empresa, phone, id_bitrix)]
                                       |
                          gpt-4o-mini + tools (LangGraph)
                              |          |
                       buscar_vehiculo    citas_vitrix
                              |          |
                          RAG API     Vitrix (Bitrix24)
                                       |
                                       v
                                POST CALLBACK_URL
```

Detalle en [`docs/architecture.md`](docs/architecture.md).

---

## Documentación

| Documento | Contenido |
|---|---|
| [`docs/api-reference.md`](docs/api-reference.md) | Endpoints HTTP: `/api/chat`, callback saliente, `/health`, `/metrics`. Schemas, ejemplos curl, status codes. |
| [`docs/architecture.md`](docs/architecture.md) | Arquitectura del sistema, componentes por carpeta, concurrencia, cache, resiliencia. |
| [`docs/configuration.md`](docs/configuration.md) | Referencia completa de variables de entorno agrupadas por sección. Distinción `TZ` vs `TIMEZONE`. |
| [`docs/deployment.md`](docs/deployment.md) | Setup local y Docker, build/run, healthcheck, troubleshooting, rotación de secretos. |
| [`docs/agent-internals.md`](docs/agent-internals.md) | Builder de prompts, templates Jinja (texto + voz), tools registradas, ToolRuntime, AgentContext. |
| [`docs/vitrix-integration.md`](docs/vitrix-integration.md) | Integración CRM: campos UF_CRM_*, payloads `edit` y `task`, fire-and-forget, normalización. |
| [`docs/observability.md`](docs/observability.md) | Logs estructurados, métricas Prometheus, healthcheck, recetas operativas. |
| [`docs/development.md`](docs/development.md) | Setup dev, stack, testing, convenciones, workflow Git, cómo agregar tools y env vars. |
| [`docs/MULTI_TENANT.md`](docs/MULTI_TENANT.md) | Diseño multi-tenant (id_empresa). |

---

## Tools del agente

| Tool | Propósito | Doc |
|---|---|---|
| `buscar_vehiculo` | Búsqueda semántica de modelos KIA vía RAG | [agent-internals](docs/agent-internals.md) |
| `agendar_cita` | Actualiza lead Vitrix + crea task "Llamar - confirmar cita IA" | [vitrix-integration](docs/vitrix-integration.md) |
| `agendar_llamada` | Marca lead como "Cliente interesado" + crea task de callback | [vitrix-integration](docs/vitrix-integration.md) |
| `marcar_desistido` | Cierra lead idempotentemente como Desistido | [vitrix-integration](docs/vitrix-integration.md) |

---

## Variables clave

| Variable | Default | Descripción |
|---|---|---|
| `OPENAI_API_KEY` | — | Requerida |
| `OPENAI_MODEL` | `gpt-4o-mini` | Modelo LLM |
| `CALLBACK_URL` | — | Webhook saliente |
| `API_KIA_RAG_URL` | `http://localhost:8000/buscar` | Endpoint RAG |
| `APIKEY_VITRIX` | — | API key del CRM |
| `VITRIX_API_URL` | `https://b24.guruxdev.com/qm/b24handlers/v2/index.php` | Endpoint del CRM |
| `REDIS_URL` | "" | Opcional; vacío = InMemorySaver |
| `TIMEZONE` | `America/Lima` | Zona del DEADLINE Vitrix y prompts |

Referencia completa: [`docs/configuration.md`](docs/configuration.md).

---

## Estructura del proyecto

```
src/autobot/
  main.py                 # FastAPI server, endpoints, lifespan
  schemas.py              # Pydantic models
  metrics.py              # Prometheus
  logger.py               # Logging centralizado

  agent/
    agent.py              # process_message, _get_agent
    content.py            # CitaStructuredResponse
    context.py            # AgentContext
    prompts/              # Builder + templates Jinja (texto + voz)
    runtime/              # LLM singleton, cache, middleware

  tools/tools.py          # AGENT_TOOLS

  services/
    busqueda_kia.py       # RAG con resilient_call
    citas_vitrix.py       # CRM Vitrix (edit + task)

  infra/                  # http_client, circuit_breaker, _resilience
  config/                 # config.py, circuit_breakers.py
```

Detalle por carpeta en [`docs/architecture.md`](docs/architecture.md).

---

## Healthcheck y observabilidad

- `GET /health` — `200` si OK, `503` si degradado (API key faltante o circuit breaker abierto).
- `GET /metrics` — métricas Prometheus.

Tags estructurados de logs: `[HTTP]`, `[CALLBACK]`, `[AGENT]`, `[TOOL]`, `[API]`, `[CB:*]`, `[LLM]`, `[CMD]`, `[CACHE]`, `[VITRIX:edit]`, `[VITRIX:edit_llamada]`, `[VITRIX:task]`, `[VITRIX:task_llamada]`, `[VITRIX:desistido]`.

Detalle en [`docs/observability.md`](docs/observability.md).

---

## Contribuir

Setup, convenciones de código, workflow Git y guía para agregar tools/env vars en [`docs/development.md`](docs/development.md). Incluye las reglas críticas para tools de escritura (sin retry, idempotencia obligatoria).
