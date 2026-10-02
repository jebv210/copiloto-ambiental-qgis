"""
Carga automatica de las capas del area de estudio en QGIS Desktop.
===============================================================================

QUE DIBUJA
----------
Un mapa de COLOMBIA, con tres capas de abajo hacia arriba:

    1. Departamentos de Colombia, en gris claro (el fondo del mapa).
    2. Municipios del area de estudio, en verde.
    3. Registros de biodiversidad, como puntos naranjas.

Y encuadra la vista sobre Colombia.

NO se anade mapa base de OpenStreetMap. Es una capa MUNDIAL: convertia el
resultado en un mapamundi con Colombia diminuta en el centro, en vez del mapa
de Colombia que es el objeto del proyecto. Ver `MOSTRAR_MAPA_BASE` si algun dia
se quiere recuperar.

COMO SE USA
-----------
    1. En QGIS: Complementos > Consola de Python  (Ctrl + Alt + P).
    2. Boton de la carpeta ("Abrir archivo de script") y elige este archivo.
    3. Boton verde de Play.

No hay que configurar nada: el script encuentra solo la carpeta del proyecto y
los archivos que genero la interfaz web.

===============================================================================
CORRECCIONES RESPECTO A LA VERSION ANTERIOR
===============================================================================

1. EL MAPA BASE NUNCA SE CARGABA.
   Estaba escrito asi:

       capa_osm = QgsVectorLayer(uri_osm, "OpenStreetMap (Base)", "wms")

   Un mapa base XYZ son imagenes (raster), no geometrias (vector). Un
   `QgsVectorLayer` con proveedor "wms" siempre es invalido, asi que
   `isValid()` daba False, el `if` no entraba y la capa se descartaba en
   silencio. Por eso el mapa salia con el fondo en blanco en vez de con
   OpenStreetMap detras. La clase correcta es `QgsRasterLayer`.

2. EL SCRIPT NO ARRANCABA EN QGIS 3.34 NI ANTERIORES.
   Tenia una barra invertida dentro de la expresion de un f-string:

       uri = f"file:///{ruta.replace('\\', '/')}?type=csv..."

   Eso solo es valido desde Python 3.12 (PEP 701). QGIS 3.34 trae Python 3.9,
   donde es un SyntaxError y el script ni siquiera se puede abrir. Ahora la
   ruta se prepara en una variable aparte, antes del f-string.

3. LOS NOMBRES DE LAS COLUMNAS DE COORDENADAS ESTABAN FIJOS.
   Se pedian `decimalLongitude` y `decimalLatitude` a pelo. Si el archivo del
   usuario las llama `latitude`/`longitude`, la capa salia vacia sin decir por
   que. Ahora se leen de la cabecera del CSV, igual que hace el resto del
   sistema.

4. AHORA HACE ZOOM AL AREA DE ESTUDIO.
   Antes el mapa quedaba donde estuviera la vista, y habia que buscar los
   datos a mano.
"""

import os

from qgis.core import (
    QgsProject,
    QgsVectorLayer,
    QgsRasterLayer,
    QgsCoordinateReferenceSystem,
    QgsCoordinateTransform,
    QgsSingleSymbolRenderer,
    QgsFillSymbol,
    QgsLineSymbol,
    QgsMarkerSymbol,
    QgsWkbTypes,
)


# ==============================================================================
# CONFIGURACION VISUAL
# ==============================================================================
# Los colores estan en formato 'R,G,B' o 'R,G,B,Alfa' (alfa de 0 a 255).

#: Si se anade o no un mapa base de teselas (OpenStreetMap) como fondo.
#:
#: DESACTIVADO A PROPOSITO. El mapa base es una capa MUNDIAL: arrastra la
#: cartografia de todo el planeta y hace que el resultado parezca un mapamundi
#: con un puntito en Colombia, en vez del mapa de Colombia que interesa aqui.
#: Ademas obliga al lienzo a trabajar en EPSG:3857 y requiere internet.
#:
#: Con esto en False, el fondo lo pone la propia capa de departamentos de
#: Colombia, que es justo el alcance del proyecto.
#:
#: Ponlo en True si algun dia quieres ver calles y relieve detras de los datos.
MOSTRAR_MAPA_BASE = False

