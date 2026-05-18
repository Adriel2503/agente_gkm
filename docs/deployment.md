# Despliegue de agent_gkm

Guía operativa para levantar el servicio AutoBot (`autobot` v2.5.0) en entorno
local de desarrollo y en producción contenerizada. Cubre build, run,
verificación, troubleshooting y operaciones de rotación de credenciales sin
downtime.

El servicio expone tres endpoints HTTP sobre el puerto `8002`:

- `POST /api/chat` — endpoint principal de chat (compatible con el gateway Go).
- `GET  /health`   — health check (200 OK / 503 degraded).
- `GET  /metrics`  — métricas Prometheus.

---

## 1. Requisitos previos

| Componente      | Versión mínima | Uso                                     |
|-----------------|----------------|-----------------------------------------|
| Python          | 3.12           | Runtime (`requires-python = ">=3.12"`)  |
| uv              | 0.9            | Gestor de dependencias y venv           |
| Docker Engine   | 24.x           | Build y run del contenedor              |
| Docker Compose  | v2 (plugin)    | Orquestación (`compose.yaml`)           |
| curl            | cualquiera     | Verificación post-deploy                |

Dependencias externas en runtime:

- **OpenAI API**: requiere `OPENAI_API_KEY` válida.
- **Redis** (opcional): si `REDIS_URL` está vacío, el agente degrada
  automáticamente a `InMemorySaver` (checkpointer en memoria, no persistente).
- **API KIA RAG**: endpoint configurado en `API_KIA_RAG_URL`.
- **Callback gateway**: `CALLBACK_URL` (destino de las respuestas del agente).

---

## 2. Setup local sin Docker

Útil para desarrollo, debugging y ejecución de tests.

```bash
# 1. Clonar y entrar al repo
git clone <repo-url> agent_gkm
cd agent_gkm

# 2. Copiar template de variables
cp .env.example .env

# 3. Editar .env y completar como mínimo:
#    OPENAI_API_KEY=sk-...
#    CALLBACK_URL=https://gateway.tu-dominio/callback
#    REDIS_URL=redis://localhost:6379/0   (opcional)
$EDITOR .env

# 4. Sincronizar dependencias (crea .venv automáticamente)
uv sync

# 5. Levantar el servicio
python run.py
```

El proceso queda escuchando en `0.0.0.0:8002` (configurable con `SERVER_HOST` /
`SERVER_PORT`). El startup imprime un banner con la configuración efectiva:
modelo, timeouts, checkpointer activo, tools cargadas y timezone.

### Verificación

```bash
# Health check
curl -s http://localhost:8002/health | jq

# Respuesta esperada (200):
# { "status": "ok", "agent": "autobot", "version": "2.5.0", "issues": [] }
```

Si `OPENAI_API_KEY` falta, el endpoint devuelve `503` con
`issues: ["openai_api_key_missing"]`.

---

## 3. Despliegue con Docker

### 3.1 Build de la imagen

```bash
docker compose build
# o equivalente:
docker build -t agent_gkm:2.5.0 .
```

El `Dockerfile` está optimizado en **dos capas de instalación de dependencias**
para maximizar el cacheo:

1. **Capa 1 — solo dependencias** (línea 27-29):
   ```dockerfile
   COPY pyproject.toml uv.lock ./
   RUN --mount=type=cache,target=/root/.cache/uv \
       uv sync --frozen --no-install-project --no-dev
   ```
   Solo se invalida cuando cambian `pyproject.toml` o `uv.lock`. Esta capa pesa
   la mayor parte del build (langchain, langgraph, fastapi, openai, etc.).

2. **Capa 2 — código del proyecto** (línea 32-34):
   ```dockerfile
   COPY src ./src
   RUN --mount=type=cache,target=/root/.cache/uv \
       uv sync --frozen --no-dev
   ```
   Se invalida cuando cambia `src/`. Reinstala solo el paquete `autobot` (no las
   deps), por lo que rebuilds típicos son segundos en lugar de minutos.

Otros aspectos del Dockerfile:

- `uv sync --frozen --no-dev`: respeta exactamente `uv.lock` y excluye el grupo
  `dev` (pytest). Falla si el lock está desactualizado.
- **Usuario `appuser`** (UID 10001) no-root: el proceso corre sin privilegios.
  El directorio `/app/logs` se crea con `chown appuser:appuser` para permitir
  escritura.
- **`ENV TZ=America/Lima`**: aplica al contenedor entero. Necesario porque la
  lógica de horarios y agendamiento depende de la zona local.
