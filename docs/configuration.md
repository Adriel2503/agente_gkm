# Configuración — agent_gkm

Referencia completa de variables de entorno consumidas por el agente.
Todas las variables se cargan en `src/autobot/config/config.py` mediante
`python-dotenv` (busca el `.env` recursivamente hacia arriba desde el módulo).

Los helpers `_get_str`, `_get_int`, `_get_float` y `_get_log_level` validan
tipos y rangos. Si una variable es inválida o queda fuera de rango, se usa
el valor por defecto silenciosamente (no aborta el arranque).

> Nota: Los ejemplos con `<placeholder>` no contienen credenciales reales.
> Reemplazar antes de desplegar.

---

## OpenAI

Configura el cliente LLM del agente.

| Nombre               | Tipo  | Default       | Requerido | Descripción                                                                 |
| -------------------- | ----- | ------------- | --------- | --------------------------------------------------------------------------- |
| `OPENAI_API_KEY`     | str   | `""`          | Si        | API key del proyecto OpenAI. Sin valor el agente no podra invocar el modelo.|
| `OPENAI_MODEL`       | str   | `gpt-4o-mini` | No        | Identificador del modelo (`gpt-4o-mini`, `gpt-4o`, etc.).                   |
| `OPENAI_TEMPERATURE` | float | `0.5`         | No        | Temperatura de muestreo. Rango `[0.0, 2.0]`.                                |
| `OPENAI_TIMEOUT`     | int   | `60`          | No        | Timeout de la llamada al modelo en segundos. Rango `[1, 300]`.              |
| `MAX_TOKENS`         | int   | `2048`        | No        | Tope de tokens de salida del modelo. Rango `[1, 128000]`.                   |

---

## Servidor

Servidor HTTP del agente (FastAPI/uvicorn).

| Nombre         | Tipo | Default   | Requerido | Descripción                                                                |
| -------------- | ---- | --------- | --------- | -------------------------------------------------------------------------- |
| `SERVER_HOST`  | str  | `0.0.0.0` | No        | Interfaz en la que escucha el servidor. `0.0.0.0` para exponer en Docker.  |
| `SERVER_PORT`  | int  | `8002`    | No        | Puerto de escucha. Rango `[1, 65535]`. Coincide con `EXPOSE` del Dockerfile.|
| `CHAT_TIMEOUT` | int  | `120`     | No        | Timeout total (segundos) para un turno de chat. Rango `[30, 300]`.         |

---

## Logging

| Nombre      | Tipo | Default | Requerido | Descripción                                                                              |
| ----------- | ---- | ------- | --------- | ---------------------------------------------------------------------------------------- |
| `LOG_LEVEL` | str  | `INFO`  | No        | Nivel de log: `DEBUG`, `INFO`, `WARNING`, `ERROR`, `CRITICAL`. Si invalido cae a default.|
| `LOG_FILE`  | str  | `""`    | No        | Ruta de archivo de log. Vacio = solo consola (stdout/stderr).                            |

---

## HTTP cliente

Parámetros del cliente HTTP compartido (`httpx`) usado por todos los servicios externos.

| Nombre                 | Tipo | Default | Requerido | Descripción                                                              |
| ---------------------- | ---- | ------- | --------- | ------------------------------------------------------------------------ |
| `API_TIMEOUT`          | int  | `10`    | No        | Timeout (segundos) por request HTTP a APIs externas. Rango `[1, 120]`.   |
| `HTTP_RETRY_ATTEMPTS`  | int  | `3`     | No        | Intentos de retry para operaciones de lectura. Rango `[1, 10]`.          |
| `HTTP_RETRY_WAIT_MIN`  | int  | `1`     | No        | Espera minima entre reintentos (segundos). Rango `[0, 30]`.              |
| `HTTP_RETRY_WAIT_MAX`  | int  | `4`     | No        | Espera maxima entre reintentos (segundos). Rango `[1, 60]`.              |
| `HTTP_MAX_CONNECTIONS` | int  | `50`    | No        | Conexiones maximas del pool httpx. Rango `[10, 500]`.                    |
| `HTTP_MAX_KEEPALIVE`   | int  | `20`    | No        | Conexiones keepalive maximas del pool. Rango `[5, 200]`.                 |

> Las operaciones de escritura (p. ej. `agendar_cita` de Vitrix) NO usan retry
> por idempotencia. Ver `write_tools_rules`.

---

## Circuit breaker

