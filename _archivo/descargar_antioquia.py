"""
Generacion de la capa municipal desde la linea de comandos.
===============================================================================

Descarga los limites municipales oficiales de Colombia y recorta el
departamento indicado, escribiendo `municipios_filtrados.geojson`, que es el
archivo que lee el script de QGIS.

Ejemplos::

    # Ver que departamentos ofrece la cartografia
    python descargar_antioquia.py --listar

    # Generar la capa de un departamento
    python descargar_antioquia.py --departamento Antioquia
    python descargar_antioquia.py --departamento "Valle del Cauca"

QUE SE DESQUEMO
---------------
La version anterior tenia dentro:

    url_municipios = "https://raw.githubusercontent.com/.../co_2018_...geojson"
    gdf[gdf["DPTO_CNMBR"].astype(str).str.upper() == "ANTIOQUIA"]

Es decir: la URL duplicada respecto a `core/dynamic_filter.py`, el nombre del
atributo escrito a mano y el departamento fijado en Antioquia con una igualdad
exacta que fallaba con cualquier nombre acentuado. Todo eso vive ahora en
`config/settings.py` y `core/dynamic_filter.py`; este archivo es solo la
interfaz de linea de comandos.

NOTA SOBRE EL NOMBRE DEL ARCHIVO
--------------------------------
Se conserva el nombre `descargar_antioquia.py` para no romper enlaces ni
documentacion previa, pero el script ya no tiene nada especifico de Antioquia.
"""

import argparse
import sys

from core.dynamic_filter import (
    generar_capa_municipal,
    listar_departamentos_cartografia,
)


def _progreso_consola(mensaje: str, fraccion: float) -> None:
    """
    Muestra el avance en una linea de la terminal.

    Args:
        mensaje: Texto del paso actual.
        fraccion: Avance entre 0.0 y 1.0.
    """
    sys.stdout.write(f"\r  {fraccion * 100:5.1f}%  {mensaje[:70]:<70}")
    sys.stdout.flush()


def main():
    """
    Punto de entrada de linea de comandos.

    Sin argumentos (o con `--listar`) muestra los departamentos disponibles;
    con `--departamento` genera la capa municipal correspondiente.

    Raises:
        SystemExit: Con codigo 1 si el departamento no existe en la
            cartografia o si la capa nacional no se pudo obtener. El mensaje
            que se imprime antes incluye los nombres validos, para que el
            usuario pueda corregir sin tener que leer el codigo.
    """
    parser = argparse.ArgumentParser(
        description="Genera la capa municipal de un departamento colombiano."
    )
    parser.add_argument("--departamento",
                        help="Departamento a recortar. Admite cualquier "
                             "grafia: se compara ignorando tildes y "
                             "mayusculas.")
    parser.add_argument("--listar", action="store_true",
                        help="Muestra los departamentos disponibles y termina.")
    argumentos = parser.parse_args()

    if argumentos.listar or not argumentos.departamento:
        nombres, campo = listar_departamentos_cartografia(_progreso_consola)
        print()
        if not nombres:
            print("No se pudo leer la cartografia. Revise la conexion a "
                  "internet o indique un archivo local en la variable "
                  "RUTA_MUNICIPIOS_NACIONAL del archivo .env.")
            return

        print(f"Departamentos disponibles (campo '{campo}'):\n")
        for nombre in nombres:
            print(f"  - {nombre}")

        if not argumentos.departamento:
            print("\nUse --departamento NOMBRE para generar la capa.")
        return

    try:
        resultado = generar_capa_municipal(argumentos.departamento,
                                           _progreso_consola)
    except ValueError as exc:
        print(f"\n\n{exc}")
        raise SystemExit(1)
    except RuntimeError as exc:
        print(f"\n\nError: {exc}")
        raise SystemExit(1)

    print("\n")
    print("=" * 70)
    print("CAPA MUNICIPAL GENERADA")
    print("=" * 70)
    print(f"Departamento : {resultado['departamento_oficial']}")
    print(f"Municipios   : {resultado['municipios']}")
    print(f"Archivo      : {resultado['archivo']}")
    print("\nYa puede cargarla en QGIS con el script cargar_capas_qgis.py")


if __name__ == "__main__":
    main()
