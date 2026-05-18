# Integración con Vitrix (Bitrix24)

Documentación de referencia de la integración de `agent_gkm` con el CRM Vitrix
construido sobre Bitrix24. Toda la lógica vive en
`src/autobot/services/citas_vitrix.py`. Este servicio agrupa las llamadas de
**escritura** que el agente conversacional dispara al cerrar una interacción
(cita agendada, callback solicitado, lead desistido).

---

## 1. Endpoint y autenticación

| Elemento | Valor |
|----------|-------|
| URL base | `app_config.VITRIX_API_URL` (variable de entorno `VITRIX_API_URL`) |
| Método HTTP | `POST` |
| Content-Type | `application/json` |
| Auth | Campo `api_key` dentro del body (no header) — valor `app_config.APIKEY_VITRIX` (env `APIKEY_VITRIX`) |
| Cliente HTTP | `httpx.AsyncClient` compartido vía `infra.get_client()` |
| Métricas | Wrapper `track_api_call("vitrix_<operacion>")` |

El mismo endpoint atiende todas las acciones; lo que cambia es el campo
`action` del body.

### Acciones soportadas

| `action` | Propósito |
|----------|-----------|
| `edit`   | Actualiza campos del lead (incluye STATUS_ID y UF_CRM_*) |
| `task`   | Crea una task asociada al lead |

No existen otras acciones consumidas por el servicio.

---

## 2. Mapa de funciones públicas

| Función | Acción Vitrix | Flujo |
|---------|---------------|-------|
| `actualizar_lead_cita` | `edit` | Cierre con cita agendada |
| `crear_task_confirmar_cita` | `task` | Task "Llamar - confirmar cita IA" |
| `actualizar_lead_y_crear_task` | `edit` + `task` (fire-and-forget) | Cierre cita completo |
| `actualizar_lead_llamada` | `edit` | Cierre callback (sin fecha de cita) |
| `crear_task_llamada` | `task` | Task "Llamar - Cliente interesado IA" |
| `actualizar_lead_llamada_y_crear_task` | `edit` + `task` (fire-and-forget) | Cierre callback completo |
| `marcar_lead_desistido` | `edit` | Lead desistido / sin contacto IA |

Builders internos de payload:

- `_build_payload` — payload del flujo **cita**.
- `_build_payload_llamada` — payload del flujo **callback** (sin `appointment_datetime`).
- Inline dentro de `marcar_lead_desistido` — payload **desistido** (minimal, sin builder externo).

Helpers de normalización:

- `_normalize_yes_no(value)` — acepta SÍ/NO en múltiples variantes y devuelve `"SÍ" | "NO" | "N.A"`.
- `_clean_str(value)` — trim y vacío → `"N.A"`.

Valor centinela: `_DEFAULT_VALUE = "N.A"`.

---

## 3. Diccionario de campos UF_CRM_*

Todos los flujos `edit` envían un subconjunto de los siguientes campos
personalizados de Bitrix24:

| Campo Bitrix | Significado funcional | Tipo / valores |
|--------------|-----------------------|----------------|
| `UF_CRM_1774974891` | Status bot — marca de cierre exitoso de sesión IA | Literal `"Sesión Bot Completada Exitosamente"` |
| `UF_CRM_1728502747862` | Etiqueta de cierre — tipo de outcome | `"Llamar - confirmar cita IA"` (cita) / `"Llamar - Cliente interesado IA"` (callback) |
| `UF_CRM_1653688826` | `financing_required` — requiere financiamiento | `SÍ` / `NO` / `N.A` |
| `UF_CRM_1774975288` | `corporate_agreement` — convenio corporativo del cliente | String libre / `N.A` |
| `UF_CRM_1653599755` | `trade_in_vehicle` — entrega de vehículo en parte de pago | `SÍ` / `NO` / `N.A` |
| `UF_CRM_1653605302623` | `used_vehicle_brand` — marca del vehículo usado | String / `N.A` |
| `UF_CRM_1653605318164` | `used_vehicle_model` — modelo del vehículo usado | String / `N.A` |
| `UF_CRM_1534532283` | `used_vehicle_year` — año del vehículo usado | String / `N.A` |
| `UF_CRM_1534533297` | `used_vehicle_mileage` — kilometraje del vehículo usado | String / `N.A` |
| `UF_CRM_1721064189466` | `purchase_expectation` — expectativa de compra | String / `N.A` |
| `UF_CRM_1559082118` | Razón de desistido (solo flujo desistido) | Literal `"No respondió a la IA"` |

