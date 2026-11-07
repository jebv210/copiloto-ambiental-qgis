"""
Arma un paquete de QGIS que se abre con doble clic.

QUE PROBLEMA RESUELVE
---------------------
Hasta ahora, cuando se pedia "un archivo para QGIS", el sistema entregaba el
script `cargar_capas_qgis.py`: hay que abrir QGIS, ir a la consola de Python,
pegarlo y ejecutarlo. Funciona, pero no es lo que nadie entiende por "un
archivo para QGIS".

Lo que se espera es lo que produce una salida de campo con QField: una
carpeta autocontenida con el proyecto, sus capas y sus rutas relativas, que
se abre haciendo doble clic y aparece todo en su sitio. Se tomo como
referencia un proyecto real del equipo, MARIPOSAS_SAN_CARLOS: proyecto .qgs
con `Absolute=false`, capas con su sistema de referencia, y todo en la misma
carpeta.

POR QUE GEOPACKAGE Y NO SHAPEFILE
---------------------------------
Porque el shapefile corta los nombres de campo a 10 caracteres, y eso no es
un detalle teorico: en el proyecto de mariposas del equipo los campos ya
llegan mutilados -- "Comportami", "Nombre cie", "Numero in" -- y nadie puede
recuperarlos despues. Un GeoPackage es un solo archivo, guarda los nombres
enteros, es UTF-8 por definicion y lo abren igual QGIS y QField.

SE ESCRIBE A MANO, SIN GDAL
---------------------------
En el contenedor no hay GDAL, ni fiona, ni geopandas: se comprobo. Pero un
GeoPackage es una base SQLite con unas tablas de catalogo y las geometrias
en binario, y `sqlite3` viene con Python. Asi que se escribe directamente,
sin anadir una dependencia de 200 MB al despliegue.

El formato binario es el mismo que este proyecto ya sabe LEER en
`core/campo.py`, cuando valida los datos que vuelven de QField. Aqui se hace
el camino inverso, y por eso las dos partes tienen que coincidir: si una
cambia, la otra deja de entender a la otra, y hay una prueba que lo vigila.
"""

import json
import os
import sqlite3
import struct
import time
import zipfile

#: Sistema de referencia de todo lo que produce el proyecto.
#:
#: Las capas se generan en grados, que es como vienen las coordenadas del
#: KMZ del area y de los datos de campo. No se reproyecta a metros: hacerlo
#: sin `pyproj` seria inventarse una transformacion, y para dibujar un mapa
#: y medir a ojo no hace falta.
SRS_ID = 4326

#: Definicion WKT del WGS 84, la que QGIS espera encontrar en el catalogo.
WKT_4326 = (
    'GEOGCS["WGS 84",DATUM["WGS_1984",SPHEROID["WGS 84",6378137,'
    '298.257223563,AUTHORITY["EPSG","7030"]],AUTHORITY["EPSG","6326"]],'
    'PRIMEM["Greenwich",0,AUTHORITY["EPSG","8901"]],'
    'UNIT["degree",0.0174532925199433,AUTHORITY["EPSG","9122"]],'
    'AUTHORITY["EPSG","4326"]]'
)

#: Numero magico que marca el archivo como GeoPackage. Es "GPKG" en ASCII.
#: Sin el, QGIS lo abre como un SQLite cualquiera y no ve ninguna capa.
APPLICATION_ID = 0x47504B47

#: Version del formato: 1.2.
USER_VERSION = 10200

#: Como se dibuja cada tipo de geometria. Los colores se eligen para que las
#: tres capas se distingan sobre un mapa base claro y el relleno del area no
#: tape los puntos.
ESTILOS = {
    "Point": {"tipo": "marker", "borde": "35,35,35,255", "tamano": "2.8"},
    "LineString": {"tipo": "line", "ancho": "0.66"},
    "Polygon": {"tipo": "fill", "borde": "255,255,255,255", "ancho": "0.6"},
}

#: Un color por capa, repartidos por tipo de geometria.
#:
#: POR QUE POR TIPO Y NO UNA LISTA UNICA
#: -------------------------------------
#: Primero se probo con una paleta sola y numerando todas las capas
#: seguidas. El resultado: las quebradas salian rojas, del mismo rojo que
#: el borde del municipio. Un rio rojo no se lee como un rio.
#:
#: Cada familia tiene su gama, y dentro de ella cada capa su tono:
#:
#:   lineas    azules y turquesas, que es como se lee el agua
#:   puntos    calidos y saturados, que destacan sobre el verde del
#:             satelite sin confundirse con el
#:   bordes    claros, para marcar un limite sin competir con los datos
#:
#: Ningun color es verde ni marron: el mapa base es verde oscuro casi
#: entero y ahi se perderian.
PALETA_POR_TIPO = {
    "Point": [
        "227,26,28,255",     # rojo
        "255,214,0,255",     # amarillo
        "166,86,247,255",    # morado
        "255,127,0,255",     # naranja
        "255,105,180,255",   # rosa
        "0,229,255,255",     # cian
    ],
    "LineString": [
        "0,180,255,255",     # azul agua
        "0,230,210,255",     # turquesa
        "120,160,255,255",   # azul claro
        "0,120,220,255",     # azul fuerte
    ],
    "Polygon": [
        "255,255,255,255",   # blanco
        "255,214,0,255",     # amarillo
        "255,127,0,255",     # naranja
    ],
}

