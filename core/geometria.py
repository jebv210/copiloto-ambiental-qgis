"""
Saca la geometria de verdad de un shapefile y la deja en grados.

POR QUE HACIA FALTA
-------------------
El proyecto ya sabia LEER un shapefile: abria la cabecera, contaba las
entidades y volcaba su tabla de atributos como texto. Con eso el agente
podia responder "esa capa existe y trae 1.882 registros", que es mejor que
callar, pero el mapa seguia sin una sola linea nueva.

Es la diferencia entre SABER QUE HAY y PODER DIBUJARLO. El caso que lo
destapo fue un .zip de Drive con el Registro Unico Nacional de Areas
Protegidas: 67 MB de poligonos reales, 1.882 areas protegidas de Colombia,
y de todo eso el sistema se quedaba con un parrafo de texto. La geometria
estaba ahi, bajada, leida y tirada a la basura.

Este modulo la recoge: lee los poligonos, los pasa a grados y los deja en
GeoJSON, que es lo que el resto del proyecto sabe dibujar.

LO QUE NO HACE, Y ES DELIBERADO
-------------------------------
No coloca geometria que no sepa situar. Si el .prj trae una proyeccion que
aqui no se sabe invertir, las coordenadas se devuelven tal cual y la capa
queda marcada como NO situable, en vez de dibujarse mil kilometros al lado
de donde esta. Un mapa equivocado es peor que un mapa que falta: el que
falta se nota.
"""

import math
import os
import struct

#: Radio de la esfera auxiliar de Web Mercator, en metros.
#:
#: Es el semieje mayor del WGS84 usado como si la Tierra fuera redonda. Lo
#: define asi el propio EPSG:3857, que es la proyeccion de casi todo lo que
#: se descarga de un visor ArcGIS -- incluido el RUNAP.
RADIO_WEB_MERCATOR = 6378137.0

#: Tipos de geometria de un shapefile, por su codigo en la cabecera.
#:
#: Las variantes Z y M son la misma forma con alturas o medidas detras. Para
#: dibujar en planta solo hacen falta X e Y, asi que se leen igual y lo de
#: atras se ignora.
FORMAS = {
    0: ("Nulo", None),
    1: ("Punto", "Point"),
    3: ("Linea", "LineString"),
    5: ("Poligono", "Polygon"),
    8: ("Multipunto", "MultiPoint"),
    11: ("Punto Z", "Point"),
    13: ("Linea Z", "LineString"),
    15: ("Poligono Z", "Polygon"),
    18: ("Multipunto Z", "MultiPoint"),
    21: ("Punto M", "Point"),
    23: ("Linea M", "LineString"),
    25: ("Poligono M", "Polygon"),
    28: ("Multipunto M", "MultiPoint"),
}

#: Cuantas entidades se leen como mucho de un shapefile.
#:
#: Es un guardia de memoria, no un juicio sobre la capa. Si se corta, se
#: DICE cuantas quedaron fuera: una capa a medias que no avisa es una capa
#: que miente sobre su contenido.
MAXIMO_ENTIDADES = 200000

#: Holgura, en grados, al recortar una capa por un rectangulo.
#:
#: Medio grado son unos 55 km. Se prefiere traer de mas: una capa nacional
#: recortada demasiado justo deja fuera el parque que empieza justo al otro
#: lado del limite municipal, que es precisamente el que hay que mirar en un
#: estudio de impacto.
MARGEN_GRADOS = 0.5


def _arco_meridiano(a: float, e2: float, lat: float) -> float:
    """
    Distancia sobre el meridiano desde el ecuador hasta una latitud.

    Es la serie clasica de Snyder. Hace falta para invertir una Transversa
    de Mercator, que es la familia a la que pertenecen todas las zonas UTM
    y los origenes nacionales de Colombia.

    Args:
        a: Semieje mayor del elipsoide, en metros.
        e2: Primera excentricidad al cuadrado.
        lat: Latitud en radianes.

    Returns:
        La distancia en metros.
    """
    return a * (
        (1 - e2 / 4 - 3 * e2 ** 2 / 64 - 5 * e2 ** 3 / 256) * lat
        - (3 * e2 / 8 + 3 * e2 ** 2 / 32 + 45 * e2 ** 3 / 1024)
        * math.sin(2 * lat)
        + (15 * e2 ** 2 / 256 + 45 * e2 ** 3 / 1024) * math.sin(4 * lat)
        - (35 * e2 ** 3 / 3072) * math.sin(6 * lat)
    )


