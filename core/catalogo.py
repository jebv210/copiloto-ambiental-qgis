"""
Catalogo dinamico: descubre QUE hay dentro del archivo que carga el usuario.
===============================================================================

PROBLEMA QUE RESUELVE
---------------------
La version anterior tenia escritas a mano, dentro de `app.py`, dos listas:

    departamentos = ["ANTIOQUIA", "AMAZONAS", ..., "BOGOTA"]        # 33 valores
    grupos        = ["Todos", "Aves", "Mariposas (Lepidoptera)", ...]

Eso tenia tres defectos graves:

  1. Dos de esos 33 nombres NO existian en la cartografia real
     ("SAN ANDRES" y "BOGOTA" frente a "ARCHIPIELAGO DE SAN ANDRES,
     PROVIDENCIA Y SANTA CATALINA" y "BOGOTA, D.C."), asi que reventaban.
  2. Los grupos taxonomicos estaban duplicados: la lista visible en `app.py` y
     el diccionario de traduccion en `dynamic_filter.py`. Agregar uno en un
     lado y olvidarlo en el otro lo hacia desaparecer en silencio.
  3. Prometian departamentos y especies que el archivo cargado podia no tener.

AHORA
-----
Nada se escribe a mano. Se ABRE el archivo que el usuario cargo, se leen sus
columnas reales y se cuentan sus valores reales. Lo que aparece en los menus
desplegables es, literalmente, lo que hay en los datos.

ESTRATEGIA DE MUESTREO
----------------------
El dataset crudo de GBIF pesa decenas de gigabytes. Leerlo completo solo para
llenar un desplegable seria absurdo. Por eso se explora una MUESTRA (por
defecto los primeros millones de filas) y se avisa en la interfaz cuantas filas
se revisaron. El usuario puede ampliar el muestreo o pedir un escaneo total.

El resultado se guarda en `cache_catalogo.json`, invalidado automaticamente si
el archivo cambia de tamano o fecha de modificacion.
"""

import os
import csv
import json
import time

from config import settings
from core.textos import normalizar, emparejar, resolver_toponimo

# ==============================================================================
# PREFERENCIAS DE AUTODETECCION DE COLUMNAS
# ==============================================================================
# OJO: esto son NOMBRES DE COLUMNA del estandar Darwin Core / GBIF, no valores
# de negocio. No son datos quemados: son pistas para preseleccionar el
# desplegable correcto. El usuario siempre puede elegir otra columna en la
# interfaz, y si el archivo usa nombres distintos el sistema simplemente no
# preselecciona nada y deja que el usuario decida.

#: Columnas candidatas a contener la division politica (departamento/estado).
COLUMNAS_GEOGRAFICAS_PROBABLES = [
    "stateProvince", "state", "province", "departamento", "departament",
    "adm1", "region",
]

#: Columnas candidatas a contener el rango taxonomico, de mas general a mas
#: especifico. El orden importa: se ofrece primero la mas util para agrupar.
COLUMNAS_TAXONOMICAS_PROBABLES = [
    "class", "order", "family", "kingdom", "phylum", "genus", "species",
    "clase", "orden", "familia", "reino",
]

#: Rangos taxonomicos que se exploran a la vez para armar el menu de especies.
#:
#: POR QUE VARIOS Y NO UNO:
#: los grupos con los que trabaja la gente NO viven todos en el mismo rango.
#: "Aves" es una CLASE, "Lepidoptera" (las mariposas) es un ORDEN y "Plantae"
#: es un REINO. Si se obligara a elegir una sola columna, quien fijara `class`
#: se quedaria sin poder filtrar mariposas, y quien fijara `order` se quedaria
#: sin las plantas.
#:
#: Explorandolos todos en la misma pasada de lectura se ofrece un unico menu
#: con todos los grupos reales del archivo, y el sistema sabe por si mismo en
#: que columna filtrar cada uno.
#:
#: `family` y `genus` se dejan fuera a proposito: un archivo mundial de GBIF
#: tiene decenas de miles de familias y generos, y meterlos en el desplegable
#: lo haria inservible. Se pueden activar en las opciones avanzadas.
RANGOS_TAXONOMICOS = ["kingdom", "phylum", "class", "order"]

