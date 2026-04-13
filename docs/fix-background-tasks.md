# Fix: Background Tasks sin referencia

## Problema

En `main.py`, el endpoint `POST /api/chat` creaba un task en background sin guardar referencia:

```python
asyncio.create_task(_process_and_callback(req))
```

Python puede hacer garbage collection del objeto `Task` si nadie lo referencia, cancelando la ejecucion antes de que termine. Esto significa que la respuesta del agente podria nunca llegar al callback.

Documentado en la [referencia oficial de asyncio](https://docs.python.org/3/library/asyncio-task.html#asyncio.create_task):

> "Save a reference to the result of this function, to avoid a task disappearing mid-execution."

## Solucion

Se agrego un `set` a nivel de modulo que mantiene vivos los tasks mientras corren:

```python
_background_tasks: set[asyncio.Task] = set()

# En el endpoint:
task = asyncio.create_task(_process_and_callback(req))
_background_tasks.add(task)
task.add_done_callback(_background_tasks.discard)
```

- `_background_tasks.add(task)` — evita que el GC lo destruya
- `task.add_done_callback(_background_tasks.discard)` — lo limpia al terminar (sin memory leak)

## Archivos modificados

- `src/autobot/main.py` (lineas 42, 108-110)

## Impacto

| Antes | Despues |
|---|---|
| Task podia ser destruido por GC | Task siempre vive hasta completarse |
| Respuesta podia perderse silenciosamente | Respuesta siempre se envia al callback |
| Sin memory leak (task era efimero) | Sin memory leak (discard limpia al terminar) |
