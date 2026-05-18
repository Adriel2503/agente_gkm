# API Reference

Referencia HTTP del agente conversacional automotriz **AutoBot v2.5.0** (FastAPI, puerto por defecto `8002`). El servicio expone tres endpoints: un endpoint de chat asíncrono (`POST /api/chat`), un health check (`GET /health`) y un endpoint de métricas Prometheus (`GET /metrics`). El procesamiento del mensaje ocurre en background y el resultado se entrega vía webhook saliente al `CALLBACK_URL` configurado.

---

## POST /api/chat

Recibe un mensaje del usuario, responde de inmediato con un acuse (`200 OK`) y procesa el mensaje en una tarea de background. Al terminar, envía el resultado al `CALLBACK_URL` configurado en el entorno.

### Request body

| Campo | Tipo | Requerido | Descripción |
|---|---|---|---|
| `question` | string | sí | Mensaje del usuario. Longitud entre 1 y 4096 caracteres. Puede contener URLs de imagen (jpg, jpeg, png, gif, webp) que se enviarán a OpenAI Vision (máximo 10). |
| `phone` | string | sí | Identificador de sesión del lead (típicamente número de WhatsApp). Longitud entre 1 y 30 caracteres. Forma parte de la clave de cache del agente y del `thread_id` del checkpointer. |
| `id_empresa` | integer | sí | ID del tenant. Usado para segmentar prompts, cache y métricas. |
| `id_chat` | integer | sí | ID de la conversación en el orquestador. Se devuelve tal cual en el callback. |
| `phone_number_id` | string | sí | ID del número emisor (WhatsApp Business). Se devuelve tal cual en el callback. |
| `id_bitrix` | string \| null | no | ID del lead en Bitrix/Vitrix. Forma parte de la cache key del agente: cambia el agente cacheado cuando un mismo `phone` se asocia a otro lead. |
| `nombre` | string \| null | no | Nombre del lead. Se inyecta en `<lead_identity>` del system prompt. |
| `marca` | string \| null | no | Marca del vehículo de interés. |
| `modelo` | string \| null | no | Modelo del vehículo de interés. |
| `version` | string \| null | no | Versión/variante del vehículo. |
| `sucursal` | string \| null | no | Sucursal asociada al lead. |
| `correo` | string \| null | no | Correo del lead. |
| `config` | object \| null | no | Objeto `GQMConfig` opcional con configuración del bot (variables del prompt Jinja2 y campos extra del `AgentContext`). Campos no declarados se ignoran. |

Notas:

- Campos extra en el body se ignoran (`extra: "ignore"`).
- El campo `config` admite campos definidos en `src/autobot/schemas.py::GQMConfig`. Hoy está vacío por defecto; los campos se agregan ahí cuando se necesitan.

### Ejemplo de request (JSON)

```json
{
  "question": "Hola, quiero info del Kia Sportage 2025",
  "phone": "5491133334444",
  "id_empresa": 12,
  "id_chat": 98765,
  "phone_number_id": "1057xxxxxxxxxxx",
  "id_bitrix": "BX-44012",
  "nombre": "Juan Pérez",
  "marca": "Kia",
  "modelo": "Sportage",
  "version": "EX 2.0",
  "sucursal": "Palermo",
  "correo": "juan.perez@example.com",
  "config": {}
}
```

### Ejemplo con curl

```bash
curl -X POST http://localhost:8002/api/chat \
  -H "Content-Type: application/json" \
  -d '{
    "question": "Hola, quiero info del Kia Sportage 2025",
    "phone": "5491133334444",
    "id_empresa": 12,
    "id_chat": 98765,
    "phone_number_id": "1057xxxxxxxxxxx",
    "nombre": "Juan Pérez",
    "marca": "Kia",
    "modelo": "Sportage"
  }'
```

### Response inmediata

`200 OK` con cuerpo:

```json
{ "status": "ok" }
```

El procesamiento real continúa de forma asíncrona. La respuesta del agente se entrega vía callback (ver siguiente sección). Si `CHAT_TIMEOUT` se vence durante el procesamiento, igual se envía un callback con un mensaje de timeout.

