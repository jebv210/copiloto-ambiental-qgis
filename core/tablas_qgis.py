"""
Lleva a QGIS los datos de las tablas que no traen coordenadas.

EL PROBLEMA QUE RESUELVE
------------------------
En la carpeta de Drive hay tablas con informacion valiosa que no se puede
dibujar: el consolidado de la cuenca trae poblacion y area por vereda, el
inventario trae 127 quebradas, y los POMCAS traen 23 columnas. Ninguna de
ellas tiene latitud ni longitud, asi que el agente las estudiaba -- podia
responder preguntas sobre ellas -- pero el mapa seguia sin saber nada.

Hay dos maneras de llevarlas al mapa, y las dos se hacen aqui:

  UNIR    Si la tabla nombra sitios que ya existen en una capa -- veredas,
          quebradas --, sus columnas se pegan a esa geometria. Se midio
          sobre los datos del proyecto: cinco de los veinte puntos del
          area llevan nombre de vereda que aparece en el consolidado, y
          con la union pasan a traer su poblacion y su superficie.

  ADJUNTAR Si no hay forma de unirla, la tabla entra igual como capa SIN
          geometria. QGIS las abre y deja unirlas a mano por el campo que
          uno quiera. Es menos comodo que lo anterior, pero es infinitamente
          mejor que dejarlas fuera: un dato que no esta en el proyecto no
          se puede usar.

LO QUE NO SE HACE
-----------------
No se inventa una coordenada. Si una fila nombra un sitio que no esta en
ninguna capa, se queda sin dibujar Y SE DICE cuales son: quien mira el
mapa tiene que saber que esas filas existen y por que no aparecen.
"""

import json
import os
import re
import unicodedata

#: Prefijos que acompanan al nombre de una vereda y no forman parte de el.
#:
#: En la capa del proyecto los puntos se llaman "Vda Arenosas", "Vda.
#: Santa Ines" o "Vereda la Esperanza", y en las tablas la misma vereda es
#: solo "Arenosas". Sin quitarlos no casa ni una.
PREFIJOS_LUGAR = ("vda", "vereda", "vda.", "v/da", "corregimiento",
                  "quebrada", "qda", "rio", "río")

#: Nombres de columna que suelen contener el nombre de un sitio.
COLUMNAS_DE_NOMBRE = ("nombre de la vereda", "vereda", "nombre", "quebrada",
                      "afluente", "sitio", "lugar", "localidad")


def normalizar(texto: str) -> str:
    """
    Reduce un nombre de sitio a su forma comparable.

    Quita acentos, pasa a minusculas, retira el prefijo que indica el tipo
    de lugar y deja solo letras y numeros separados por un espacio. Asi
    "Vda. Cañaveral" y "cañaveral" se reconocen como el mismo sitio.

    Args:
        texto: El nombre tal como viene.

    Returns:
        El nombre normalizado, o cadena vacia.
    """
    plano = unicodedata.normalize("NFKD", str(texto or ""))
    plano = "".join(c for c in plano if not unicodedata.combining(c)).lower()
    plano = plano.strip()
    for prefijo in PREFIJOS_LUGAR:
        if plano.startswith(prefijo + " ") or plano.startswith(prefijo + ". "):
            plano = plano[len(prefijo):].lstrip(". ")
            break
    return re.sub(r"[^a-z0-9]+", " ", plano).strip()


def columna_de_nombres(columnas: list):
    """
    Elige que columna de una tabla contiene nombres de sitios.

    Args:
        columnas: Nombres de columna de la tabla.

    Returns:
        El nombre de la columna elegida, o None si ninguna encaja.
    """
    bajas = {c.lower().strip(): c for c in columnas if c}
    for quiero in COLUMNAS_DE_NOMBRE:
        for baja, original in bajas.items():
            if baja == quiero:
                return original
    for quiero in COLUMNAS_DE_NOMBRE:
        for baja, original in bajas.items():
            if quiero in baja:
                return original
    return None