#: Mapa base a usar cuando `MOSTRAR_MAPA_BASE` sea True. Admite cualquier
#: servicio de teselas XYZ.
URL_MAPA_BASE = "https://tile.openstreetmap.org/{z}/{x}/{y}.png"

#: Sistema de coordenadas del proyecto. Todas las capas del sistema se generan
#: en EPSG:4326 (grados, WGS84), asi que fijarlo evita reproyecciones al vuelo
#: y hace que el encuadre sea exacto.
CRS_PROYECTO = "EPSG:4326"

#: Departamentos de Colombia: gris muy claro, casi transparente, de fondo.
ESTILO_DEPARTAMENTOS = {
    "color": "245,245,245,150",
    "outline_color": "180,180,180",
    "outline_width": "0.2",
}

#: Municipios del area de estudio: verde suave para que destaquen.
ESTILO_MUNICIPIOS = {
    "color": "180,220,180,160",
    "outline_color": "46,125,50",
    "outline_width": "0.35",
}

#: Registros de biodiversidad: naranja intenso con borde blanco, para que se
#: distingan aunque se solapen miles de puntos.
ESTILO_PUNTOS = {
    "color": "230,74,25",
    "size": "1.3",
    "outline_color": "255,255,255",
    "outline_width": "0.15",
}

#: Nombres probables de las columnas de coordenadas, en orden de preferencia.
COLUMNAS_LATITUD = ["decimalLatitude", "latitude", "lat", "latitud"]
COLUMNAS_LONGITUD = ["decimalLongitude", "longitude", "lon", "lng", "longitud"]

#: Archivos que genera la interfaz web. Son un contrato con `config/settings.py`:
#: si alla se renombran, hay que renombrarlos aqui tambien.
ARCHIVO_DEPARTAMENTOS = "colombia_departamentos.geojson"
ARCHIVO_MUNICIPIOS = "municipios_filtrados.geojson"
ARCHIVO_BIODIVERSIDAD = "biodiversidad_filtrada.csv"

# ------------------------------------------------------------------------------
# CAPAS PROPIAS DEL USUARIO
# ------------------------------------------------------------------------------
# Ademas de las tres capas que genera el sistema, se cargan automaticamente las
# capas que quieras anadir tu: el poligono de tu area de trabajo, los puntos de
# un formulario de campo (QField), una ortofoto o imagen satelital, etc.
#
# COMO SE USA, dos formas, y puedes combinarlas:
#
#   1. Copia los archivos dentro de la carpeta `capas_extra/` del proyecto.
#
#   2. O crea `capas_extra/rutas.txt` y escribe dentro la ruta completa de cada
#      archivo, una por linea. Asi no hace falta copiar nada: util cuando la
#      capa es una imagen de varios cientos de MB y prefieres dejarla donde
#      esta. Las lineas que empiecen por # se ignoran.
#
# NADA de esto esta escrito en el codigo: lo que aparezca ahi, se carga.

#: Carpeta, dentro del proyecto, donde buscar las capas propias del usuario.
CARPETA_CAPAS_EXTRA = "capas_extra"

#: Archivo opcional, dentro de esa carpeta, con rutas absolutas (una por linea).
ARCHIVO_RUTAS_EXTRA = "rutas.txt"

#: Formatos vectoriales reconocidos (poligonos, lineas y puntos).
EXTENSIONES_VECTOR = (".shp", ".geojson", ".json", ".gpkg", ".kml", ".kmz",
                      ".gml", ".gpx", ".tab", ".dxf")