def _inversa_transversa(parametros: dict):
    """
    Devuelve la funcion que pasa de Transversa de Mercator a grados.

    CUBRE MAS DE LO QUE PARECE
    --------------------------
    Toda zona UTM es una Transversa de Mercator con otro meridiano central,
    y los origenes de MAGNA-SIRGAS de Colombia tambien. Resolviendo la
    familia entera se resuelven de golpe los sistemas en los que llega casi
    cualquier cartografia oficial colombiana, sin depender de pyproj: el
    contenedor no trae GDAL ni se va a cargar por esto.

    Args:
        parametros: Lo leido del .prj.

    Returns:
        Una funcion `(x, y) -> (lon, lat)` en grados.
    """
    a = parametros["a"]
    f_inv = parametros["f_inv"]
    f = 1.0 / f_inv if f_inv else 0.0
    e2 = 2 * f - f * f
    ep2 = e2 / (1 - e2) if e2 < 1 else 0.0
    k0 = parametros.get("k0", 1.0) or 1.0
    fe = parametros.get("fe", 0.0)
    fn = parametros.get("fn", 0.0)
    lon0 = math.radians(parametros.get("lon0", 0.0))
    lat0 = math.radians(parametros.get("lat0", 0.0))
    m0 = _arco_meridiano(a, e2, lat0)
    e1 = (1 - math.sqrt(1 - e2)) / (1 + math.sqrt(1 - e2)) if e2 else 0.0

    def convertir(x, y):
        """
        Pasa un par proyectado a grados.

        Args:
            x: Coordenada este, en las unidades de la proyeccion.
            y: Coordenada norte, en las unidades de la proyeccion.

        Returns:
            Tupla `(longitud, latitud)` en grados decimales.
        """
        m = m0 + (y - fn) / k0
        mu = m / (a * (1 - e2 / 4 - 3 * e2 ** 2 / 64 - 5 * e2 ** 3 / 256))
        lat1 = (mu
                + (3 * e1 / 2 - 27 * e1 ** 3 / 32) * math.sin(2 * mu)
                + (21 * e1 ** 2 / 16 - 55 * e1 ** 4 / 32) * math.sin(4 * mu)
                + (151 * e1 ** 3 / 96) * math.sin(6 * mu)
                + (1097 * e1 ** 4 / 512) * math.sin(8 * mu))
        seno = math.sin(lat1)
        coseno = math.cos(lat1)
        if abs(coseno) < 1e-12:
            return math.degrees(lon0), math.degrees(lat1)
        tang = math.tan(lat1)
        c1 = ep2 * coseno * coseno
        t1 = tang * tang
        n1 = a / math.sqrt(1 - e2 * seno * seno)
        r1 = a * (1 - e2) / (1 - e2 * seno * seno) ** 1.5
        d = (x - fe) / (n1 * k0)
        lat = lat1 - (n1 * tang / r1) * (
            d ** 2 / 2
            - (5 + 3 * t1 + 10 * c1 - 4 * c1 ** 2 - 9 * ep2) * d ** 4 / 24
            + (61 + 90 * t1 + 298 * c1 + 45 * t1 ** 2 - 252 * ep2
               - 3 * c1 ** 2) * d ** 6 / 720)
        lon = lon0 + (
            d
            - (1 + 2 * t1 + c1) * d ** 3 / 6
            + (5 - 2 * c1 + 28 * t1 - 3 * c1 ** 2 + 8 * ep2
               + 24 * t1 ** 2) * d ** 5 / 120) / coseno
        return math.degrees(lon), math.degrees(lat)

    return convertir


