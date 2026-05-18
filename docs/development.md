# Guía de desarrollo — agent_gkm

Esta guía está dirigida a desarrolladores que se incorporan al proyecto `agent_gkm` (paquete `autobot`). Cubre setup local, estructura del repositorio, stack tecnológico, testing, convenciones de código, workflow Git y procedimientos para extender el agente.

Para detalle profundo de arquitectura interna y flujos de mensajes, ver `docs/architecture.md` y `docs/agent-internals.md`.

---

## 1. Setup local desde cero

### 1.1. Requisitos previos

| Herramienta | Versión mínima | Notas |
|-------------|----------------|-------|
| Python | 3.12 | Definido en `pyproject.toml` (`requires-python = ">=3.12"`) |
| uv | 0.9+ | Gestor de paquetes/entornos. Instalable desde `https://docs.astral.sh/uv/` |
| Git | 2.30+ | — |
| Redis | 7.x | Opcional en local; requerido si se usa `AsyncRedisSaver` como checkpointer |
| Docker | 24+ | Opcional; sólo si se reproduce el contenedor de producción |

### 1.2. Pasos

1. Clonar el repositorio.

   ```bash
   git clone <repo-url> agent_gkm
   cd agent_gkm
   ```

2. Crear el archivo `.env` a partir del template.

   ```bash
   cp .env.example .env
   ```

   Editar `.env` con las credenciales reales (API key de OpenAI, URL de la API RAG KIA, claves de Vitrix/Bitrix, Redis URL, etc.). Las variables están listadas y documentadas en `docs/configuration.md`.

3. Sincronizar dependencias con `uv`. Esto crea `.venv/` y resuelve desde `uv.lock`.

   ```bash
   uv sync
   ```

   Para incluir dependencias de desarrollo (pytest):

   ```bash
   uv sync --group dev
   ```

4. Levantar el servidor de desarrollo.

   ```bash
   uv run python run.py
   ```

   El servidor expone por defecto el puerto `8002` (mismo que el `EXPOSE` del `Dockerfile`). Endpoints principales:

   - `POST /api/chat` — entrada del agente conversacional
   - `GET /health` — healthcheck
   - `GET /metrics` — métricas Prometheus

5. Verificar que el servicio responde.

   ```bash
   curl http://localhost:8002/health
   ```

### 1.3. Levantar con Docker (opcional)

```bash
docker compose up --build
```

Usa `compose.yaml` y `Dockerfile` del repo. El contenedor expone `8002` y monta `./logs` como bind mount para observabilidad.

---

## 2. Estructura del repositorio

A nivel alto, el repositorio se organiza así:

```
agent_gkm/
├── src/autobot/          Paquete principal del agente
├── test/                 Suite de pruebas (unit + integration)
├── docs/                 Documentación del proyecto
├── data/                 Datos estáticos / fixtures externas
├── run.py                Entrypoint para desarrollo local
├── compose.yaml          Stack Docker Compose
├── Dockerfile            Imagen de producción
├── pyproject.toml        Definición de paquete y dependencias
├── uv.lock               Lockfile de uv
└── README.md             Visión general
```

### 2.1. Carpetas dentro de `src/autobot/`

| Carpeta / módulo | Responsabilidad |
|------------------|-----------------|
| `agent/` | Construcción del agente LangGraph, prompt templates, runtime context |
| `config/` | Carga y validación de variables de entorno (`config.py`) |
| `infra/` | Infraestructura: clientes HTTP, Redis checkpointer, caches |
| `services/` | Integraciones con APIs externas (RAG KIA, Vitrix/Bitrix24) |
| `tools/` | Tools expuestas al LLM (registro central en `tools.py`) |
| `logger.py` | Logger estructurado con tags |
| `main.py` | Bootstrap FastAPI / uvicorn |
| `metrics.py` | Definición de métricas Prometheus |
| `schemas.py` | Modelos Pydantic de request/response |

El árbol detallado y las relaciones entre módulos están en `docs/architecture.md`. No duplicar acá.

---

## 3. Stack tecnológico

Versiones tomadas de `pyproject.toml` (resueltas exactas en `uv.lock`).