#: Relleno de los poligonos: casi transparente, para no tapar el satelite.
#: Un poligono relleno de color saturado esconderia el mapa base, que es
#: justo lo que se quiso anadir.
RELLENO_POLIGONO = "255,255,255,38"


def _color_de_capa(indice: int, tipo: str) -> str:
    """
    Elige el color de una capa segun su tipo y su posicion.

    Args:
        indice: Posicion de la capa entre las de su mismo tipo.
        tipo: "Point", "LineString" o "Polygon".

    Returns:
        El color en "r,g,b,a".
    """
    if tipo == "Polygon":
        return RELLENO_POLIGONO
    gama = PALETA_POR_TIPO.get(tipo) or PALETA_POR_TIPO["Point"]
    return gama[indice % len(gama)]


#: Mapa base de satelite que se pone debajo de todo en el proyecto.
#:
#: POR QUE AQUI SI Y EN EL SCRIPT NO
#: ---------------------------------
#: `cargar_capas_qgis.py` lo trae desactivado, y con razon: carga las capas
#: en un QGIS que ya esta abierto, con el encuadre que tenga la persona, y
#: un mapa base MUNDIAL deja la vista en un mapamundi con un puntito en
#: Colombia.
#:
#: En un paquete no pasa: el .qgs fija su propio encuadre sobre el area de
#: estudio, asi que la primera vista ya sale encima de San Carlos. El
#: satelite da contexto -- relieve, cultivos, el casco urbano -- que un
#: poligono verde no da.
#:
#: Se usa el servicio de imagenes de Esri porque no pide clave ni cuenta.
#: Necesita internet: sin conexion la capa sale en blanco y las demas se
#: dibujan igual, que es el comportamiento correcto.
MAPA_BASE = {
    "nombre": "Satelite (Esri World Imagery)",
    "url": ("https://server.arcgisonline.com/ArcGIS/rest/services/"
            "World_Imagery/MapServer/tile/%7Bz%7D/%7By%7D/%7Bx%7D"),
    "zmin": 0,
    "zmax": 19,
}


