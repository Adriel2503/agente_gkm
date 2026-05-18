# Agente conversacional — Internals

Este documento describe el núcleo del agente comercial automotriz de `agent_gkm`:
cómo se construye el system prompt, cómo se cachean los agentes compilados, qué
tools tiene registradas, cómo fluye un mensaje y cómo se inyecta el contexto runtime.

Audiencia: desarrolladores que mantengan o extiendan el agente.

---

## 1. Visión general del ciclo de ejecución

El punto de entrada es `process_message` en `src/autobot/agent/agent.py`. Para cada
mensaje entrante el flujo es:

1. Validar mensaje no vacío e interceptar comandos del sistema (`/clear`, `/restart`).
2. Tomar `acquire_session_lock(phone, id_bitrix)` para serializar requests concurrentes
   del mismo lead sobre el mismo `thread_id` del checkpointer.
3. Resolver el agente compilado vía `_get_agent(...)`:
   - Cache hit por `(id_empresa, phone, id_bitrix)` → reusar.
   - Cache miss → tomar `acquire_agent_lock(cache_key)`, double-check, construir
     `system_prompt` con `build_gqm_system_prompt(...)` y compilar con
     `create_agent(...)` de LangChain 1.2+.
4. Preparar `AgentContext` con `_prepare_agent_context(...)`.
5. Invocar `agent.ainvoke({"messages": [...]}, config={"thread_id": ...}, context=agent_context)`.
6. Parsear `result["structured_response"]` (un `CitaStructuredResponse`) y devolver
   `(reply, urls, event)` al orquestador. `event` viene del contexto y lo setean
   las tools de escritura.

Errores OpenAI (`AuthenticationError`, `RateLimitError`, `InternalServerError`,
`APIConnectionError`, `BadRequestError`) se mapean a mensajes amigables vía
`_OPENAI_ERRORS` y se registran como métricas. Cualquier otra excepción cae en el
catch genérico con `record_chat_error("agent_execution_error")`.

`thread_id` del checkpointer = `f"{id_empresa}_{phone}_{id_bitrix}"`. El comando
`/clear` borra ese thread con `await get_checkpointer().adelete_thread(...)`.

---

## 2. Builder del system prompt

Archivo: `src/autobot/agent/prompts/__init__.py`.

`build_gqm_system_prompt(id_empresa, config, nombre, marca, modelo, version,
id_bitrix, sucursal, correo)` arma el dict de variables y renderiza el template
Jinja2 `gqm_system.j2`.

### Variables inyectadas

Provienen de cuatro orígenes:

| Origen | Variables |
| --- | --- |
| Identidad del lead (parámetros directos) | `nombre`, `marca`, `modelo`, `version`, `sucursal`, `correo`, `id_bitrix` |
| Tenant | `id_empresa` |
| Fecha/hora (Perú, `app_config.TIMEZONE`) | `fecha_iso` (`YYYY-MM-DD`), `hora_actual` (`HH:MM AM/PM`), `fecha_completa` (`"17 de mayo de 2026 es lunes"`) |
| Calendario rolling 14 días | `calendario_proximos_dias` (lista `- 2026-05-17 → lunes (17 de mayo)`) |
| `GQMConfig` (Pydantic) | Cualquier campo no-None de `config.model_dump(exclude_none=True)` |

El calendario se construye en Python con `timedelta(days=delta)` para evitar que el
LLM resuelva mal expresiones del tipo "el sábado". El template solo interpola el
string ya formado.

### Cómo agregar nueva data dinámica

En `prompts/__init__.py` hay un bloque comentado-guía. El patrón recomendado:

1. Crear módulo en `services/prompt_data/` con funciones `async` (una por dataset):
   `async def fetch_horario(id_empresa: int) -> str: ...`
2. Importarlas en `prompts/__init__.py`.
3. Lanzar las llamadas en paralelo dentro de `build_gqm_system_prompt`:

   ```python
   results = await asyncio.gather(
       fetch_horario(id_empresa),
       fetch_faqs(config.id_chatbot if config else None),
       return_exceptions=True,
   )
   variables["horario"] = results[0] if not isinstance(results[0], Exception) else ""
   variables["faqs"]    = results[1] if not isinstance(results[1], Exception) else ""
   ```

