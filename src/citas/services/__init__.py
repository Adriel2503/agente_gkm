"""
Servicios del agente: busqueda_kia, prompt_data, busqueda_productos.
"""

from .prompt_data import fetch_contexto_negocio, fetch_horario_reuniones
from .prompt_data import fetch_nombres_productos_servicios, format_nombres_para_prompt
from .busqueda_productos import buscar_productos_servicios, format_productos_para_respuesta
from .busqueda_kia import buscar_modelos_kia, format_kia_resultados
from .prompt_data import fetch_preguntas_frecuentes, format_preguntas_frecuentes_para_prompt

__all__ = [
    "fetch_contexto_negocio",
    "fetch_horario_reuniones",
    "fetch_nombres_productos_servicios",
    "format_nombres_para_prompt",
    "buscar_productos_servicios",
    "format_productos_para_respuesta",
    "buscar_modelos_kia",
    "format_kia_resultados",
    "fetch_preguntas_frecuentes",
    "format_preguntas_frecuentes_para_prompt",
]