def _capa_de_mapa_base() -> tuple:
    """
    Construye el XML del mapa base y su entrada en el arbol de capas.

    DOS COSAS QUE PARECEN DETALLE Y NO LO SON
    -----------------------------------------
    1. EL RECUADRO VA EN METROS, NO EN GRADOS. La primera version le ponia
       el recuadro del area de estudio, que esta en grados
       (-75,1 a -74,7). Pero esta capa vive en EPSG:3857, donde las
       unidades son metros: para QGIS eso era una caja de cuarenta
       centimetros junto a la costa de Africa, y por eso no dibujaba nada.
       Un servicio de teselas cubre el mundo, asi que su recuadro es el
       mundo entero en metros.

    2. HACE FALTA EL `<pipe>` CON SU RENDERIZADOR. Sin el, QGIS carga la
       capa, la da por valida y no la pinta: no sabe como convertir las
       teselas en pixeles. Son imagenes en color, asi que el renderizador
       es `singlebandcolordata` sobre la banda 1.

    Las dos faltaban a la vez, y las dos daban el mismo sintoma: la capa
    aparecia en la lista con un interrogante y el mapa salia gris. Por eso
    `isValid()` devolvia True y la bateria la daba por buena: "valida" no
    significa "se ve".

    Returns:
        Tupla `(xml_de_la_capa, xml_del_arbol, identificador)`.
    """
    ident = "mapa_base_satelite"
    fuente = (f"type=xyz&amp;url={MAPA_BASE['url']}"
              f"&amp;zmax={MAPA_BASE['zmax']}&amp;zmin={MAPA_BASE['zmin']}")
    # El mundo entero en Pseudo-Mercator: es lo que cubre el servicio.
    borde = 20037508.342789244
    srs_tesela = (
        '<spatialrefsys>'
        '<proj4>+proj=merc +a=6378137 +b=6378137 +lat_ts=0 +lon_0=0 '
        '+x_0=0 +y_0=0 +k=1 +units=m +nadgrids=@null +wktext +no_defs</proj4>'
        '<srsid>3857</srsid><srid>3857</srid><authid>EPSG:3857</authid>'
        '<description>WGS 84 / Pseudo-Mercator</description>'
        '<projectionacronym>merc</projectionacronym>'
        '<ellipsoidacronym>EPSG:7030</ellipsoidacronym>'
        '<geographicflag>false</geographicflag></spatialrefsys>')
    tuberia = (
        '<pipe>'
        '<provider><resampling enabled="false" maxOversampling="2" '
        'zoomedInResamplingMethod="nearestNeighbour" '
        'zoomedOutResamplingMethod="nearestNeighbour"/></provider>'
        '<rasterrenderer nodataColor="" opacity="1" '
        'type="singlebandcolordata" band="1" alphaBand="-1">'
        '<rasterTransparency/></rasterrenderer>'
        '</pipe>')
    # El proveedor de teselas se llama "wms" en QGIS aunque sea XYZ: es el
    # mismo proveedor con otro `type` en la cadena de conexion.
    capa = (
        f'<maplayer type="raster" hasScaleBasedVisibilityFlag="0">'
        f'<extent><xmin>-{borde}</xmin><ymin>-{borde}</ymin>'
        f'<xmax>{borde}</xmax><ymax>{borde}</ymax></extent>'
        f'<wgs84extent><xmin>-180</xmin><ymin>-85.0511287798066</ymin>'
        f'<xmax>180</xmax><ymax>85.0511287798066</ymax></wgs84extent>'
        f'<id>{ident}</id>'
        f'<datasource>{fuente}</datasource>'
        f'<layername>{MAPA_BASE["nombre"]}</layername>'
        f'<srs>{srs_tesela}</srs>'
        f'<provider>wms</provider>'
        f'{tuberia}'
        f'</maplayer>')
    arbol = (f'<layer-tree-layer id="{ident}" '
             f'name="{MAPA_BASE["nombre"]}" source="{fuente}" '
             f'providerKey="wms" checked="Qt::Checked" expanded="0">'
             f'<customproperties/></layer-tree-layer>')
    return capa, arbol, ident


def _wkb(tipo: str, coordenadas) -> bytes:
    """
    Convierte una geometria de GeoJSON a WKB.

    Args:
        tipo: "Point", "LineString" o "Polygon".
        coordenadas: La lista de coordenadas tal como viene del GeoJSON.

    Returns:
        La geometria en WKB, en orden de bytes little-endian.

    Raises:
        ValueError: Si el tipo de geometria no se sabe escribir.
    """
    if tipo == "Point":
        x, y = coordenadas[0], coordenadas[1]
        return struct.pack("<BIdd", 1, 1, float(x), float(y))

    if tipo == "LineString":
        cuerpo = struct.pack("<BII", 1, 2, len(coordenadas))
        for punto in coordenadas:
            cuerpo += struct.pack("<dd", float(punto[0]), float(punto[1]))
        return cuerpo

    if tipo == "Polygon":
        cuerpo = struct.pack("<BII", 1, 3, len(coordenadas))
        for anillo in coordenadas:
            cuerpo += struct.pack("<I", len(anillo))
            for punto in anillo:
                cuerpo += struct.pack("<dd", float(punto[0]), float(punto[1]))
        return cuerpo

    raise ValueError(f"no se sabe escribir una geometria '{tipo}'")


def _vertices(tipo: str, coordenadas):
    """
    Recorre todos los puntos de una geometria, sea del tipo que sea.

    Sirve para calcular la envolvente sin repetir tres bucles distintos.

    Args:
        tipo: "Point", "LineString" o "Polygon".
        coordenadas: Las coordenadas del GeoJSON.

    Yields:
        Pares `(x, y)`.
    """
    if tipo == "Point":
        yield float(coordenadas[0]), float(coordenadas[1])
    elif tipo == "LineString":
        for punto in coordenadas:
            yield float(punto[0]), float(punto[1])
    elif tipo == "Polygon":
        for anillo in coordenadas:
            for punto in anillo:
                yield float(punto[0]), float(punto[1])


