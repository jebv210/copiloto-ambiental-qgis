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


def variantes_de_nombre(nombre: str) -> set:
    """
    Genera las grafias con las que un mismo lugar puede venir escrito.

    POR QUE NO VALE UNA LISTA A MANO
    --------------------------------
    La version anterior de la interfaz llevaba escrito esto:

        if dpto_select == "ANTIOQUIA":
            variantes.update(["Antioquia", "Antioquia", "ANTIOQUIA"])
        elif dpto_select == "BOYACA":
            ...

    Solo cubria tres departamentos de los treinta y tres. Elegir cualquier otro
    con acento -- Narino, Choco, Cordoba, Bolivar, Atlantico... -- perdia en
    silencio todos los registros escritos con la grafia que no estuviera en la
    lista. Aqui las variantes se calculan, asi que valen para cualquier nombre
    y para cualquier pais.

    Args:
        nombre: El nombre tal como se eligio.

    Returns:
        Conjunto con el nombre original y sus variantes de caja y de acentos.
    """
    import unicodedata

    if not nombre:
        return set()

    def sin_tildes(texto):
        """
        Devuelve el texto sin marcas diacriticas.

        Args:
            texto: Texto de entrada.

        Returns:
            El mismo texto sin tildes ni dieresis.
        """
        descompuesto = unicodedata.normalize("NFD", texto)
        return "".join(c for c in descompuesto
                       if unicodedata.category(c) != "Mn")

    semillas = {nombre.strip(), sin_tildes(nombre.strip())}
    variantes = set()
    for semilla in semillas:
        if not semilla:
            continue
        variantes.update({semilla, semilla.upper(), semilla.lower(),
                          semilla.title(), semilla.capitalize()})
    return {v for v in variantes if v}


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


# ==============================================================================
# 4. RESUMEN AGREGADO DE UNA TABLA GRANDE
# ==============================================================================

#: Cuantos valores distintos puede tener una columna para considerarla
#: CATEGORICA. Por encima de esto es un identificador o un texto libre, y
#: agrupar por el daria una linea por fila.
MAXIMO_CATEGORIAS = 60

#: Tope de lineas del resumen. Sin el, una tabla con muchas columnas
#: categoricas produciria un resumen tan largo como el problema que resuelve.
#:
#: POR QUE 1.200 Y NO 400
#: ----------------------
#: Con 400 se medio lo que costaba: la capa de ecosistemas tiene 31 columnas
#: que merecen reparto y solo entraban 26. Las cinco que se quedaban fuera
#: eran no_anfibio, no_aves, no_magnoli, no_mamifer y no_reptile: en un
#: proyecto ambiental, justo las que mas importan. Y se caian sin decirlo.
#:
#: 1.200 cubre esa capa entera con holgura y sigue acotando el caso malo.
#: Cuando aun asi no quepa, el resumen NOMBRA las columnas que faltan en
#: vez de recortarlas en silencio.
MAXIMO_LINEAS_RESUMEN = 1200

#: Nombres que suelen contener una magnitud que conviene sumar.
COLUMNAS_SUMABLES = ("area_ha", "area", "superficie", "hectareas", "ha",
                     "longitud", "length", "shape_area", "area_km2")


def _miles(numero) -> str:
    """
    Escribe un numero con punto de millar, como se usa en Colombia.

    POR QUE UNA FUNCION Y NO UN `.replace`
    --------------------------------------
    Antes el separador se arreglaba aplicando `.replace(",", ".")` a la linea
    entera. Pero los literales de texto adyacentes se pegan ANTES de llamar al
    metodo, asi que el reemplazo alcanzaba tambien las comas de la frase y
    salian resumenes que decian "no se indexa fila a fila. se resume" y
    "24.187 registros. 488.818 area_ha". Formatear solo el numero deja la
    prosa en paz.

    Args:
        numero: Entero o decimal a escribir.

    Returns:
        El numero en texto, con punto cada tres cifras.
    """
    if isinstance(numero, float):
        return f"{numero:,.0f}".replace(",", ".")
    return f"{numero:,}".replace(",", ".")


def _es_numero(valor) -> bool:
    """
    Dice si un valor de texto se puede sumar.

    Args:
        valor: Lo leido de la celda.

    Returns:
        True si se puede convertir a numero.
    """
    try:
        float(valor)
        return True
    except (TypeError, ValueError):
        return False