def _inversa_mercator(parametros: dict):
    """
    Devuelve la funcion que pasa de Mercator esferica a grados.

    Es la inversa de EPSG:3857, la proyeccion de los mapas de teselas y de
    casi toda descarga hecha desde un visor web.

    Args:
        parametros: Lo leido del .prj.

    Returns:
        Una funcion `(x, y) -> (lon, lat)` en grados.
    """
    radio = parametros.get("a") or RADIO_WEB_MERCATOR
    fe = parametros.get("fe", 0.0)
    fn = parametros.get("fn", 0.0)

    def convertir(x, y):
        """
        Pasa un par proyectado a grados.

        Args:
            x: Coordenada este, en las unidades de la proyeccion.
            y: Coordenada norte, en las unidades de la proyeccion.

        Returns:
            Tupla `(longitud, latitud)` en grados decimales.
        """
        lon = math.degrees((x - fe) / radio)
        lat = math.degrees(2 * math.atan(math.exp((y - fn) / radio))
                           - math.pi / 2)
        return lon, lat

    return convertir


def _numeros_del_prj(texto: str) -> dict:
    """
    Saca del texto de un .prj los parametros que hacen falta para invertirlo.

    El .prj es WKT de una sola linea. No se parsea entero -- eso seria
    reescribir media libreria --, solo se buscan los parametros con nombre,
    que es como los escriben todos los programas que generan shapefiles.

    Args:
        texto: Contenido del .prj.

    Returns:
        Diccionario con `a`, `f_inv`, `k0`, `fe`, `fn`, `lon0` y `lat0`.
    """
    import re

    def parametro(nombre, defecto):
        """
        Busca un PARAMETER del WKT por su nombre.

        Args:
            nombre: Nombre del parametro tal como aparece en el WKT.
            defecto: Valor que se usa si el parametro no esta.

        Returns:
            El valor como numero.
        """
        patron = (r'PARAMETER\s*\[\s*"' + nombre
                  + r'"\s*,\s*(-?[0-9.eE+]+)')
        hallado = re.search(patron, texto, re.IGNORECASE)
        return float(hallado.group(1)) if hallado else defecto

    elipsoide = re.search(
        r'SPHEROID\s*\[\s*"[^"]*"\s*,\s*(-?[0-9.eE+]+)\s*,\s*(-?[0-9.eE+]+)',
        texto, re.IGNORECASE)
    a = float(elipsoide.group(1)) if elipsoide else RADIO_WEB_MERCATOR
    f_inv = float(elipsoide.group(2)) if elipsoide else 0.0

    return {
        "a": a,
        "f_inv": f_inv,
        "k0": parametro("Scale_Factor", 1.0) or parametro("scale_factor", 1.0),
        "fe": parametro("False_Easting", 0.0),
        "fn": parametro("False_Northing", 0.0),
        "lon0": parametro("Central_Meridian",
                          parametro("longitude_of_center", 0.0)),
        "lat0": parametro("Latitude_Of_Origin",
                          parametro("latitude_of_origin", 0.0)),
    }


def conversion_de_wkt(texto: str) -> dict:
    """
    Prepara la conversion a grados a partir del texto de un sistema WKT.

    Se separa de `leer_prj` porque el mismo texto llega de dos sitios muy
    distintos: en un shapefile viene en un archivo `.prj` al lado, y en una
    geodatabase viene incrustado en la cabecera binaria de la capa. Es la
    misma decision tomada sobre el mismo texto, asi que se toma en un solo
    sitio.

    Args:
        texto: Definicion WKT del sistema de referencia.

    Returns:
        Diccionario con `nombre`, `grados`, `convertir` y `aviso`.
    """
    texto = (texto or "").strip()
    if not texto:
        return {"nombre": "", "grados": False, "convertir": None,
                "aviso": "la capa no dice en que sistema de referencia "
                         "estan sus coordenadas"}

    trozos = texto.split('"')
    nombre = trozos[1] if len(trozos) > 1 else texto[:70]
    alto = texto.upper()

    # Sin PROJCS no hay proyeccion: son grados y no hay nada que convertir.
    if "PROJCS" not in alto:
        return {"nombre": nombre, "grados": True, "convertir": None,
                "aviso": ""}

    parametros = _numeros_del_prj(texto)
    if "MERCATOR_AUXILIARY_SPHERE" in alto or '"MERCATOR"' in alto:
        return {"nombre": nombre, "grados": False,
                "convertir": _inversa_mercator(parametros), "aviso": ""}
    if "TRANSVERSE_MERCATOR" in alto:
        return {"nombre": nombre, "grados": False,
                "convertir": _inversa_transversa(parametros), "aviso": ""}

    return {"nombre": nombre, "grados": False, "convertir": None,
            "aviso": f"la capa viene proyectada en '{nombre}', que este "
                     f"proyecto todavia no sabe convertir a grados; sus "
                     f"coordenadas no se pueden situar en el mapa"}