Cortocircuito por API externa. Instanciado en `src/autobot/config/circuit_breakers.py`.
El registry expone `get_health_issues()` para `/health`.

| Nombre         | Tipo | Default | Requerido | Descripción                                                                             |
| -------------- | ---- | ------- | --------- | --------------------------------------------------------------------------------------- |
| `CB_THRESHOLD` | int  | `3`     | No        | Fallos consecutivos antes de abrir el circuito. Rango `[1, 20]`.                        |
| `CB_RESET_TTL` | int  | `300`   | No        | Segundos que el circuito permanece OPEN antes de volver a CLOSED (no hay half-open). Rango `[60, 3600]`. |
| `CB_MAX_KEYS`  | int  | `500`   | No        | Maximo de keys particionadas por CB (LRU). Rango `[50, 10000]`. Evita memory leak.      |

Actualmente registrado: `kia_rag_cb` (RAG KIA, key `"global"`). Para agregar
nuevos CBs ver el comentario en `circuit_breakers.py`.

---

## Cache

Cache en memoria (TTL + LRU) para agente compilado y resultados de búsqueda.
La key del cache de agente es `(id_empresa, phone, id_bitrix)` — no modificar.

| Nombre                     | Tipo | Default | Requerido | Descripción                                                                  |
| -------------------------- | ---- | ------- | --------- | ---------------------------------------------------------------------------- |
| `AGENT_CACHE_TTL_MINUTES`  | int  | `60`    | No        | TTL del agente compilado por sesion. Rango `[5, 1440]`.                      |
| `AGENT_CACHE_MAXSIZE`      | int  | `500`   | No        | Maximo de agentes cacheados. Rango `[10, 5000]`.                             |
| `SEARCH_CACHE_TTL_MINUTES` | int  | `15`    | No        | **Reservado**: cargado en `config.py` pero sin consumidor en código actual. Rango `[1, 60]`. |
| `SEARCH_CACHE_MAXSIZE`     | int  | `2000`  | No        | **Reservado**: cargado en `config.py` pero sin consumidor en código actual. Rango `[10, 10000]`. |
| `MAX_MESSAGES_HISTORY`     | int  | `20`    | No        | Mensajes historicos retenidos por conversacion. Rango `[4, 200]`.            |

---

## Redis

Persistencia de checkpoints LangGraph (opcional). Si `REDIS_URL` esta vacio,
el agente corre sin checkpoint persistente.

| Nombre                       | Tipo | Default | Requerido | Descripción                                                                |
| ---------------------------- | ---- | ------- | --------- | -------------------------------------------------------------------------- |
| `REDIS_URL`                  | str  | `""`    | No        | URL Redis (`redis://host:6379/0`). Vacio = sin persistencia.               |
| `REDIS_CHECKPOINT_TTL_HOURS` | int  | `24`    | No        | TTL del checkpoint en horas. `0` = sin TTL. Rango `[0, 8760]` (1 ano).     |

---

## APIs externas

URLs y credenciales de servicios externos. Validar conectividad de red en
deploy (firewall, DNS, certificados).

| Nombre                    | Tipo | Default                                                       | Requerido | Descripción                                                          |
| ------------------------- | ---- | ------------------------------------------------------------- | --------- | -------------------------------------------------------------------- |
| `API_KIA_RAG_URL`         | str  | `http://localhost:8000/buscar`                                | No        | Endpoint del servicio RAG para busqueda de modelos KIA.              |
| `CALLBACK_URL`            | str  | `""`                                                          | No        | URL donde se envia la respuesta del agente en modo asincrono.        |
| `APIKEY_VITRIX`           | str  | `""`                                                          | Si (CRM)  | API key de Vitrix (Bitrix24) para tools de citas.                    |
| `VITRIX_API_URL`          | str  | `https://b24.guruxdev.com/qm/b24handlers/v2/index.php`        | No        | Endpoint de handlers Vitrix.                                         |
| `DATABASE_URL`            | str  | `""`                                                          | No        | URL de Postgres (reservada; sin consumidor activo en código).        |

---

## Zona horaria

Distinción entre `TZ` y `TIMEZONE` — son dos variables independientes con consumidores distintos.

| Nombre     | Tipo | Default        | Requerido | Descripción                                                                      |
| ---------- | ---- | -------------- | --------- | -------------------------------------------------------------------------------- |
| `TZ`       | str  | `America/Lima` | No        | Variable estandar de libc. Afecta `date`, `strftime`, timestamps de logs.        |
| `TIMEZONE` | str  | `America/Lima` | No        | Leida por Python (`config.py`) para el DEADLINE de Vitrix y prompts del agente.  |

