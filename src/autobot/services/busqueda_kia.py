"""
Servicio de búsqueda semántica de vehículos KIA vía RAG API.
Llama al endpoint /buscar del servicio FastAPI con pgvector.

Resiliencia:
  - Circuit breaker (kia_rag_cb): 3 fallos de red → abierto 5 min, auto-reset.
  - Retry: tenacity en post_with_logging → post_with_retry (TransportError, exponential backoff).
  - Logging: request/response en DEBUG via post_with_logging.
  - Métricas: track_api_call mide latencia, track_tool_execution mide duración total.
"""

from .. import config as app_config
from ..logger import get_logger
from ..metrics import track_tool_execution, track_api_call, DEGRADATION
from ..infra import post_with_logging, resilient_call
from ..config import kia_rag_cb

logger = get_logger(__name__)


async def buscar_vehiculo_rag(
    query: str,
    log_apis: bool = False,
) -> dict:
    """
    Busca modelos KIA por similitud semántica usando la RAG API.

    Args:
        query: Texto libre de búsqueda (ej: "auto familiar barato", "New Picanto")
        log_apis: Debug flag para logging detallado

    Returns:
        {"success": bool, "resultados": [...], "total": int, "mensaje": str | None, "error": str | None}
    """
    try:
        if log_apis:
            logger.info("[buscar_vehiculo_rag] Llamando RAG API con query: %s", query)

        with track_tool_execution("buscar_vehiculo_rag"):
            with track_api_call("kia_rag"):
                response = await resilient_call(
                    lambda: post_with_logging(app_config.API_KIA_RAG_URL, {"query": query}),
                    cb=kia_rag_cb,
                    circuit_key="global",
                    service_name="KIA_RAG",
                )

        if not response or "resultados" not in response:
            logger.warning("[buscar_vehiculo_rag] Respuesta inesperada: %s", response)
            return {
                "success": False,
                "resultados": [],
                "total": 0,
                "mensaje": "No encontré vehículos en esa búsqueda. Intentá con otro modelo o marca para ayudarte mejor.",
                "error": "Respuesta inesperada de la API",
            }

        resultados = response.get("resultados", [])
        result = {
            "success": True,
            "resultados": resultados,
            "total": int(response.get("total", len(resultados)) or 0),
            "mensaje": response.get("mensaje"),
            "error": None,
        }
        logger.info(
            "[buscar_vehiculo_rag] %d resultado(s) para: %s",
            len(result["resultados"]),
            query,
        )
        return result

    except RuntimeError:
        DEGRADATION.labels(service="kia_rag", reason="circuit_open").inc()
        logger.warning("[buscar_vehiculo_rag] Circuit breaker abierto — RAG API no disponible")
        return {
            "success": False,
            "resultados": [],
            "total": 0,
            "mensaje": "No encontré vehículos en esa búsqueda. Intentá con otro modelo o marca para ayudarte mejor.",
            "error": "Servicio no disponible temporalmente",
        }
    except Exception as e:
        logger.error("[buscar_vehiculo_rag] Error: %s", e, exc_info=True)
        return {
            "success": False,
            "resultados": [],
            "total": 0,
            "mensaje": "No encontré vehículos en esa búsqueda. Intentá con otro modelo o marca para ayudarte mejor.",
            "error": str(e),
        }


# Campos que van en el encabezado (no se repiten en los grupos).
# La función SQL devuelve 62 columnas; el objetivo es mostrar las claves
# principales arriba y el resto agrupado de forma legible para el LLM.
_HEADER_FIELDS: set[str] = {
    "marca",
    "modelo",
    "ano_modelo",
    "variante",
    "gama",
    "detalle_de_version",
    "tipo_de_gama",
    "precio_lista_usd",
}