def leer_prj(ruta: str) -> dict:
    """
    Lee el sistema de referencia de un shapefile y prepara su conversion.

    Args:
        ruta: Ruta del .prj. Puede no existir.

    Returns:
        Lo mismo que `conversion_de_wkt`. Cuando `convertir` es None y
        `grados` es False, la capa NO se puede situar y hay que decirlo.
    """
    if not os.path.exists(ruta):
        return {"nombre": "", "grados": False, "convertir": None,
                "aviso": "no viene el .prj, asi que no se sabe en que "
                         "sistema de referencia estan estas coordenadas"}
    try:
        with open(ruta, encoding="utf-8", errors="replace") as fh:
            texto = fh.read(4000)
    except OSError as exc:
        return {"nombre": "", "grados": False, "convertir": None,
                "aviso": f"no se pudo leer el .prj: {str(exc)[:80]}"}
    return conversion_de_wkt(texto)


def _anillos(contenido: bytes, desde: int) -> list:
    """
    Lee las partes de una linea o un poligono de un shapefile.

    Args:
        contenido: Bytes del registro, desde el codigo de forma.
        desde: Posicion donde empieza el recuento de partes.

    Returns:
        Lista de listas de pares `(x, y)`.
    """
    n_partes, n_puntos = struct.unpack_from("<ii", contenido, desde)
    inicio = desde + 8
    partes = list(struct.unpack_from(f"<{n_partes}i", contenido, inicio))
    inicio += 4 * n_partes
    planos = struct.unpack_from(f"<{2 * n_puntos}d", contenido, inicio)

    partes.append(n_puntos)
    anillos = []
    for i in range(n_partes):
        desde_p, hasta_p = partes[i], partes[i + 1]
        anillos.append([(planos[2 * j], planos[2 * j + 1])
                        for j in range(desde_p, hasta_p)])
    return anillos


def _area_con_signo(anillo: list) -> float:
    """
    Area con signo de un anillo, para saber si es contorno o agujero.

    En un shapefile el contorno exterior va en sentido horario y los
    agujeros al reves. GeoJSON no usa ese convenio para decidir quien es
    quien: espera el exterior primero y los agujeros detras. Sin esta
    distincion, una laguna dentro de un parque se dibuja como un parque
    mas.

    Args:
        anillo: Lista de pares `(x, y)`.

    Returns:
        El doble del area, con signo.
    """
    total = 0.0
    for i in range(len(anillo) - 1):
        x1, y1 = anillo[i]
        x2, y2 = anillo[i + 1]
        total += x1 * y2 - x2 * y1
    return total