#: Formatos raster reconocidos (imagenes satelitales, ortofotos, modelos).
EXTENSIONES_RASTER = (".tif", ".tiff", ".img", ".jp2", ".ecw", ".vrt",
                      ".png", ".jpg", ".jpeg")

#: Estilo por defecto de los poligonos propios: SIN relleno y con borde grueso,
#: para que dejen ver lo que hay debajo (la imagen satelital, los municipios).
#: Si al lado del archivo hay un `.qml` con el mismo nombre, QGIS aplica ese
#: estilo y este se ignora: es la forma de conservar los colores que ya
#: definiste en tu propio proyecto.
ESTILO_EXTRA_POLIGONO = {
    "style": "no",
    "outline_color": "255,152,0",
    "outline_width": "0.8",
}

#: Estilo por defecto de las lineas propias.
ESTILO_EXTRA_LINEA = {"line_color": "255,152,0", "line_width": "0.6"}

#: Estilo por defecto de los puntos propios (por ejemplo, un formulario QField).
ESTILO_EXTRA_PUNTO = {
    "color": "255,193,7",
    "size": "2.6",
    "outline_color": "60,60,60",
    "outline_width": "0.3",
}


# ==============================================================================
# 1. LOCALIZAR LA CARPETA DEL PROYECTO
# ==============================================================================

def detectar_carpeta_proyecto():
    """
    Encuentra la carpeta del proyecto sin ninguna ruta escrita a mano.

    Se prueban cuatro pistas, en orden, y gana la primera carpeta que contenga
    de verdad el archivo de biodiversidad:

        1. Preguntarselo al servidor API local, si esta encendido.
        2. La carpeta donde se guardo este mismo script.
        3. La carpeta del proyecto .qgz que este abierto en QGIS.
        4. La carpeta de trabajo actual.

    Asi funciona tanto si el script se ejecuta desde la carpeta del proyecto
    como si se descargo a "Descargas" y se abrio desde ahi.

    Returns:
        Tupla `(carpeta, comprobada)`:
            carpeta   : ruta que se va a usar.
            comprobada: True si en esa carpeta esta el archivo de
                        biodiversidad; False si es solo la mejor suposicion.
    """
    candidatos = []

    # Pista 1: el servidor API conoce la ruta exacta del proyecto.
    try:
        import urllib.request
        import json
        with urllib.request.urlopen(
            "http://127.0.0.1:8000/base-path", timeout=1
        ) as respuesta:
            datos = json.loads(respuesta.read().decode("utf-8"))
            if datos.get("base_path"):
                candidatos.append(datos["base_path"])
    except Exception:
        # El servidor apagado no es un problema: quedan otras tres pistas.
        pass

    # Pista 2: la ubicacion de este archivo.
    try:
        candidatos.append(os.path.dirname(os.path.abspath(__file__)))
    except NameError:
        # __file__ no existe si el codigo se pego directamente en la consola.
        pass

    # Pista 3: la carpeta del proyecto QGIS abierto.
    ruta_qgz = QgsProject.instance().absolutePath()
    if ruta_qgz:
        candidatos.append(ruta_qgz)

    # Pista 4: el directorio de trabajo.
    candidatos.append(os.getcwd())

    for carpeta in candidatos:
        if os.path.exists(os.path.join(carpeta, ARCHIVO_BIODIVERSIDAD)):
            return carpeta, True

    return (candidatos[0] if candidatos else os.getcwd()), False


# ==============================================================================
# 2. LECTURA DE LA CABECERA DEL CSV
# ==============================================================================