4. Consumir `{{ horario }}` / `{{ faqs }}` en `gqm_system.j2`.

`return_exceptions=True` evita que un fetcher caído rompa la construcción del prompt:
ante excepción, la variable cae a `""` y el resto del prompt sigue siendo válido.

### Singletons del entorno Jinja

`_jinja_env` y `_system_template` son cargados una sola vez al importar el módulo
(`FileSystemLoader` sobre `_TEMPLATES_DIR`). El template se compila en memoria; cada
build solo llama `_system_template.render(**variables)`.

---

## 3. Templates Jinja: texto vs voz

Archivos:

- `prompts/gqm_system.j2` — canal texto (WhatsApp).
- `prompts/gqm_system - voz.j2` — canal voz outbound (último foco de iteración;
  alineado con el schema Ultravox y nomenclatura camelCase de tools en `gqm.js`).

Ambos comparten los bloques `<identity>`, `<language>`, `<datetime>`, `<banks>`,
`<stores>`, `<lead_identity>`, etc. Diferencias clave:

| Aspecto | `gqm_system.j2` | `gqm_system - voz.j2` |
| --- | --- | --- |
| Canal | Mensajería escrita | Llamada saliente (vos llamás al cliente) |
| Formato hora hablada | `HH:MM` 24h o `"3pm"` libre | Decir hora en palabras (`"tres de la tarde"`); `HH:MM` solo dentro del tool |
| Nombres de tools | snake_case (`agendar_cita`, `buscar_vehiculo`) | camelCase (`agendarCita`, `buscarVehiculo`, `marcarDesistido`) — coincide con el schema que Ultravox espera |
| Idioma | Espejo por mensaje | Espejo por turno hablado |
| URLs / medios | Sí (sale en `urls[]`) | No aplica |

Hoy el builder siempre carga `gqm_system.j2`. Para servir voz, el switch se hace
afuera del builder (a futuro: parámetro `channel` o template dinámico). Si añadís el
switch, mantené alineadas las variables inyectadas; ambos templates consumen el mismo
set (`fecha_completa`, `calendario_proximos_dias`, `nombre`, `marca`, `modelo`,
`version`, `sucursal`, `correo`, `id_bitrix`).

---

## 4. Tools registradas

Archivo: `src/autobot/tools/tools.py`. Todas se exportan vía `AGENT_TOOLS` que se
pasa a `create_agent(tools=AGENT_TOOLS, ...)`.

### 4.1 `buscar_vehiculo(query: str)` — RAG semántico

Búsqueda por similitud sobre el catálogo KIA (extensible a las demás marcas que
vende GQM). Devuelve hasta 3 modelos agrupados por secciones (Identificación,
Descripción, Precio, Motor, Dimensiones, Exterior, Interior, Tecnología, Seguridad,
Suspensión, Mantenimiento).

Cuándo la llama el LLM (según prompt):

- Cliente pregunta modelos, precios, cuotas, specs, versiones, colores, equipamiento.
- Es la única tool de **lectura**. Permite retry/CB porque no produce efectos.
- Query óptima: `marca + modelo + versión` (la específica devuelve ficha exacta).

Validaciones internas: query no vacía. Errores RAG devuelven texto neutro al LLM
(`"No pude buscar los modelos en este momento."`).

### 4.2 `agendar_cita(...)` — escritura en CRM + task

Parámetros (todos opcionales salvo `resumen`):

| Parámetro | Descripción |
| --- | --- |
| `resumen` | Resumen libre de la conversación hasta agendar. |
| `financing_required` | `"SÍ"` / `"NO"`. |
| `corporate_agreement` | Convenio corporativo del cliente. |
| `trade_in_vehicle` | `"SÍ"` / `"NO"` (auto en parte de pago). |
| `used_vehicle_brand` / `model` / `year` / `mileage` | Datos del auto usado. |
| `purchase_expectation` | Expectativa de compra. |
| `budget_description` | Presupuesto en texto (ej: `"3000 dólares"`). |
| `appointment_datetime` | Fecha/hora en `"YYYY-MM-DD HH:MM"` 24h. |

Campos vacíos: usar `"N.A"`. La tool:

1. Llama `actualizar_lead_y_crear_task(id_bitrix=..., ...)` en `services/citas_vitrix.py`.
2. Crea task en CRM: `"Llamar - confirmar cita IA"`.
3. Si éxito → setea `runtime.context.event = "cita_agendada"`.
4. Si `resolve_errors` → devuelve campo + mensaje + opciones para que el LLM
   reintente con valores válidos (no es un error de la tool, es validación del CRM).

Cuándo la llama el LLM: cliente confirmó fecha/hora de visita presencial y se
completó el perfilamiento mínimo del flujo comercial.

### 4.3 `agendar_llamada(...)` — variante callback

Misma forma que `agendar_cita` pero **sin `appointment_datetime`**. Marca el lead
como `"Cliente interesado"` y crea task `"Llamar - Cliente interesado IA"`.

Cuándo la llama el LLM: cliente prefiere callback en vez de visita (rama B del Paso 9).
Setea `runtime.context.event = "callback_solicitado"` en éxito.

### 4.4 `marcar_desistido()` — cierre del lead

Sin parámetros. Idempotente. Llama `marcar_lead_desistido(id_bitrix=...)`.

Cuándo la llama el LLM:

- Cliente no responde tras 2 reformulaciones de una pregunta clave.
- Cliente rechaza tanto cita como callback (Paso 8).
- Cliente pide no ser contactado (`"no me molesten"`, `"quítenme de la lista"`).

Setea `runtime.context.event = "desistido"` en éxito.

---

## 5. ToolRuntime y context snapshot

Todas las tools reciben `runtime: ToolRuntime = None` como último parámetro
(inyectado por LangChain). El runtime expone `runtime.context`, que es la instancia
de `AgentContext` pasada en `agent.ainvoke(..., context=agent_context)`.

`_context_snapshot(runtime)` (en `tools/tools.py`) construye un dict trazable para
logs estructurados. Sólo los tres primeros campos y `event_previo` provienen del
`AgentContext` real; los demás se resuelven con `getattr(ctx, "<campo>", None)` y
**hoy siempre devuelven `None`** porque los datos del lead viven en el system
prompt (`<lead_identity>`), no en el runtime context. Se mantienen en el
snapshot por compatibilidad si en el futuro se mueven al contexto.

```python
{
    "phone": ctx.phone,           # del AgentContext
    "id_empresa": ctx.id_empresa, # del AgentContext
    "id_bitrix": ctx.id_bitrix,   # del AgentContext
    "nombre": None,               # placeholder: getattr(ctx, "nombre", None)
    "marca": None,                # placeholder
    "modelo": None,               # placeholder
    "sucursal": None,             # placeholder
    "event_previo": ctx.event,    # útil para detectar doble invocación
}
```

Cada tool emite al menos:

- `INPUT args=... context=...` al entrar.
- `EVENT_SET event=... id_bitrix=... duration_ms=... request_id=...` en éxito.
- `VALIDATION_FAIL` / `VALIDATION_REJECTED` / `REJECTED` / `UNEXPECTED_ERROR`
  según el caso, con `duration_ms`.

El `request_id` viene del cliente HTTP de Vitrix (ver `services/citas_vitrix.py`) y
permite correlacionar logs con el side de CRM.

---

## 6. Reglas de tools de escritura

Las tres tools de escritura (`agendar_cita`, `agendar_llamada`, `marcar_desistido`)
siguen las reglas de tools de escritura (ver `docs/development.md` §5.2):

1. **Sin retry automático.** Un fallo de red no se reintenta a nivel de tool. Si la
   API de Vitrix falla, la tool devuelve un mensaje neutro y el LLM decide qué hacer
   (lo más común: re-pedir confirmación al cliente y reintentar).
2. **Sin Circuit Breaker.** Una caída prolongada se gestiona a nivel del servicio
   externo (`services/citas_vitrix.py`), no en la tool.
3. **Idempotencia delegada al prompt.** El LLM tiene instrucciones explícitas
   (`<tools>` en el template) de NO llamar dos veces la misma tool en la misma
   conversación. El campo `event_previo` del snapshot ayuda a auditarlo; si llegara
   a ocurrir, el side de CRM debe tolerar la repetición.
4. **Mensajes de salida cortos y neutros.** La tool nunca pide datos al cliente
   directamente; eso lo hace el LLM si la tool devuelve un `resolve_errors`.

