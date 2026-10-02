"""
Filtrado del dataset de biodiversidad desde la linea de comandos.
===============================================================================

Hace lo mismo que la pestana "Area de Estudio" de la interfaz web, pero sin
navegador. Util para procesar un archivo muy grande dejandolo corriendo en una
terminal, o para automatizarlo desde otro script.

Ejemplos::

    # Ver que columnas y cuantos registros tiene el archivo
    python filtrar_biodiversidad.py --archivo C:\\datos\\gbif.csv --describir

    # Descubrir que departamentos y que taxones contiene
    python filtrar_biodiversidad.py --archivo C:\\datos\\gbif.csv --explorar

    # Filtrar
    python filtrar_biodiversidad.py --archivo C:\\datos\\gbif.csv ^
        --departamento Antioquia --taxon Lepidoptera --columna-taxon order

QUE SE DESQUEMO
---------------
La version anterior era un script de un solo uso: tenia el nombre del archivo
de GBIF escrito a mano, el separador fijo en tabulador y el departamento fijo
en "Antioquia" dentro de un `str.contains("Antioquia|Antioquía")`. Solo servia
para ese archivo y ese departamento. Ahora todo se pasa por argumentos y, si no
se pasa nada, se descubre.
"""

import argparse
import sys

from config import settings
from core import catalogo
from core.dynamic_filter import ejecutar_filtrado_dinamico


def _progreso_consola(mensaje: str, fraccion: float) -> None:
    """
    Muestra el avance en una sola linea de la terminal.

    Args:
        mensaje: Texto descriptivo del paso actual.
        fraccion: Avance entre 0.0 y 1.0.
    """
    barra_ancho = 30
    llenas = int(fraccion * barra_ancho)
    barra = "#" * llenas + "-" * (barra_ancho - llenas)
    sys.stdout.write(f"\r[{barra}] {fraccion * 100:5.1f}%  {mensaje[:70]:<70}")
    sys.stdout.flush()


def _resolver_archivo(indicado: str) -> str:
    """
    Determina que archivo procesar.

    Args:
        indicado: Ruta pasada por argumento, o None.

    Returns:
        Ruta del archivo a usar.

    Raises:
        SystemExit: Si no se indico ninguno y tampoco se pudo descubrir.
    """
    ruta = indicado or settings.ruta_dataset_biodiversidad()
    if not ruta:
        print(
            "No se indico ningun archivo y no se encontro ninguno "
            "automaticamente.\nUse --archivo RUTA, o defina "
            "DATASET_BIODIVERSIDAD en el archivo .env."
        )
        raise SystemExit(1)
    return ruta


def comando_describir(ruta: str) -> None:
    """
    Imprime la ficha tecnica del archivo: tamano, columnas y filas estimadas.

    Args:
        ruta: Archivo a describir.
    """
    ficha = catalogo.describir_dataset(ruta)
    print(f"Archivo    : {ficha['nombre']}")
    print(f"Ruta       : {ficha['ruta']}")
    print(f"Tamano     : {ficha['tamano_legible']}")
    print(f"Separador  : {ficha['delimitador_nombre']}")
    print(f"Columnas   : {ficha['total_columnas']}")
    print(f"Filas (est): {ficha['filas_estimadas']:,}".replace(",", "."))
    print(f"Apto QGIS  : {'si' if ficha['apto_para_qgis'] else 'no (faltan coordenadas)'}")
    print("\nColumnas sugeridas:")
    print(f"  Departamento : {ficha['columna_geografica']}")
    print(f"  Taxon        : {ficha['columna_taxonomica']}")
    print(f"  Latitud      : {ficha['columna_latitud']}")
    print(f"  Longitud     : {ficha['columna_longitud']}")
    print("\nTodas las columnas:")
    for nombre in ficha["columnas"]:
        print(f"  - {nombre}")


def comando_explorar(ruta: str, columna_geo: str, columna_tax: str,
                     filas: int, tope: int) -> None:
    """
    Recorre una muestra del archivo y lista los valores que contiene.

    Args:
        ruta: Archivo a explorar.
        columna_geo: Columna del departamento.
        columna_tax: Columna del taxon.
        filas: Cuantas filas revisar.
        tope: Cuantos valores mostrar por columna.
    """
    resultado = catalogo.explorar_valores(
        ruta=ruta,
        columnas=[c for c in (columna_geo, columna_tax) if c],
        filas_max=filas,
        progreso=_progreso_consola,
    )
    print()
    catalogo.guardar_cache(ruta, resultado)

    filas_leidas = f"{resultado['filas_leidas']:,}".replace(",", ".")
    print(f"\nFilas revisadas: {filas_leidas} "
          f"en {resultado['segundos']} segundos\n")

    for columna, conteos in resultado["valores"].items():
        grupos = catalogo.agrupar_variantes(conteos)
        print(f"--- {columna} ({len(grupos)} valores distintos) ---")
        for grupo in grupos[:tope]:
            total = f"{grupo['total']:,}".replace(",", ".")
            variantes = (f"  [grafias: {', '.join(grupo['variantes'])}]"
                         if len(grupo["variantes"]) > 1 else "")
            print(f"  {grupo['etiqueta']:<45} {total:>12}{variantes}")
        if len(grupos) > tope:
            print(f"  ... y {len(grupos) - tope} mas (use --tope para ver mas)")
        print()