def _blob(tipo: str, coordenadas) -> bytes:
    """
    Envuelve el WKB en el encabezado que espera un GeoPackage.

    EL ENCABEZADO NO ES DECORATIVO
    ------------------------------
    Son ocho bytes mas la envolvente: la marca "GP", la version, una bandera
    y el identificador del sistema de referencia. La bandera dice, entre
    otras cosas, que tipo de envolvente viene detras, y es justo el byte que
    `core/campo.py` lee para saber cuantos bytes saltar. Aqui se escribe
    siempre con envolvente de cuatro dobles (minx, maxx, miny, maxy), que es
    el valor 1 de ese campo.

    Args:
        tipo: Tipo de geometria.
        coordenadas: Las coordenadas del GeoJSON.

    Returns:
        El binario completo, listo para guardar en la columna de geometria.
    """
    puntos = list(_vertices(tipo, coordenadas))
    equis = [p[0] for p in puntos]
    yes = [p[1] for p in puntos]

    # bit 0: los enteros van en little-endian. bits 1-3: envolvente del
    # tipo 1, que son cuatro dobles. Juntos dan 0b011 = 3.
    bandera = 0x01 | (1 << 1)
    cabecera = struct.pack("<ccBBi", b"G", b"P", 0, bandera, SRS_ID)
    envolvente = struct.pack("<dddd", min(equis), max(equis),
                             min(yes), max(yes))
    return cabecera + envolvente + _wkb(tipo, coordenadas)


def _nombre_de_tabla(nombre: str) -> str:
    """
    Convierte el nombre de una capa en un nombre de tabla valido.

    Args:
        nombre: Nombre visible de la capa.

    Returns:
        Un nombre en minusculas, sin acentos ni espacios.
    """
    import unicodedata

    plano = unicodedata.normalize("NFKD", nombre)
    plano = "".join(c for c in plano if not unicodedata.combining(c))
    limpio = "".join(c if c.isalnum() else "_" for c in plano.lower())
    while "__" in limpio:
        limpio = limpio.replace("__", "_")
    return limpio.strip("_") or "capa"


def _crear_catalogo(con) -> None:
    """
    Crea las tablas de catalogo que todo GeoPackage tiene que traer.

    Sin estas tres tablas el archivo es un SQLite normal y QGIS no encuentra
    ninguna capa dentro, aunque los datos esten.

    Args:
        con: Conexion de sqlite3 ya abierta.
    """
    con.execute(f"PRAGMA application_id = {APPLICATION_ID}")
    con.execute(f"PRAGMA user_version = {USER_VERSION}")

    con.execute("""
        CREATE TABLE gpkg_spatial_ref_sys (
            srs_name TEXT NOT NULL,
            srs_id INTEGER NOT NULL PRIMARY KEY,
            organization TEXT NOT NULL,
            organization_coordsys_id INTEGER NOT NULL,
            definition TEXT NOT NULL,
            description TEXT)""")
    # Las tres filas de abajo las exige la norma, incluso las dos que no se
    # usan: un lector estricto rechaza el archivo si faltan.
    con.executemany(
        "INSERT INTO gpkg_spatial_ref_sys VALUES (?, ?, ?, ?, ?, ?)",
        [("Undefined cartesian SRS", -1, "NONE", -1, "undefined",
          "sistema cartesiano sin definir"),
         ("Undefined geographic SRS", 0, "NONE", 0, "undefined",
          "sistema geografico sin definir"),
         ("WGS 84 geodetic", 4326, "EPSG", 4326, WKT_4326,
          "coordenadas en grados, las del GPS")])

    con.execute("""
        CREATE TABLE gpkg_contents (
            table_name TEXT NOT NULL PRIMARY KEY,
            data_type TEXT NOT NULL,
            identifier TEXT UNIQUE,
            description TEXT DEFAULT '',
            last_change DATETIME NOT NULL
                DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
            min_x DOUBLE, min_y DOUBLE, max_x DOUBLE, max_y DOUBLE,
            srs_id INTEGER,
            CONSTRAINT fk_gc_r_srs_id FOREIGN KEY (srs_id)
                REFERENCES gpkg_spatial_ref_sys(srs_id))""")

    con.execute("""
        CREATE TABLE gpkg_geometry_columns (
            table_name TEXT NOT NULL,
            column_name TEXT NOT NULL,
            geometry_type_name TEXT NOT NULL,
            srs_id INTEGER NOT NULL,
            z TINYINT NOT NULL,
            m TINYINT NOT NULL,
            CONSTRAINT pk_geom_cols PRIMARY KEY (table_name, column_name),
            CONSTRAINT fk_gc_tn FOREIGN KEY (table_name)
                REFERENCES gpkg_contents(table_name))""")


