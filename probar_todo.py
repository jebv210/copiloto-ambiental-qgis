"""
Lanzador de todas las comprobaciones del proyecto.
===============================================================================

Ejecuta las tres baterias en orden, de lo mas aislado a lo mas integrado, y
resume al final. Se usa asi::

    python probar_todo.py           # todo lo que se pueda comprobar
    python probar_todo.py --rapido  # solo lo que no necesita red ni Docker

El orden importa: si falla el corpus, lo demas falla en cascada y conviene
mirar primero lo de abajo del todo.
"""

import os
import subprocess
import sys


AQUI = os.path.dirname(os.path.abspath(__file__))

#: Cada bateria con lo que comprueba y si necesita servicios levantados.
BATERIAS = [
    ("probar_corpus.py",
     "Corpus documental: troceado, indice, citas y procedencia",
     False),
    ("probar_vectorial.py",
     "Indice vectorial: embeddings, cache, firma del modelo y degradacion",
     True),
    ("probar_despliegue.py",
     "Pila montada: API, Qdrant, n8n y flujos",
     True),
    ("probar_agente.py",
     "Agente: memoria, imagenes, estudio del corpus y portabilidad",
     True),
    ("probar_qgis.py",
     "Script de QGIS: carga de capas y deteccion de coordenadas",
     False),
    ("probar_drive.py",
     "Fuente de Google Drive: carpetas, categorias, imagenes e incremental",
     False),
]


def entorno_hijo() -> dict:
    """
    Prepara el entorno con el que se lanzan las baterias.

    POR QUE HACE FALTA TOCAR EL PATH
    --------------------------------
    El interprete de QGIS encuentra `sqlite3.dll` porque vive en
    `QGIS\\bin`, no dentro del arbol de Python. Al lanzar un subproceso esa
    carpeta no queda en el PATH y el hijo revienta con
    "DLL load failed while importing _sqlite3", aunque el padre importe
    sqlite3 sin problema. Se midio: anadiendo esa carpeta, funciona.

    Tambien se fija la codificacion de la salida: sin ella, los acentos de los
    mensajes rompen la captura en consolas con cp1252.

    Returns:
        Copia del entorno, lista para el subproceso.
    """
    entorno = dict(os.environ)
    entorno["PYTHONIOENCODING"] = "utf-8"

    raiz_qgis = os.path.abspath(os.path.join(sys.prefix, "..", ".."))

    # `QGISin` trae sqlite3.dll. `apps/qgis-ltr/bin` trae las bibliotecas de
    # QGIS en si, que hacen falta para que `probar_qgis.py` pueda importar
    # qgis.core. Sin la segunda, esa bateria se omite en vez de ejecutarse.
    for sufijo in (("bin",), ("apps", "qgis-ltr", "bin"), ("apps", "qgis", "bin")):
        carpeta = os.path.join(raiz_qgis, *sufijo)
        if os.path.isdir(carpeta):
            entorno["PATH"] = carpeta + os.pathsep + entorno.get("PATH", "")

    # GDAL y PROJ buscan sus tablas de datos por variable de entorno. Sin
    # ellas, QGIS arranca pero avisa a gritos por cada reproyeccion.
    for variable, sufijo in (("GDAL_DATA", ("share", "gdal")),
                             ("PROJ_LIB", ("share", "proj"))):
        carpeta = os.path.join(raiz_qgis, *sufijo)
        if variable not in entorno and os.path.isdir(carpeta):
            entorno[variable] = carpeta

    return entorno


def ejecutar(archivo: str) -> tuple:
    """
    Lanza una bateria y devuelve como fue.

    Args:
        archivo: Nombre del script de pruebas.

    Returns:
        Tupla `(codigo, ultima_linea_util)`.
    """
    proceso = subprocess.run(
        [sys.executable, os.path.join(AQUI, archivo)],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        env=entorno_hijo())
    salida = (proceso.stdout or "") + (proceso.stderr or "")

    resumen = ""
    for linea in reversed(salida.splitlines()):
        if "superadas" in linea or "FALLIDAS" in linea:
            resumen = linea.strip()
            break
    if not resumen:
        resumen = "sin resumen; revisa la salida completa"

    if proceso.returncode != 0:
        print(salida[-2500:])
    return proceso.returncode, resumen


def main() -> int:
    """Ejecuta las baterias y devuelve el codigo de salida."""
    rapido = "--rapido" in sys.argv
    resultados = []

    for archivo, descripcion, necesita_servicios in BATERIAS:
        if rapido and necesita_servicios:
            print(f"\n{'=' * 70}\n{archivo}  (omitida por --rapido)\n{'=' * 70}")
            continue
        if not os.path.exists(os.path.join(AQUI, archivo)):
            print(f"\n[!] No se encuentra {archivo}")
            resultados.append((archivo, 1, "archivo ausente"))
            continue

        print(f"\n{'=' * 70}")
        print(f"{archivo}")
        print(f"{descripcion}")
        print("=" * 70)
        codigo, resumen = ejecutar(archivo)
        print(("  OK    " if codigo == 0 else "  FALLA ") + resumen)
        resultados.append((archivo, codigo, resumen))

    print(f"\n{'=' * 70}\nRESUMEN\n{'=' * 70}")
    for archivo, codigo, resumen in resultados:
        marca = "OK   " if codigo == 0 else "FALLA"
        print(f"  [{marca}] {archivo:24s} {resumen}")

    fallidas = [a for a, c, _ in resultados if c != 0]
    if fallidas:
        print(f"\nHay {len(fallidas)} bateria(s) con fallos: {', '.join(fallidas)}")
        return 1
    print("\nTodo en orden.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