#: Formatos que se pueden recorrer ENTEROS y resumir agregando, sin gastar
#: cuota del modelo. Es la lista que decide si un archivo grande "entra
#: resumido" o se queda sin analizar.
#:
#: Que un formato este aqui significa que existe un lector que lo recorre
#: fila a fila SIN cargarlo en memoria. Para los de texto es el lector de
#: CSV; para Excel es openpyxl en modo `read_only`, que va devolviendo filas
#: segun las lee del ZIP en vez de construir el libro completo.
FORMATOS_RESUMIBLES = {".csv", ".tsv", ".txt", ".xlsx", ".xlsm", ".xls"}

#: Media maxima de palabras por celda para considerar que algo es una tabla.
#:
#: Se midio sobre los archivos del proyecto: la capa de POMCAS da 1,45
#: palabras por celda (23 columnas) y la de ecosistemas, 418 MB, da 2,01 (48
#: columnas). Un informe de prosa partido por sus comas da 8,00. El 4 queda
#: justo en medio: deja sitio a una columna de observaciones corta y no deja
#: pasar un texto corrido.
MAXIMO_PALABRAS_POR_CELDA = 4.0

#: Version del formato del resumen. Se sube cuando cambia lo que escribe
#: `resumir_tabla`.
#:
#: POR QUE HACE FALTA
#: ------------------
#: El resumen de una capa de 418 MB se guarda en disco para no volver a
#: bajarla cada noche. Pero ese archivo guardado no sabia de que version
#: era, asi que sobrevivia a cualquier arreglo del resumidor. Se vio: tres
#: correcciones -- decir "COMPLETA" solo cuando se leyo completa, avisar al
#: cortar por el tope, y no romper las comas de la frase al poner los
#: puntos de millar -- no llegaban a las cuatro capas del proyecto porque
#: sus resumenes eran del 25 de octubre y nadie los volvia a calcular.
#:
#: Con la version dentro, un resumen viejo se descarta solo y se rehace.
VERSION_RESUMEN = 6

#: Cuantas filas como mucho se recorren de una tabla.
#:
#: POR QUE EXISTE UN TOPE SI LA PROMESA ES "COMPLETO"
#: --------------------------------------------------
#: Porque recorrer cuesta tiempo, no cuota. Se midio sobre la capa real de
#: ecosistemas, de 417,8 MB: 460.350 filas en 27,7 segundos, o sea 16.619
#: filas por segundo y 15 MB/s, con el pico de memoria subiendo solo 67 MB.
#: Un tope existe para que un archivo corrupto o absurdamente grande no deje
#: la sincronizacion nocturna colgada hasta el amanecer.
#:
#: Pero el tope NO miente. Si se alcanza, el resumen lo dice con todas las
#: letras y deja de afirmar que recorrio la tabla entera. Antes decia
#: "COMPLETA" pasara lo que pasara, que es la clase de afirmacion que luego
#: el agente repite como verdad.
#:
#: Se sube con `MAXIMO_FILAS_RESUMEN` en el .env, sin tocar codigo. A la
#: velocidad medida, veinte millones de filas son unos 20 minutos de
#: lectura, que un cron de las 2 de la manana se puede permitir; la capa mas
#: grande del proyecto tiene 460.350 filas, asi que el margen es de
#: cuarenta veces.
MAXIMO_FILAS_RESUMEN = int(os.environ.get("MAXIMO_FILAS_RESUMEN")
                           or 20_000_000)