Estas reglas evitan dobles altas en CRM y simplifican el side effect tracking.

---

## 7. AgentContext y eventos

Archivo: `src/autobot/agent/context.py`.

```python
@dataclass
class AgentContext:
    id_empresa: int
    phone: str = ""
    id_bitrix: str | None = None
    event: str | None = None   # "cita_agendada" | "callback_solicitado" | "desistido"
```

`_prepare_agent_context(id_empresa, config, phone, id_bitrix)` arranca con
`{id_empresa, phone, id_bitrix}` y, si hay `GQMConfig`, mergea **solo los campos
cuyo nombre coincide con un atributo del dataclass** (vía
`dc_fields(AgentContext)`). Los campos de identidad del lead (`nombre`, `marca`,
`modelo`, `sucursal`, `correo`, `version`) viven en el system prompt — no en el
contexto runtime. Las tools que los necesitan los leen vía `getattr(ctx, "...", None)`
solo para enriquecer logs.

### Valores de `event`

| Valor | Lo setea | Significado | Acción del orquestador |
| --- | --- | --- | --- |
| `"cita_agendada"` | `agendar_cita` en éxito | Lead actualizado + task de confirmación | Confirmar al canal externo, frenar follow-ups automáticos |
| `"callback_solicitado"` | `agendar_llamada` en éxito | Lead marcado como "Cliente interesado" + task | Notificar al asesor para callback manual |
| `"desistido"` | `marcar_desistido` en éxito | Lead cerrado como "Desistido" | Detener la sesión, no más mensajes salientes |
| `None` | — | Conversación en curso | Continuar normal |

`process_message` devuelve la terna `(reply, urls, event)`. El orquestador externo
(WhatsApp / voz) usa `event` como señal para detener el ciclo conversacional.

---

## 8. Cache de agentes

Archivo: `src/autobot/agent/runtime/_cache.py`.

### Por qué la key incluye `id_bitrix`

El system prompt incluye el bloque `<lead_identity>` renderizado con datos del lead
específico (`nombre`, `marca`, `modelo`, `version`, `sucursal`, `correo`, `id_bitrix`).
Dos leads distintos del mismo `phone` (caso real: cliente que reabre una nueva
cotización tras desistir) tendrían `<lead_identity>` diferente → no pueden compartir
el agente compilado.

La cache key es `(id_empresa, phone, id_bitrix)`:

- `id_empresa`: tenant key (multitenant).
- `phone`: identificador de sesión WhatsApp.
- `id_bitrix`: lead específico de Vitrix.

Si `id_bitrix is None`, leads sin ID del mismo `phone` comparten cache. Esto es OK
porque sin lead no hay datos de identidad que pre-cargar.

### TTL y eviction

- `_agent_cache: _LoggingTTLCache(maxsize=AGENT_CACHE_MAXSIZE, ttl=AGENT_CACHE_TTL_MINUTES * 60)`.
- Default: 60 minutos.
- `_LoggingTTLCache` loggea cada eviction (`[CACHE] Agente desalojado - key=... cache_size=...`)
  para observabilidad de churn.

### Locks

Hay dos diccionarios de locks, ambos con cleanup perezoso (solo cuando superan su
threshold):

| Lock | Key | Propósito |
| --- | --- | --- |
| `_agent_cache_locks` | `(id_empresa, phone, id_bitrix)` | Evitar thundering herd al construir el agente por primera vez. Patrón double-checked locking. |
| `_session_locks` | `f"{phone}_{id_bitrix}"` | Serializar requests concurrentes del mismo lead sobre el mismo `thread_id` del checkpointer. |

Cleanup: un lock se elimina solo si no está bloqueado en ese momento y, en el caso
de `_agent_cache_locks`, si su entrada en el cache ya expiró. La función
`_cleanup_stale_*_locks` se ejecuta de forma inline en `acquire_*` cuando el dict
supera su threshold (`1.5x maxsize` para agent locks, `1x maxsize` para session locks).

---

## 9. LLM, checkpointer y middleware

Archivo: `src/autobot/agent/runtime/_llm.py`.

