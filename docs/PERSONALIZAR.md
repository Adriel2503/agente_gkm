# Personalizar Agente — Guía de Desacoplamiento

Guía para clonar `agent_citas` y crear un nuevo agente (demo, nuevo vertical, etc.)
deshabilitando o reemplazando solo las piezas necesarias.

La arquitectura está desacoplada en 3 capas independientes:

```
┌─────────────────────────────────────────────────┐
│                  agent.py                        │
│   create_agent(tools=..., system_prompt=...)     │
│          ▲                    ▲                   │
│          │                    │                   │
│    ┌─────┴─────┐     ┌───────┴────────┐          │
│    │   Tools   │     │ System Prompt  │          │
│    │ tools.py  │     │  prompts/      │          │
│    └───────────┘     │  ├ __init__.py  │          │
│                      │  └ *.j2         │          │
│                      │       ▲         │          │
│                      │       │         │          │
│                      │  ┌────┴──────┐  │          │
│                      │  │ Services  │  │          │
│                      │  │ (fetches) │  │          │
│                      │  └───────────┘  │          │
│                      └────────────────┘          │
└─────────────────────────────────────────────────┘
```

Cada capa se puede activar/desactivar sin afectar las demás.

---

## 1. Tools (herramientas del agente)

**Archivo:** `src/citas/tools/tools.py` — línea 337

Las tools se registran en `AGENT_TOOLS` y se pasan a `create_agent()` en `agent.py:87`.
Son independientes del prompt: el LLM las ve como funciones disponibles sin importar
lo que diga el system prompt.

### Opción A: Deshabilitar todas las tools

```python
# Antes
AGENT_TOOLS = [
    check_availability,
    create_booking,
    search_productos_servicios,
]

# Después
AGENT_TOOLS = []
```

El agente funciona como chatbot puro — solo conversa, no ejecuta acciones.

### Opción B: Deshabilitar tools selectivamente

```python
AGENT_TOOLS = [
    check_availability,       # ✅ mantener consulta de horarios
    # create_booking,         # ❌ no crear reservas
    # search_productos_servicios,  # ❌ no buscar productos
]
```

### Opción C: Variable de entorno (sin tocar código)

```python
import os

_ALL_TOOLS = [check_availability, create_booking, search_productos_servicios]
AGENT_TOOLS = _ALL_TOOLS if os.getenv("ENABLE_TOOLS", "1") == "1" else []
```

En `.env`:
```
ENABLE_TOOLS=0   # chatbot puro, sin tools
ENABLE_TOOLS=1   # todas las tools activas (default)
```

### Notas sobre tools

- Si el prompt **no menciona** las tools, el LLM igual puede invocarlas
  (las ve en el API call como function definitions).
- Si el prompt **sí las menciona** pero `AGENT_TOOLS = []`, el LLM no puede usarlas
  (no hay funciones registradas, solo texto en el prompt).
- **Para mejor resultado:** si las tools están activas, el prompt debería describir cuándo usarlas.

---

## 2. Services (datos inyectados en el prompt)

**Archivo:** `src/citas/agent/prompts/__init__.py` — líneas 73-101

Los services hacen 4 HTTP calls paralelas para obtener datos de negocio:

| Service | Función | Variable en template |
|---------|---------|---------------------|
| Horario de atención | `fetch_horario_reuniones()` | `{{ horario_atencion }}` |
| Productos/Servicios | `fetch_nombres_productos_servicios()` | `{{ lista_productos_servicios }}` |
| Contexto de negocio | `fetch_contexto_negocio()` | `{{ contexto_negocio }}` |
| Preguntas frecuentes | `fetch_preguntas_frecuentes()` | `{{ preguntas_frecuentes }}` |

### Opción A: Deshabilitar todos los services

Eliminar o comentar las líneas 72-101 en `prompts/__init__.py`.

**Antes:**
```python
    # Cargar horario, productos/servicios, contexto de negocio y preguntas frecuentes en paralelo
    results = await asyncio.gather(
        fetch_horario_reuniones(id_empresa),
        fetch_nombres_productos_servicios(id_empresa),
        fetch_contexto_negocio(id_empresa),
        fetch_preguntas_frecuentes(config.id_chatbot if config else None),
        return_exceptions=True,
    )

    # ... manejo de results (líneas 81-94) ...

    variables["horario_atencion"] = horario_atencion
    variables["nombres_productos"] = nombres_productos
    variables["nombres_servicios"] = nombres_servicios
    variables["lista_productos_servicios"] = format_nombres_para_prompt(...)
    variables["contexto_negocio"] = contexto_negocio
    variables["preguntas_frecuentes"] = preguntas_frecuentes_str or ""

    return _citas_template.render(**variables)
```