def comando_filtrar(ruta: str, ficha: dict, argumentos) -> None:
    """
    Ejecuta el filtrado y escribe los archivos que consume QGIS.

    Args:
        ruta: Archivo a filtrar.
        ficha: Ficha tecnica del archivo (de `catalogo.describir_dataset`).
        argumentos: Namespace de argparse con las opciones de la corrida.
    """
    columna_geo = argumentos.columna_departamento or ficha["columna_geografica"]
    columna_tax = argumentos.columna_taxon or ficha["columna_taxonomica"]

    # Se resuelven las grafias reales del valor pedido usando el catalogo, para
    # no perder registros escritos de otra forma ("Antioquia" vs "ANTIOQUIA").
    def _variantes(columna, valor):
        """
        Resuelve todas las grafias con que aparece un valor en el archivo.

        Si el usuario escribe `--departamento Antioquia` pero el archivo trae
        "ANTIOQUIA" y "Antioquía", hay que filtrar por las tres o se pierden
        registros. Las grafias se sacan del catalogo guardado por `--explorar`.

        Args:
            columna: Columna donde buscar. None desactiva el filtro.
            valor: Valor pedido por el usuario. None desactiva el filtro.

        Returns:
            Lista de grafias literales a usar en el filtro, o None si no hay
            filtro que aplicar. Si aun no se ha explorado el archivo, devuelve
            el valor tal cual, y quien lo use deberia ejecutar antes
            `--explorar` para afinar.
        """
        if not valor or not columna:
            return None
        cache = catalogo.cargar_cache(ruta) or {}
        conteos = (cache.get("valores") or {}).get(columna)
        if not conteos:
            # Sin catalogo previo se usa el valor tal cual; el usuario puede
            # ejecutar antes --explorar para afinar.
            return [valor]
        from core.textos import normalizar
        objetivo = normalizar(valor)
        encontradas = [v for v in conteos if normalizar(v) == objetivo]
        return encontradas or [valor]

    resultado = ejecutar_filtrado_dinamico(
        ruta_dataset=ruta,
        departamento=argumentos.departamento if not argumentos.sin_cartografia else None,
        columna_geografica=columna_geo if argumentos.departamento else None,
        valores_geograficos=_variantes(columna_geo, argumentos.departamento),
        columna_taxonomica=columna_tax if argumentos.taxon else None,
        valores_taxonomicos=_variantes(columna_tax, argumentos.taxon),
        delimitador=ficha["delimitador"],
        generar_cartografia=bool(argumentos.departamento
                                 and not argumentos.sin_cartografia),
        filas_max=argumentos.filas_max,
        progreso=_progreso_consola,
    )
    print("\n")

    biodiversidad = resultado["biodiversidad"]
    cartografia = resultado["cartografia"]

    print("=" * 70)
    print("FILTRADO COMPLETADO")
    print("=" * 70)
    print(f"Filas revisadas : {biodiversidad['filas_leidas']:,}".replace(",", "."))
    print(f"Filas guardadas : {biodiversidad['filas_guardadas']:,}".replace(",", "."))
    # Registros que coincidian en departamento y taxon pero eran de otro pais
    # (por ejemplo, el estado brasileno de Amazonas). Se informa para que se
    # vea que el filtro de pais esta actuando y cuanto habria contaminado.
    if biodiversidad.get("filas_otro_pais"):
        print(f"Otros paises    : {biodiversidad['filas_otro_pais']:,} descartadas "
              f"(se conserva {', '.join(biodiversidad.get('pais') or [])})"
              .replace(",", "."))
    print(f"Duracion        : {biodiversidad['segundos']} s")
    print(f"Archivo         : {biodiversidad['archivo']}")

    if cartografia:
        print(f"\nCapa municipal  : {cartografia['municipios']} municipios de "
              f"{cartografia['departamento_oficial']}")
        print(f"Archivo         : {cartografia['archivo']}")

    if biodiversidad["filas_guardadas"] == 0:
        print("\nAVISO: ninguna fila cumplio los filtros. Ejecute --explorar "
              "para ver que valores existen realmente en el archivo.")


def comando_listar_departamentos() -> None:
    """
    Muestra los departamentos que ofrece la cartografia oficial.

    Son los nombres EXACTOS contra los que hay que pedir el recorte. Verlos
    antes evita el error mas comun: escribir "San Andres" cuando la
    cartografia lo llama "ARCHIPIELAGO DE SAN ANDRES, PROVIDENCIA Y SANTA
    CATALINA".
    """
    from core.dynamic_filter import listar_departamentos_cartografia

    nombres, campo = listar_departamentos_cartografia(_progreso_consola)
    print()
    if not nombres:
        print("No se pudo leer la cartografia. Revise la conexion a internet, "
              "o indique un archivo local en RUTA_MUNICIPIOS_NACIONAL (.env).")
        return

    print(f"Departamentos disponibles (campo '{campo}'):\n")
    for nombre in nombres:
        print(f"  - {nombre}")


