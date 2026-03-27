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

- **Cache de agentes**: hoy se cachea por `(id_empresa,)`. Con tenants sigue igual, cada empresa tiene su agente cacheado con sus tools y prompt.
- **Circuit breakers**: cada tenant puede definir sus propios CBs en su `config.py`. El registry de CBs global sigue funcionando para /health.
- **GQMConfig**: se renombrara a algo generico (TenantConfig o AgentConfig) cuando se implemente. Los campos dinamicos vienen en el JSON del request.
- **Testing**: cada tenant se testea independientemente. Se puede testear un tenant sin levantar los demas.