---

## Callback saliente (webhook)

Cuando el agente termina de procesar el mensaje, hace un `POST` al `CALLBACK_URL` configurado en el entorno con el siguiente payload JSON.

### Payload

| Campo | Tipo | Descripción |
|---|---|---|
| `message` | string | Respuesta del agente al usuario. Nunca vacío: si el LLM no devuelve contenido, se reemplaza por un mensaje genérico. |
| `urls` | string[] | URLs de archivos adjuntos (imágenes, PDFs, etc.) propuestos por el agente. Lista vacía si no aplica. Normalizada: strings vacíos y `null` se descartan. |
| `phone` | string | Eco del `phone` del request. |
| `id_empresa` | integer | Eco del `id_empresa` del request. |
| `id_chat` | integer | Eco del `id_chat` del request. |
| `phone_number_id` | string | Eco del `phone_number_id` del request. |
| `event` | string \| null | Evento de negocio detectado durante la conversación. Valores conocidos: `"cita_agendada"`, `"callback_solicitado"`, `"desistido"`. `null` si no hubo evento. |

### Ejemplo de payload

```json
{
  "message": "El Kia Sportage EX 2.0 está disponible. ¿Querés que coordinemos una visita en la sucursal Palermo?",
  "urls": ["https://cdn.example.com/sportage-ex.jpg"],
  "phone": "5491133334444",
  "id_empresa": 12,
  "id_chat": 98765,
  "phone_number_id": "1057xxxxxxxxxxx",
  "event": null
}
```

### Comportamiento del callback

- Si `CALLBACK_URL` no está configurada, la respuesta se descarta y se loguea un error `[CALLBACK] CALLBACK_URL no configurada`.
- Cualquier respuesta no-2xx del callback se loguea como error pero no se reintenta (las tools de escritura del agente son las que aplican idempotencia; el callback es fire-and-forget).
- Se loguea siempre el JSON saliente bajo `[CALLBACK] JSON salida` y la duración del POST.

---

## GET /health

Health check sintético. Devuelve `200 OK` cuando el servicio está en estado `ok`, o `503 Service Unavailable` cuando hay al menos un issue.

### Response

```json
{
  "status": "ok",
  "agent": "autobot",
  "version": "2.5.0",
  "issues": []
}
```

| Campo | Tipo | Descripción |
|---|---|---|
| `status` | string | `"ok"` si `issues` está vacío. `"degraded"` si hay al menos un issue. |
| `agent` | string | Nombre del servicio: `"autobot"`. |
| `version` | string | Versión semántica del agente (ej. `"2.5.0"`). |
| `issues` | string[] | Lista de problemas detectados. Vacía cuando todo está sano. |

### Issues posibles

| Issue | Causa |
|---|---|
| `openai_api_key_missing` | La variable `OPENAI_API_KEY` está vacía o no configurada. |
| `kia_rag_api_degraded` | El circuit breaker de la API RAG de KIA está abierto (umbral de fallos consecutivos superado, en período de reset). |

Nuevos circuit breakers registrados vía `_register()` en `src/autobot/config/circuit_breakers.py` aparecen automáticamente en `issues` con el sufijo `_degraded`.

### Códigos de estado

| Status HTTP | Significado |
|---|---|
| `200` | Sin issues. Servicio sano. |
| `503` | Al menos un issue presente. Servicio degradado o no operativo. |

Los healthchecks `200` se filtran del log de acceso de uvicorn. Los `503` sí se loguean para facilitar diagnóstico.

---

## GET /metrics

Expone métricas en formato Prometheus (text exposition format) montadas vía `prometheus_client.make_asgi_app()`. No requiere autenticación.

### Ejemplo

```bash
curl http://localhost:8002/metrics
```

Las métricas exportadas (chat requests, duraciones de LLM, tokens, cache hits/misses, circuit breakers, etc.) están documentadas en detalle en [`docs/observability.md`](./observability.md). Este endpoint es el que debe scrapear Prometheus.

---

## Comandos especiales

