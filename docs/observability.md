# Observabilidad — agent_gkm

Guía operativa de logs, métricas y tracing por request del agente AutoBot
(`agent_gkm`). Documenta el formato exacto de los logs, todas las métricas
expuestas en `/metrics`, el healthcheck y recetas de troubleshooting.

Archivos de referencia:

- `src/autobot/logger.py` — configuración de logging y ContextVars.
- `src/autobot/metrics.py` — definición de métricas Prometheus.
- `src/autobot/main.py` — seteo de `trace_id` / `phone_ctx` por request,
  endpoint `/health` y `/metrics`.

---

## 1. Logs estructurados

### 1.1 Formato

El formato se define en `setup_logging()` (`logger.py`):

```
%(asctime)s - %(name)s - %(levelname)s - [%(filename)s:%(lineno)d] - [trace=%(trace_id)s] [phone=%(phone)s] - %(message)s
```

Cada línea contiene:

| Campo        | Origen                                         | Ejemplo                       |
|--------------|------------------------------------------------|-------------------------------|
| `asctime`    | Timestamp del SO (libc, `TZ`)                  | `2026-05-17 14:23:11,432`     |
| `name`       | Nombre del logger (`__name__` del módulo)      | `autobot.agent`               |
| `levelname`  | Nivel del registro                             | `INFO`                        |
| `filename:lineno` | Archivo y línea que emitió el log         | `agent.py:142`                |
| `trace_id`   | `ContextVar` seteado en `/api/chat`            | `[trace=a1b2c3d4]`            |
| `phone`      | `ContextVar` con teléfono del lead             | `[phone=5491134567890]`       |
| `message`    | Mensaje + tag estructurado (ver 1.2)           | `[AGENT] Llamando LLM...`     |

Ejemplo real:

```
2026-05-17 14:23:11,432 - autobot.main - INFO - [main.py:105] - [trace=a1b2c3d4] [phone=5491134567890] - [HTTP] Mensaje recibido - Session: 5491134567890, Empresa: gkm, Length: 27 chars
```

### 1.2 Propagación por ContextVar

`trace_id` y `phone_ctx` se definen como `ContextVar` en `logger.py` y se
setean al inicio de cada request en `main.py::chat`:

```python
trace_id.set(uuid.uuid4().hex[:8])
phone_ctx.set(req.phone)
```

`asyncio.create_task` copia automáticamente el contexto, por lo que la tarea
background `_process_and_callback` y todas sus coroutines hijas (agent,
tools, llamadas LLM/API) heredan los mismos valores sin pasarlos
explícitamente. El `_TraceFilter` los inyecta en cada `LogRecord`.

### 1.3 Tags estructurados

Todos los logs usan un prefijo en mayúsculas entre corchetes que identifica
el subsistema. Esto permite filtrar con `grep '\[TAG\]'` o con queries
estructuradas.

| Tag                  | Subsistema                | Información que lleva                                                            |
|----------------------|---------------------------|----------------------------------------------------------------------------------|
| `[HTTP]`             | Endpoint `/api/chat`      | Mensaje recibido, JSON de entrada, timeouts, errores de parseo.                  |
| `[CALLBACK]`         | Webhook saliente          | JSON de salida, status code y duración del POST al `CALLBACK_URL`.               |
| `[AGENT]`            | Grafo LangGraph           | Inicio/fin del agente, decisiones de routing, mensajes acumulados, finalización. |
| `[TOOL]`             | Ejecución genérica tool   | Nombre + argumentos serializados antes de ejecutar.                              |
| `[TOOL:<nombre>]`    | Tool específica           | Logs internos de la tool (`[TOOL:buscar_vehiculo]`, `[TOOL:agendar_cita]`...).   |
| `[API]`              | Llamadas HTTP salientes   | Endpoint, status, payload (DEBUG), reintentos, duración.                         |
| `[CB:*]`             | Circuit breaker           | Apertura/cierre del CB (`[CB:kia_rag_api]`), contadores y reset.                 |
| `[LLM]`              | Llamadas al modelo        | Modelo, tokens (input/output/total), duración, status.                           |
| `[CMD]`              | Comandos del agente       | Comandos especiales del cliente (`/clear`, `/restart`).                          |
| `[CACHE]`            | TTLCache del agente       | Hits/misses, evictions y duraciones del cache de agentes.                        |
| `[VITRIX:edit]`           | Vitrix — edición de lead (cita) | POST `action=edit` para flujo cita: campos modificados, ID Bitrix.         |
| `[VITRIX:edit_llamada]`   | Vitrix — edición de lead (callback) | POST `action=edit` para flujo callback: campos modificados, ID Bitrix. |
| `[VITRIX:task]`           | Vitrix — task de cita     | POST `action=task` "Llamar - confirmar cita IA" (fire-and-forget +5s).           |
| `[VITRIX:task_llamada]`   | Vitrix — task de callback | POST `action=task` "Llamar - Cliente interesado IA" (fire-and-forget +5s).       |
| `[VITRIX:desistido]`      | Vitrix — desistimiento    | POST `action=edit` marcando lead como desistido.                                 |