#: Nombre en espanol de cada rango, para mostrarlo junto al grupo.
NOMBRE_RANGO = {
    "kingdom": "reino",
    "phylum": "filo",
    "class": "clase",
    "order": "orden",
    "family": "familia",
    "genus": "genero",
    "species": "especie",
}

#: Columnas candidatas a contener las coordenadas. Se usan solo para informar
#: al usuario si el archivo servira o no para generar puntos en QGIS.
COLUMNAS_LATITUD_PROBABLES = ["decimalLatitude", "latitude", "lat", "latitud"]
COLUMNAS_LONGITUD_PROBABLES = ["decimalLongitude", "longitude", "lon", "lng",
                               "longitud"]

#: Columnas candidatas a contener el pais como CODIGO ISO de dos letras ("CO").
#: Solo se aceptan columnas de codigo, no de nombre ("country" = "Colombia"),
#: para que el valor con el que se compara sea siempre del mismo tipo. En las
#: descargas de GBIF la columna es `countryCode` (termino Darwin Core vigente).
COLUMNAS_PAIS_PROBABLES = ["countryCode", "country_code", "codigoPais",
                           "codigo_pais"]

#: Delimitadores que se prueban al analizar un archivo tabular desconocido.
DELIMITADORES_POSIBLES = ["\t", ",", ";", "|"]


# ==============================================================================
# 1. INSPECCION DEL ARCHIVO
# ==============================================================================

def detectar_delimitador(ruta: str, muestra_bytes: int = 262144) -> str:
    """
    Deduce con que caracter estan separadas las columnas del archivo.

    Antes esto estaba quemado como `sep="\\t"`, aunque el selector de archivos
    de la interfaz permite elegir `.csv`, `.tsv` y `.txt`. Un CSV separado por
    comas se leia como una unica columna gigante y el filtrado fallaba con un
    KeyError incomprensible.

    Metodo: se lee una muestra del inicio del archivo y se prueba cada
    delimitador candidato; gana el que produzca mas columnas en la primera
    linea, siempre que ese mismo numero se repita en las lineas siguientes
    (consistencia). Se apoya en `csv.Sniffer` como primera opcion.

    Args:
        ruta: Ruta al archivo tabular.
        muestra_bytes: Cuantos bytes leer del inicio para decidir.

    Returns:
        El caracter delimitador detectado. Si no logra decidir, devuelve "\\t",
        que es el formato en que GBIF entrega sus descargas.
    """
    try:
        with open(ruta, "r", encoding="utf-8", errors="replace") as f:
            muestra = f.read(muestra_bytes)
    except OSError:
        return "\t"

    if not muestra:
        return "\t"

    # Intento 1: el detector de la libreria estandar.
    try:
        dialecto = csv.Sniffer().sniff(muestra, delimiters="".join(DELIMITADORES_POSIBLES))
        if dialecto.delimiter in DELIMITADORES_POSIBLES:
            return dialecto.delimiter
    except csv.Error:
        pass

    # Intento 2: conteo manual con verificacion de consistencia entre lineas.
    lineas = [l for l in muestra.splitlines() if l.strip()][:20]
    if not lineas:
        return "\t"

    mejor, mejor_columnas = "\t", 1
    for delim in DELIMITADORES_POSIBLES:
        conteos = [len(l.split(delim)) for l in lineas]
        if conteos[0] <= 1:
            continue
        # Consistente = al menos el 80% de las lineas tienen igual numero de
        # columnas que la cabecera.
        iguales = sum(1 for c in conteos if c == conteos[0])
        if iguales / len(conteos) >= 0.8 and conteos[0] > mejor_columnas:
            mejor, mejor_columnas = delim, conteos[0]

    return mejor