def _geometria_de(codigo: int, contenido: bytes):
    """
    Convierte un registro de shapefile en geometria de GeoJSON.

    Args:
        codigo: Codigo de forma del registro.
        contenido: Bytes del registro, desde el codigo de forma.

    Returns:
        Diccionario de geometria GeoJSON, o None si el registro es nulo.
    """
    tipo = FORMAS.get(codigo, (None, None))[1]
    if tipo is None:
        return None

    if tipo == "Point":
        x, y = struct.unpack_from("<dd", contenido, 4)
        return {"type": "Point", "coordinates": [x, y]}

    if tipo == "MultiPoint":
        n = struct.unpack_from("<i", contenido, 36)[0]
        planos = struct.unpack_from(f"<{2 * n}d", contenido, 40)
        return {"type": "MultiPoint",
                "coordinates": [[planos[2 * i], planos[2 * i + 1]]
                                for i in range(n)]}

    anillos = _anillos(contenido, 36)
    if not anillos:
        return None

    if tipo == "LineString":
        if len(anillos) == 1:
            return {"type": "LineString",
                    "coordinates": [list(p) for p in anillos[0]]}
        return {"type": "MultiLineString",
                "coordinates": [[list(p) for p in a] for a in anillos]}

    return agrupar_anillos(anillos)


def agrupar_anillos(anillos: list) -> dict:
    """
    Convierte una lista de anillos en un poligono o multipoligono GeoJSON.

    Cada anillo con area negativa se considera agujero del contorno que lo
    precede. Lo usan los dos lectores de geometria del proyecto -- el de
    shapefile y el de geodatabase -- porque los dos formatos guardan los
    anillos seguidos y con el mismo convenio de giro.

    Args:
        anillos: Lista de anillos, cada uno lista de pares `(x, y)`.

    Returns:
        Geometria GeoJSON de tipo Polygon o MultiPolygon.
    """
    poligonos = []
    for anillo in anillos:
        if _area_con_signo(anillo) < 0 and poligonos:
            poligonos[-1].append([list(p) for p in anillo])
        else:
            poligonos.append([[list(p) for p in anillo]])
    if len(poligonos) == 1:
        return {"type": "Polygon", "coordinates": poligonos[0]}
    return {"type": "MultiPolygon", "coordinates": poligonos}


def _mover(geometria: dict, convertir) -> dict:
    """
    Aplica una conversion de coordenadas a toda una geometria.

    Args:
        geometria: Geometria GeoJSON.
        convertir: Funcion `(x, y) -> (lon, lat)`.

    Returns:
        La geometria con las coordenadas convertidas.
    """
    def recorrer(nodo):
        """
        Baja por las listas anidadas hasta cada par de coordenadas.

        Args:
            nodo: Un par de coordenadas o una lista de nodos.

        Returns:
            La misma estructura con las coordenadas convertidas.
        """
        if (isinstance(nodo, list) and len(nodo) == 2
                and isinstance(nodo[0], (int, float))
                and isinstance(nodo[1], (int, float))):
            lon, lat = convertir(nodo[0], nodo[1])
            return [round(lon, 7), round(lat, 7)]
        return [recorrer(hijo) for hijo in nodo]

    return {"type": geometria["type"],
            "coordinates": recorrer(geometria["coordinates"])}


def _recuadro(geometria: dict) -> tuple:
    """
    Rectangulo que ocupa una geometria.

    Args:
        geometria: Geometria GeoJSON.

    Returns:
        Tupla `(xmin, ymin, xmax, ymax)`.
    """
    xs, ys = [], []

    def recorrer(nodo):
        """
        Baja por las listas anidadas apuntando cada coordenada.

        Args:
            nodo: Un par de coordenadas o una lista de nodos.
        """
        if (isinstance(nodo, list) and len(nodo) == 2
                and isinstance(nodo[0], (int, float))
                and isinstance(nodo[1], (int, float))):
            xs.append(nodo[0])
            ys.append(nodo[1])
            return
        for hijo in nodo:
            recorrer(hijo)

    recorrer(geometria["coordinates"])
    if not xs:
        return (0.0, 0.0, 0.0, 0.0)
    return (min(xs), min(ys), max(xs), max(ys))