### 1.4 Niveles

| Nivel    | Uso                                                                          |
|----------|------------------------------------------------------------------------------|
| `DEBUG`  | Request/response HTTP completos, payloads detallados, dumps de estado.       |
| `INFO`   | Default. Tráfico normal: requests, callbacks, decisiones del agente.         |
| `WARNING`| Fallos recuperables (retries, fallbacks, CB en `half-open`).                 |
| `ERROR`  | Excepciones, timeouts, CB abierto, errores irrecuperables del request.      |
| `CRITICAL` | Reservado para fallos de inicialización (no usado en operación normal).    |

Se controla con la variable de entorno `LOG_LEVEL` (default `INFO`).
Subir a `DEBUG` activa también `httpx`/`httpcore`/`openai` aunque por
default están en `WARNING` para evitar ruido (ver `logger.py:70-73`).

### 1.5 Rotación y persistencia

Si `LOG_FILE` está seteado, además del `StreamHandler` (stdout) se agrega
un `RotatingFileHandler`:

| Parámetro     | Valor                                |
|---------------|--------------------------------------|
| `maxBytes`    | `10_485_760` (10 MB)                 |
| `backupCount` | `5`                                  |
| `encoding`    | `utf-8`                              |

Esto produce hasta 6 archivos: `autobot.log`, `autobot.log.1`, …, `autobot.log.5`
(máx. 60 MB en disco).

En `compose.yaml` el directorio se monta con bind mount:

```yaml
volumes:
  - ./logs:/app/logs
```

y `LOG_FILE=/app/logs/autobot.log`, para que los logs persistan en el host
incluso si se recrea el contenedor.

### 1.6 Correlación

Filtrar por **request** (todas las líneas de una llamada):

```bash
grep 'trace=a1b2c3d4' logs/autobot.log
```

Filtrar por **lead** (toda la conversación de un teléfono):

```bash
grep 'phone=5491134567890' logs/autobot.log
```

Filtrar por subsistema:

```bash
grep '\[CALLBACK\]' logs/autobot.log | tail -50
grep '\[CB:'         logs/autobot.log
```

Combinaciones útiles:

```bash
# Todas las tools ejecutadas en una conversación
grep 'phone=5491134567890' logs/autobot.log | grep '\[TOOL'

# Errores con su trace para reconstruir el contexto
grep -E 'ERROR|CRITICAL' logs/autobot.log | grep -oE 'trace=[a-f0-9]+' | sort -u
```

### 1.7 Timestamp y zona horaria

`asctime` usa el reloj del SO vía `time.localtime()` (libc). El offset **no
se imprime** en el formato actual; la zona viene determinada por la
variable de entorno `TZ` del contenedor.

Recomendación: setear `TZ=America/Lima` (o la zona del
deploy) en `compose.yaml` para que `asctime` y los timestamps internos
del agente coincidan. Si dos hosts tienen `TZ` distintos, los logs no son
directamente comparables — siempre correlacionar por `trace_id`, no por
hora.

---

## 2. Métricas Prometheus

Endpoint: `GET /metrics` (montado en `main.py` vía
`prometheus_client.make_asgi_app()`).

Prefijo común: `gqm_*`. Todas las métricas son globales al proceso.

### 2.1 Counters