def leer_encabezado(ruta: str, delimitador: str = None):
    """
    Lee unicamente la primera linea del archivo y devuelve sus columnas.

    Es una operacion instantanea aunque el archivo pese 99 GB, porque no carga
    nada mas que la cabecera.

    Args:
        ruta: Ruta al archivo tabular.
        delimitador: Separador a usar. Si es None se detecta automaticamente.

    Returns:
        Tupla `(columnas, delimitador)` donde `columnas` es una lista de str.

    Raises:
        FileNotFoundError: Si el archivo no existe.
    """
    if not os.path.exists(ruta):
        raise FileNotFoundError(f"No existe el archivo: {ruta}")

    if delimitador is None:
        delimitador = detectar_delimitador(ruta)

    with open(ruta, "r", encoding="utf-8", errors="replace", newline="") as f:
        primera = f.readline()

    columnas = [c.strip().strip('"').strip("'")
                for c in primera.rstrip("\r\n").split(delimitador)]
    return [c for c in columnas if c], delimitador


def estimar_filas(ruta: str, muestra_bytes: int = 5242880) -> int:
    """
    Estima cuantas filas de datos tiene el archivo sin recorrerlo entero.

    Antes el total estaba quemado como `161369380.0` dentro del calculo de la
    barra de progreso. Ese numero corresponde a UNA descarga concreta de GBIF;
    con cualquier otro archivo la barra mostraba porcentajes sin sentido.

    Metodo: se leen los primeros MB, se cuenta cuantas lineas caben y se
    extrapola al tamano total. Es una estimacion, no un conteo exacto, y asi se
    reporta en la interfaz.

    Args:
        ruta: Ruta al archivo tabular.
        muestra_bytes: Bytes a leer para la extrapolacion.

    Returns:
        Numero estimado de filas de datos (sin contar la cabecera). 0 si falla.
    """
    try:
        tamano_total = os.path.getsize(ruta)
        if tamano_total == 0:
            return 0

        with open(ruta, "rb") as f:
            muestra = f.read(min(muestra_bytes, tamano_total))

        lineas_muestra = muestra.count(b"\n")
        if lineas_muestra == 0:
            return 0

        bytes_por_linea = len(muestra) / lineas_muestra
        estimado = int(tamano_total / bytes_por_linea) - 1  # -1 por la cabecera
        return max(estimado, 0)
    except OSError:
        return 0


def formatear_tamano(bytes_totales: int) -> str:
    """
    Convierte un tamano en bytes a texto legible (KB / MB / GB / TB).

    Args:
        bytes_totales: Tamano en bytes.

    Returns:
        Cadena como "99.5 GB".
    """
    unidad = float(bytes_totales)
    for sufijo in ("B", "KB", "MB", "GB", "TB"):
        if unidad < 1024 or sufijo == "TB":
            return f"{unidad:.1f} {sufijo}"
        unidad /= 1024
    return f"{unidad:.1f} TB"


def sugerir_columna(columnas, candidatas):
    """
    Elige, de entre las columnas reales del archivo, la primera que coincida
    con una lista de nombres probables.

    La comparacion ignora mayusculas y tildes, asi que funciona igual con
    `stateProvince`, `StateProvince` o `state_province`.

    Args:
        columnas: Columnas reales presentes en el archivo.
        candidatas: Nombres probables, en orden de preferencia.

    Returns:
        El nombre real de la columna elegida, o None si ninguna coincide.
    """
    indice = {normalizar(c).replace(" ", ""): c for c in columnas}
    for candidata in candidatas:
        clave = normalizar(candidata).replace(" ", "")
        if clave in indice:
            return indice[clave]
    return None