- **Healthcheck** (línea 43-44): ver sección 6.
- **CMD**: `.venv/bin/python -m autobot.main` (entrypoint del módulo, no
  `run.py`).

### 3.2 Run

```bash
# Levantar en background
docker compose up -d

# Ver logs en streaming
docker compose logs -f autobot

# Estado del contenedor y healthcheck
docker compose ps

# Shell dentro del contenedor (debug)
docker compose exec autobot sh

# Detener
docker compose down
```

### 3.3 Bind mount de logs

`compose.yaml` declara:

```yaml
volumes:
  - ./logs:/app/logs
```

Los logs persisten en `./logs` del host aunque el contenedor se recree o se
elimine. Si `LOG_FILE` apunta a una ruta bajo `/app/logs/` (por ejemplo
`/app/logs/autobot.log`), se aplica `RotatingFileHandler` (10MB × 5 backups).
Si `LOG_FILE` está vacío, los logs van solo a stdout y los captura el driver
`json-file` de Docker (configurado a `max-size: 10m, max-file: 3`).

### 3.4 Recreación tras cambios en `.env`

Docker Compose lee `.env` al arrancar el contenedor y no monitoriza cambios.
Para aplicar nuevos valores hay que recrear:

```bash
# Edita .env primero
$EDITOR .env

# Forzar recreación (no rebuilda la imagen)
docker compose up -d --force-recreate
```

Si además cambió `pyproject.toml` o `uv.lock`:

```bash
docker compose up -d --build --force-recreate
```

---

## 4. Verificación post-deploy

```bash
# 1. Health
curl -s http://localhost:8002/health | jq
# Esperado: {"status":"ok","agent":"autobot","version":"2.5.0","issues":[]}

# 2. Métricas Prometheus
curl -s http://localhost:8002/metrics | head -40
# Esperado: líneas tipo gqm_chat_requests_total, gqm_http_duration_seconds, etc.

# 3. Smoke test del endpoint /api/chat
curl -s -X POST http://localhost:8002/api/chat \
  -H 'Content-Type: application/json' \
  -d '{
        "question": "Hola, quiero información de un Kia Sportage",
        "phone": "+51999111222",
        "id_empresa": 1,
        "id_chat": 100,
        "phone_number_id": "1234567890"
      }'
# Esperado: 200 con { "status": "ok" }
# La respuesta real del agente llega de forma asíncrona al CALLBACK_URL.
```

Si `/health` devuelve `503`, revisar `issues`:

- `openai_api_key_missing` → completar `OPENAI_API_KEY` en `.env`.
- `*_degraded` → algún circuit breaker está abierto (ver sección 6).

---

## 5. Troubleshooting

### Puerto 8002 ya en uso

```bash
# Linux/Mac
lsof -i :8002
# Windows
netstat -ano | findstr :8002
```

Soluciones:

1. Detener el proceso que lo ocupa.
2. Cambiar el mapping en `compose.yaml` (ej. `"8003:8002"`).

### `OPENAI_API_KEY` faltante

`/health` devuelve 503 con `issues: ["openai_api_key_missing"]` y cualquier
request a `/api/chat` falla al invocar el LLM. Solución: completar la variable
en `.env` y recrear (`docker compose up -d --force-recreate`).

### `REDIS_URL` inaccesible

Si `REDIS_URL` está seteada pero el servidor Redis no responde, el agente
**degrada automáticamente a `InMemorySaver`** al arrancar. Consecuencias:

- El estado conversacional no persiste entre reinicios.
- Múltiples instancias no comparten contexto.

Recomendación: dejar `REDIS_URL` vacío explícitamente si no se va a usar Redis,
para evitar errores de conexión en logs.

### Logs no aparecen en `./logs`

Verificar:

1. `LOG_FILE` apunta a una ruta dentro de `/app/logs/` (no a `/app/` ni a
   rutas fuera del mount).
2. El directorio `./logs` del host existe y tiene permisos. En primera
   ejecución, Docker lo crea como `root`; si hay problemas de permisos:
   ```bash
   mkdir -p ./logs && chmod 777 ./logs
   ```

### Build falla con `uv.lock out of sync`

```bash
# Regenerar lock
uv lock
# Commit y rebuild
docker compose build --no-cache
```

---

## 6. Healthcheck Docker y circuit breakers

El Dockerfile declara:

```dockerfile
HEALTHCHECK --interval=300s --timeout=5s --start-period=10s --retries=2 \
    CMD .venv/bin/python -c "import urllib.request; \
        urllib.request.urlopen('http://localhost:8002/health')" || exit 1
```

