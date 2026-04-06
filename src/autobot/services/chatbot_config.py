"""
Servicio para leer chatbot_config y datos relacionados de la BD.
"""

from ..logger import get_logger
from .db import get_pool

logger = get_logger(__name__)


async def get_chatbot_config(id_empresa: int) -> dict | None:
    """
    Lee la configuración del chatbot para una empresa.
    Retorna dict con todos los campos o None si no existe.
    """
    pool = get_pool()
    if pool is None:
        return None

    row = await pool.fetchrow(
        """
        SELECT id, id_empresa, nombre_bot, personalidad, mensaje_bienvenida,
               prompt_sistema, tools
        FROM chatbot_config
        WHERE id_empresa = $1 AND estado_registro = 1
        LIMIT 1
        """,
        id_empresa,
    )
    if row is None:
        logger.warning("[CHATBOT_CONFIG] No hay config para id_empresa=%s", id_empresa)
        return None

    return dict(row)


async def get_empresa_timezone(id_empresa: int) -> str:
    """Lee la zona horaria de la empresa via zona_horaria. Default: America/Lima."""
    pool = get_pool()
    if pool is None:
        return "America/Lima"

    row = await pool.fetchrow(
        """
        SELECT zh.codigo
        FROM empresa e
        JOIN zona_horaria zh ON zh.id = e.id_zona_horaria AND zh.estado_registro = 1
        WHERE e.id = $1 AND e.estado_registro = 1
        """,
        id_empresa,
    )
    if row and row["codigo"]:
        return row["codigo"]
    return "America/Lima"


async def get_sucursales_por_marca(id_empresa: int) -> str:
    """
    Lee sucursales agrupadas por marca para inyectar en el prompt.
    Retorna texto formateado listo para el template.
    """
    pool = get_pool()
    if pool is None:
        return ""

    rows = await pool.fetch(
        """
        SELECT m.nombre AS marca, s.nombre AS sucursal
        FROM sucursal s
        JOIN marca m ON m.id_empresa = s.id_empresa AND m.estado_registro = 1
        WHERE s.id_empresa = $1 AND s.estado_registro = 1
        ORDER BY m.nombre, s.orden, s.nombre
        """,
        id_empresa,
    )
    if not rows:
        return ""

    marcas: dict[str, list[str]] = {}
    for row in rows:
        marca = row["marca"]
        if marca not in marcas:
            marcas[marca] = []
        marcas[marca].append(row["sucursal"])

    lineas = []
    for marca, sucursales in marcas.items():
        lineas.append(f"{marca.upper()}:")
        for s in sucursales:
            lineas.append(f"- {s}")
        lineas.append("")

    return "\n".join(lineas).strip()


async def get_faqs(id_empresa: int) -> str:
    """
    Lee las preguntas frecuentes de la empresa.
    Retorna texto formateado listo para el prompt.
    """
    pool = get_pool()
    if pool is None:
        return ""

    rows = await pool.fetch(
        """
        SELECT pregunta, respuesta
        FROM preguntas_frecuentes
        WHERE id_empresa = $1 AND estado_registro = 1
        ORDER BY numero
        """,
        id_empresa,
    )
    if not rows:
        return ""

    lineas = []
    for row in rows:
        lineas.append(f"P: \"{row['pregunta']}\"")
        lineas.append(f"R: \"{row['respuesta']}\"")
        lineas.append("")

    return "\n".join(lineas).strip()


async def get_marcas(id_empresa: int) -> str:
    """Lee las marcas de la empresa como texto separado por comas."""
    pool = get_pool()
    if pool is None:
        return ""

    rows = await pool.fetch(
        """
        SELECT nombre FROM marca
        WHERE id_empresa = $1 AND estado_registro = 1
        ORDER BY nombre
        """,
        id_empresa,
    )
    if not rows:
        return ""

    return ", ".join(row["nombre"] for row in rows)