def parece_tabla(ruta: str) -> bool:
    """
    Dice si un archivo es de verdad una tabla, mirando su contenido.

    POR QUE NO BASTA LA EXTENSION
    -----------------------------
    `.txt` esta en `FORMATOS_RESUMIBLES` porque muchas capas de datos llegan
    con esa extension. Pero un informe de prosa tambien es `.txt`, y
    resumirlo "agrupando por columnas" no tiene ningun sentido: saldria un
    recuento de valores sobre una cabecera que no existe, y el informe
    quedaria descrito como una tabla de una columna. Se vio en la bateria:
    un .txt de 3 MB de prosa se colaba por el desvio de tablas grandes.

    Lo que distingue una tabla no es la extension, es la REGULARIDAD: el
    mismo separador repetido el mismo numero de veces, fila tras fila. Eso
    es lo que se comprueba aqui, sobre el principio del archivo.

    Un libro de Excel se da por tabla sin mirar: es una rejilla de celdas por
    construccion.

    Args:
        ruta: Archivo ya descargado.

    Returns:
        True si sus primeras lineas comparten estructura de columnas.
    """
    extension = os.path.splitext(ruta)[1].lower()
    if extension in (".xlsx", ".xlsm", ".xls"):
        return True

    try:
        with open(ruta, encoding="utf-8-sig", errors="replace") as fh:
            principio = fh.read(64 * 1024)
    except OSError:
        return False

    filas = [f for f in principio.splitlines() if f.strip()][:200]
    # La ultima puede venir cortada por la mitad del bloque leido.
    if len(filas) > 3:
        filas = filas[:-1]
    if len(filas) < 3:
        return False

    for separador in (",", ";", chr(9), "|"):
        cuentas = [f.count(separador) for f in filas]
        con_columnas = [c for c in cuentas if c >= 1]
        if len(con_columnas) < 3:
            continue
        mas_comun = max(set(con_columnas), key=con_columnas.count)
        if con_columnas.count(mas_comun) < max(3, len(filas) * 0.6):
            continue

        # LA REGULARIDAD DEL SEPARADOR NO BASTA
        # -------------------------------------
        # Se midio: un informe de prosa con la misma frase repetida tiene el
        # MISMO numero de comas en cada linea, asi que pasaba este filtro y
        # se resumia como si fuera una tabla.
        #
        # Lo que de verdad separa una celda de una frase es cuanto cabe
        # dentro. Una celda trae un dato -- "El Jordan", "Bosque denso",
        # "12.5" --, entre una y tres palabras. Una oracion partida por sus
        # comas deja trozos de siete u ocho. Se mide eso.
        palabras, campos = 0, 0
        for fila in filas:
            if fila.count(separador) != mas_comun:
                continue
            for campo in fila.split(separador):
                campos += 1
                palabras += len(campo.split())
        if campos and palabras / campos <= MAXIMO_PALABRAS_POR_CELDA:
            return True
    return False


def _filas_de_texto(ruta: str):
    """
    Abre una tabla de texto y devuelve sus columnas y un generador de filas.

    Args:
        ruta: Archivo .csv, .tsv o .txt ya descargado.

    Returns:
        Tupla `(columnas, generador_de_filas, nota)`. Cada fila es un
        diccionario columna -> valor.
    """
    import csv

    columnas, delimitador = leer_encabezado(ruta)

    def filas():
        with open(ruta, encoding="utf-8-sig", errors="replace",
                  newline="") as fh:
            for fila in csv.DictReader(fh, delimiter=delimitador):
                yield fila

    return columnas, filas(), ""


#: Cuantas filas se miran como mucho buscando la cabecera de una hoja.
FILAS_PARA_BUSCAR_CABECERA = 12

#: Celdas con contenido que debe tener una fila para ser cabecera.
#:
#: Una fila de titulo trae UNA celda -- "DISTRIBUCION DE POBLACION Y AREA
#: POR VEREDAS - SAN CARLOS" -- y a veces una segunda de subtitulo. Una
#: cabecera de verdad nombra varias columnas.
MINIMO_CELDAS_CABECERA = 2


def _buscar_cabecera(hoja) -> tuple:
    """
    Localiza en que fila empieza de verdad la tabla de una hoja.

    POR QUE NO VALE LA PRIMERA FILA
    -------------------------------
    Una hoja hecha por una persona casi nunca empieza por la cabecera.
    Se midio en las tablas consolidadas de la cuenca del proyecto:

        fila 1: DISTRIBUCION DE POBLACION Y AREA POR VEREDAS - SAN CARLOS
        fila 2: Tabla procesada de la capa ...
        fila 3: (vacia)
        fila 4: N | Nombre de la Vereda | Poblacion | Area | Densidad

    Tomando la fila 1 como cabecera, la hoja entraba al indice con UNA
    columna llamada como el titulo y ningun dato aprovechable. Cuatro hojas
    del libro estaban asi: el agente las habia "estudiado" y no sabia nada
    de ellas.

    COMO SE DECIDE
    --------------
    La cabecera es la primera fila con al menos dos celdas con contenido
    que vaya SEGUIDA de otra igual de ancha. Lo segundo importa: sin eso,
    un titulo repartido en dos celdas se tomaria por cabecera.

    Args:
        hoja: Hoja abierta de openpyxl en modo lectura.

    Returns:
        Tupla `(numero_de_fila, celdas)`, con la fila en base 1. Si no se
        encuentra nada, `(1, ())`.
    """
    vistas = []
    for numero, fila in enumerate(
            hoja.iter_rows(max_row=FILAS_PARA_BUSCAR_CABECERA,
                           values_only=True), start=1):
        llenas = sum(1 for c in fila if c is not None and str(c).strip())
        vistas.append((numero, fila, llenas))

    for indice, (numero, fila, llenas) in enumerate(vistas):
        if llenas < MINIMO_CELDAS_CABECERA:
            continue
        siguiente = vistas[indice + 1] if indice + 1 < len(vistas) else None
        if siguiente and siguiente[2] >= MINIMO_CELDAS_CABECERA:
            return numero, fila
    # Ninguna fila cumple: se devuelve la primera que tuviera algo, y si no
    # hay ninguna, la hoja esta vacia de verdad.
    for numero, fila, llenas in vistas:
        if llenas:
            return numero, fila
    return 1, ()