| Dependencia | Versión | Propósito |
|-------------|---------|-----------|
| `fastapi` | 0.135.1 | Framework HTTP ASGI (`POST /api/chat`, `GET /health`) |
| `uvicorn[standard]` | 0.41.0 | Servidor ASGI |
| `pydantic` | 2.12.5 | Validación de request/response y modelos de booking |
| `openai` | 2.26.0 | Tipos de error del SDK (AuthenticationError, RateLimitError, etc.) |
| `langchain` | 1.2.10 | `create_agent`, decorador `@tool`, `ToolRuntime`, `wrap_model_call` |
| `langchain-core` | 1.2.17 | `trim_messages`, `BaseMessage` |
| `langchain-openai` | 1.1.10 | `init_chat_model("openai:gpt-4o-mini")` |
| `langgraph` | 1.0.10 | Grafo del agente y flujo de mensajes |
| `langgraph-checkpoint` | 4.0.1 | `InMemorySaver` (checkpointer en memoria) |
| `langgraph-checkpoint-redis` | 0.4.0 | `AsyncRedisSaver` (checkpointer Redis) |
| `httpx` | 0.28.1 | Cliente HTTP async hacia APIs externas |
| `tenacity` | 9.1.4 | Retry con backoff exponencial en operaciones de LECTURA |
| `python-dotenv` | 1.2.2 | Carga de `.env` en `config.py` |
| `jinja2` | 3.1.6 | Templates de system prompt (`gqm_system.j2`) |
| `prometheus-client` | 0.24.1 | Métricas expuestas en `/metrics` |
| `cachetools` | 7.0.3 | `TTLCache` para agentes, horarios, búsquedas y contexto |

Dependencias de desarrollo: `pytest==9.0.2`, `pytest-asyncio==1.3.0`.

---

## 4. Testing

### 4.1. Estructura

```
test/
├── conftest.py           Fixtures compartidos (id_empresa, api_base_url)
├── unit/                 Tests aislados, sin red
│   ├── test_cache.py
│   ├── test_config.py
│   ├── test_content.py
│   └── test_middleware.py
└── integration/          Tests con stack completo del agente
    ├── test_agent.py
    └── test_api.py
```

Configuración relevante de pytest (en `pyproject.toml`):

- `testpaths = ["test"]`
- `asyncio_mode = "auto"` — todas las funciones `async def test_*` se ejecutan automáticamente como asyncio.

### 4.2. Cómo correr los tests

Desde la raíz del repo:

```bash
uv run pytest                       # suite completa
uv run pytest test/unit              # sólo unit
uv run pytest test/integration       # sólo integration
uv run pytest -k test_cache          # por keyword
uv run pytest -vv -x                 # verbose, detener en primer fallo
```

### 4.3. Patrones de mocking

- **`httpx`**: usar `httpx.MockTransport` o `respx` (si se agrega) para interceptar requests. No mockear `httpx.AsyncClient` con `MagicMock` salvo casos triviales; preferir transports reales que validen el shape del request.
- **`openai`**: parchear `langchain_openai` a nivel de `init_chat_model` o inyectar un fake `BaseChatModel` vía dependencia. Para errores específicos (rate limit, auth) construir las excepciones del SDK con sus campos requeridos.
- **Redis / checkpointer**: usar `InMemorySaver` de `langgraph-checkpoint` en tests, no levantar Redis.
- **Tiempo**: para TTLs y caches, parchear `time.monotonic` o el reloj que use `cachetools`.

### 4.4. Convención de organización

Los tests siguen estructura espejo de `src/autobot/`. Para un módulo `src/autobot/tools/tools.py` el test correspondiente vive en `test/unit/test_tools.py` o `test/integration/test_tools_*.py` según el alcance.

---

## 5. Convenciones de código

### 5.1. Logging

- Usar siempre el logger estructurado definido en `src/autobot/logger.py`. **Nunca** usar `print()`.
- Cada log lleva un tag en bracket que identifica el subsistema. Tags vigentes:

  | Tag | Uso |
  |-----|-----|
  | `[HTTP]` | Endpoint `/api/chat` (request/response) |
  | `[CALLBACK]` | Webhook saliente al `CALLBACK_URL` |
  | `[AGENT]` | Construcción y ejecución del grafo LangGraph |
  | `[TOOL]` / `[TOOL:<nombre>]` | Ejecución genérica y por tool específica |
  | `[API]` | Llamadas HTTP salientes a APIs externas (RAG, Vitrix) |
  | `[CB:*]` | Eventos del circuit breaker (`[CB:kia_rag_api]`...) |
  | `[LLM]` | Llamadas al modelo (tokens, duración) |
  | `[CMD]` | Comandos especiales del cliente (`/clear`, `/restart`) |
  | `[CACHE]` | Hits/misses y evictions de los TTLCache |
  | `[VITRIX:edit]` / `[VITRIX:edit_llamada]` / `[VITRIX:task]` / `[VITRIX:task_llamada]` / `[VITRIX:desistido]` | Operaciones contra Vitrix |