El agente intercepta ciertos mensajes antes de invocar al LLM. Se comparan en lowercase tras `strip()` sobre `question`.

| Comando | Acción | Respuesta |
|---|---|---|
| `/clear` | Borra el historial del thread del checkpointer correspondiente a `(id_empresa, phone, id_bitrix)`. | `"Historial limpiado. ¿En qué puedo ayudarte?"` |
| `/restart` | Comando reservado, sin acción operativa. Se loguea como warning. | `"Este comando está reservado para administradores."` |

Ambos comandos siguen el flujo normal del callback: la respuesta sintética se envía al `CALLBACK_URL` igual que cualquier otro mensaje.

---

## Errores y manejo de fallos

El procesamiento en background nunca devuelve un error HTTP al cliente (el ACK ya fue enviado). En su lugar, los errores se traducen a un `message` legible que se entrega vía callback y se incrementa la métrica `record_chat_error(<key>)`.

### Errores de OpenAI

| Excepción | Métrica | Log tag | Mensaje al usuario |
|---|---|---|---|
| `AuthenticationError` | `openai_auth_error` | `OpenAI-401` | "No puedo procesar tu mensaje, la clave de acceso al servicio no es válida." |
| `RateLimitError` | `openai_rate_limit` | `OpenAI-429` | "Estoy recibiendo demasiadas solicitudes en este momento, por favor intenta en unos segundos." |
| `InternalServerError` | `openai_server_error` | `OpenAI-5xx` | "El servicio de inteligencia artificial está presentando problemas, por favor intenta nuevamente." |
| `APIConnectionError` | `openai_connection_error` | `OpenAI-conn` | "No pude conectarme al servicio de inteligencia artificial, por favor intenta nuevamente." |
| `BadRequestError` | `openai_bad_request` | `OpenAI-400` | "Tu mensaje no pudo ser procesado por el servicio, ¿puedes reformularlo?" |

### Errores del flujo HTTP

| Situación | Status interno | Log | Mensaje al usuario |
|---|---|---|---|
| Timeout (`CHAT_TIMEOUT` superado) | `timeout` | `[HTTP] Timeout en process_message` | "La solicitud tardó más de Ns. Por favor, intenta de nuevo." |
| `ValueError` (config inválida) | `error` | `[HTTP] Error de configuración: ...` | "Error de configuración: ..." |
| Error creando agente | `error` | `[AGENT] Error creando agent` | "Disculpa, tuve un problema de configuración. ¿Podrías intentar nuevamente?" |
| Excepción inesperada del agente | `error` | `[AGENT] Error inesperado` | "Disculpa, tuve un problema al procesar tu mensaje. ¿Podrías intentar nuevamente?" |
| Respuesta del LLM sin formato estructurado | (warning) | `[AGENT] Respuesta fuera de formato estructurado` | Contenido raw o "El asistente respondió en un formato inesperado, por favor intenta nuevamente." |
| Respuesta estructurada vacía | (warning) | `[AGENT] structured.reply vacío y sin urls` | "El asistente envió una respuesta vacía, por favor intenta nuevamente." |

Todas las excepciones inesperadas se loguean con `exc_info=True` para preservar el traceback. Las métricas de duración (`HTTP_DURATION`) y conteo (`HTTP_REQUESTS{status=...}`) se actualizan siempre en el bloque `finally`, salvo cuando el task es cancelado externamente.

---

## Notas de operación

- **Trace ID**: cada request a `/api/chat` genera un `trace_id` de 8 caracteres hexadecimales que se propaga en los logs vía `contextvars`.
- **Phone context**: el `phone` del request se inyecta en todos los logs de la tarea de background mediante el `phone_ctx` global.
- **Concurrencia**: requests concurrentes para el mismo `(phone, id_bitrix)` se serializan con un lock por sesión para evitar condiciones de carrera sobre el `thread_id` del checkpointer.
- **Cache del agente**: clave `(id_empresa, phone, id_bitrix)`, TTL gobernado por `AGENT_CACHE_TTL_MINUTES` (default 60 min).