def unir(ruta_capa: str, filas: list, columna: str = None) -> dict:
    """
    Pega las columnas de una tabla a las entidades de una capa que coinciden.

    Args:
        ruta_capa: GeoJSON cuyas entidades tienen un campo `nombre`.
        filas: Filas de la tabla, como diccionarios columna -> valor.
        columna: Columna con el nombre del sitio. Si se omite, se busca.

    Returns:
        Diccionario con `entidades`, `unidas`, `sin_union`, `sin_sitio` y
        `columna`. `sin_sitio` son las filas de la tabla que no encontraron
        geometria; `sin_union`, las entidades que se quedaron sin datos.
    """
    try:
        with open(ruta_capa, encoding="utf-8") as fh:
            capa = json.load(fh)
    except (OSError, ValueError) as exc:
        return {"error": f"no se pudo leer la capa: {str(exc)[:90]}"}

    if not filas:
        return {"error": "la tabla no trae ninguna fila"}

    if columna is None:
        # Las claves salen de TODAS las filas, no de la primera. En un
        # libro de varias hojas cada fila trae solo las columnas de la
        # suya, y la primera hoja del consolidado del proyecto es un
        # resumen ejecutivo que no nombra ninguna vereda: mirando solo esa
        # fila, la union se daba por imposible teniendo 20 veredas dos
        # hojas mas alla.
        todas = []
        for fila in filas:
            for clave in fila:
                if clave not in todas:
                    todas.append(clave)
        columna = columna_de_nombres(todas)
    if not columna:
        return {"error": "la tabla no tiene ninguna columna con nombres de "
                         "sitios con la que unirla a una capa"}

    por_nombre = {}
    for fila in filas:
        clave = normalizar(fila.get(columna))
        if clave:
            por_nombre.setdefault(clave, fila)

    entidades, unidas, sin_union = [], 0, []
    usados = set()
    for entidad in capa.get("features") or []:
        propiedades = dict(entidad.get("properties") or {})
        clave = normalizar(propiedades.get("nombre"))
        datos = por_nombre.get(clave)
        if datos:
            unidas += 1
            usados.add(clave)
            for campo, valor in datos.items():
                if campo == columna or campo.startswith("__"):
                    continue
                if valor not in (None, ""):
                    propiedades[campo] = valor
        else:
            sin_union.append(propiedades.get("nombre") or "(sin nombre)")
        entidades.append({**entidad, "properties": propiedades})

    return {
        "entidades": entidades,
        "unidas": unidas,
        "sin_union": sin_union,
        # Las filas de la tabla que no encontraron sitio. Se devuelven con
        # su nombre para poder nombrarlas: una fila que no aparece en el
        # mapa y nadie menciona es una fila perdida.
        "sin_sitio": sorted(c for c in por_nombre if c not in usados),
        "columna": columna,
    }


def escribir_capa_unida(destino: str, resultado: dict) -> dict:
    """
    Guarda el resultado de una union como GeoJSON.

    Args:
        destino: Ruta del archivo a escribir.
        resultado: Lo que devolvio `unir`.

    Returns:
        Diccionario con `ruta` y `entidades`, o con `error`.
    """
    if resultado.get("error"):
        return resultado
    try:
        os.makedirs(os.path.dirname(os.path.abspath(destino)), exist_ok=True)
        with open(destino, "w", encoding="utf-8") as fh:
            json.dump({"type": "FeatureCollection",
                       "features": resultado["entidades"]},
                      fh, ensure_ascii=False)
    except OSError as exc:
        return {"error": f"no se pudo escribir: {str(exc)[:90]}"}
    return {"ruta": destino, "entidades": len(resultado["entidades"])}


def escribir_tabla_suelta(destino: str, columnas: list, filas: list) -> dict:
    """
    Escribe una tabla sin geometria en CSV, para abrirla en QGIS.

    POR QUE EN CSV Y NO EN GEOPACKAGE
    ---------------------------------
    Porque una tabla sin geometria en GeoPackage necesita declararse como
    `attributes` en el catalogo, y QGIS la abre igual desde un CSV con una
    linea menos de ceremonia. Lo que importa es que entre en el proyecto y
    se pueda unir a mano por el campo que haga falta.

    Args:
        destino: Ruta del .csv a escribir.
        columnas: Nombres de columna, en orden.
        filas: Filas como diccionarios.

    Returns:
        Diccionario con `ruta` y `filas`, o con `error`.
    """
    import csv

    utiles = [c for c in columnas if c and not c.startswith("__")]
    if not utiles or not filas:
        return {"error": "la tabla esta vacia"}
    try:
        os.makedirs(os.path.dirname(os.path.abspath(destino)), exist_ok=True)
        with open(destino, "w", encoding="utf-8-sig", newline="") as fh:
            escritor = csv.DictWriter(fh, fieldnames=utiles,
                                      extrasaction="ignore")
            escritor.writeheader()
            for fila in filas:
                escritor.writerow({c: fila.get(c, "") for c in utiles})
    except OSError as exc:
        return {"error": f"no se pudo escribir: {str(exc)[:90]}"}
    return {"ruta": destino, "filas": len(filas), "columnas": len(utiles)}