- Adjuntar `phone_ctx` (contexto del teléfono) y duraciones en milisegundos cuando aplique. La observabilidad de logs ya está estandarizada con `phone_ctx` global, duraciones por operación y métricas de eviction. No reinventar.

### 5.2. Tools de ESCRITURA — reglas críticas

Aplican a cualquier tool que mute estado en sistemas externos (Vitrix/Bitrix24, etc.):

1. **Sin retry automático**. No usar `post_with_retry`/`tenacity` en POST/PUT/DELETE: el retry crea duplicados. Llamar a `get_client().post()` directamente.
2. **Idempotencia obligatoria**. Diseñar el payload y la llamada para ser seguros ante reintentos manuales (idempotency keys, dedupe por correlación, búsqueda previa antes de crear).
3. **Sin circuit breaker compartido** con operaciones de lectura. Si se necesita CB, instanciar uno dedicado para escritura.
4. **Tools de lectura sí pueden usar `tenacity`** (`post_with_logging` está pensado para reads).

### 5.3. Cache del agente

- La key del cache de agentes ya fue rediseñada: `(id_empresa, phone, id_bitrix)`. **No modificar la estructura de la key.** Cualquier necesidad de invalidar o segmentar debe agregarse vía dimensión adicional, no rotando la key existente.
- Las env vars `SEARCH_CACHE_*` están cargadas en `config.py` pero **no tienen consumidor activo** (reservadas para una futura cache de búsquedas). El counter `gqm_search_cache_total` existe pero tampoco se incrementa hoy.

### 5.4. `thread_id` de LangGraph

El `thread_id` que se pasa al checkpointer debe construirse en espejo con la key del cache de agentes:

```python
thread_id = f"{id_empresa}_{phone}_{id_bitrix}"
```

Esto garantiza que un mismo (empresa, teléfono, contacto Bitrix) recupere su estado conversacional de forma consistente entre el cache en memoria y Redis.

### 5.5. Async end-to-end

- Todo el pipeline es `async`. No introducir llamadas síncronas bloqueantes (`requests`, `time.sleep`, I/O de disco sin `aiofiles`) dentro de handlers, tools o servicios.
- Si se necesita CPU-bound, encapsular en `asyncio.to_thread` o `run_in_executor`. Documentarlo en el PR.
- Clientes HTTP: usar el `httpx.AsyncClient` ya construido en `infra/` (singleton), no instanciar clientes nuevos por request.

### 5.6. Tipado y estilo

- Type hints obligatorios en funciones públicas y firmas de tools.
- Pydantic v2 para todos los modelos de datos cruzando boundaries (request, response, payload de tools).
- Evitar `Any` salvo en interop con LLM (mensajes intermedios).

---

## 6. Workflow Git

### 6.1. Branching

- Rama base: `main`.
- Crear feature branches con prefijo descriptivo: `feat/<tema>`, `fix/<tema>`, `refactor/<tema>`.
- Mantener las branches cortas y rebasar sobre `main` antes del PR.

### 6.2. Convención de commits

Commits convencionales. Tipos válidos:

| Tipo | Uso |
|------|-----|
| `feat` | Nueva funcionalidad |
| `fix` | Corrección de bug |
| `refactor` | Cambio interno sin alterar comportamiento |
| `chore` | Tareas de mantenimiento (deps, build, config) |
| `docs` | Sólo documentación |
| `test` | Sólo tests |
| `perf` | Mejora de performance |

Formato:

```
<tipo>(<scope opcional>): <descripción imperativa en minúsculas>

<cuerpo opcional explicando el por qué>
```

Ejemplos reales del repo:

```
feat(prompt voz): adaptar flujo conversacional a llamada outbound
fix(prompt voz): alinear tool calls con schema Ultravox de gqm.js
refactor(prompt): build calendar string in Python, not in Jinja
```