def comando_solo_cartografia(departamento: str) -> None:
    """
    Genera unicamente la capa municipal, sin tocar el dataset de biodiversidad.

    Util para preparar el mapa de QGIS cuando el filtrado de especies ya se
    hizo, o para probar otro departamento sin volver a recorrer un archivo de
    decenas de GB.

    Args:
        departamento: Departamento a recortar, en cualquier grafia.

    Raises:
        SystemExit: Con codigo 1 si el departamento no existe en la
            cartografia o si la capa nacional no se pudo obtener. El mensaje
            previo lista los nombres validos.
    """
    from core.dynamic_filter import generar_capa_municipal

    try:
        resultado = generar_capa_municipal(departamento, _progreso_consola)
    except (ValueError, RuntimeError) as exc:
        print(f"\n\n{exc}")
        raise SystemExit(1)

    print("\n")
    print("=" * 70)
    print("CAPA MUNICIPAL GENERADA")
    print("=" * 70)
    print(f"Departamento : {resultado['departamento_oficial']}")
    print(f"Municipios   : {resultado['municipios']}")
    print(f"Archivo      : {resultado['archivo']}")


def main():
    """
    Punto de entrada de linea de comandos.

    Reune en un solo lugar las cinco operaciones sobre los datos: describir el
    archivo, explorar sus valores, listar los departamentos de la cartografia,
    generar solo la capa municipal y filtrar el dataset completo.

    Raises:
        SystemExit: Con codigo 1 si no se encuentra el archivo a procesar, o
            si el departamento pedido no existe en la cartografia.
    """
    parser = argparse.ArgumentParser(
        description="Filtra el dataset de biodiversidad y genera las capas de QGIS.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--archivo", help="Ruta del dataset crudo. Si se omite, "
                                          "se busca automaticamente.")
    parser.add_argument("--listar-departamentos", dest="listar_departamentos",
                        action="store_true",
                        help="Lista los departamentos de la cartografia oficial.")
    parser.add_argument("--solo-cartografia", dest="solo_cartografia",
                        action="store_true",
                        help="Genera solo la capa municipal (requiere "
                             "--departamento). No lee el dataset.")
    parser.add_argument("--describir", action="store_true",
                        help="Solo muestra la ficha tecnica del archivo.")
    parser.add_argument("--explorar", action="store_true",
                        help="Solo lista los valores que contiene el archivo.")
    parser.add_argument("--departamento", help="Departamento a conservar.")
    parser.add_argument("--taxon", help="Valor taxonomico a conservar "
                                        "(por ejemplo Lepidoptera o Aves).")
    parser.add_argument("--columna-departamento", dest="columna_departamento",
                        help="Columna del departamento. Se autodetecta.")
    parser.add_argument("--columna-taxon", dest="columna_taxon",
                        help="Columna del taxon. Se autodetecta.")
    parser.add_argument("--filas", type=int, default=2000000,
                        help="Filas a revisar en --explorar. Por defecto 2000000.")
    parser.add_argument("--filas-max", dest="filas_max", type=int, default=0,
                        help="Tope de filas al filtrar. 0 = archivo completo.")
    parser.add_argument("--tope", type=int, default=25,
                        help="Cuantos valores mostrar por columna en --explorar.")
    parser.add_argument("--sin-cartografia", dest="sin_cartografia",
                        action="store_true",
                        help="No genera la capa municipal, solo el CSV filtrado.")

    argumentos = parser.parse_args()

    # Estas dos operaciones no necesitan el dataset, asi que se resuelven antes
    # de intentar localizarlo (que abortaria si no hay ninguno).
    if argumentos.listar_departamentos:
        comando_listar_departamentos()
        return

    if argumentos.solo_cartografia:
        if not argumentos.departamento:
            print("--solo-cartografia necesita ademas --departamento NOMBRE.")
            print("Use --listar-departamentos para ver los nombres validos.")
            return
        comando_solo_cartografia(argumentos.departamento)
        return

    ruta = _resolver_archivo(argumentos.archivo)
    ficha = catalogo.describir_dataset(ruta)

    if argumentos.describir:
        comando_describir(ruta)
        return

    if argumentos.explorar:
        comando_explorar(
            ruta,
            argumentos.columna_departamento or ficha["columna_geografica"],
            argumentos.columna_taxon or ficha["columna_taxonomica"],
            argumentos.filas,
            argumentos.tope,
        )
        return

    if not argumentos.departamento and not argumentos.taxon:
        print("Indique al menos --departamento o --taxon.\n")
        parser.print_help()
        return

    comando_filtrar(ruta, ficha, argumentos)


if __name__ == "__main__":
    main()