def describir_dataset(ruta: str) -> dict:
    """
    Genera la ficha tecnica completa del archivo cargado por el usuario.

    Es lo que la interfaz muestra apenas se selecciona un archivo, para que la
    persona confirme "si, este es mi archivo y tiene esta cantidad de datos"
    ANTES de lanzar un proceso que puede tardar horas.

    Args:
        ruta: Ruta al archivo tabular.

    Returns:
        Diccionario con:
            ruta                 : ruta absoluta.
            nombre               : nombre del archivo.
            tamano_bytes         : tamano exacto en bytes.
            tamano_legible       : tamano formateado ("99.5 GB").
            delimitador          : separador detectado.
            delimitador_nombre   : nombre legible del separador.
            columnas             : lista de columnas reales.
            total_columnas       : cuantas columnas hay.
            filas_estimadas      : estimacion de filas de datos.
            columna_geografica   : sugerencia de columna de departamento.
            columna_taxonomica   : sugerencia de columna de taxon.
            columna_latitud      : sugerencia de columna de latitud.
            columna_longitud     : sugerencia de columna de longitud.
            apto_para_qgis       : True si tiene ambas coordenadas.

    Raises:
        FileNotFoundError: Si el archivo no existe.
    """
    if not os.path.exists(ruta):
        raise FileNotFoundError(f"No existe el archivo: {ruta}")

    columnas, delimitador = leer_encabezado(ruta)
    nombres_delimitador = {"\t": "tabulacion", ",": "coma", ";": "punto y coma",
                           "|": "barra vertical"}

    col_lat = sugerir_columna(columnas, COLUMNAS_LATITUD_PROBABLES)
    col_lon = sugerir_columna(columnas, COLUMNAS_LONGITUD_PROBABLES)

    return {
        "ruta": os.path.abspath(ruta),
        "nombre": os.path.basename(ruta),
        "tamano_bytes": os.path.getsize(ruta),
        "tamano_legible": formatear_tamano(os.path.getsize(ruta)),
        "delimitador": delimitador,
        "delimitador_nombre": nombres_delimitador.get(delimitador, repr(delimitador)),
        "columnas": columnas,
        "total_columnas": len(columnas),
        "filas_estimadas": estimar_filas(ruta),
        "columna_geografica": sugerir_columna(columnas, COLUMNAS_GEOGRAFICAS_PROBABLES),
        "columna_taxonomica": sugerir_columna(columnas, COLUMNAS_TAXONOMICAS_PROBABLES),
        "columna_latitud": col_lat,
        "columna_longitud": col_lon,
        "apto_para_qgis": bool(col_lat and col_lon),
    }


# ==============================================================================
# 2. EXPLORACION DE VALORES REALES
# ==============================================================================