_FIELD_GROUPS: dict[str, set[str]] = {
    "Identificación": {
        "introduccion_de_modelo",
        "caracteristicas_generales",
        "colores_disponibles",
    },
    "Descripción": {
        "descripcion_del_modelo",
    },
    "Motor y mecánica": {
        "tipo_de_carroceria",
        "motor_cilindros",
        "cilindrada_combustible_tipo",
        "transmision",
        "garantia",
        "potencia",
        "torque",
        "distancia_entre_ejes",
        "tamano_aros",
        "dimensiones",
        "traccion",
    },
    "Mantenimiento": {
        "paquete_de_mantenimiento_prepago",
        "primer_servicio_de_mantenimiento",
        "frecuencia_de_mantenimiento",
    },
    "Equipamiento": {
        "sunroof",
        "capacidad_de_la_maletera",
        "espejos_electricos",
        "aire_acondicionado",
        "radio_tactil",
        "camara_de_retroceso",
        "alzavidrios_electricos",
        "volante_forrado_en_cuero",
        "sensores_de_estacionamiento",
        "faros_delanteros_led",
        "faros_posteriores_led",
        "faros_neblineros",
        "panel_de_instrumentos",
        "tipo_de_llave",
        "conectividad_de_la_radio",
        "llanta_de_repuesto",
        "cargador_inalambrico",
        "material_de_asientos",
        "maletero_inteligente",
        "freno_de_estacionamiento",
        "numero_de_asientos",
        "monitor_de_punto_ciego",
        "asientos_electricos",
        "autonomia_ev",
    },
    "ADAS y seguridad": {
        "airbags",
        "sistema_de_frenos",
        "tipo_de_frenos",
        "prevencion_colision_frontal_fca",
        "prevencion_colision_punto_ciego_bca",
        "mantenimiento_carril_lka",
        "prevencion_colision_trafico_cruzado_rcca",
        "seguimiento_carril_lfa",
        "control_crucero",
        "rieles_en_el_techo",
        "suspension_delanteros",
        "suspension_posteriores",
    },
    "Recursos multimedia": {
        "url_imagen",
        "url_pdf",
        "url_video",
    },
}

_FIELD_LABELS: dict[str, str] = {
    "marca": "Marca",
    "modelo": "Modelo",
    "introduccion_de_modelo": "Introducción del modelo",
    "caracteristicas_generales": "Características generales",
    "colores_disponibles": "Colores disponibles",
    "variante": "Variante",
    "gama": "Gama",
    "detalle_de_version": "Detalle de versión",
    "ano_modelo": "Año modelo",
    "precio_lista_usd": "Precio lista USD",
    "tipo_de_carroceria": "Tipo de carrocería",
    "motor_cilindros": "Motor cilindros",
    "cilindrada_combustible_tipo": "Cilindrada / combustible / tipo",
    "tipo_de_gama": "Tipo de gama",
    "descripcion_del_modelo": "Descripción del modelo",
    "transmision": "Transmisión",
    "garantia": "Garantía",
    "potencia": "Potencia",
    "torque": "Torque",
    "distancia_entre_ejes": "Distancia entre ejes",
    "tamano_aros": "Tamaño aros",
    "dimensiones": "Dimensiones",
    "paquete_de_mantenimiento_prepago": "Paquete de mantenimiento prepago",
    "primer_servicio_de_mantenimiento": "Primer servicio de mantenimiento",
    "frecuencia_de_mantenimiento": "Frecuencia de mantenimiento",
    "sunroof": "Sunroof",
    "capacidad_de_la_maletera": "Capacidad de la maletera",
    "espejos_electricos": "Espejos eléctricos",
    "aire_acondicionado": "Aire acondicionado",
    "radio_tactil": "Radio táctil",
    "camara_de_retroceso": "Cámara de retroceso",
    "airbags": "Airbags",
    "sistema_de_frenos": "Sistema de frenos",
    "tipo_de_frenos": "Tipo de frenos",
    "alzavidrios_electricos": "Alzavidrios eléctricos",
    "volante_forrado_en_cuero": "Volante forrado en cuero",
    "sensores_de_estacionamiento": "Sensores de estacionamiento",
    "faros_delanteros_led": "Faros delanteros LED",
    "faros_posteriores_led": "Faros posteriores LED",
    "faros_neblineros": "Faros neblineros",
    "panel_de_instrumentos": "Panel de instrumentos",
    "tipo_de_llave": "Tipo de llave",
    "conectividad_de_la_radio": "Conectividad de la radio",
    "suspension_delanteros": "Suspensión delantera",
    "suspension_posteriores": "Suspensión posterior",
    "llanta_de_repuesto": "Llanta de repuesto",
    "cargador_inalambrico": "Cargador inalámbrico",
    "material_de_asientos": "Material de asientos",
    "maletero_inteligente": "Maletero inteligente",
    "prevencion_colision_frontal_fca": "Prevención colisión frontal FCA",
    "prevencion_colision_punto_ciego_bca": "Prevención colisión punto ciego BCA",
    "mantenimiento_carril_lka": "Mantenimiento carril LKA",
    "prevencion_colision_trafico_cruzado_rcca": "Prevención colisión tráfico cruzado RCCA",
    "seguimiento_carril_lfa": "Seguimiento carril LFA",
    "control_crucero": "Control crucero",
    "rieles_en_el_techo": "Rieles en el techo",
    "freno_de_estacionamiento": "Freno de estacionamiento",
    "numero_de_asientos": "Número de asientos",
    "traccion": "Tracción",
    "monitor_de_punto_ciego": "Monitor de punto ciego",
    "asientos_electricos": "Asientos eléctricos",
    "autonomia_ev": "Autonomía EV",
    "url_imagen": "Imagen",
    "url_pdf": "Ficha técnica (PDF)",
    "url_video": "Video",
}