def detectar_columnas_coordenadas(ruta_csv):
    """
    Averigua como se llaman las columnas de latitud y longitud del CSV.

    Lee unicamente la primera linea, asi que es instantaneo.

    Args:
        ruta_csv: Ruta al archivo de biodiversidad.

    Returns:
        Tupla `(columna_latitud, columna_longitud, delimitador)`. Cualquiera de
        las columnas puede ser None si no se reconoce ninguna, en cuyo caso no
        se pueden dibujar los puntos.
    """
    try:
        with open(ruta_csv, "r", encoding="utf-8", errors="replace") as f:
            primera_linea = f.readline()
    except OSError:
        return None, None, ","

    # Detectar el separador probando cual produce mas columnas.
    delimitador = ","
    maximo = 1
    for candidato in (",", "\t", ";", "|"):
        cuantas = len(primera_linea.split(candidato))
        if cuantas > maximo:
            delimitador, maximo = candidato, cuantas

    columnas = [c.strip().strip('"').strip("'")
                for c in primera_linea.rstrip("\r\n").split(delimitador)]

    def buscar(nombres_probables):
        """
        Devuelve el nombre real de la columna que corresponde.

        Args:
            nombres_probables: Nombres a buscar, en orden de preferencia.

        Returns:
            El nombre tal como aparece en el archivo, o None.
        """
        indice = {c.lower().replace("_", ""): c for c in columnas}
        for probable in nombres_probables:
            clave = probable.lower().replace("_", "")
            if clave in indice:
                return indice[clave]
        return None

    return buscar(COLUMNAS_LATITUD), buscar(COLUMNAS_LONGITUD), delimitador


# ==============================================================================
# 3. CARGA DE CADA CAPA
# ==============================================================================

def cargar_mapa_base(proyecto):
    """
    Anade el mapa base de teselas al fondo, si esta activado.

    Por defecto NO se anade: ver `MOSTRAR_MAPA_BASE`. Es una capa mundial y
    convierte el resultado en un mapamundi con Colombia diminuta en el centro,
    justo lo contrario de lo que interesa en este proyecto.

    Cuando se activa, se carga como RASTER: un servicio de teselas XYZ entrega
    imagenes, no geometrias. Usar `QgsVectorLayer` aqui, como hacia una version
    anterior, produce siempre una capa invalida que se descarta sin avisar.

    Args:
        proyecto: Instancia de `QgsProject` donde anadir la capa.

    Returns:
        La capa creada, o None si esta desactivada o no se pudo cargar (por
        ejemplo, sin internet). Que falte el fondo no impide ver los datos.
    """
    if not MOSTRAR_MAPA_BASE:
        return None

    # QGIS espera las llaves de la plantilla codificadas en la URI.
    url_codificada = (URL_MAPA_BASE
                      .replace("{", "%7B")
                      .replace("}", "%7D"))
    uri = "type=xyz&zmin=0&zmax=19&url=" + url_codificada
    capa = QgsRasterLayer(uri, "OpenStreetMap (base)", "wms")

    if not capa.isValid():
        print("  [--] Mapa base: no se pudo cargar (revisa la conexion a "
              "internet). El resto de capas si se cargan.")
        return None

    proyecto.addMapLayer(capa)
    print("  [OK] Mapa base de OpenStreetMap.")
    return capa


def cargar_capa_poligonos(proyecto, ruta, nombre, estilo, descripcion):
    """
    Carga un GeoJSON de poligonos y le aplica un estilo de relleno.

    Args:
        proyecto: Instancia de `QgsProject`.
        ruta: Ruta del archivo GeoJSON.
        nombre: Nombre con el que aparecera en el panel de capas.
        estilo: Diccionario de propiedades para `QgsFillSymbol.createSimple`.
        descripcion: Texto para el mensaje de resultado.

    Returns:
        La capa cargada, o None si el archivo no existe o no es valido.
    """
    if not os.path.exists(ruta):
        print(f"  [--] {descripcion}: no existe el archivo "
              f"'{os.path.basename(ruta)}'.")
        return None

    capa = QgsVectorLayer(ruta, nombre, "ogr")
    if not capa.isValid():
        print(f"  [!!] {descripcion}: el archivo existe pero QGIS no lo pudo "
              f"interpretar.")
        return None

    capa.setCrs(QgsCoordinateReferenceSystem("EPSG:4326"))
    proyecto.addMapLayer(capa)

    capa.setRenderer(QgsSingleSymbolRenderer(
        QgsFillSymbol.createSimple(estilo)
    ))
    capa.triggerRepaint()

    print(f"  [OK] {descripcion}: {capa.featureCount()} elementos.")
    return capa