| Métrica                              | Labels                          | Semántica                                                                 |
|--------------------------------------|---------------------------------|---------------------------------------------------------------------------|
| `gqm_http_requests_total`            | `status`                        | Requests a `/api/chat`. `status` ∈ `success`, `timeout`, `error`.        |
| `gqm_chat_requests_total`            | `empresa_id`                    | Mensajes recibidos por el agente, segmentado por tenant.                  |
| `gqm_chat_errors_total`              | `error_type`                    | Errores en el procesamiento del mensaje (ver §2.1.1 para valores posibles). |
| `gqm_tool_calls_total`               | `tool_name`                     | Invocaciones de cada tool del agente.                                     |
| `gqm_tool_errors_total`              | `tool_name`, `error_type`       | Excepciones lanzadas dentro de una tool (incluye `validation_error`).     |
| `gqm_api_calls_total`                | `endpoint`, `status`            | Llamadas a APIs externas (ver §2.1.2 para `endpoint`). `status`: `success` o `error_<Exc>`. |
| `gqm_agent_cache_total`              | `result`                        | Cache `(id_empresa, phone, id_bitrix)`. `result` ∈ `hit`, `miss`.         |
| `gqm_search_cache_total`             | `result`                        | **Reservado**: counter declarado sin sitio de incremento en código actual. |
| `gqm_llm_tokens_total`               | `type`                          | Tokens LLM globales. `type` ∈ `input`, `output`, `total`.                 |
| `gqm_llm_tokens_by_empresa_total`    | `empresa_id`, `type`            | Lo mismo, por tenant — para facturación / análisis de uso.                |
| `gqm_availability_degradation_total` | `service`, `reason`             | Fallback silencioso (`kia_rag`/`checkpointer` × `circuit_open`/`redis_unavailable`/`import_missing`). |

#### 2.1.1 Valores posibles de `error_type` en `gqm_chat_errors_total`

| `error_type`               | Origen                                                                 |
|----------------------------|------------------------------------------------------------------------|
| `openai_auth_error`        | `openai.AuthenticationError` (401, API key inválida).                  |
| `openai_rate_limit`        | `openai.RateLimitError` (429, throttling).                             |
| `openai_server_error`      | `openai.InternalServerError` (5xx del lado OpenAI).                    |
| `openai_connection_error`  | `openai.APIConnectionError` (red/DNS hacia OpenAI).                    |
| `openai_bad_request`       | `openai.BadRequestError` (400, payload rechazado).                     |
| `agent_creation_error`     | Fallo construyendo el agente compilado (excepción en `_get_agent`).    |
| `agent_execution_error`    | Cualquier otra excepción no mapeada durante la ejecución del agente.   |

#### 2.1.2 Valores posibles de `endpoint` en `gqm_api_calls_total`

| `endpoint`                    | Origen (`with track_api_call(...)`)                                  |
|-------------------------------|----------------------------------------------------------------------|
| `kia_rag`                     | `services/busqueda_kia.py` — POST al RAG de KIA.                     |
| `vitrix_edit_lead`            | `services/citas_vitrix.py` — `actualizar_lead_cita` (flujo cita).     |
| `vitrix_crear_task`           | `services/citas_vitrix.py` — `crear_task_confirmar_cita`.            |
| `vitrix_edit_llamada`         | `services/citas_vitrix.py` — `actualizar_lead_llamada` (callback).   |
| `vitrix_crear_task_llamada`   | `services/citas_vitrix.py` — `crear_task_llamada`.                   |
| `vitrix_desistido`            | `services/citas_vitrix.py` — `marcar_lead_desistido`.                |

### 2.2 Histograms (latencia, segundos)

| Métrica                                 | Labels        | Buckets                                                  | Mide                                                  |
|-----------------------------------------|---------------|----------------------------------------------------------|--------------------------------------------------------|
| `gqm_http_duration_seconds`             | —             | `0.25, 0.5, 1, 2.5, 5, 10, 20, 30, 60, 90, 120`          | End-to-end de `/api/chat` (incluye LLM y tools).      |
| `gqm_chat_response_duration_seconds`    | `status`      | `0.1, 0.5, 1, 2, 5, 10, 30, 60, 90`                      | Tiempo del agente respondiendo (sin la red del callback). |
| `gqm_tool_execution_duration_seconds`   | `tool_name`   | `0.1, 0.5, 1, 2, 5, 10, 20, 30`                          | Tiempo por tool individual.                            |
| `gqm_api_call_duration_seconds`         | `endpoint`    | `0.1, 0.25, 0.5, 1, 2.5, 5, 10`                          | RTT de cada API externa.                               |
| `gqm_llm_call_duration_seconds`         | `status`      | `0.5, 1, 2, 5, 10, 20, 30, 60, 90`                       | Tiempo por llamada al LLM (`success` / `error`).       |