**Después:**
```python
    return _citas_template.render(**variables)
```

Se ahorran 4 HTTP calls por mensaje. Las variables de fecha (`fecha_iso`, `hora_actual`,
`fecha_completa`) se calculan antes (líneas 59-64) y siguen disponibles.

### Opción B: No renderizar en template (services se ejecutan pero no aparecen)

Si quieres que los fetches se ejecuten (útil para logging/debug) pero no inyectar
los datos en el prompt: simplemente no uses las variables en tu `.j2`.

Jinja2 ignora las variables que no se referencian — cero errores.

### Opción C: Deshabilitar selectivamente

Comentar solo los fetches que no necesitas:

```python
    results = await asyncio.gather(
        fetch_horario_reuniones(id_empresa),        # ✅ mantener
        # fetch_nombres_productos_servicios(id_empresa),  # ❌ no necesario
        fetch_contexto_negocio(id_empresa),          # ✅ mantener
        # fetch_preguntas_frecuentes(config.id_chatbot if config else None),  # ❌ no necesario
        return_exceptions=True,
    )
```

> **Importante:** Si eliminas un fetch del `gather`, ajustar los índices de `results[N]`
> o usar `return_exceptions=True` con nombres explícitos para evitar confusión.

---

## 3. System Prompt (template Jinja2)

**Archivo:** `src/citas/agent/prompts/gqm_system.j2`

El template es 100% independiente. Puedes reemplazar todo su contenido.

### Variables siempre disponibles (no dependen de services)

| Variable | Origen | Ejemplo |
|----------|--------|---------|
| `{{ fecha_iso }}` | `datetime.now()` | `2026-03-13` |
| `{{ hora_actual }}` | `datetime.now()` | `02:30 PM` |
| `{{ fecha_completa }}` | `datetime.now()` | `13 de marzo de 2026 es jueves` |
| `{{ id_empresa }}` | Request | `42` |
| `{{ duracion_cita_minutos }}` | Config | `30` |
| `{{ personalidad }}` | Config | `Amable y profesional` |

### Variables que requieren services activos

| Variable | Service requerido |
|----------|------------------|
| `{{ horario_atencion }}` | `fetch_horario_reuniones` |
| `{{ lista_productos_servicios }}` | `fetch_nombres_productos_servicios` |
| `{{ contexto_negocio }}` | `fetch_contexto_negocio` |
| `{{ preguntas_frecuentes }}` | `fetch_preguntas_frecuentes` |

Si el service está deshabilitado y el template usa la variable → Jinja2 la renderiza vacía (sin error).

### Ejemplo: template mínimo para demo

```jinja2
Eres un asistente virtual de {{ personalidad | default('una empresa') }}.
Hoy es {{ fecha_completa }}, son las {{ hora_actual }}.

Responde de forma amable y profesional.
```

---

## Resumen: Combinaciones comunes

| Escenario | Tools | Services | Template |
|-----------|-------|----------|----------|
| **Agente completo (producción)** | `AGENT_TOOLS` con 3 tools | 4 fetches activos | Template completo con todas las variables |
| **Chatbot demo (sin acciones)** | `AGENT_TOOLS = []` | Eliminar líneas 72-101 | Template personalizado mínimo |
| **Agente con tools propias** | Reemplazar lista | Agregar/quitar fetches | Template que describe las nuevas tools |
| **Solo consulta de horarios** | Solo `check_availability` | Solo `fetch_horario_reuniones` | Template con `{{ horario_atencion }}` |

### Checklist para clonar el proyecto

1. **`tools/tools.py`** → Ajustar `AGENT_TOOLS` (vacío, parcial, o nuevas tools)
2. **`prompts/__init__.py`** → Eliminar/comentar fetches no necesarios (líneas 72-101)
3. **`prompts/gqm_system.j2`** → Reemplazar con tu prompt personalizado
4. **`.env`** → Configurar `OPENAI_API_KEY`, `OPENAI_MODEL`, quitar `REDIS_URL` si no usas Redis

Todo lo demás (`agent.py`, `runtime/`, `config/`, `infra/`) queda igual — no tocar.