def cargar_capa_puntos(proyecto, ruta_csv):
    """
    Convierte el CSV de biodiversidad en una capa de puntos.

    Los nombres de las columnas de coordenadas y el separador se leen de la
    cabecera del propio archivo, no se dan por supuestos.

    Args:
        proyecto: Instancia de `QgsProject`.
        ruta_csv: Ruta del CSV de biodiversidad filtrada.

    Returns:
        La capa de puntos, o None si el archivo falta o no trae coordenadas
        reconocibles.
    """
    if not os.path.exists(ruta_csv):
        print(f"  [--] Biodiversidad: no existe '{os.path.basename(ruta_csv)}'. "
              f"Genera el area de estudio en la interfaz web.")
        return None

    columna_lat, columna_lon, delimitador = detectar_columnas_coordenadas(ruta_csv)

    if not columna_lat or not columna_lon:
        print("  [!!] Biodiversidad: el archivo no tiene columnas de latitud y "
              "longitud reconocibles, no se pueden dibujar los puntos.")
        return None

    # La ruta se prepara ANTES del f-string. Meter un `.replace('\\', '/')`
    # dentro de la expresion del f-string es un SyntaxError en Python 3.11 y
    # anteriores, que es lo que traen las versiones de QGIS mas usadas.
    ruta_uri = ruta_csv.replace("\\", "/")

    # El delimitador viaja codificado: un tabulador literal rompe la URI.
    delimitador_uri = {"\t": "%09", ",": ",", ";": ";", "|": "%7C"}.get(
        delimitador, ","
    )

    uri = (
        "file:///" + ruta_uri
        + "?type=csv"
        + "&delimiter=" + delimitador_uri
        + "&xField=" + columna_lon
        + "&yField=" + columna_lat
        + "&crs=EPSG:4326"
        + "&spatialIndex=yes"      # acelera el dibujado con muchos puntos
        + "&subsetIndex=no"
        + "&watchFile=no"
    )

    capa = QgsVectorLayer(uri, "Biodiversidad (puntos)", "delimitedtext")
    if not capa.isValid():
        print("  [!!] Biodiversidad: QGIS no pudo convertir el CSV en capa de "
              "puntos.")
        return None

    proyecto.addMapLayer(capa)

    capa.setRenderer(QgsSingleSymbolRenderer(
        QgsMarkerSymbol.createSimple(ESTILO_PUNTOS)
    ))
    capa.triggerRepaint()

    print(f"  [OK] Biodiversidad: {capa.featureCount()} registros "
          f"(coordenadas en '{columna_lat}' / '{columna_lon}').")
    return capa


# ==============================================================================
# 3-bis. CAPAS PROPIAS DEL USUARIO
# ==============================================================================