def leer_shp(ruta: str, recuadro: tuple = None,
             maximo: int = MAXIMO_ENTIDADES) -> dict:
    """
    Lee un shapefile entero y lo devuelve como entidades de GeoJSON.

    QUE SE DEVUELVE Y EN QUE UNIDADES
    ---------------------------------
    Siempre en grados decimales cuando se ha podido convertir, porque es lo
    que usa el resto del proyecto. Si la proyeccion no se supo invertir, las
    coordenadas salen tal cual y `situable` viene en False: quien lo use
    tiene que mirarlo antes de dibujar nada.

    Args:
        ruta: Ruta del .shp.
        recuadro: `(lonmin, latmin, lonmax, latmax)` en grados. Si se da,
            solo se quedan las entidades que lo tocan. Sirve para no
            arrastrar un pais entero cuando interesa un municipio.
        maximo: Tope de entidades a leer.

    Returns:
        Diccionario con `entidades`, `tipo`, `crs`, `situable`, `total`,
        `descartadas`, `recortadas` y `aviso`. Con `error` si no se pudo
        leer.
    """
    base = os.path.splitext(ruta)[0]
    try:
        tamano = os.path.getsize(ruta)
        with open(ruta, "rb") as fh:
            cabecera = fh.read(100)
            if (len(cabecera) < 100
                    or struct.unpack(">I", cabecera[:4])[0] != 9994):
                return {"error": "no parece un shapefile valido"}
            codigo = struct.unpack("<i", cabecera[32:36])[0]
            crudo = fh.read()
    except (OSError, struct.error) as exc:
        return {"error": f"no se pudo leer el .shp: {str(exc)[:90]}"}

    sistema = leer_prj(base + ".prj")
    convertir = sistema["convertir"]
    situable = bool(sistema["grados"] or convertir)

    atributos = []
    dbf = base + ".dbf"
    if os.path.exists(dbf):
        atributos = leer_dbf_filas(dbf, maximo)

    entidades = []
    total = descartadas = recortadas = 0
    posicion = 0
    fin = len(crudo)
    while posicion + 8 <= fin:
        longitud = struct.unpack_from(">i", crudo, posicion + 4)[0] * 2
        inicio = posicion + 8
        posicion = inicio + longitud
        if longitud <= 0 or posicion > fin:
            break
        total += 1
        if len(entidades) >= maximo:
            recortadas += 1
            continue
        try:
            forma = struct.unpack_from("<i", crudo, inicio)[0]
            geometria = _geometria_de(forma, crudo[inicio:inicio + longitud])
        except (struct.error, IndexError):
            geometria = None
        if geometria is None:
            descartadas += 1
            continue

        # EL FILTRO VA ANTES DE CONVERTIR, Y SE NOTA
        # ------------------------------------------
        # Convertir cuesta dos funciones trigonometricas POR VERTICE, y el
        # RUNAP trae cuatro millones de vertices. Mirando primero el
        # rectangulo -- cuatro numeros, dos conversiones -- se descarta el
        # 99% del pais sin tocar un solo punto de su contorno. Medido en el
        # archivo real: de 97 segundos a menos de 4.
        #
        # El margen no es decoracion. Las dos proyecciones que aqui se
        # invierten son monotonas, asi que las esquinas del rectangulo
        # siguen siendo las esquinas; aun asi se deja medio grado de
        # holgura, porque un error de redondeo que deje fuera un parque que
        # si tocaba el area es un fallo que nadie veria.
        if recuadro and situable:
            xmin, ymin, xmax, ymax = _recuadro(geometria)
            if convertir:
                xmin, ymin = convertir(xmin, ymin)
                xmax, ymax = convertir(xmax, ymax)
            if (xmax < recuadro[0] - MARGEN_GRADOS
                    or xmin > recuadro[2] + MARGEN_GRADOS
                    or ymax < recuadro[1] - MARGEN_GRADOS
                    or ymin > recuadro[3] + MARGEN_GRADOS):
                descartadas += 1
                continue

        if convertir:
            geometria = _mover(geometria, convertir)
        propiedades = (atributos[total - 1]
                       if total - 1 < len(atributos) else {})
        entidades.append({"type": "Feature", "geometry": geometria,
                          "properties": propiedades})

    return {
        "entidades": entidades,
        "tipo": FORMAS.get(codigo, (str(codigo), None))[0],
        "crs": sistema["nombre"],
        "situable": situable,
        "total": total,
        "descartadas": descartadas,
        "recortadas": recortadas,
        "aviso": sistema["aviso"],
        "mb": round(tamano / (1024 * 1024), 1),
        # La tabla ENTERA, no solo la de las entidades que quedaron. Sirve
        # para resumir el archivo completo: "1.882 areas protegidas, de
        # estas categorias" es informacion que se pierde si solo se mira
        # el recorte.
        "todas": atributos,
    }