def escribir_geopaquete(ruta: str, capas: list) -> dict:
    """
    Escribe un GeoPackage con las capas que se le pasen.

    Args:
        ruta: Donde crear el archivo .gpkg. Si existe, se sobrescribe.
        capas: Lista de diccionarios con `nombre`, `tipo` (Point,
            LineString o Polygon) y `entidades`, cada una con
            `propiedades` y `coordenadas`.

    Returns:
        Diccionario con `capas` (lista de lo escrito, con su tabla,
        recuento y envolvente) y `bytes`.

    Raises:
        ValueError: Si una capa no trae entidades o su tipo no se conoce.
    """
    if os.path.exists(ruta):
        os.remove(ruta)

    escritas = []
    con = sqlite3.connect(ruta)
    try:
        _crear_catalogo(con)

        for capa in capas:
            entidades = capa.get("entidades") or []
            if not entidades:
                raise ValueError(f"la capa '{capa.get('nombre')}' no trae "
                                 f"ninguna entidad")
            tipo = capa["tipo"]
            tabla = _nombre_de_tabla(capa["nombre"])

            # Las columnas salen de las propiedades de TODAS las entidades,
            # no de la primera: en un GeoJSON cada entidad puede traer sus
            # propias claves y quedarse sin una columna perderia datos.
            columnas = []
            for entidad in entidades:
                for clave in (entidad.get("propiedades") or {}):
                    if clave not in columnas:
                        columnas.append(clave)

            definicion = ", ".join(f'"{c}" TEXT' for c in columnas)
            con.execute(
                f'CREATE TABLE "{tabla}" ('
                f'fid INTEGER PRIMARY KEY AUTOINCREMENT, '
                f'geom BLOB'
                + (", " + definicion if definicion else "") + ")")

            limites = [None, None, None, None]
            filas = []
            for entidad in entidades:
                coordenadas = entidad["coordenadas"]
                blob = _blob(tipo, coordenadas)
                for x, y in _vertices(tipo, coordenadas):
                    limites[0] = x if limites[0] is None else min(limites[0], x)
                    limites[1] = y if limites[1] is None else min(limites[1], y)
                    limites[2] = x if limites[2] is None else max(limites[2], x)
                    limites[3] = y if limites[3] is None else max(limites[3], y)
                propiedades = entidad.get("propiedades") or {}
                valores = [blob] + [
                    None if propiedades.get(c) is None
                    else str(propiedades.get(c)) for c in columnas]
                filas.append(valores)

            huecos = ", ".join("?" * (len(columnas) + 1))
            nombres = ", ".join(['geom'] + [f'"{c}"' for c in columnas])
            con.executemany(
                f'INSERT INTO "{tabla}" ({nombres}) VALUES ({huecos})', filas)

            con.execute(
                "INSERT INTO gpkg_contents (table_name, data_type, "
                "identifier, description, last_change, min_x, min_y, max_x, "
                "max_y, srs_id) VALUES (?, 'features', ?, ?, ?, ?, ?, ?, ?, ?)",
                (tabla, capa["nombre"], capa.get("descripcion", ""),
                 time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime()),
                 limites[0], limites[1], limites[2], limites[3], SRS_ID))
            con.execute(
                "INSERT INTO gpkg_geometry_columns VALUES (?, 'geom', ?, ?, "
                "0, 0)", (tabla, tipo.upper(), SRS_ID))

            escritas.append({"nombre": capa["nombre"], "tabla": tabla,
                             "tipo": tipo, "entidades": len(entidades),
                             "columnas": columnas, "limites": limites})
        con.commit()
    finally:
        con.close()

    return {"capas": escritas, "bytes": os.path.getsize(ruta)}


def _simbolo(tipo: str, indice: int = 0) -> str:
    """
    Devuelve el XML del simbolo con que se dibuja una capa.

    Un proyecto sin estilos se abre, pero QGIS le pone a cada capa un color
    al azar y el area de estudio puede salir tapando los puntos. Definirlos
    cuesta poco y hace que el mapa se entienda nada mas abrirlo.

    Args:
        tipo: "Point", "LineString" o "Polygon".
        indice: Posicion de la capa entre las de su tipo, para que cada
            una lleve un color distinto de la paleta.

    Returns:
        El fragmento XML del `renderer-v2`.
    """
    e = dict(ESTILOS[tipo])
    e["color"] = _color_de_capa(indice, tipo)
    # El borde de un poligono sigue la paleta: es lo unico que lo
    # distingue, porque el relleno va casi transparente para no tapar el
    # mapa base.
    if tipo == "Polygon":
        gama = PALETA_POR_TIPO["Polygon"]
        e["borde"] = gama[indice % len(gama)]
    if tipo == "Point":
        capa_simbolo = (
            '<layer class="SimpleMarker">'
            f'<Option type="Map">'
            f'<Option name="color" type="QString" value="{e["color"]}"/>'
            f'<Option name="outline_color" type="QString" value="{e["borde"]}"/>'
            '<Option name="outline_width" type="QString" value="0.2"/>'
            '<Option name="name" type="QString" value="circle"/>'
            f'<Option name="size" type="QString" value="{e["tamano"]}"/>'
            '</Option></layer>')
        clase = "marker"
    elif tipo == "LineString":
        capa_simbolo = (
            '<layer class="SimpleLine">'
            '<Option type="Map">'
            f'<Option name="line_color" type="QString" value="{e["color"]}"/>'
            f'<Option name="line_width" type="QString" value="{e["ancho"]}"/>'
            '</Option></layer>')
        clase = "line"
    else:
        capa_simbolo = (
            '<layer class="SimpleFill">'
            '<Option type="Map">'
            f'<Option name="color" type="QString" value="{e["color"]}"/>'
            f'<Option name="outline_color" type="QString" value="{e["borde"]}"/>'
            f'<Option name="outline_width" type="QString" value="{e["ancho"]}"/>'
            '<Option name="style" type="QString" value="solid"/>'
            '</Option></layer>')
        clase = "fill"

    return (f'<renderer-v2 type="singleSymbol" symbollevels="0">'
            f'<symbols><symbol name="0" type="{clase}" alpha="1">'
            f'{capa_simbolo}</symbol></symbols></renderer-v2>')