def recopilar_capas_extra(carpeta_proyecto):
    """
    Reune las capas propias del usuario, de las dos fuentes posibles.

    Busca en `capas_extra/`:
      * los archivos geograficos que haya sueltos dentro, y
      * las rutas absolutas escritas en `capas_extra/rutas.txt`.

    Nada esta escrito en el codigo: lo que el usuario ponga ahi, se carga.

    Args:
        carpeta_proyecto: Carpeta raiz del proyecto.

    Returns:
        Lista de rutas absolutas, sin repetidos y en orden alfabetico. Vacia si
        no existe la carpeta o no hay nada dentro.
    """
    carpeta = os.path.join(carpeta_proyecto, CARPETA_CAPAS_EXTRA)
    if not os.path.isdir(carpeta):
        return []

    reconocidas = EXTENSIONES_VECTOR + EXTENSIONES_RASTER
    encontradas = []

    # --- Archivos sueltos dentro de la carpeta -------------------------------
    for nombre in sorted(os.listdir(carpeta)):
        completa = os.path.join(carpeta, nombre)
        if os.path.isfile(completa) and nombre.lower().endswith(reconocidas):
            encontradas.append(completa)

    # --- Rutas apuntadas en rutas.txt ----------------------------------------
    listado = os.path.join(carpeta, ARCHIVO_RUTAS_EXTRA)
    if os.path.exists(listado):
        try:
            with open(listado, "r", encoding="utf-8", errors="replace") as f:
                for linea in f:
                    ruta = linea.strip().strip('"')
                    if not ruta or ruta.startswith("#"):
                        continue
                    if not os.path.isabs(ruta):
                        ruta = os.path.join(carpeta, ruta)
                    if os.path.exists(ruta):
                        encontradas.append(ruta)
                    else:
                        print(f"  [--] Capa propia no encontrada: {ruta}")
        except OSError as exc:
            print(f"  [--] No se pudo leer {ARCHIVO_RUTAS_EXTRA}: {exc}")

    # Sin repetidos, conservando el orden de aparicion.
    unicas = []
    for ruta in encontradas:
        if ruta not in unicas:
            unicas.append(ruta)
    return unicas


def cargar_capa_extra(proyecto, ruta):
    """
    Carga una capa propia del usuario, sea vectorial o raster.

    El estilo por defecto solo se aplica si la capa no trae uno propio. QGIS
    carga automaticamente el archivo `.qml` que est al lado con el mismo
    nombre, asi que para conservar los colores de tu proyecto original basta
    con exportar el estilo (clic derecho sobre la capa > Exportar > Guardar
    estilo) junto al archivo.

    Args:
        proyecto: Instancia de `QgsProject`.
        ruta: Ruta del archivo a cargar.

    Returns:
        Tupla `(capa, es_raster)`, o `(None, False)` si no se pudo cargar.
    """
    nombre = os.path.splitext(os.path.basename(ruta))[0]
    es_raster = ruta.lower().endswith(EXTENSIONES_RASTER)

    if es_raster:
        capa = QgsRasterLayer(ruta, nombre)
        if not capa.isValid():
            print(f"  [!!] {nombre}: QGIS no pudo abrir la imagen.")
            return None, True
        proyecto.addMapLayer(capa)
        print(f"  [OK] {nombre} (imagen de fondo)")
        return capa, True

    capa = QgsVectorLayer(ruta, nombre, "ogr")
    if not capa.isValid():
        print(f"  [!!] {nombre}: QGIS no pudo interpretar la capa.")
        return None, False

    proyecto.addMapLayer(capa)

    # Si la capa trajo su propio .qml, QGIS ya lo aplico y no se toca nada.
    if not os.path.exists(os.path.splitext(ruta)[0] + ".qml"):
        geometria = QgsWkbTypes.geometryType(capa.wkbType())
        if geometria == QgsWkbTypes.PolygonGeometry:
            simbolo = QgsFillSymbol.createSimple(ESTILO_EXTRA_POLIGONO)
        elif geometria == QgsWkbTypes.LineGeometry:
            simbolo = QgsLineSymbol.createSimple(ESTILO_EXTRA_LINEA)
        else:
            simbolo = QgsMarkerSymbol.createSimple(ESTILO_EXTRA_PUNTO)
        capa.setRenderer(QgsSingleSymbolRenderer(simbolo))
        capa.triggerRepaint()

    print(f"  [OK] {nombre}: {capa.featureCount()} elementos")
    return capa, False