Campos estándar de Bitrix que también se envían:

| Campo | Significado | Valor |
|-------|-------------|-------|
| `SOURCE_DESCRIPTION` | Texto compuesto con metadata de la sesión | `"Presupuesto: ... \| Cita: ... \| Resumen: ..."` (cita) o `"Presupuesto: ... \| Resumen: ..."` (callback) |
| `STATUS_ID` | Etapa del lead en el pipeline | `"Asignado a Vendedor (Sin Contacto)"` (cierre OK) / `"Desistido"` (desistido) |
| `ID` | Lead ID en Bitrix (entero) | Casteado desde `id_bitrix` |

---

## 4. Flujo: cierre con cita

Función pública: `actualizar_lead_y_crear_task(id_bitrix, **edit_kwargs)`.

### 4.1 Payload `action=edit`

Construido por `_build_payload`. Incluye **todos** los campos del cuadro
anterior salvo `UF_CRM_1559082118` (razón desistido).

Ejemplo:

```json
{
  "action": "edit",
  "api_key": "<APIKEY_VITRIX>",
  "ID": 12345,
  "UF_CRM_1774974891": "Sesión Bot Completada Exitosamente",
  "UF_CRM_1728502747862": "Llamar - confirmar cita IA",
  "STATUS_ID": "Asignado a Vendedor (Sin Contacto)",
  "UF_CRM_1653688826": "SÍ",
  "UF_CRM_1774975288": "Convenio Empresa X",
  "UF_CRM_1653599755": "NO",
  "UF_CRM_1653605302623": "N.A",
  "UF_CRM_1653605318164": "N.A",
  "UF_CRM_1534532283": "N.A",
  "UF_CRM_1534533297": "N.A",
  "UF_CRM_1721064189466": "Comprar en 1 mes",
  "SOURCE_DESCRIPTION": "Presupuesto: 25000 dólares | Cita: 2026-05-20 16:00 | Resumen: Cliente interesado en SUV X, agenda visita."
}
```

Notas:

- Todo campo `None` o vacío termina como `"N.A"`.
- `financing_required` y `trade_in_vehicle` pasan por `_normalize_yes_no`.
- `SOURCE_DESCRIPTION` se compone siempre, aun cuando todas las partes sean `N.A`.

### 4.2 Payload `action=task`

Construido inline en `crear_task_confirmar_cita`:

```json
{
  "action": "task",
  "api_key": "<APIKEY_VITRIX>",
  "ID": 12345,
  "TITLE": "Llamar - confirmar cita IA",
  "DESCRIPTION": "-",
  "DEADLINE": "2026-05-17 14:30:00"
}
```

- `TITLE` hardcoded: `"Llamar - confirmar cita IA"`.
- `DESCRIPTION` hardcoded: `"-"`.
- `DEADLINE` = `datetime.now(ZoneInfo(TIMEZONE)) + 30 min`, formato `%Y-%m-%d %H:%M:%S`.
  - **Importante:** "ahora" es la zona definida por `app_config.TIMEZONE`.
  - Este deadline representa la **ventana de ejecución del asesor humano**
    (recordatorio para llamar al cliente y confirmar la cita), **no** la hora
    de la cita en sí.

### 4.3 Patrón fire-and-forget

`actualizar_lead_y_crear_task` ejecuta:

1. `await actualizar_lead_cita(...)` — síncrono, devuelve el resultado al caller.
2. `asyncio.create_task(_delayed_task())` — `_delayed_task` hace
   `await asyncio.sleep(5)` y luego `crear_task_confirmar_cita`.

El `sleep(5)` garantiza que Vitrix/Bitrix haya procesado el `edit` antes de
recibir el `task` (evita race condition donde Bitrix aún no tiene el lead en
el estado correcto cuando llega la task). Si la task falla, se loguea como
warning pero **no** afecta el retorno al caller.

---

## 5. Flujo: cierre con callback (llamada)

Función pública: `actualizar_lead_llamada_y_crear_task(id_bitrix, **edit_kwargs)`.

Idéntico al flujo de cita salvo por:

- **No** se envía `appointment_datetime` (no aplica — el cliente no agendó).
- `UF_CRM_1728502747862` = `"Llamar - Cliente interesado IA"`.
- `SOURCE_DESCRIPTION` omite la sección `"Cita: ..."`:
  `"Presupuesto: ... | Resumen: ..."`.
- La task usa `TITLE = "Llamar - Cliente interesado IA"`.

### 5.1 Payload `action=edit` (callback)

```json
{
  "action": "edit",
  "api_key": "<APIKEY_VITRIX>",
  "ID": 12345,
  "UF_CRM_1774974891": "Sesión Bot Completada Exitosamente",
  "UF_CRM_1728502747862": "Llamar - Cliente interesado IA",
  "STATUS_ID": "Asignado a Vendedor (Sin Contacto)",
  "UF_CRM_1653688826": "NO",
  "UF_CRM_1774975288": "N.A",
  "UF_CRM_1653599755": "SÍ",
  "UF_CRM_1653605302623": "Toyota",
  "UF_CRM_1653605318164": "Corolla",
  "UF_CRM_1534532283": "2018",
  "UF_CRM_1534533297": "85000",
  "UF_CRM_1721064189466": "Sin definir",
  "SOURCE_DESCRIPTION": "Presupuesto: 18000 dólares | Resumen: Cliente solicita llamada, no quiere agendar aún."
}
```

### 5.2 Payload `action=task` (callback)

```json
{
  "action": "task",
  "api_key": "<APIKEY_VITRIX>",
  "ID": 12345,
  "TITLE": "Llamar - Cliente interesado IA",
  "DESCRIPTION": "-",
  "DEADLINE": "2026-05-17 14:30:00"
}
```

Mismo patrón fire-and-forget con `sleep(5)` antes del POST de la task.

---

## 6. Flujo: lead desistido

Función pública: `marcar_lead_desistido(id_bitrix)`.

Sin builder externo, payload inline minimalista. La IA no aporta parámetros
adicionales; los valores son 100% hardcoded.

```json
{
  "action": "edit",
  "api_key": "<APIKEY_VITRIX>",
  "ID": 12345,
  "UF_CRM_1774974891": "Sesión Bot Completada Exitosamente",
  "UF_CRM_1559082118": "No respondió a la IA",
  "STATUS_ID": "Desistido"
}
```

No crea task asociada — el lead queda cerrado en el pipeline.

---

## 7. Normalización de entrada

### 7.1 `_normalize_yes_no(value)`

Acepta:

- `None` → `"N.A"`.
- `bool` → `"SÍ"` / `"NO"`.
- `str` → normaliza con `unicodedata.NFKD`, baja a ASCII y a minúsculas,
  colapsa whitespace.

Reconoce afirmativos como: `si`, `s`, `yes`, `y`, `true`, `1`, `claro`,
`si quiero`, `quiero financiamiento`, `requiero financiamiento`,
`quiero financiar`, `afirmativo`, `con financiamiento`, además de variantes
con prefijos (`si `, `si,`, `si.`).