def explorar_valores(ruta: str, columnas, filas_max: int = 2000000,
                     delimitador: str = None, tamano_bloque: int = 250000,
                     progreso=None) -> dict:
    """
    Recorre una muestra del archivo y cuenta los valores distintos por columna.

    Este es el corazon del catalogo dinamico: de aqui salen los departamentos y
    los grupos taxonomicos que se ofrecen en la interfaz. Ninguno esta escrito
    en el codigo; todos vienen de los datos.

    Se lee por bloques (`chunksize`) para que la memoria no dependa del tamano
    del archivo, y solo se cargan las columnas pedidas (`usecols`), lo que en un
    archivo de 250 columnas reduce el trabajo en mas de un 99%.

    Args:
        ruta: Ruta al archivo tabular.
        columnas: Lista de columnas cuyos valores se quieren catalogar.
        filas_max: Tope de filas a leer. Usar 0 o None para leer el archivo
            completo (puede tardar horas en un dataset de decenas de GB).
        delimitador: Separador. Si es None se detecta automaticamente.
        tamano_bloque: Filas por bloque de lectura.
        progreso: Callback opcional `f(mensaje: str, fraccion: float)` para
            alimentar la barra de progreso de la interfaz.

    Returns:
        Diccionario con:
            valores        : {columna: {valor: cantidad}} ordenado desc.
            filas_leidas   : cuantas filas se alcanzaron a revisar.
            completo       : True si se leyo el archivo entero.
            segundos       : duracion del escaneo.

    Raises:
        FileNotFoundError: Si el archivo no existe.
        ValueError: Si ninguna de las columnas pedidas existe en el archivo.
    """
    import pandas as pd
    from collections import Counter

    if not os.path.exists(ruta):
        raise FileNotFoundError(f"No existe el archivo: {ruta}")

    disponibles, delimitador_detectado = leer_encabezado(ruta, delimitador)
    delimitador = delimitador or delimitador_detectado

    columnas_validas = [c for c in columnas if c in disponibles]
    if not columnas_validas:
        raise ValueError(
            f"Ninguna de las columnas {list(columnas)} existe en el archivo. "
            f"Columnas disponibles: {disponibles[:15]}..."
        )

    contadores = {c: Counter() for c in columnas_validas}
    filas_leidas = 0
    inicio = time.time()

    filas_estimadas = estimar_filas(ruta) or 1
    objetivo = filas_max if filas_max else filas_estimadas

    lector = pd.read_csv(
        ruta,
        sep=delimitador,
        usecols=columnas_validas,
        dtype=str,             # todo como texto: evita que pandas infiera mal
        chunksize=tamano_bloque,
        low_memory=False,
        on_bad_lines="skip",   # una fila corrupta no debe abortar el escaneo
        encoding="utf-8",
        encoding_errors="replace",
    )

    completo = True
    for bloque in lector:
        for columna in columnas_validas:
            serie = bloque[columna].dropna()
            serie = serie[serie.astype(str).str.strip() != ""]
            contadores[columna].update(serie.astype(str).str.strip().tolist())

        filas_leidas += len(bloque)

        if progreso:
            fraccion = min(filas_leidas / max(objetivo, 1), 0.99)
            progreso(
                f"Explorando el archivo: {filas_leidas:,} filas revisadas".replace(",", "."),
                fraccion,
            )

        if filas_max and filas_leidas >= filas_max:
            completo = filas_leidas >= filas_estimadas
            break

    if progreso:
        progreso(f"Exploracion terminada: {filas_leidas:,} filas".replace(",", "."), 1.0)

    return {
        "valores": {
            columna: dict(contador.most_common())
            for columna, contador in contadores.items()
        },
        "filas_leidas": filas_leidas,
        "completo": completo,
        "segundos": round(time.time() - inicio, 1),
    }


def agrupar_variantes(conteos: dict):
    """
    Agrupa las distintas grafias de un mismo valor en una sola opcion de menu.

    En un dataset real, el mismo departamento aparece escrito de varias formas:
    "Antioquia", "ANTIOQUIA", "Antioquía". Si se ofrecieran las tres por
    separado, el usuario elegiria una y perderia silenciosamente los registros
    de las otras dos.

    Aqui se agrupan por su forma normalizada, se muestra como etiqueta la
    grafia mas frecuente (la mas representativa de los datos) y se conservan
    TODAS las variantes para que el filtro las incluya a todas.

    Args:
        conteos: Diccionario {valor_literal: cantidad_de_filas}.

    Returns:
        Lista de diccionarios ordenada de mayor a menor frecuencia:
            etiqueta  : grafia mas frecuente, la que se muestra en el menu.
            total     : suma de filas de todas las variantes.
            variantes : lista de grafias literales a usar en el filtro.

    Ejemplo:
        >>> agrupar_variantes({"Antioquia": 90, "ANTIOQUIA": 10})
        [{'etiqueta': 'Antioquia', 'total': 100, 'variantes': ['Antioquia', 'ANTIOQUIA']}]
    """
    grupos = {}
    for valor, cantidad in (conteos or {}).items():
        clave = normalizar(valor)
        if not clave:
            continue
        grupo = grupos.setdefault(clave, {"total": 0, "variantes": {}})
        grupo["total"] += int(cantidad)
        grupo["variantes"][valor] = grupo["variantes"].get(valor, 0) + int(cantidad)

    resultado = []
    for grupo in grupos.values():
        # La etiqueta es la grafia que mas veces aparece en los datos reales.
        etiqueta = max(grupo["variantes"].items(), key=lambda par: par[1])[0]
        resultado.append({
            "etiqueta": etiqueta,
            "total": grupo["total"],
            "variantes": sorted(grupo["variantes"],
                                key=lambda v: -grupo["variantes"][v]),
        })

    resultado.sort(key=lambda g: (-g["total"], g["etiqueta"]))
    return resultado