def cargar_capas_extra(proyecto, carpeta_proyecto, solo_raster):
    """
    Carga las capas propias del usuario, separando fondo de primer plano.

    Se llama DOS veces, y el orden importa porque en QGIS cada capa nueva se
    coloca encima de las anteriores:

      * `solo_raster=True`  antes que nada: las imagenes van al fondo, si no
        taparian los municipios y los puntos.
      * `solo_raster=False` al final: tus poligonos y puntos quedan arriba del
        todo, visibles sobre el resto.

    Args:
        proyecto: Instancia de `QgsProject`.
        carpeta_proyecto: Carpeta raiz del proyecto.
        solo_raster: True para cargar solo imagenes; False para solo vectores.

    Returns:
        Lista de las capas cargadas en esta pasada.
    """
    cargadas = []
    for ruta in recopilar_capas_extra(carpeta_proyecto):
        if ruta.lower().endswith(EXTENSIONES_RASTER) != solo_raster:
            continue
        capa, _ = cargar_capa_extra(proyecto, ruta)
        if capa is not None:
            cargadas.append(capa)
    return cargadas


# ==============================================================================
# 4. ENCUADRE DEL MAPA
# ==============================================================================

def encuadrar(capa_referencia, capa_contexto=None):
    """
    Mueve la vista de QGIS para que se vea el area de estudio.

    Antes habia que buscar los datos a mano tras ejecutar el script.

    Se encuadra sobre `capa_contexto` (los departamentos, para ver Colombia
    entera como en la imagen de referencia) y, si no hay, sobre
    `capa_referencia`.

    Args:
        capa_referencia: Capa con los datos del area de estudio.
        capa_contexto: Capa que da el marco general. Opcional.

    Returns:
        None. Si QGIS no expone la interfaz grafica (`iface`), no hace nada.
    """
    capa = capa_contexto or capa_referencia
    if capa is None:
        return

    try:
        from qgis.utils import iface
        if iface is None:
            return

        lienzo = iface.mapCanvas()
        extension = capa.extent()

        # La extension de la capa esta en su propio CRS; el lienzo puede estar
        # en otro (con el mapa base XYZ, normalmente EPSG:3857). Sin esta
        # transformacion el encuadre se va a un punto cualquiera del oceano.
        crs_capa = capa.crs()
        crs_lienzo = lienzo.mapSettings().destinationCrs()
        if crs_capa != crs_lienzo:
            transformacion = QgsCoordinateTransform(
                crs_capa, crs_lienzo, QgsProject.instance()
            )
            extension = transformacion.transformBoundingBox(extension)

        extension.scale(1.05)   # un poco de margen alrededor
        lienzo.setExtent(extension)
        lienzo.refresh()
        print("  [OK] Vista centrada en el area de estudio.")
    except Exception as exc:
        print(f"  [--] No se pudo centrar la vista automaticamente: {exc}")


# ==============================================================================
# 5. FUNCION PRINCIPAL
# ==============================================================================