def _filas_de_excel(ruta: str):
    """
    Abre un libro de Excel en modo lectura y devuelve columnas y filas.

    POR QUE `read_only`
    -------------------
    Un .xlsx es un ZIP con un XML por hoja. Abrirlo del modo normal construye
    en memoria un objeto por celda: un libro de 400 MB comprimido se convierte
    en varios gigas de RAM y tumba el contenedor. En modo `read_only` openpyxl
    recorre el XML en streaming y va soltando las filas, asi que la memoria no
    depende del tamano del libro.

    POR QUE SE PEGAN LAS HOJAS
    --------------------------
    Un libro suele traer la misma tabla partida por anos o por zonas. Para el
    resumen agregado interesa el total, asi que las filas de todas las hojas
    se tratan como una sola tabla y se anade la columna `__hoja__` para no
    perder de donde salio cada una: asi el reparto se puede pedir por hoja.

    Las columnas se toman de la PRIMERA hoja. Si otra hoja trae columnas
    distintas, sus valores se ignoran en el reparto, pero sus filas se siguen
    contando: el recuento total sigue siendo exacto.

    Args:
        ruta: Libro .xlsx, .xlsm o .xls ya descargado.

    Returns:
        Tupla `(columnas, generador_de_filas, nota)`. La nota avisa si el
        libro traia varias hojas.
    """
    import openpyxl

    libro = openpyxl.load_workbook(ruta, read_only=True, data_only=True)
    hojas = list(libro.sheetnames)

    # CADA HOJA TIENE SU PROPIA CABECERA
    # ----------------------------------
    # Antes se tomaban las columnas de la primera hoja y se aplicaban a
    # todas. En un libro donde cada hoja es la MISMA tabla partida por
    # anos, eso funciona. En el libro real del proyecto no: la hoja 1 es
    # un resumen ejecutivo y la 2 es poblacion por vereda, con columnas
    # distintas. Los nombres de vereda acababan etiquetados como "VALOR
    # CONSOLIDADO", que es peor que no tener etiqueta: es una etiqueta
    # falsa.
    #
    # Ahora la lista de columnas es la UNION de las de todas las hojas, y
    # cada fila trae solo las suyas. Agrupar por una columna que una hoja
    # no tiene simplemente no cuenta sus filas, que es lo correcto.
    cabeceras = {}
    columnas = []
    for nombre_hoja in hojas:
        fila_cab, celdas = _buscar_cabecera(libro[nombre_hoja])
        propias = [str(c).strip() if c is not None else "" for c in celdas]
        propias = [c for c in propias if c]
        cabeceras[nombre_hoja] = (fila_cab, propias)
        for c in propias:
            if c not in columnas:
                columnas.append(c)
    libro.close()

    if not columnas:
        return [], iter(()), "el libro esta vacio"

    def filas():
        # Se vuelve a abrir porque en modo `read_only` el libro no se puede
        # recorrer dos veces: el iterador del XML ya se consumio al leer la
        # cabecera.
        wb = openpyxl.load_workbook(ruta, read_only=True, data_only=True)
        try:
            for nombre_hoja in hojas:
                hoja = wb[nombre_hoja]
                suya, propias = cabeceras[nombre_hoja]
                for numero, valores in enumerate(
                        hoja.iter_rows(values_only=True), start=1):
                    if numero <= suya:
                        continue  # titulo y cabecera no son datos
                    fila = {"__hoja__": nombre_hoja}
                    for columna, valor in zip(propias, valores):
                        fila[columna] = ("" if valor is None
                                         else str(valor).strip())
                    yield fila
        finally:
            wb.close()

    nota = ""
    if len(hojas) > 1:
        nota = (f"El libro trae {len(hojas)} hojas y se recorren todas: "
                + ", ".join(hojas[:8]))
    return ["__hoja__"] + columnas, filas(), nota