def _vertices(geometria: dict):
    """
    Recorre los vertices de una geometria, uno a uno.

    Args:
        geometria: Geometria GeoJSON.

    Yields:
        Pares `(lon, lat)`.
    """
    pila = [geometria.get("coordinates")]
    while pila:
        nodo = pila.pop()
        if (isinstance(nodo, list) and len(nodo) == 2
                and isinstance(nodo[0], (int, float))
                and isinstance(nodo[1], (int, float))):
            yield nodo[0], nodo[1]
        elif isinstance(nodo, list):
            pila.extend(nodo)


def anillos_de(capa: dict) -> list:
    """
    Saca los anillos exteriores de una capa de poligonos.

    Args:
        capa: GeoJSON cargado.

    Returns:
        Lista de anillos, cada uno lista de pares `(lon, lat)`.
    """
    anillos = []
    for entidad in capa.get("features") or []:
        geometria = entidad.get("geometry") or {}
        tipo = geometria.get("type")
        if tipo == "Polygon":
            if geometria["coordinates"]:
                anillos.append([tuple(p) for p in geometria["coordinates"][0]])
        elif tipo == "MultiPolygon":
            for parte in geometria["coordinates"]:
                if parte:
                    anillos.append([tuple(p) for p in parte[0]])
    return anillos


def marcar_contacto(entidades: list, anillos: list, campo: str = "en_el_area"):
    """
    Anota en cada entidad si toca el area de estudio o solo queda cerca.

    POR QUE NO BASTA EL RECUADRO
    ----------------------------
    Recortar una capa nacional por el rectangulo del municipio trae de
    sobra -- con el margen, 129 areas protegidas para un municipio donde
    solo unas pocas lo tocan de verdad. Si todas entran al mapa sin
    distinguirse, quien lo mire tiene que adivinar cuales le afectan, y en
    un estudio de impacto esa es LA pregunta.

    COMO SE DECIDE, Y QUE CUESTA
    ----------------------------
    Primero el rectangulo exacto del area, sin margen: lo que no se solapa
    ni siquiera ahi se marca "cerca" y no se mira mas. Solo las pocas que
    quedan pasan por la prueba de verdad, que es cruzar vertices en los dos
    sentidos -- alguno suyo dentro del area, o alguno del area dentro de
    ella --, con salida en cuanto hay un acierto.

    Ese orden es lo que hace que la prueba sea barata. Sin el, cada entidad
    costaria sus miles de vertices por los 3.405 del municipio.

    LO QUE ESTA PRUEBA NO VE
    ------------------------
    Dos poligonos pueden cruzarse sin que ningun vertice de uno caiga
    dentro del otro: dos rectangulos en cruz. Es raro con limites
    administrativos reales y el precio de verlo seria comparar todos los
    segmentos con todos. Cuando pase, la entidad queda como "cerca", que es
    quedarse corto y no pasarse: sigue en el mapa y se puede mirar.

    Args:
        entidades: Entidades GeoJSON, que se modifican en el sitio.
        anillos: Anillos del area de estudio.
        campo: Nombre de la propiedad que se escribe.

    Returns:
        Cuantas entidades tocan el area.
    """
    from core.area_estudio import _dentro_del_anillo

    if not anillos:
        return 0

    xs = [p[0] for a in anillos for p in a]
    ys = [p[1] for a in anillos for p in a]
    caja = (min(xs), min(ys), max(xs), max(ys))
    tocan = 0

    for entidad in entidades:
        propiedades = entidad.setdefault("properties", {})
        geometria = entidad.get("geometry") or {}
        if not geometria.get("coordinates"):
            propiedades[campo] = "sin geometria"
            continue

        suyos = list(_vertices(geometria))
        sx = [p[0] for p in suyos]
        sy = [p[1] for p in suyos]
        solapa = not (max(sx) < caja[0] or min(sx) > caja[2]
                      or max(sy) < caja[1] or min(sy) > caja[3])
        if not solapa:
            propiedades[campo] = "cerca"
            continue

        # Solo se prueban los vertices que caen en el rectangulo del area.
        # Los demas no pueden estar dentro de ella, y cada prueba cuesta
        # recorrer los 3.405 vertices del municipio: descartarlos por una
        # comparacion de cuatro numeros bajo de 25 segundos a 3.
        cerca = [p for p in suyos
                 if caja[0] <= p[0] <= caja[2] and caja[1] <= p[1] <= caja[3]]
        toca = any(_dentro_del_anillo(p, anillo)
                   for anillo in anillos for p in cerca)
        if not toca:
            propia = [tuple(p) for p in suyos]
            propio = (min(sx), min(sy), max(sx), max(sy))
            toca = any(_dentro_del_anillo(v, propia)
                       for anillo in anillos for v in anillo
                       if propio[0] <= v[0] <= propio[2]
                       and propio[1] <= v[1] <= propio[3])
        propiedades[campo] = "si" if toca else "cerca"
        tocan += 1 if toca else 0

    return tocan