def cargar_capas_area_de_estudio():
    """
    Carga en QGIS el mapa de Colombia con el area de estudio y encuadra la vista.

    Es la funcion que se ejecuta al pulsar Play en la consola de QGIS.

    Returns:
        Diccionario con las capas cargadas (`base`, `departamentos`,
        `municipios`, `puntos`). Cada valor puede ser None si esa capa no se
        pudo cargar; el resto se cargan igualmente.
    """
    print("=" * 70)
    print("  COPILOTO AMBIENTAL - Carga de capas en QGIS")
    print("=" * 70)

    proyecto = QgsProject.instance()

    # Se fija el sistema de coordenadas del proyecto antes de cargar nada.
    # Todas las capas del sistema se generan en EPSG:4326, asi que con esto el
    # lienzo trabaja en las mismas unidades y el encuadre sale exacto. Sin
    # fijarlo, QGIS adopta el CRS de la primera capa: si esa era el mapa base
    # mundial, el lienzo pasaba a EPSG:3857 y la vista se iba al mundo entero.
    proyecto.setCrs(QgsCoordinateReferenceSystem(CRS_PROYECTO))

    carpeta, comprobada = detectar_carpeta_proyecto()
    if comprobada:
        print(f"Carpeta del proyecto: {carpeta}")
    else:
        print(f"AVISO: no se encontro '{ARCHIVO_BIODIVERSIDAD}' en ninguna "
              f"ubicacion conocida.")
        print(f"       Se probara con: {carpeta}")
    print()

    capas = {}

    # El orden importa: en QGIS cada capa nueva se coloca ENCIMA de las
    # anteriores. Cargando el fondo primero y los puntos al final, los puntos
    # quedan visibles sobre los poligonos.
    capas["base"] = cargar_mapa_base(proyecto)

    # Las imagenes propias van al fondo del todo (debajo de los departamentos),
    # si no taparian los municipios y los puntos.
    capas["imagenes_propias"] = cargar_capas_extra(
        proyecto, carpeta, solo_raster=True
    )

    capas["departamentos"] = cargar_capa_poligonos(
        proyecto,
        os.path.join(carpeta, ARCHIVO_DEPARTAMENTOS),
        "Departamentos de Colombia",
        ESTILO_DEPARTAMENTOS,
        "Departamentos de Colombia",
    )

    capas["municipios"] = cargar_capa_poligonos(
        proyecto,
        os.path.join(carpeta, ARCHIVO_MUNICIPIOS),
        "Municipios del area de estudio",
        ESTILO_MUNICIPIOS,
        "Municipios del area de estudio",
    )

    capas["puntos"] = cargar_capa_puntos(
        proyecto, os.path.join(carpeta, ARCHIVO_BIODIVERSIDAD)
    )

    # Tus poligonos y puntos, encima de todo lo demas.
    capas["capas_propias"] = cargar_capas_extra(
        proyecto, carpeta, solo_raster=False
    )

    print()
    # Si has anadido capas propias, esas definen tu zona de trabajo y el mapa
    # se encuadra sobre ellas. Si no, sobre Colombia entera.
    #
    # Se descartan las capas VACIAS: un formulario de campo recien creado no
    # tiene todavia ningun punto, y su extension es (0, 0). Encuadrar sobre ella
    # mandaria la vista al golfo de Guinea, donde no hay nada que ver.
    propias = [
        capa for capa in ((capas["capas_propias"] or [])
                          + (capas["imagenes_propias"] or []))
        if capa is not None and not capa.extent().isEmpty()
        and (capa.extent().xMinimum() != 0 or capa.extent().yMinimum() != 0)
    ]

    if propias:
        encuadrar(propias[0], propias[0])
    else:
        encuadrar(capas["puntos"] or capas["municipios"], capas["departamentos"])

    print()
    print("=" * 70)
    # El mapa base solo cuenta si se pidio: con MOSTRAR_MAPA_BASE en False no
    # es una capa que falte, es una capa que no se queria.
    esperadas = 3 + (1 if MOSTRAR_MAPA_BASE else 0)
    del_sistema = [capas["base"], capas["departamentos"],
                   capas["municipios"], capas["puntos"]]
    cargadas = len([c for c in del_sistema if c is not None])
    propias = len(capas["capas_propias"]) + len(capas["imagenes_propias"])

    print(f"  Carga finalizada: {cargadas} de {esperadas} capas del sistema"
          + (f" + {propias} capas propias." if propias else "."))

    if capas["municipios"] is None or capas["puntos"] is None:
        print("  Faltan capas del area de estudio. Generala en la interfaz")
        print("  web (pestana 'Area de Estudio') y vuelve a ejecutar.")

    if not propias:
        print(f"  Para anadir tus propias capas (poligonos, formularios de")
        print(f"  campo, imagenes satelitales), ponlas en la carpeta")
        print(f"  '{CARPETA_CAPAS_EXTRA}' del proyecto. Ver el encabezado de este")
        print(f"  script para las dos formas de hacerlo.")
    print("=" * 70)

    return capas


# Ejecutar al pulsar Play en la consola de QGIS.
cargar_capas_area_de_estudio()
