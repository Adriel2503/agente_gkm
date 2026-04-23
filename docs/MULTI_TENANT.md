# Multi-Tenant: Arquitectura por Tenant

## Problema

Hoy el agente es single-tenant (GQM). Tools, services y prompts estan hardcodeados para un solo cliente. Para escalar a multiples empresas automotrices, cada tenant necesita su propio conjunto de tools, services y template de prompt.

## Decision: Estructura por Tenant

Se evaluo organizar por tipo (tools/gqm/, services/gqm/) vs por tenant (tenants/gqm/). Se eligio **por tenant** porque:

- Agregar empresa = crear 1 carpeta, copiar de otra como template
- Eliminar empresa = borrar 1 carpeta
- Todo lo de un tenant esta junto, no hay que saltar entre carpetas
- Cada tenant es independiente, no contamina a los demas
- Escala mejor con 10+ empresas

## Estructura Propuesta

```
src/autobot/
  core/                          # Compartido entre todos los tenants
    __init__.py
    config/                      # Config global (env, timeouts, CB params)
    infra/                       # HTTP client, circuit breaker, resilience
    schemas.py                   # ChatRequest, ChatResponse, AckResponse
    metrics.py                   # Prometheus metrics
    logger.py                    # Logging centralizado
  agent/                         # Runtime del agente (compartido)
    runtime/                     # LLM, cache, middleware, checkpointer
    content.py                   # CitaStructuredResponse, _build_content
    context.py                   # AgentContext, _prepare_agent_context
    agent.py                     # process_message (orquesta tenant)
  tenants/
    __init__.py                  # TenantRegistry
    gqm/                         # Primer tenant (Grupo Quality Motors)
      __init__.py                # register: id_empresa, tools, template
      tools.py                   # search_kia_modelos
      services/
        busqueda_kia.py          # RAG API KIA
      prompts/
        system.j2                # Template de prompt GQM
      config.py                  # Config especifica (CB, URLs, defaults)
    dealer_ejemplo/              # Segundo tenant (template para copiar)
      __init__.py
      tools.py
      services/
        busqueda_catalogo.py
      prompts/
        system.j2
      config.py
  registry.py                   # Mapea id_empresa -> tenant
  main.py                       # FastAPI server
```

## Flujo de un Request

```
POST /api/chat { id_empresa: 11, question: "...", phone: "..." }
  |
  v
main.py -> process_message(id_empresa=11, ...)
  |
  v
registry.get(11) -> TenantGQM
  |
  v
Usa: TenantGQM.tools, TenantGQM.template, TenantGQM.services
  |
  v
LLM procesa con tools y prompt del tenant
  |
  v
Callback al orquestador
```

## Registry: Como Funciona

```python
# tenants/__init__.py
from dataclasses import dataclass

@dataclass
class TenantConfig:
    id_empresa: int
    tools: list          # Tools del agente para este tenant
    template: str        # Path al template .j2
    name: str            # Nombre display (para logs)

_registry: dict[int, TenantConfig] = {}

def register(config: TenantConfig):
    _registry[config.id_empresa] = config

def get_tenant(id_empresa: int) -> TenantConfig:
    tenant = _registry.get(id_empresa)
    if not tenant:
        raise ValueError(f"Tenant no registrado: id_empresa={id_empresa}")
    return tenant
```

```python
# tenants/gqm/__init__.py
from ..  import register, TenantConfig
from .tools import TOOLS

register(TenantConfig(
    id_empresa=11,
    tools=TOOLS,
    template="tenants/gqm/prompts/system.j2",
    name="GQM",
))
```

## Que es Compartido vs Que es por Tenant

| Componente | Compartido | Por Tenant |
|---|---|---|
| FastAPI server (main.py) | Si | |
| HTTP client, circuit breaker | Si | |
| LLM model, checkpointer | Si | |
| Metrics, logging | Si | |
| ChatRequest/Response schemas | Si | |
| AgentContext | Si | |
| Tools (funciones @tool) | | Si |
| Services (busqueda, APIs) | | Si |
| System prompt (.j2) | | Si |
| Circuit breaker instancias | | Si (cada tenant puede tener sus propios CBs) |

## Como Agregar un Nuevo Tenant

1. Crear carpeta `tenants/mi_dealer/`
2. Copiar estructura de `tenants/gqm/` como template
3. Modificar:
   - `tools.py` — definir tools especificas
   - `services/` — implementar llamadas a APIs del dealer
   - `prompts/system.j2` — escribir el prompt del dealer
   - `__init__.py` — registrar con id_empresa
4. El server detecta el tenant automaticamente por id_empresa en el request

## Migracion desde Estructura Actual

Pasos para migrar de la estructura actual (plana) a tenants:

1. Crear `core/` y mover ahi: config/, infra/, schemas, metrics, logger
2. Crear `tenants/gqm/` y mover ahi: tools/, services/, prompts/
3. Crear `registry.py` con el TenantRegistry
4. Modificar `agent.py` para resolver tenant por id_empresa
5. Modificar `_get_agent()` para usar tools y template del tenant
6. Actualizar imports

## Consideraciones