def escribir_proyecto(ruta: str, capas: list, nombre_gpkg: str,
                      titulo: str = "Area de estudio") -> None:
    """
    Escribe el archivo .qgs que abre las capas ya colocadas.

    LAS RUTAS VAN RELATIVAS
    -----------------------
    `<Absolute>false</Absolute>` es lo que hace que el paquete funcione en
    otro ordenador. Con rutas absolutas, el proyecto buscaria las capas en
    la carpeta del servidor que lo genero y las encontraria vacias. Es el
    mismo ajuste que trae el proyecto de mariposas del equipo.

    EL ORDEN DE LAS CAPAS NO ES CASUAL
    ----------------------------------
    Los poligonos van ABAJO y los puntos ARRIBA. Al reves, el relleno del
    area de estudio taparia los puntos de muestreo y el mapa se abriria
    pareciendo vacio.

    Args:
        ruta: Donde escribir el .qgs.
        capas: Lo que devolvio `escribir_geopaquete` en su clave `capas`.
        nombre_gpkg: Nombre del .gpkg, que va en las rutas relativas.
        titulo: Titulo del proyecto.
    """
    orden = {"Polygon": 0, "LineString": 1, "Point": 2}
    de_abajo_a_arriba = sorted(capas, key=lambda c: orden.get(c["tipo"], 9))

    limites = [c["limites"] for c in capas if c.get("limites")]
    if limites:
        xmin = min(l[0] for l in limites)
        ymin = min(l[1] for l in limites)
        xmax = max(l[2] for l in limites)
        ymax = max(l[3] for l in limites)
    else:
        xmin, ymin, xmax, ymax = -75.2, 6.0, -74.8, 6.4
    # Un margen del 5% para que lo dibujado no quede pegado al borde.
    ancho, alto = (xmax - xmin) or 0.1, (ymax - ymin) or 0.1
    xmin, xmax = xmin - ancho * 0.05, xmax + ancho * 0.05
    ymin, ymax = ymin - alto * 0.05, ymax + alto * 0.05

    srs = (
        '<spatialrefsys>'
        f'<wkt>{WKT_4326}</wkt>'
        '<proj4>+proj=longlat +datum=WGS84 +no_defs</proj4>'
        '<srsid>3452</srsid><srid>4326</srid><authid>EPSG:4326</authid>'
        '<description>WGS 84</description><projectionacronym>longlat'
        '</projectionacronym><ellipsoidacronym>EPSG:7030</ellipsoidacronym>'
        '<geographicflag>true</geographicflag></spatialrefsys>')

    arbol, proyectadas, ordenadas = [], [], []
    # Se numeran por separado dentro de cada tipo: asi la primera capa de
    # puntos y la primera de lineas pueden compartir color sin confundirse,
    # y tres capas de puntos salen en tres colores distintos.
    vistos = {}
    for capa in de_abajo_a_arriba:
        vistos[capa["tipo"]] = vistos.get(capa["tipo"], -1) + 1
        indice_color = vistos[capa["tipo"]]
        ident = f"{capa['tabla']}_capa"
        fuente = (f"./{nombre_gpkg}|layername={capa['tabla']}")
        arbol.append(
            f'<layer-tree-layer id="{ident}" name="{capa["nombre"]}" '
            f'source="{fuente}" providerKey="ogr" checked="Qt::Checked" '
            f'expanded="1"><customproperties/></layer-tree-layer>')
        ordenadas.append(f'<layer id="{ident}"/>')
        l = capa["limites"]
        proyectadas.append(
            f'<maplayer type="vector" geometry="{capa["tipo"]}" '
            f'wkbType="{capa["tipo"]}" hasScaleBasedVisibilityFlag="0">'
            f'<extent><xmin>{l[0]}</xmin><ymin>{l[1]}</ymin>'
            f'<xmax>{l[2]}</xmax><ymax>{l[3]}</ymax></extent>'
            f'<id>{ident}</id>'
            f'<datasource>{fuente}</datasource>'
            f'<layername>{capa["nombre"]}</layername>'
            f'<srs>{srs}</srs>'
            f'<provider encoding="UTF-8">ogr</provider>'
            f'{_simbolo(capa["tipo"], indice_color)}'
            f'</maplayer>')

    # EL MAPA BASE VA DEBAJO DE TODO
    # ------------------------------
    # Se anade el ultimo a estas listas, que estan en orden de abajo a
    # arriba, asi que al invertirlas acaba al final del arbol: el fondo.
    # Encima de las capas taparia justo lo que se quiere ver.
    capa_base, arbol_base, ident_base = _capa_de_mapa_base()
    proyectadas.insert(0, capa_base)
    arbol.insert(0, arbol_base)
    ordenadas.insert(0, f'<layer id="{ident_base}"/>')

    # El orden del arbol se escribe al reves: QGIS pinta la primera capa de
    # la lista ARRIBA del todo.
    arbol.reverse()
    ordenadas.reverse()

    xml = (
        '<!DOCTYPE qgis PUBLIC \'http://mrcc.com/qgis.dtd\' \'SYSTEM\'>\n'
        '<qgis version="3.40.0" projectname="' + titulo + '">\n'
        '<homePath path=""/>\n'
        f'<title>{titulo}</title>\n'
        f'<projectCrs>{srs}</projectCrs>\n'
        '<layer-tree-group>\n' + "\n".join(arbol) +
        '\n<custom-order enabled="0"/>\n</layer-tree-group>\n'
        '<mapcanvas name="theMapCanvas">'
        f'<units>degrees</units>'
        f'<extent><xmin>{xmin}</xmin><ymin>{ymin}</ymin>'
        f'<xmax>{xmax}</xmax><ymax>{ymax}</ymax></extent>'
        f'<destinationsrs>{srs}</destinationsrs>'
        '</mapcanvas>\n'
        '<projectlayers>\n' + "\n".join(proyectadas) + '\n</projectlayers>\n'
        '<layerorder>' + "".join(ordenadas) + '</layerorder>\n'
        # DE AQUI LEE QGIS EL SISTEMA DE REFERENCIA DEL PROYECTO
        # ------------------------------------------------------
        # No del bloque <projectCrs> de arriba, que tambien se escribe
        # porque es el que llevan los proyectos modernos. Se comprobo
        # probando las dos cosas por separado: con <projectCrs> solo, el
        # proyecto abria SIN sistema de referencia; anadiendo esto, con
        # EPSG:4326.
        #
        # Sin sistema de referencia QGIS no puede reproyectar nada, y el
        # mapa base de satelite -- que viene en metros -- no se dibujaba:
        # la capa salia con un interrogante y el fondo, gris. Los vectores
        # se veian igual porque ya estaban en grados, y por eso el fallo
        # parecia ser solo del satelite.
        '<properties>'
        '<SpatialRefSys>'
        '<ProjectionsEnabled type="int">1</ProjectionsEnabled>'
        '<ProjectCrs type="QString">EPSG:4326</ProjectCrs>'
        '<ProjectCRSID type="int">3452</ProjectCRSID>'
        '<ProjectCRSProj4String type="QString">'
        '+proj=longlat +datum=WGS84 +no_defs</ProjectCRSProj4String>'
        '</SpatialRefSys>'
        '<Paths><Absolute type="bool">false</Absolute></Paths>'
        '</properties>\n'
        '</qgis>\n')

    with open(ruta, "w", encoding="utf-8") as fh:
        fh.write(xml)