**Por qué ambas:**

- **`TZ`** la lee `libc` (glibc/musl). Es la variable estándar POSIX que
  determina la zona del sistema. El `Dockerfile` la fija a `America/Lima`
  via `ENV TZ=America/Lima`. Sin `TZ`, los timestamps de logs y cualquier
  llamada a `time(2)`/`localtime(3)` se reportan en UTC.
- **`TIMEZONE`** la lee el código Python en `config.py`. Se inyecta en
  prompts del LLM (fecha/hora actual visible al agente) y en el cálculo
  del `DEADLINE` para la tool `agendar_cita` de Vitrix. Sin `TIMEZONE`,
  el default codificado es `"America/Lima"`.

**Recomendación:** mantenerlas sincronizadas. Si se cambia la zona del
sistema, actualizar las dos. Una desincronización produce logs en una
zona y razonamiento del agente sobre fechas en otra — difícil de depurar.

---

## Ejemplo `.env` mínimo

Archivo de ejemplo con placeholders. No commitear credenciales reales:
el `.env` esta en `.gitignore`.

```dotenv
# === OpenAI ===
OPENAI_API_KEY=<openai-api-key>
OPENAI_MODEL=gpt-4o-mini
OPENAI_TIMEOUT=60
OPENAI_TEMPERATURE=0.5
MAX_TOKENS=2048

# === Servidor ===
SERVER_HOST=0.0.0.0
SERVER_PORT=8002
CHAT_TIMEOUT=120

# === Logging ===
LOG_LEVEL=INFO
# LOG_FILE=/app/logs/autobot.log

# === HTTP ===
API_TIMEOUT=10
HTTP_RETRY_ATTEMPTS=3
HTTP_RETRY_WAIT_MIN=1
HTTP_RETRY_WAIT_MAX=4
HTTP_MAX_CONNECTIONS=50
HTTP_MAX_KEEPALIVE=20

# === Circuit breaker ===
CB_THRESHOLD=3
CB_RESET_TTL=300
CB_MAX_KEYS=500

# === Cache ===
AGENT_CACHE_TTL_MINUTES=60
AGENT_CACHE_MAXSIZE=500
SEARCH_CACHE_TTL_MINUTES=15
SEARCH_CACHE_MAXSIZE=2000
MAX_MESSAGES_HISTORY=20

# === Redis (opcional) ===
# REDIS_URL=redis://redis:6379/0
REDIS_CHECKPOINT_TTL_HOURS=24

# === APIs externas ===
API_KIA_RAG_URL=http://kia-rag:8000/buscar
CALLBACK_URL=<callback-url>
APIKEY_VITRIX=<vitrix-api-key>
VITRIX_API_URL=https://b24.guruxdev.com/qm/b24handlers/v2/index.php

# === Zona horaria ===
TIMEZONE=America/Lima
# TZ se define en el Dockerfile; sobrescribir solo si es necesario.
```

---

## Verificación dentro del contenedor

Confirmar que `TZ` y `TIMEZONE` quedan sincronizadas en runtime:

```bash
docker compose exec autobot env | grep -E "^(TZ|TIMEZONE)="
```

Salida esperada:

```
TZ=America/Lima
TIMEZONE=America/Lima
```

Verificar la hora del sistema dentro del contenedor:

```bash
docker compose exec autobot date
```

Inspeccionar todas las variables relevantes:

```bash
docker compose exec autobot env | grep -E "^(OPENAI_|SERVER_|HTTP_|CB_|AGENT_CACHE|SEARCH_CACHE|REDIS_|API_|VITRIX|TZ|TIMEZONE|LOG_)"
```

Healthcheck del servicio (incluye degradación de circuit breakers):

```bash
docker compose exec autobot .venv/bin/python -c \
  "import urllib.request,json; print(json.loads(urllib.request.urlopen('http://localhost:8002/health').read()))"
```

---

## Referencias

- `src/autobot/config/config.py` — definicion y validacion de todas las vars.
- `src/autobot/config/circuit_breakers.py` — instancias de CB y registry de `/health`.
- `Dockerfile` — fija `TZ=America/Lima` a nivel de imagen.
- `.env.example` — plantilla versionada para nuevos entornos.