def leer_dbf_filas(ruta: str, maximo: int = MAXIMO_ENTIDADES) -> list:
    """
    Lee la tabla de atributos de un shapefile como diccionarios.

    POR QUE NO SE REUSA EL LECTOR DE TEXTO
    --------------------------------------
    `corpus._leer_dbf` existe y funciona, pero devuelve parrafos pensados
    para buscar por significado. Para construir una capa hacen falta los
    valores sueltos, con su columna, para que QGIS pueda filtrar y etiquetar
    por ellos. Es el mismo archivo leido con otro proposito.

    Args:
        ruta: Ruta del .dbf.
        maximo: Tope de registros.

    Returns:
        Lista de diccionarios `columna -> valor`. Vacia si no se pudo leer.
    """
    try:
        with open(ruta, "rb") as fh:
            cabecera = fh.read(32)
            if len(cabecera) < 32:
                return []
            registros = struct.unpack_from("<I", cabecera, 4)[0]
            largo_cabecera = struct.unpack_from("<H", cabecera, 8)[0]
            largo_registro = struct.unpack_from("<H", cabecera, 10)[0]

            codificacion = "latin-1"
            cpg = os.path.splitext(ruta)[0] + ".cpg"
            if os.path.exists(cpg):
                try:
                    with open(cpg, encoding="ascii", errors="ignore") as fc:
                        posible = fc.read(40).strip().lower()
                    if posible:
                        "".encode(posible)
                        codificacion = posible
                except (OSError, LookupError):
                    pass

            campos = []
            while True:
                bruto = fh.read(32)
                if not bruto or bruto[0] == 0x0D:
                    break
                nombre = bruto[:11].split(b"\x00")[0].decode(
                    codificacion, "replace").strip()
                campos.append((nombre, chr(bruto[11]), bruto[16]))

            fh.seek(largo_cabecera)
            filas = []
            for _ in range(min(registros, maximo)):
                bruto = fh.read(largo_registro)
                if len(bruto) < largo_registro:
                    break
                if bruto[:1] == b"*":      # registro borrado
                    continue
                fila, desde = {}, 1
                for nombre, tipo, ancho in campos:
                    crudo = bruto[desde:desde + ancho]
                    desde += ancho
                    valor = crudo.decode(codificacion, "replace").strip()
                    if tipo in "NF" and valor:
                        try:
                            valor = (int(valor) if "." not in valor
                                     else float(valor))
                        except ValueError:
                            pass
                    elif tipo == "L":
                        valor = valor.upper() in ("T", "Y")
                    if valor != "":
                        fila[nombre] = valor
                filas.append(fila)
            return filas
    except (OSError, struct.error):
        return []