_ALL_GROUPED: set[str] = _HEADER_FIELDS | set().union(*_FIELD_GROUPS.values())

_SKIP_FIELDS: set[str] = {"id"}


def _format_field(key: str, value: object) -> str:
    """Devuelve una línea legible para el LLM a partir de una clave SQL."""
    label = _FIELD_LABELS.get(key, key.replace("_", " ").strip().title())
    return f"  {label}: {value}"


def format_kia_resultados(resultados: list[dict]) -> str:
    """Formatea los resultados del RAG para que el LLM los presente bien.

    Encabezado con marca, modelo, año, versión, gama y precio.
    Luego todos los campos conocidos agrupados por categoría. Si la API
    agrega campos nuevos, aparecen automáticamente en el grupo "Otros".
    """
    if not resultados:
        return "No encontré modelos que coincidan con tu búsqueda."

    lineas = []
    for r in resultados:
        # Encabezado: marca + modelo + año + versión + precio
        marca = r.get("marca", "N/A")
        modelo = r.get("modelo", "N/A")
        anio = r.get("ano_modelo", "")
        lineas.append(f"=== {marca} {modelo} {anio} ===")

        # Subencabezado: variante | gama | detalle | tipo | precio
        sub = []
        if r.get("variante"):
            sub.append(str(r["variante"]))
        if r.get("gama"):
            sub.append(str(r["gama"]))
        if r.get("detalle_de_version"):
            sub.append(str(r["detalle_de_version"]))
        if r.get("tipo_de_gama"):
            sub.append(str(r["tipo_de_gama"]))
        if r.get("precio_lista_usd"):
            sub.append(str(r["precio_lista_usd"]))
        if sub:
            lineas.append(" | ".join(sub))

        # Grupos de campos
        for group_name, fields in _FIELD_GROUPS.items():
            group_lines = []
            for key in fields:
                value = r.get(key)
                if value is not None and value != "" and value != "N/A":
                    group_lines.append(_format_field(key, value))
            if group_lines:
                lineas.append(f"[{group_name}]")
                lineas.extend(sorted(group_lines))

        # Campos no agrupados (nuevos de la API)
        otros = []
        for key, value in r.items():
            if key in _SKIP_FIELDS:
                continue
            if key not in _ALL_GROUPED and value is not None and value != "" and value != "N/A":
                otros.append(f"  {key}: {value}")
        if otros:
            lineas.append("[Otros]")
            lineas.extend(sorted(otros))

        lineas.append("")

    return "\n".join(lineas).strip()