### 2.3 Gauges

| Métrica              | Labels        | Significado                                       |
|----------------------|---------------|---------------------------------------------------|
| `gqm_cache_entries`  | `cache_type`  | Cantidad de entradas vivas en cada cache (agente, búsqueda, etc.). |

### 2.4 Info

| Métrica     | Labels (estáticos)                              | Uso                                          |
|-------------|-------------------------------------------------|----------------------------------------------|
| `gqm_info`  | `version`, `model`, `agent_type` (`= "gqm"`)    | Identifica build/modelo en queries y alertas. |

### 2.5 Ejemplos de scrape

`prometheus.yml`:

```yaml
scrape_configs:
  - job_name: agent_gkm
    metrics_path: /metrics
    scrape_interval: 15s
    static_configs:
      - targets: ['agent_gkm:8002']
```

Verificación manual:

```bash
curl -s http://localhost:8002/metrics | grep -E '^gqm_' | head -40
```

---

## 3. Healthcheck

Endpoint: `GET /health` (`main.py::health`).

Respuesta:

```json
{
  "status": "ok" | "degraded",
  "agent": "autobot",
  "version": "X.Y.Z",
  "issues": ["openai_api_key_missing", "kia_rag_api_degraded"]
}
```

| Condición                              | Status code | `status`    |
|----------------------------------------|-------------|-------------|
| Sin issues                             | `200`       | `ok`        |
| `OPENAI_API_KEY` ausente               | `503`       | `degraded`  |
| Circuit breaker abierto (`get_health_issues()`) | `503` | `degraded`  |
| Cualquier otro issue reportado por config       | `503` | `degraded`  |

### 3.1 Healthcheck Docker

El `Dockerfile` declara el healthcheck con `python urllib` (no requiere `curl`
en la imagen) y los siguientes parámetros:

```dockerfile
HEALTHCHECK --interval=300s --timeout=5s --start-period=10s --retries=2 \
    CMD .venv/bin/python -c "import urllib.request; urllib.request.urlopen('http://localhost:8002/health')" || exit 1
```

`urllib.request.urlopen` lanza `HTTPError` ante 5xx, por lo que un `/health`
con `503` falla la prueba y, tras 2 reintentos espaciados 300 s, marca al
contenedor `unhealthy`. Esto desconecta al servicio del load balancer / Go
gateway hasta que el issue se resuelva (ej. CB cerrándose). Detalle completo
en `docs/deployment.md` §Healthcheck.

Los logs de Uvicorn para `GET /health` con respuesta 200 están filtrados
(ver `_HealthLogFilter` en `main.py`), para no inundar el log con tráfico
del healthcheck. Las respuestas 503 sí se loguean.

---

## 4. Recetas operativas

### 4.1 Dashboards Prometheus / Grafana sugeridos

Paneles mínimos:

**Tráfico**

```promql
sum by (status) (rate(gqm_http_requests_total[5m]))
```

**Latencia p95 end-to-end**

```promql
histogram_quantile(0.95, sum by (le) (rate(gqm_http_duration_seconds_bucket[5m])))
```

**Latencia p95 por tool**

```promql
histogram_quantile(
  0.95,
  sum by (le, tool_name) (rate(gqm_tool_execution_duration_seconds_bucket[5m]))
)
```

**Tasa de error de tools**

```promql
sum by (tool_name) (rate(gqm_tool_errors_total[5m]))
  /
sum by (tool_name) (rate(gqm_tool_calls_total[5m]))
```

**Cache hit ratio (agente)**

```promql
sum(rate(gqm_agent_cache_total{result="hit"}[5m]))
  /
sum(rate(gqm_agent_cache_total[5m]))
```

**Consumo de tokens por empresa**

```promql
sum by (empresa_id) (rate(gqm_llm_tokens_by_empresa_total{type="total"}[1h]))
```

**Degradación silenciosa**

```promql
sum by (service, reason) (rate(gqm_availability_degradation_total[5m]))
```

### 4.2 Alertas básicas