def capas_desde_geojson(rutas: list) -> list:
    """
    Lee los GeoJSON del proyecto y los deja listos para empaquetar.

    Cada archivo puede traer geometrias de un solo tipo; si trae varias, se
    parte en una capa por tipo, que es lo que QGIS espera.

    Args:
        rutas: Rutas de los .geojson a leer. Las que no existan se saltan.

    Returns:
        Lista de capas, cada una con `nombre`, `tipo` y `entidades`.
    """
    capas = []
    for ruta in rutas:
        if not os.path.exists(ruta):
            continue
        try:
            with open(ruta, encoding="utf-8") as fh:
                datos = json.load(fh)
        except (OSError, ValueError):
            continue

        base = os.path.splitext(os.path.basename(ruta))[0].replace("_", " ")
        por_tipo = {}
        for entidad in datos.get("features") or []:
            geometria = entidad.get("geometry") or {}
            tipo = geometria.get("type")
            if tipo not in ESTILOS:
                continue
            por_tipo.setdefault(tipo, []).append({
                "propiedades": entidad.get("properties") or {},
                "coordenadas": geometria.get("coordinates"),
            })

        for tipo, entidades in por_tipo.items():
            nombre = base if len(por_tipo) == 1 else f"{base} ({tipo})"
            capas.append({"nombre": nombre.strip().capitalize(),
                          "tipo": tipo, "entidades": entidades})
    return capas