- `get_model()` — singleton `init_chat_model("openai:...")` con
  `OPENAI_MODEL`, `OPENAI_API_KEY`, `OPENAI_TEMPERATURE`, `MAX_TOKENS`, `OPENAI_TIMEOUT`.
  Compartido por todas las empresas (la config viene de env vars).
- `init_checkpointer()` — async, se llama desde el lifespan de FastAPI. Si
  `REDIS_URL` está seteado intenta `AsyncRedisSaver` (con `JsonPlusRedisSerializer`
  y allowlist para `CitaStructuredResponse`); fallback a `InMemorySaver` si Redis
  no está disponible o el paquete `langgraph-checkpoint-redis` no está instalado.
  Las degradaciones se registran en la métrica `DEGRADATION`.
- TTL Redis: `REDIS_CHECKPOINT_TTL_HOURS` (en minutos al pasarlo al saver).
- `close_checkpointer()` cierra la conexión Redis en el shutdown del lifespan.

### `message_window` middleware

Archivo: `src/autobot/agent/runtime/middleware.py`.

```python
@wrap_model_call
async def message_window(request, handler):
    trimmed = trim_messages(
        list(request.messages),
        max_tokens=app_config.MAX_MESSAGES_HISTORY,
        strategy="last",
        token_counter=len,      # cuenta MENSAJES, no tokens reales
        allow_partial=False,    # nunca corta un par AI↔Tool
        include_system=True,    # preserva el system prompt
        start_on="human",       # el recorte arranca en un mensaje del usuario
    )
    return await handler(request.override(messages=trimmed))
```

Importante: el middleware **no toca el checkpointer**. El historial completo sigue
persistido en Redis/InMemory; solo se recorta lo que ve el LLM en cada llamada.
Esto permite mantener trazabilidad sin que el contexto crezca indefinidamente.

---

## 10. Respuesta estructurada

Archivo: `src/autobot/agent/content.py`.

```python
class CitaStructuredResponse(BaseModel):
    reply: str = Field(description="Respuesta al cliente. Nunca vacío.")
    urls: list[str] = Field(default_factory=list, description="Archivos adjuntos.")
    model_config = {"extra": "ignore"}
```

`create_agent(response_format=CitaStructuredResponse, ...)` fuerza al LLM a emitir
esta estructura. En `process_message` se lee `result["structured_response"]`:

- Si `reply` es `None` o vacío → log warning y fallback al cliente.
- `urls` se filtra a strings no vacíos.
- Si por alguna razón el agente cae al modo sin schema (no debería),
  `process_message` recupera `messages[-1].content` y loggea como anomalía.

### `_build_content`

Convierte el `message` del usuario en lo que espera la API de OpenAI:

- Solo texto → `str` (caso 1).
- Texto + URLs de imagen (jpg/jpeg/png/gif/webp) → lista de bloques
  `{"type": "text", "text": ...}` + `{"type": "image_url", ...}` (Vision).
- Máximo `_MAX_IMAGES = 10` (límite de OpenAI Vision).

El regex `_IMAGE_URL_RE` extrae URLs y las separa del texto antes de armar los
bloques.

---

## 11. Archivos relacionados

| Archivo | Rol |
| --- | --- |
| `src/autobot/agent/agent.py` | `process_message`, `_get_agent`, mapeo de errores OpenAI |
| `src/autobot/agent/context.py` | `AgentContext`, `_prepare_agent_context` |
| `src/autobot/agent/content.py` | `CitaStructuredResponse`, `_build_content` |
| `src/autobot/agent/prompts/__init__.py` | `build_gqm_system_prompt`, inyección de variables |
| `src/autobot/agent/prompts/gqm_system.j2` | Template canal texto |
| `src/autobot/agent/prompts/gqm_system - voz.j2` | Template canal voz outbound |
| `src/autobot/agent/runtime/_llm.py` | Singleton LLM + checkpointer |
| `src/autobot/agent/runtime/_cache.py` | TTLCache + locks |
| `src/autobot/agent/runtime/middleware.py` | `message_window` |
| `src/autobot/tools/tools.py` | `AGENT_TOOLS`, snapshot de contexto |
| `src/autobot/services/citas_vitrix.py` | Cliente HTTP de Vitrix (lado escritura) |
| `src/autobot/services/busqueda_kia.py` | RAG semántico (lado lectura) |