def filtrar_por_referencia(grupos, nombres_referencia):
    """
    Deja solo los valores que corresponden a una lista oficial de referencia.

    PARA QUE SIRVE:
    una descarga de GBIF es MUNDIAL. Su columna `stateProvince` trae millares
    de regiones de todo el planeta: California, Texas, Ontario, England... Un
    desplegable con todas ellas es inservible en un proyecto sobre Colombia.

    Aqui se cruzan los valores encontrados en el archivo contra los nombres
    oficiales de la cartografia colombiana, y se conservan unicamente los que
    corresponden a un departamento real del pais.

    Notese que esto NO es volver a una lista escrita a mano: la referencia se
    lee de `colombia_departamentos.geojson`. Cambiando esa capa, cambia el
    ambito geografico del sistema sin tocar una linea de codigo.

    Ademas fusiona los valores que apunten al mismo departamento. Si el archivo
    trae "Antioquia" y "Antioquia Department" por separado, se convierten en
    una unica opcion cuyo filtro incluye las dos grafias, de modo que no se
    pierde ni un registro.

    Args:
        grupos: Salida de `agrupar_variantes`.
        nombres_referencia: Nombres oficiales admitidos. Si va vacia, se
            devuelven los grupos tal cual (no hay con que cruzar).

    Returns:
        Lista de diccionarios ordenada de mayor a menor numero de registros:
            etiqueta        : nombre OFICIAL del departamento.
            total           : registros sumados de todas sus grafias.
            variantes       : grafias literales del archivo, para el filtro.
            etiquetas_datos : como aparecia escrito en el archivo.

    Ejemplo:
        >>> g = [{"etiqueta": "Antioquia", "total": 9, "variantes": ["Antioquia"]},
        ...      {"etiqueta": "California", "total": 5, "variantes": ["California"]}]
        >>> [x["etiqueta"] for x in filtrar_por_referencia(g, ["ANTIOQUIA"])]
        ['ANTIOQUIA']
    """
    if not nombres_referencia:
        return list(grupos)

    fusionados = {}

    for grupo in grupos:
        # Palabras completas, no trozos: con `emparejar`, "Coahuila"
        # (Mexico) aparecia en el menu como HUILA. Ver resolver_toponimo.
        oficial = resolver_toponimo(grupo["etiqueta"], nombres_referencia)
        if not oficial:
            continue

        acumulado = fusionados.setdefault(oficial, {
            "etiqueta": oficial,
            "total": 0,
            "variantes": [],
            "etiquetas_datos": [],
        })
        acumulado["total"] += grupo["total"]
        acumulado["etiquetas_datos"].append(grupo["etiqueta"])
        for variante in grupo["variantes"]:
            if variante not in acumulado["variantes"]:
                acumulado["variantes"].append(variante)

    resultado = list(fusionados.values())
    resultado.sort(key=lambda g: (-g["total"], g["etiqueta"]))
    return resultado


def columnas_taxonomicas_presentes(columnas, rangos=None):
    """
    Devuelve que columnas de rango taxonomico existen realmente en el archivo.

    Args:
        columnas: Columnas reales del archivo.
        rangos: Rangos a buscar. Por defecto, `RANGOS_TAXONOMICOS`.

    Returns:
        Lista de nombres reales de columna, en orden de mas general a mas
        especifico. Vacia si el archivo no trae informacion taxonomica.
    """
    encontradas = []
    for rango in (rangos or RANGOS_TAXONOMICOS):
        real = sugerir_columna(columnas, [rango])
        if real and real not in encontradas:
            encontradas.append(real)
    return encontradas