Parámetros:

- **`--interval=300s`** (5 min): chequeo poco frecuente para no saturar logs ni
  generar carga innecesaria. AutoBot ya filtra los 200 OK de `/health` en
  `uvicorn.access`, pero los 503 sí quedan registrados.
- **`--timeout=5s`**: si `/health` no responde en 5s, falla.
- **`--start-period=10s`**: gracia inicial mientras arranca FastAPI.
- **`--retries=2`**: dos fallos consecutivos marcan el contenedor `unhealthy`.

### Relación con circuit breakers

`/health` consulta `get_health_issues()` que agrega el estado de todos los
circuit breakers registrados (ver `src/autobot/config/circuit_breakers.py`).
Cuando un CB de una dependencia externa (ej. API KIA RAG) supera
`CB_THRESHOLD` fallos consecutivos, se abre y `/health` devuelve:

```json
{
  "status": "degraded",
  "issues": ["kia_rag_api_degraded"]
}
```

Con HTTP **503**. Esto dispara `unhealthy` en Docker tras dos chequeos
fallidos (10 min con el intervalo actual). El CB se cierra solo tras
`CB_RESET_TTL` segundos (default 300s) sin nuevos fallos. Ajustar
`--interval` si se necesita detección más rápida en orquestadores externos
(Kubernetes, ECS, etc.).

`restart: unless-stopped` en `compose.yaml` no reinicia por `unhealthy` —
solo reinicia si el proceso muere. Para reinicio automático ante `unhealthy`
se requiere un orquestador externo o un sidecar como `autoheal`.

---

## 7. Rotación de credenciales sin downtime

`APIKEY_VITRIX`, `OPENAI_API_KEY` y similares se leen desde `.env` en startup.
La rotación implica un reinicio del contenedor. El downtime efectivo es
≈2-5 segundos (tiempo de stop + start de uvicorn).

Procedimiento estándar (single-instance):

```bash
# 1. Editar la variable
$EDITOR .env

# 2. Recrear contenedor (Docker conserva imagen, solo restartea proceso)
docker compose up -d --force-recreate

# 3. Verificar
curl -s http://localhost:8002/health | jq .status
# Esperado: "ok"
```

Para verdadero **zero-downtime** con múltiples instancias detrás de un load
balancer:

```bash
# Asumiendo dos servicios autobot-a y autobot-b en compose.yaml,
# con healthcheck activo en el LB:

# 1. Rotar credencial en .env
$EDITOR .env

# 2. Recrear instancia A
docker compose up -d --force-recreate autobot-a
# Esperar que el LB la marque healthy nuevamente
until curl -sf http://localhost:8002/health > /dev/null; do sleep 2; done

# 3. Recrear instancia B
docker compose up -d --force-recreate autobot-b
```

Durante la transición, el LB rutea todo el tráfico a la instancia que aún
tiene la credencial vieja válida (si la rotación es aditiva en el proveedor)
o a la nueva (si es revocación inmediata, coordinar la ventana).

Notas:

- Las requests `in-flight` durante el `force-recreate` se interrumpen. Si la
  llamada al LLM ya está en curso, el callback no se entrega. El gateway Go
  debe tener su propio retry sobre `/api/chat`.
- Tras rotar `OPENAI_API_KEY`, monitorear `/metrics` por errores de
  autenticación (`gqm_chat_errors_total{error_type="openai_auth_error"}`) durante los
  primeros minutos.
- Tras rotar `APIKEY_VITRIX`, verificar que el CB correspondiente no quede
  abierto: si la nueva key estaba mal copiada y generó 3 fallos, hay que
  esperar `CB_RESET_TTL` o reiniciar de nuevo.

---

## 8. Referencia rápida de archivos

| Archivo                     | Propósito                                       |
|-----------------------------|-------------------------------------------------|
| `Dockerfile`                | Build de imagen, usuario no-root, healthcheck   |
| `compose.yaml`              | Orquestación, bind mount de logs, log rotation  |
| `pyproject.toml`            | Dependencias declaradas (lock fuente de verdad) |
| `uv.lock`                   | Versiones congeladas (commiteado al repo)       |
| `run.py`                    | Entrypoint local (`python run.py`)              |
| `src/autobot/main.py`       | App FastAPI, endpoints, startup banner          |
| `.env.example`              | Template de variables (copiar a `.env`)         |
| `logs/`                     | Bind mount persistente del host                 |
