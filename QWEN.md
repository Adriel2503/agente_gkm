## Qwen Added Memories
- El proyecto AutoBot solo tiene logica de LECTURA actualmente. Para agregar una tool de ESCRITURA: NO usar post_with_retry (el retry crea duplicados), usar get_client().post() directamente sin retry automatico, agregar logica de idempotencia si es necesario, y posiblemente crear un circuit breaker separado.