def combinar_taxones(valores: dict, columnas_orden=None):
    """
    Funde los valores de varios rangos taxonomicos en un solo menu.

    Cada opcion recuerda de que columna salio, de modo que al filtrar el
    sistema sabe si "Lepidoptera" hay que buscarlo en `order` y "Aves" en
    `class`, sin que el usuario tenga que saberlo ni elegirlo.

    Args:
        valores: Diccionario `{columna: {valor: cantidad}}`, tal como lo
            devuelve `explorar_valores` en su clave `valores`.
        columnas_orden: Columnas a incluir y en que orden de preferencia. Por
            defecto, todas las que traiga `valores`.

    Returns:
        Lista de diccionarios ordenada de mayor a menor numero de registros:
            etiqueta  : el taxon tal como aparece en los datos.
            rango     : nombre del rango en espanol ("clase", "orden"...).
            columna   : columna real donde filtrar.
            total     : registros que tiene.
            variantes : grafias literales a usar en el filtro.

    Ejemplo:
        >>> combinar_taxones({"class": {"Aves": 5}, "order": {"Lepidoptera": 9}})[0]["etiqueta"]
        'Lepidoptera'
    """
    columnas = columnas_orden or list(valores.keys())
    combinados = []

    for columna in columnas:
        conteos = valores.get(columna) or {}
        if not conteos:
            continue

        # El nombre en espanol se busca por el nombre normalizado de la
        # columna, para que funcione igual con "class", "Class" o "clase".
        clave = normalizar(columna).lower().replace(" ", "")
        rango = NOMBRE_RANGO.get(clave, columna)

        for grupo in agrupar_variantes(conteos):
            combinados.append({
                "etiqueta": grupo["etiqueta"],
                "rango": rango,
                "columna": columna,
                "total": grupo["total"],
                "variantes": grupo["variantes"],
            })

    combinados.sort(key=lambda g: (-g["total"], g["etiqueta"]))
    return combinados


# ==============================================================================
# 3. VALORES DE UNA CAPA GEOGRAFICA
# ==============================================================================

def propiedades_geojson(origen: str):
    """
    Lee los atributos de un GeoJSON local SIN cargar las geometrias.

    POR QUE EXISTE ESTE ATAJO:
    para llenar un desplegable con nombres de departamento no hace falta ni una
    sola coordenada. Ademas, `geopandas.read_file` necesita que PROJ y GDAL
    tengan sus variables de entorno bien puestas; cuando el proyecto se ejecuta
    fuera del lanzador de QGIS, eso falla con un `DataDirError` y la lista de
    departamentos quedaba vacia sin explicacion.

    Leyendo el GeoJSON como JSON plano se evita esa dependencia por completo:
    los nombres oficiales se obtienen siempre, haya o no entorno geoespacial.

    Args:
        origen: Ruta a un archivo `.geojson` o `.json` local.

    Returns:
        Lista de diccionarios de propiedades (una por entidad). Lista vacia si
        el archivo no es un GeoJSON local legible.
    """
    if str(origen).lower().startswith(("http://", "https://")):
        return []
    if not str(origen).lower().endswith((".geojson", ".json")):
        return []
    if not os.path.exists(origen):
        return []

    try:
        with open(origen, "r", encoding="utf-8", errors="replace") as f:
            contenido = json.load(f)
    except (OSError, json.JSONDecodeError, MemoryError):
        return []

    entidades = contenido.get("features")
    if not isinstance(entidades, list):
        return []

    return [
        e.get("properties") or {}
        for e in entidades
        if isinstance(e, dict)
    ]


def valores_unicos_capa(origen: str, campo: str):
    """
    Lee un archivo geografico y devuelve los valores unicos de un campo.

    Se usa para obtener los nombres OFICIALES de departamento tal como los
    escribe la cartografia. Comparar contra estos valores (en vez de contra una
    lista escrita a mano) es lo que evita el error de "SAN ANDRES", que no
    existe con ese nombre en la cartografia del DANE.

    Intenta primero la lectura como JSON plano (rapida y sin dependencias) y
    solo recurre a geopandas si el archivo no es un GeoJSON local.

    Args:
        origen: Ruta local o URL de la capa.
        campo: Nombre del atributo a extraer (por ejemplo "DPTO_CNMBR").

    Returns:
        Lista de valores unicos ordenada. Lista vacia si el campo no existe o
        la capa no se puede leer.
    """
    propiedades = propiedades_geojson(origen)
    if propiedades:
        valores = {
            str(p[campo]).strip()
            for p in propiedades
            if isinstance(p, dict) and p.get(campo) is not None
            and str(p[campo]).strip()
        }
        if valores:
            return sorted(valores)

    try:
        import geopandas as gpd
        gdf = gpd.read_file(origen)
    except Exception:
        return []

    if campo not in gdf.columns:
        return []

    valores = gdf[campo].dropna().astype(str).str.strip()
    return sorted({v for v in valores if v})