def _agregar(columnas: list, filas_iter, maximo_filas: int) -> dict:
    """
    Recorre las filas contando valores por columna y sumando magnitudes.

    Es el nucleo compartido: da igual si las filas vienen de un CSV o de un
    Excel, porque llegan como diccionarios. Anadir un formato nuevo es
    escribir su lector, no repetir este conteo.

    Args:
        columnas: Nombres de columna a vigilar.
        filas_iter: Iterable de diccionarios columna -> valor.
        maximo_filas: Tope de filas a recorrer, por prudencia.

    Returns:
        Diccionario con `conteos`, `sumas`, `filas`, `sumable`, `utiles` y
        `error`.
    """
    from collections import Counter, defaultdict

    conteos = {c: Counter() for c in columnas}
    sumas = {c: defaultdict(float) for c in columnas}
    descartadas = set()
    sumable = next((c for c in columnas
                    if c.strip().lower() in COLUMNAS_SUMABLES), None)

    filas = 0
    truncado = False
    error = ""
    try:
        for fila in filas_iter:
            filas += 1
            if filas > maximo_filas:
                # Se alcanzo el tope. Importa DECIRLO: el resumen afirma
                # haber recorrido la tabla completa, y a partir de aqui
                # dejaria de ser verdad.
                truncado = True
                filas -= 1
                break
            magnitud = 0.0
            if sumable and _es_numero(fila.get(sumable)):
                magnitud = float(fila[sumable])
            for columna in columnas:
                if columna in descartadas:
                    continue
                valor = (fila.get(columna) or "")
                valor = valor.strip() if isinstance(valor, str) else str(valor)
                if not valor:
                    continue
                conteos[columna][valor] += 1
                if magnitud:
                    sumas[columna][valor] += magnitud
                # En cuanto una columna se pasa de categorias deja de
                # mirarse: es un identificador, no una clasificacion.
                if len(conteos[columna]) > MAXIMO_CATEGORIAS:
                    descartadas.add(columna)
                    conteos[columna].clear()
                    sumas[columna].clear()
    except Exception as exc:
        error = str(exc)[:160]

    utiles = [c for c in columnas
              if c not in descartadas
              and 1 < len(conteos[c]) <= MAXIMO_CATEGORIAS]
    return {"conteos": conteos, "sumas": sumas, "filas": filas,
            "sumable": sumable, "utiles": utiles, "error": error,
            "truncado": truncado}