Reconoce negativos como: `no`, `n`, `false`, `0`, `no gracias`, `contado`,
`compra de contado`, `sin financiamiento`, `no requiero financiamiento`,
`prefiero contado`, `sin credito`/`sin crédito`, y variantes con prefijos.

Si no hay match en ninguna lista, devuelve `"N.A"` (nunca devuelve string
crudo del usuario en estos campos).

### 7.2 `_clean_str(value)`

- `None` → `"N.A"`.
- String vacío o solo whitespace → `"N.A"`.
- Caso contrario → `value.strip()`.

Aplica a todos los campos `UF_CRM_*` de texto libre y a las partes del
`SOURCE_DESCRIPTION`.

---

## 8. Validación de `id_bitrix`

En todas las funciones públicas, el primer paso es:

```python
try:
    lead_id = int(id_bitrix)
except (TypeError, ValueError):
    logger.warning(...)
    return {"success": False, "lead_id": None, "error": "id_bitrix inválido", ...}
```

Cualquier valor no convertible a entero corta el flujo sin emitir HTTP.

---

## 9. Política de errores

Esta es una **tool de escritura**. Reglas fijas:

| Aspecto | Política |
|---------|----------|
| Retry | **No.** Ningún reintento automático. |
| Circuit breaker | **No.** |
| Cache | **No.** |
| Idempotencia | **Delegada al prompt del agente** — el prompt es responsable de no llamar dos veces con los mismos datos. |
| Timeout | El del cliente compartido (`infra.get_client()`). |

### 9.1 Casos manejados explícitamente

| Excepción / condición | Tratamiento |
|-----------------------|-------------|
| `id_bitrix` no entero | Retorno inmediato con `success=False`, `error="id_bitrix inválido"`. Sin HTTP. |
| `httpx.TransportError` | Log `error` con `exc_info=True`. Retorno con `success=False`, `error="transport: <detalle>"`. |
| Body no parseable como JSON (`ValueError` en `response.json()`) | Log con preview de 300 chars. Retorno con `error="Respuesta no JSON del API (HTTP <status>)"`. (Solo en `action=edit`.) |
| `httpx.HTTPStatusError` (solo `action=task`, que llama `raise_for_status()`) | Log con body preview. Retorno `{"success": False, "task_id": None, "error": "HTTP <status>"}`. |
| `data.success = False` con `resolve_errors` no vacío | Log warning "validación rechazada". Se preservan `resolve_errors` en el resultado. |
| `data.success = False` sin `resolve_errors` | Log warning "rechazo API" con status y body. Si el status fue `>= 400` y no había `error` en el body, se setea `error = "HTTP <status>"`. |
| Cualquier otra `Exception` | Log `error` con `exc_info=True`. Retorno con `success=False`, `error=str(e)`. |

### 9.2 Diferencia entre `edit` y `task` ante HTTP error

- `action=edit` **no** llama `response.raise_for_status()`. Parsea siempre el
  body si es JSON y deriva `success` del payload. Si el body no es JSON,
  retorna error explícito.
- `action=task` **sí** llama `response.raise_for_status()`. Cualquier 4xx/5xx
  lanza `HTTPStatusError`, se loguea y retorna sin parsear.

Esta asimetría existe porque `edit` puede devolver `200 OK` con
`success=False` + `resolve_errors` (validaciones de Bitrix), mientras que
`task` no tiene ese contrato.

---

## 10. Shape de la respuesta normalizada

### 10.1 `actualizar_lead_cita` / `actualizar_lead_llamada`

```python
{
    "success":         bool,           # data["success"] del API
    "lead_id":         int | None,     # data["lead_id"] o el casteado localmente
    "request_id":      str | None,     # correlation id del API
    "resolve_errors":  list,           # errores de validación de campos Bitrix
    "brand_routed":    list,           # ruteo por marca (informativo)
    "bitrix_response": dict | None,    # respuesta cruda de Bitrix
    "error":           str | None,     # mensaje de error o "HTTP <status>"
    "time_ms":         int | None,     # duración reportada por el API
}
```