- **Cache de agentes**: hoy se cachea por `(id_empresa, phone, id_bitrix)`. Con tenants sigue igual: el agente se segmenta por lead de Vitrix porque el prompt incluye `<lead_identity>` con datos específicos. Ver sección "Decisión de cache key" más abajo.
- **Circuit breakers**: cada tenant puede definir sus propios CBs en su `config.py`. El registry de CBs global sigue funcionando para /health.
- **GQMConfig**: se renombrara a algo generico (TenantConfig o AgentConfig) cuando se implemente. Los campos dinamicos vienen en el JSON del request.
- **Testing**: cada tenant se testea independientemente. Se puede testear un tenant sin levantar los demas.

## Decisión de cache key: Opciones evaluadas

### Contexto del problema

El orquestador envía 5 campos de identidad del lead por request (`nombre`, `marca`, `modelo`, `version`, `id_bitrix`), que se renderizan en el system prompt dentro de `<lead_identity>`. Esto significa que **el prompt varía por persona**, no solo por empresa.

La cache key original `(id_empresa,)` causaba un bug: el primer lead que escribía a una empresa generaba el agente con SU prompt, y los siguientes leads de la misma empresa durante los próximos 60 min (TTL) recibían ese prompt ajeno.

Solución evidente: incluir `phone` en la cache key. Pero quedaba una duda: ¿qué pasa si los campos del lead cambian durante la vida del cache? Se evaluaron 3 opciones.

### Opción A — Comparación de campos

**Idea:** cache key `(id_empresa, phone)` + guardar los 4 campos junto al agente en una `CachedAgent` NamedTuple. En cada HIT, comparar los campos del request con los cacheados; si alguno cambió, invalidar manualmente y recrear.

```python
class CachedAgent(NamedTuple):
    agent: Any
    nombre: str | None
    marca: str | None
    modelo: str | None
    version: str | None
    id_bitrix: str | None

# En _get_agent():
cached = get_cached_agent(cache_key)
if cached is not None:
    if (cached.nombre == nombre and cached.marca == marca
        and cached.modelo == modelo and cached.version == version
        and cached.id_bitrix == id_bitrix):
        return cached.agent      # HIT exacto
    else:
        invalidate_agent(cache_key)   # STALE, pop manual
        # cae al flujo de creación
```

**Pros:** invalidación inmediata al detectar cambio, siempre 1 entrada por persona, debuggeable (se ven los campos en el cache).
**Contras:** código extra (NamedTuple + función `invalidate_agent` + comparaciones).

### Opción B — Hash en la cache key

**Idea:** meter el hash de los 4 campos dentro de la propia key. Si cambia cualquier campo → key distinta → miss automático, sin lógica de invalidación.

```python
cache_key = (id_empresa, phone, hash((nombre, marca, modelo, version, id_bitrix)))
cache[cache_key] = agent
```

**Pros:** código compacto, sin invalidación explícita (el TTLCache expira solo las entradas viejas).
**Contras:**
- Las entradas viejas quedan huérfanas hasta que expire su TTL → hasta 2-3 entradas por persona temporalmente.
- Inspección opaca: al mirar el cache solo se ven hashes, no los datos del lead reales.
- La "ventaja" de la invalidación automática es marginal porque los campos casi nunca cambian.

### Opción C — Minimalista (la implementada, evolucionada a incluir id_bitrix)

**Idea:** cache key `(id_empresa, phone, id_bitrix)`, sin validar el resto de los campos. El `id_bitrix` en la key resuelve el caso real observado en producción: Vitrix envía el mismo `phone` con distintos leads (distinto `id_bitrix`). Sin `id_bitrix` en la key, el segundo lead recibía el prompt del primero. El resto de los campos (marca, modelo, etc.) sigue el mismo criterio YAGNI: cambios son raros en ventana de 60 min.

```python
cache_key: tuple = (id_empresa, phone, id_bitrix)
cached = get_cached_agent(cache_key)
if cached is not None:
    return cached
# MISS: build + cache
```

Para mantener el historial de conversación aislado por lead (espejo de la cache key), el `thread_id` del checkpointer usa la misma estructura:

```python
run_config = {"configurable": {"thread_id": f"{id_empresa}_{phone}_{id_bitrix}"}}
```

**Pros:**
- Cambio simple: agregar `id_bitrix` a la key y al `thread_id`.
- YAGNI: no se agrega complejidad por un edge case residual (cambios de marca/modelo dentro de los 60 min).
- 1 entrada por lead en el cache.

**Contras aceptados:**
- Si el orquestador cambia silenciosamente `marca`/`modelo`/`version`/`sucursal` del mismo `id_bitrix`, el prompt viejo vive hasta 60 min.
- Leads distintos del mismo phone producen entradas de cache separadas (aceptable: cada lead es una conversación comercial independiente).

**Por qué se eligió C:** los campos no-identitarios del lead cambian en escalas de **días**, no de minutos. En un TTL de 60 min la probabilidad es baja. El caso verdaderamente frecuente (multi-lead por phone desde Vitrix) se resuelve con `id_bitrix` en la key, sin código de invalidación.

### Escalada futura

Si en producción se observa que `marca`/`modelo`/`version`/`sucursal` cambian con frecuencia para el mismo `id_bitrix` y causa problemas reales, migrar a **Opción A** es ~15 minutos de trabajo:
1. Crear `CachedAgent` NamedTuple en `runtime/_cache.py`.
2. Agregar función `invalidate_agent(cache_key)`.
3. En `_get_agent()`, envolver el retorno en `CachedAgent` y agregar bloque de comparación.

La Opción B no se considera para escalada porque sus ventajas son marginales y la inspección opaca complica debugging.