### 6.3. Reglas del repo

- **Sin `Co-Authored-By: Claude`** en los commits. Es regla explícita de este proyecto. Los commits van firmados sólo por el desarrollador humano.
- No usar `--no-verify` para saltar hooks. Si un hook falla, arreglar la causa.
- Preferir commits nuevos sobre `--amend`.

### 6.4. Pull Requests

- PR siempre contra `main`.
- Título corto (< 70 chars) siguiendo el formato de commit convencional.
- Body con: resumen de cambios, motivación y checklist de pruebas (qué se corrió localmente).
- Marcar explícitamente cuando se toquen áreas sensibles: cache key, `thread_id`, tools de escritura, prompts.

---

## 7. Cómo agregar una nueva tool al agente

Paso a paso:

1. Crear la función de la tool en el módulo correspondiente dentro de `src/autobot/tools/` (un archivo por dominio funcional). Firmar como `async def` y usar el decorador `@tool` de `langchain`.

   ```python
   from langchain.tools import tool
   from autobot.logger import get_logger

   logger = get_logger(__name__)

   @tool
   async def mi_nueva_tool(arg1: str, arg2: int) -> dict:
       """Descripción que VERÁ el modelo. Sé conciso y preciso."""
       logger.info("[TOOL:mi_nueva_tool] start", extra={"arg1": arg1})
       # ... lógica
       return {"ok": True}
   ```

2. Registrar la tool en `src/autobot/tools/tools.py` agregándola a la lista `AGENT_TOOLS` (orden alfabético dentro de su grupo lectura/escritura).

3. Instrumentar la ejecución con `track_tool_execution` (helper existente en `tools/` o `metrics.py`) para que la duración y el resultado se reflejen en Prometheus.

4. Tomar **snapshot del runtime context** al loguear inicio y fin (id_empresa, phone, id_bitrix). Esto se hace via el helper estándar; replicar el patrón de las tools existentes.

5. Si la tool es de **ESCRITURA**, aplicar las reglas de la sección 5.2: sin retry, idempotencia, sin CB compartido.

6. Agregar tests:
   - Unit test en `test/unit/test_tools_<dominio>.py` cubriendo el happy path y errores.
   - Integration test en `test/integration/test_agent.py` que verifique que el agente invoca la tool con los argumentos correctos en un escenario representativo.

7. Si la tool requiere config nueva, seguir la sección 8.

Detalles del runtime context, contrato de inputs/outputs y wrapping del modelo: ver `docs/agent-internals.md`.

---

## 8. Cómo agregar una nueva variable de entorno

1. Definir la variable en `src/autobot/config/config.py` usando los helpers manuales (`_get_str`, `_get_int`, `_get_float`, `_get_bool`). Aceptan `default`, `min_val`/`max_val` y emiten warnings si el valor cae fuera de rango.

   ```python
   MI_NUEVA_VAR: int = _get_int("MI_NUEVA_VAR", default=30, min_val=1, max_val=300)
   ```

   Exportarla en `src/autobot/config/__init__.py` para que sea importable como `from autobot import config as app_config` → `app_config.MI_NUEVA_VAR`.

2. Documentar la variable en `docs/configuration.md`, en la tabla del subsistema correspondiente. Incluir: nombre, tipo, default, descripción y si es obligatoria.

3. Agregarla a `.env.example` con un valor de ejemplo (no real) y comentario corto.

   ```dotenv
   # Timeout en segundos para X (default: 30)
   MI_NUEVA_VAR=30
   ```

4. Si la variable controla un comportamiento riesgoso (escritura externa, retries, cache TTL), incluir en el PR la justificación del default y cómo se observa en métricas/logs.

5. Nunca leer `os.environ` directamente desde código de aplicación. Toda lectura pasa por `autobot.config`.

---

## 9. Recursos relacionados

- `docs/architecture.md` — Arquitectura interna y árbol detallado de `src/autobot/`.
- `docs/agent-internals.md` — Construcción del grafo LangGraph, runtime context y wrapping del modelo.
- `docs/configuration.md` — Catálogo completo de variables de entorno.
- `docs/api-reference.md` — Contratos de `POST /api/chat` y `GET /health`.
- `docs/MULTI_TENANT.md` — Modelo de aislamiento por `id_empresa`.
- `README.md` — Visión general y árbol completo del repo.
