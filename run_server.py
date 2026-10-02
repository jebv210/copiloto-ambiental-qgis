"""
Lanzador del servidor API.
===============================================================================

Existe para que el archivo `.bat` no tenga que conocer el host ni el puerto.
Antes esos valores estaban escritos en tres sitios a la vez (el mensaje del
`.bat`, la linea de uvicorn y la constante `API_URL` de `app.py`), y cambiarlos
obligaba a editar los tres sin olvidarse de ninguno.

Ahora el unico sitio donde se configuran es `config/settings.py` (o el archivo
`.env`), y tanto este lanzador como la interfaz web leen de ahi.

Uso::

    python run_server.py
"""

from config import settings


def main():
    """
    Arranca el servidor FastAPI con la configuracion resuelta del proyecto.

    El modo `reload` se activa solo si la variable de entorno `API_RELOAD` no
    esta puesta en "0", para poder desactivarlo en una demostracion y evitar
    que un guardado accidental reinicie el servidor a mitad de la sustentacion.
    """
    import uvicorn

    host = settings.api_host()
    puerto = settings.api_port()
    recargar = settings.env("API_RELOAD", "1") != "0"

    print("=" * 78)
    print("  SERVIDOR DE API - EVALUACION DE IMPACTO AMBIENTAL (CONESA + QGIS)")
    print("=" * 78)
    print(f"  Servidor      : http://{host}:{puerto}")
    print(f"  Documentacion : http://{host}:{puerto}/docs")
    print(f"  Configuracion : http://{host}:{puerto}/config")
    print()

    # Diagnostico de arranque: se ve de inmediato que encontro y que no, en vez
    # de descubrirlo al primer error de una consulta.
    for concepto, valor in settings.diagnostico().items():
        estado = "OK" if valor else "--"
        print(f"  [{estado}] {concepto}: {valor or 'no disponible'}")

    print()
    print("  Presiona Ctrl+C para detener el servidor.")
    print("=" * 78)

    uvicorn.run("api.server:app", host=host, port=puerto, reload=recargar)


if __name__ == "__main__":
    main()
