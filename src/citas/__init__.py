"""
Agente especializado en citas - MaravIA

LangChain 1.2+ API Moderna

Sistema mejorado con:
- LangChain 1.2+ API moderna con create_agent
- Memoria automática con checkpointer
- Runtime context para tools
- Logging centralizado
- Performance async (httpx)
- Cache global con TTL
- Validación de datos con Pydantic
- Métricas y observabilidad (Prometheus)
"""

from importlib.metadata import version as _pkg_version

__version__ = _pkg_version("citas")
__author__ = "Ariel Amado Frias Rojas"