def construir(destino: str, rutas_geojson: list,
              titulo: str = "Area de estudio - San Carlos") -> dict:
    """
    Arma el paquete completo y lo deja en un .zip.

    Args:
        destino: Ruta del .zip a crear.
        rutas_geojson: Capas de origen.
        titulo: Titulo del proyecto de QGIS.

    Returns:
        Diccionario con `zip`, `capas`, `entidades` y `bytes`.

    Raises:
        ValueError: Si no hay ninguna capa con geometria que empaquetar.
    """
    import shutil
    import tempfile

    capas = capas_desde_geojson(rutas_geojson)
    if not capas:
        raise ValueError(
            "No hay ninguna capa con geometria que empaquetar. Genera "
            "primero el area de estudio: sin eso no hay nada que dibujar.")

    carpeta = tempfile.mkdtemp(prefix="paquete_qgis_")
    try:
        nombre_gpkg = "capas.gpkg"
        escrito = escribir_geopaquete(os.path.join(carpeta, nombre_gpkg),
                                      capas)
        escribir_proyecto(os.path.join(carpeta, "proyecto.qgs"),
                          escrito["capas"], nombre_gpkg, titulo)

        with open(os.path.join(carpeta, "LEEME.txt"), "w",
                  encoding="utf-8") as fh:
            fh.write(_leeme(titulo, escrito["capas"], nombre_gpkg))

        os.makedirs(os.path.dirname(os.path.abspath(destino)), exist_ok=True)
        with zipfile.ZipFile(destino, "w", zipfile.ZIP_DEFLATED) as z:
            for archivo in sorted(os.listdir(carpeta)):
                z.write(os.path.join(carpeta, archivo), archivo)

        return {
            "zip": destino,
            "capas": escrito["capas"],
            "entidades": sum(c["entidades"] for c in escrito["capas"]),
            "bytes": os.path.getsize(destino),
        }
    finally:
        shutil.rmtree(carpeta, ignore_errors=True)


def _leeme(titulo: str, capas: list, nombre_gpkg: str) -> str:
    """
    Redacta el LEEME que acompana al paquete.

    Args:
        titulo: Titulo del proyecto.
        capas: Las capas escritas.
        nombre_gpkg: Nombre del GeoPackage.

    Returns:
        El texto del archivo.
    """
    lineas = [
        titulo,
        "=" * len(titulo),
        "",
        "COMO SE ABRE",
        "  Descomprime esta carpeta entera y haz doble clic en",
        "  'proyecto.qgs'. Las capas se cargan solas, con sus colores.",
        "",
        "  Importante: no muevas 'proyecto.qgs' fuera de la carpeta. Las",
        "  rutas son relativas a proposito, para que el paquete funcione en",
        "  cualquier ordenador, y eso significa que el proyecto y el",
        f"  '{nombre_gpkg}' tienen que seguir juntos.",
        "",
        "QUE TRAE",
    ]
    for capa in capas:
        lineas.append(f"  {capa['nombre']}")
        lineas.append(f"      {capa['entidades']} entidades de tipo "
                      f"{capa['tipo']}")
        if capa["columnas"]:
            lineas.append(f"      campos: {', '.join(capa['columnas'])}")
    lineas += [
        "",
        "SISTEMA DE REFERENCIA",
        "  EPSG:4326 (WGS 84), coordenadas en grados. Es el mismo que usan",
        "  el GPS y los datos de campo de QField, asi que las capas que",
        "  traigas de alli encajan sin reproyectar.",
        "",
        "POR QUE GEOPACKAGE Y NO SHAPEFILE",
        "  Un shapefile corta los nombres de campo a 10 caracteres. El",
        "  GeoPackage los guarda enteros, es un solo archivo en vez de",
        "  cinco, y lo abren igual QGIS y QField.",
        "",
        "  Si alguien necesita shapefile, desde QGIS: clic derecho en la",
        "  capa > Exportar > Guardar objetos como... > ESRI Shapefile.",
        "",
        "DE DONDE SALEN ESTOS DATOS",
        "  Los genera el Copiloto Ambiental a partir de lo que hay cargado",
        "  en la carpeta de Drive del proyecto. No se escribio nada a mano.",
    ]
    return "\n".join(lineas) + "\n"