def resumir_tabla(ruta: str, maximo_filas: int = None) -> dict:
    """
    Recorre una tabla entera y la resume agrupando por sus columnas.

    POR QUE RESUMIR EN VEZ DE INDEXAR FILA A FILA
    ---------------------------------------------
    Se midio sobre la capa de ecosistemas del proyecto: 434 MB, 460.350 filas.
    Indexarla entera darian 644.936 fichas, que son 97 horas de proceso y 3,5
    anos de cuota gratuita del modelo. Es imposible, no caro.

    Resumirla cuesta otra cosa: recorrer el archivo completo tarda 6 segundos
    y no gasta NI UN embedding, porque agrupar y sumar es trabajo local. De
    ahi salen unas pocas decenas de fichas que si responden lo que se le
    pregunta a una capa: que ecosistemas hay, cuantas hectareas ocupa cada
    uno, en que zonas hidrograficas.

    Una fila suelta de esa tabla no significa nada para una busqueda por
    significado; el agregado si.

    QUE SIGNIFICA "POR COMPLETO"
    ----------------------------
    Se leen TODAS las filas del archivo, no una muestra. Lo que se recorta es
    el resumen que sale, no la lectura que entra: el recuento de filas y las
    sumas son exactos sobre el archivo entero. Vale igual para un CSV que
    para un libro de Excel con varias hojas.

    Args:
        ruta: Archivo tabular ya descargado (.csv, .tsv, .txt, .xlsx, .xls).
        maximo_filas: Tope de filas a recorrer. Por omision,
            `MAXIMO_FILAS_RESUMEN`, que se ajusta desde el .env.

    Returns:
        Diccionario con `lineas` (el resumen, listo para indexar), `filas`,
        `columnas_resumidas` y `segundos`. Si no se pudo leer, `lineas` viene
        vacia y `error` explica por que.
    """
    import time

    if maximo_filas is None:
        maximo_filas = MAXIMO_FILAS_RESUMEN
    arranque = time.time()
    extension = os.path.splitext(ruta)[1].lower()
    # El nombre del temporal lleva prefijo; para elegir lector hay que mirar
    # la extension real, que en los temporales se conserva al final.
    if extension in (".xlsx", ".xlsm", ".xls"):
        abrir = _filas_de_excel
    else:
        abrir = _filas_de_texto

    def fallo(mensaje, filas=0):
        return {"lineas": [], "filas": filas, "columnas_resumidas": [],
                "segundos": round(time.time() - arranque, 1),
                "error": mensaje[:160]}

    try:
        columnas, filas_iter, nota = abrir(ruta)
    except Exception as exc:
        return fallo(str(exc))

    if not columnas:
        return fallo(nota or "no se encontraron columnas")

    agregado = _agregar(columnas, filas_iter, maximo_filas)
    if agregado["error"] and agregado["filas"] == 0:
        return fallo(agregado["error"])

    filas = agregado["filas"]
    sumable = agregado["sumable"]
    conteos, sumas = agregado["conteos"], agregado["sumas"]

    # El nombre va sin el prefijo del temporal: la ficha la lee una persona y
    # "_resumir_e_eccmc_100K_2024.csv" no es el nombre de su archivo.
    visible = os.path.basename(ruta)
    for marca in ("_resumir_", "_estimar_", "_muestra_"):
        if visible.startswith(marca):
            visible = visible[len(marca):]
    if agregado["truncado"]:
        # Nunca se dice "completa" si no se leyo completa. El tope existe por
        # prudencia, pero afirmar lo que no se ha hecho es peor que el tope.
        alcance = (f"Se leyeron las primeras {_miles(filas)} filas y ahi se "
                   f"corto por el tope de prudencia: el reparto de abajo NO "
                   f"cubre la tabla entera.")
    else:
        alcance = ("Se recorrio COMPLETA; no se indexa fila a fila, se "
                   "resume agrupando.")
    lineas = [f"[Resumen de {visible}]",
              f"Tabla de {_miles(filas)} filas y {len(columnas)} columnas. "
              f"{alcance}"]
    if nota:
        lineas.append(nota)
    if sumable:
        lineas.append(f"Las cantidades se suman sobre la columna '{sumable}'.")

    # UNA COLUMNA EMPEZADA NO ES UNA COLUMNA CONTADA
    # ----------------------------------------------
    # El primer intento apuntaba la columna como escrita nada mas poner su
    # cabecera. Pero el tope puede caer a mitad de sus valores, y entonces
    # queda un "Reparto por X:" con tres de sus cuarenta categorias y nadie
    # avisa. Media verdad presentada como entera es peor que un hueco
    # declarado. Solo cuenta como completa la que escribio TODOS sus
    # valores.
    completas = []
    for columna in agregado["utiles"]:
        if len(lineas) >= MAXIMO_LINEAS_RESUMEN:
            break
        lineas.append("")
        lineas.append(f"Reparto por {columna}:")
        cortada = False
        for valor, cuantas in conteos[columna].most_common(MAXIMO_CATEGORIAS):
            if len(lineas) >= MAXIMO_LINEAS_RESUMEN:
                cortada = True
                break
            total = sumas[columna].get(valor, 0.0)
            if total:
                lineas.append(f"  {valor}: {_miles(cuantas)} registros, "
                              f"{_miles(total)} {sumable}")
            else:
                lineas.append(f"  {valor}: {_miles(cuantas)} registros")
        if not cortada:
            completas.append(columna)

    # SI NO CUPO TODO, SE DICE QUE FALTA
    # ----------------------------------
    # Recortar es aceptable; recortar sin avisar no. Quien lea este resumen
    # tiene que poder saber que de esas columnas no se le esta contando
    # nada, en vez de concluir que la tabla no las trae.
    sin_sitio = [c for c in agregado["utiles"] if c not in completas]
    if sin_sitio:
        lineas.append("")
        lineas.append(
            f"NO CABEN enteros en este resumen los repartos de: "
            f"{', '.join(sin_sitio)}. La tabla SI trae esas columnas; lo "
            f"que falta, entero o en parte, es su desglose, por el tope de "
            f"longitud del resumen.")

    return {"lineas": lineas, "filas": filas,
            "columnas_resumidas": agregado["utiles"],
            "columnas_sin_sitio": sin_sitio,
            "segundos": round(time.time() - arranque, 1),
            "truncado": agregado["truncado"],
            "version": VERSION_RESUMEN,
            "error": agregado["error"]}