def detectar_campo_departamento(origen: str):
    """
    Descubre cual es el atributo de departamento dentro de una capa geografica.

    Prueba nombres probables (`DPTO_CNMBR`, `departamento`, ...) contra las
    columnas reales de la capa. Si ninguno coincide, devuelve None y quien
    llame debe pedirle al usuario que lo elija.

    Args:
        origen: Ruta local o URL de la capa.

    Returns:
        Nombre del campo, o None.
    """
    columnas = []

    propiedades = propiedades_geojson(origen)
    if propiedades:
        columnas = list(propiedades[0].keys())

    if not columnas:
        try:
            import geopandas as gpd
            columnas = list(gpd.read_file(origen, rows=1).columns)
        except Exception:
            return None

    return sugerir_columna(
        columnas,
        ["DPTO_CNMBR", "NOMBRE_DPT", "departamento", "dpto", "NAME_1", "state"],
    )


# ==============================================================================
# 4. CACHE EN DISCO
# ==============================================================================

def _firma_archivo(ruta: str) -> str:
    """
    Genera una huella barata del archivo para invalidar la cache.

    No se calcula un hash del contenido (leer 99 GB para eso seria absurdo):
    basta con tamano + fecha de modificacion. Si el usuario reemplaza el
    archivo, cualquiera de los dos cambia y la cache se descarta sola.

    Args:
        ruta: Ruta al archivo.

    Returns:
        Cadena "tamano-mtime", o "" si el archivo no es accesible.
    """
    try:
        st = os.stat(ruta)
        return f"{st.st_size}-{int(st.st_mtime)}"
    except OSError:
        return ""


def guardar_cache(ruta_dataset: str, datos: dict) -> None:
    """
    Persiste el catalogo descubierto para no re-escanear en el proximo arranque.

    Args:
        ruta_dataset: Archivo al que corresponde el catalogo.
        datos: Resultado de `explorar_valores` mas metadatos.
    """
    destino = settings.ruta_proyecto(settings.ARCHIVO_CACHE_CATALOGO)
    contenido = {
        "dataset": os.path.abspath(ruta_dataset),
        "firma": _firma_archivo(ruta_dataset),
        "guardado": time.strftime("%Y-%m-%d %H:%M:%S"),
        "datos": datos,
    }
    try:
        with open(destino, "w", encoding="utf-8") as f:
            json.dump(contenido, f, ensure_ascii=False)
    except OSError:
        # Que no se pueda escribir la cache no es motivo para fallar: solo
        # significa que el proximo escaneo sera igual de lento que este.
        pass


def cargar_cache(ruta_dataset: str):
    """
    Recupera el catalogo guardado para un dataset, si sigue siendo valido.

    Args:
        ruta_dataset: Archivo del que se quiere el catalogo.

    Returns:
        El diccionario de `explorar_valores` guardado, o None si no hay cache
        o el archivo cambio desde que se guardo.
    """
    origen = settings.ruta_proyecto(settings.ARCHIVO_CACHE_CATALOGO)
    if not os.path.exists(origen):
        return None

    try:
        with open(origen, "r", encoding="utf-8") as f:
            contenido = json.load(f)
    except (OSError, json.JSONDecodeError):
        return None

    if contenido.get("dataset") != os.path.abspath(ruta_dataset):
        return None
    if contenido.get("firma") != _firma_archivo(ruta_dataset):
        return None

    return contenido.get("datos")