```yaml
groups:
  - name: agent_gkm
    rules:
      - alert: AgentGkmHighLatencyP95
        expr: histogram_quantile(0.95, sum by (le) (rate(gqm_http_duration_seconds_bucket[5m]))) > 15
        for: 10m
        labels: { severity: warning }
        annotations:
          summary: "Latencia p95 > 15s en /api/chat durante 10m"

      - alert: AgentGkmErrorRate
        expr: |
          sum(rate(gqm_http_requests_total{status!="success"}[5m]))
            /
          sum(rate(gqm_http_requests_total[5m]))
          > 0.05
        for: 5m
        labels: { severity: critical }
        annotations:
          summary: "Más del 5% de requests fallando en /api/chat"

      - alert: AgentGkmCircuitBreakerOpen
        expr: increase(gqm_availability_degradation_total{reason="circuit_open"}[5m]) > 0
        for: 2m
        labels: { severity: warning }
        annotations:
          summary: "Circuit breaker abierto en {{ $labels.service }}"

      - alert: AgentGkmHealthDegraded
        expr: up{job="agent_gkm"} == 0
        for: 2m
        labels: { severity: critical }
        annotations:
          summary: "/health en 503 o servicio caído"
```

### 4.3 Debug de una conversación específica

1. Obtener el `trace_id` del request problemático — viene en el log de
   entrada `[HTTP] Mensaje recibido` o en el callback de salida.

   ```bash
   grep 'phone=5491134567890' logs/autobot.log \
     | grep '\[HTTP\] Mensaje recibido' \
     | tail -1
   ```

2. Extraer todo el ciclo de vida del request:

   ```bash
   grep 'trace=a1b2c3d4' logs/autobot.log
   ```

3. Aislar ramas:

   ```bash
   grep 'trace=a1b2c3d4' logs/autobot.log | grep '\[TOOL'      # tools llamadas
   grep 'trace=a1b2c3d4' logs/autobot.log | grep '\[API\]'    # APIs externas
   grep 'trace=a1b2c3d4' logs/autobot.log | grep '\[LLM\]'    # llamadas LLM
   grep 'trace=a1b2c3d4' logs/autobot.log | grep '\[CALLBACK\]' # respuesta enviada
   ```

4. Si la conversación abarca varios requests, repetir filtrando por
   `phone=` y ordenar por timestamp.

### 4.4 Cuándo subir a DEBUG

Subir `LOG_LEVEL=DEBUG` temporalmente para:

- Inspeccionar payloads HTTP completos en `[API]` y `[CALLBACK]`.
- Ver request/response de `httpx` (cuerpo, headers).
- Ver el primer fragmento del mensaje del usuario (`[HTTP] Message: %s...`).

No dejar `DEBUG` en producción de forma permanente: la rotación de 10 MB
se llena en horas con tráfico moderado y `httpx` registra cada handshake.

### 4.5 Reset rápido de métricas

Las métricas son in-memory del proceso. Para resetear, reiniciar el
contenedor:

```bash
docker compose restart agent_gkm
```

(esto **no** borra logs persistidos en el bind mount).

---

## 5. Variables de entorno relacionadas

| Variable      | Default  | Efecto                                                       |
|---------------|----------|--------------------------------------------------------------|
| `LOG_LEVEL`   | `INFO`   | Nivel global de logging.                                     |
| `LOG_FILE`    | (vacío)  | Si está seteado, habilita `RotatingFileHandler`.             |
| `TZ`          | `America/Lima` (fijado en el `Dockerfile`; UTC si se corre sin el container) | Zona horaria del SO; afecta `asctime` y timestamps internos. |
| `CHAT_TIMEOUT`| ver `config` | Timeout total de `/api/chat`; clasifica como `status=timeout`. |

---

## 6. Resumen rápido

- `trace_id` (8 hex) + `phone` se inyectan en cada log vía `ContextVar`.
- Tags `[HTTP]`, `[CALLBACK]`, `[AGENT]`, `[TOOL]`, `[TOOL:*]`, `[API]`,
  `[CB:*]`, `[LLM]`, `[CMD]`, `[VITRIX:*]` segmentan los subsistemas.
- Rotación: 10 MB × 5 archivos en `/app/logs/autobot.log` (bind mount
  `./logs`).
- `/metrics` expone counters, histogramas y gauges con prefijo `gqm_*`.
- `/health` devuelve 503 cuando hay degradación (API key / CB abierto);
  Docker lo usa para `healthcheck`.
- Para debuggear una conversación: `grep 'trace=<id>'` o
  `grep 'phone=<num>'` sobre `logs/autobot.log`.