### 10.2 `crear_task_confirmar_cita` / `crear_task_llamada`

```python
{
    "success": bool,
    "task_id": int | None,
    "error":   str | None,
}
```

### 10.3 `marcar_lead_desistido`

```python
{
    "success":    bool,
    "lead_id":    int | None,
    "request_id": str | None,
    "error":      str | None,
}
```

### 10.4 `actualizar_lead_y_crear_task` / `actualizar_lead_llamada_y_crear_task`

Retornan exactamente el shape de `actualizar_lead_cita` /
`actualizar_lead_llamada` respectivamente. La task corre en background y su
resultado **no** se incluye en el retorno (solo se loguea si falla).

---

## 11. Logging

Cada operación emite (como mínimo):

- `INPUT` — log informativo con `lead_id` y lista de campos enviados.
- `REQUEST` — payload completo (incluye `api_key`; usar redacción en producción si aplica).
- `RESPONSE OK` — log informativo con `lead_id`, `request_id`, `time_ms`, body.
- `RESPONSE validación rechazada` — warning con `resolve_errors`.
- `RESPONSE rechazo API` — warning con status y body completo.
- `RESPONSE TransportError` / `Error inesperado` — error con `exc_info=True`.

Tags por flujo:

| Tag de log | Operación |
|------------|-----------|
| `[VITRIX:edit]` | `actualizar_lead_cita` |
| `[VITRIX:task]` | `crear_task_confirmar_cita` |
| `[VITRIX:edit_llamada]` | `actualizar_lead_llamada` |
| `[VITRIX:task_llamada]` | `crear_task_llamada` |
| `[VITRIX:desistido]` | `marcar_lead_desistido` |

---

## 12. Constantes relevantes

Definidas al inicio del módulo:

```python
_ACTION = "edit"
_TASK_ACTION = "task"
_TASK_TITLE = "Llamar - confirmar cita IA"
_TASK_DESCRIPTION = "-"
_TASK_DEADLINE_FMT = "%Y-%m-%d %H:%M:%S"
_STATUS_BOT = "Sesión Bot Completada Exitosamente"
_CITA_VALUE = _TASK_TITLE          # mismo string que el TITLE de la task de cita
_LLAMADA_LABEL = "Llamar - Cliente interesado IA"
_DEFAULT_VALUE = "N.A"
_DESISTIDO_REASON = "No respondió a la IA"
_DESISTIDO_STATUS_ID = "Desistido"
```

Notar que `_CITA_VALUE` y el `TITLE` de la task de cita son la **misma
constante**: la etiqueta de cierre (`UF_CRM_1728502747862`) en el lead
coincide con el título de la task asociada. El flujo callback rompe esa
simetría: `_LLAMADA_LABEL` se usa tanto en el campo como en el `TITLE`.

---

## 13. Dependencias de configuración

| Variable | Origen | Uso |
|----------|--------|-----|
| `VITRIX_API_URL` | `app_config.VITRIX_API_URL` | Endpoint POST. |
| `APIKEY_VITRIX` | `app_config.APIKEY_VITRIX` | Auth en el body. |
| `TIMEZONE` | `app_config.TIMEZONE` | Cálculo de `DEADLINE` para tasks. |

Todas se cargan vía el módulo `config` del paquete `autobot`.

---

## 14. Resumen para el caller

- Usar **siempre** los wrappers `actualizar_lead_y_crear_task` y
  `actualizar_lead_llamada_y_crear_task` para cierres exitosos: garantizan
  orden `edit → sleep(5) → task` y delegan la task a background.
- Usar `marcar_lead_desistido` para cualquier cierre sin éxito comercial
  (no respondió, ruido, etc.). No agrega task.
- El caller solo necesita inspeccionar `success`. En `False`, revisar
  `resolve_errors` (validación) y `error` (transport / HTTP / mensaje API).
- No reintentar automáticamente desde el caller: el prompt decide.
