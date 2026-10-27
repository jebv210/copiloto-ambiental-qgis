"""
Area de estudio de San Carlos (Antioquia).
===============================================================================

QUE RESUELVE
------------
El sistema necesita saber DONDE trabaja. Sin un poligono de referencia no se
puede responder la pregunta que de verdad importa cuando llega informacion de
campo: "esto que me mandaron, cae dentro o no".

Se midio lo que pasaba sin el: una exportacion de QField con cuatro notas
entro sin que nada chistara, y las notas estaban a 187 km del area, en Bogota.
El sistema no mintio -- sencillamente no tenia con que comparar.

DE DONDE SALE EL POLIGONO
-------------------------
Del propio material del proyecto, no de una lista escrita a mano. El archivo
`AREA PROYECTO SAN CARLOS.kmz` trae varios poligonos y cada uno significa una
cosa distinta:

    908,75 km2   POMCA_Embalse y Rio Guatape - NSS   unidad hidrografica
    720,77 km2   San Carlos                          limite municipal
    127,70 km2   INSCRITA                            area inscrita del proyecto
     36,74 km2   Punchina                            embalse

Cual de ellos es "el area de estudio" NO lo decide este modulo: lo elige quien
sabe del proyecto, y queda guardado. Elegir por el seria exactamente el tipo de
dato quemado que este sistema evita, y ademas cambiaria que notas de campo se
aceptan y cuales se rechazan.

EL CONTRATO CON QGIS
--------------------
El archivo de salida se llama `municipios_filtrados.geojson` y ese nombre es
fijo: `cargar_capas_qgis.py` lo busca asi. Vive en `config.settings`.
"""

import json
import math
import os
import xml.etree.ElementTree as ET
import zipfile

from config import settings

#: Espacio de nombres de KML. Todos los KMZ de Google Earth lo usan.
NS_KML = {"k": "http://www.opengis.net/kml/2.2"}

#: Radio medio de la Tierra, en kilometros. Se usa para medir areas y
#: distancias sin depender de una biblioteca de proyecciones: para decidir si
#: un punto cae dentro de un municipio, el error de la aproximacion esferica
#: esta muy por debajo de lo que importa.
RADIO_TIERRA_KM = 6371.0088

#: Donde se recuerda que poligono se eligio. Va en `data/`, que es carpeta
#: montada y sobrevive al contenedor.
NOMBRE_ELECCION = "area_estudio.json"

#: Poligonos mas pequenos que esto no son areas de estudio: son detalles de
#: dibujo del KMZ (marcas, recortes). Se omiten al listar para que la eleccion
#: no se pierda entre veinte lineas de ruido.
MINIMO_KM2 = 0.5


def ruta_eleccion() -> str:
    """
    Devuelve el archivo donde se guarda el area elegida.

    Returns:
        Ruta dentro de `data/`.
    """
    carpeta = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")
    os.makedirs(carpeta, exist_ok=True)
    return os.path.join(carpeta, NOMBRE_ELECCION)


# ==============================================================================
# MEDIDAS
# ==============================================================================

def area_km2(anillo: list) -> float:
    """
    Mide el area de un anillo de coordenadas en kilometros cuadrados.

    Args:
        anillo: Lista de pares `(lon, lat)` en grados decimales.

    Returns:
        El area, siempre positiva. 0.0 si el anillo no cierra un poligono.
    """
    if len(anillo) < 3:
        return 0.0
    suma = 0.0
    for i in range(len(anillo)):
        lon1, lat1 = anillo[i]
        lon2, lat2 = anillo[(i + 1) % len(anillo)]
        suma += math.radians(lon2 - lon1) * (
            2 + math.sin(math.radians(lat1)) + math.sin(math.radians(lat2)))
    return abs(suma * RADIO_TIERRA_KM * RADIO_TIERRA_KM / 2.0)


def distancia_km(punto_a: tuple, punto_b: tuple) -> float:
    """
    Distancia en linea recta entre dos coordenadas geograficas.

    Args:
        punto_a: Par `(lon, lat)`.
        punto_b: Par `(lon, lat)`.

    Returns:
        Kilometros entre los dos puntos.
    """
    lon1, lat1 = punto_a
    lon2, lat2 = punto_b
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = (math.sin(dlat / 2) ** 2
         + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2))
         * math.sin(dlon / 2) ** 2)
    return RADIO_TIERRA_KM * 2 * math.asin(math.sqrt(a))


def _dentro_del_anillo(punto: tuple, anillo: list) -> bool:
    """
    Dice si un punto cae dentro de un anillo, por el metodo del rayo.

    Se cuenta cuantas veces un rayo horizontal lanzado desde el punto cruza
    el borde: impar significa dentro. No necesita proyectar nada ni instalar
    bibliotecas, y a esta escala el error es despreciable.

    Args:
        punto: Par `(lon, lat)`.
        anillo: Lista de pares `(lon, lat)`.

    Returns:
        True si el punto esta dentro.
    """
    x, y = punto
    dentro = False
    j = len(anillo) - 1
    for i in range(len(anillo)):
        xi, yi = anillo[i]
        xj, yj = anillo[j]
        if ((yi > y) != (yj > y)) and (
                x < (xj - xi) * (y - yi) / ((yj - yi) or 1e-15) + xi):
            dentro = not dentro
        j = i
    return dentro


def _borde_mas_cercano(punto: tuple, anillo: list) -> float:
    """
    Distancia del punto al vertice mas cercano del anillo.

    Es una cota superior de la distancia real al borde, no la distancia
    exacta a los segmentos. Para decir "esto esta a 187 km del area" sobra, y
    se evita arrastrar una biblioteca de geometria entera.

    Args:
        punto: Par `(lon, lat)`.
        anillo: Lista de pares `(lon, lat)`.

    Returns:
        Kilometros al vertice mas proximo.
    """
    return min(distancia_km(punto, v) for v in anillo) if anillo else 0.0


# ==============================================================================
# LECTURA DEL KMZ
# ==============================================================================

def _anillos_de_placemark(lugar) -> list:
    """
    Saca los anillos exteriores de un elemento Placemark del KML.

    Args:
        lugar: Elemento `Placemark` ya parseado.

    Returns:
        Lista de anillos; cada anillo es una lista de pares `(lon, lat)`.
    """
    anillos = []
    for poligono in lugar.findall(".//k:Polygon", NS_KML):
        coords = poligono.find(".//k:outerBoundaryIs//k:coordinates", NS_KML)
        if coords is None or not coords.text:
            continue
        puntos = []
        for trozo in coords.text.split():
            partes = trozo.split(",")
            if len(partes) >= 2:
                try:
                    puntos.append((float(partes[0]), float(partes[1])))
                except ValueError:
                    continue
        if len(puntos) >= 3:
            anillos.append(puntos)
    return anillos


#: Nombres de los archivos de referencia que se extraen del mismo archivo del
#: que sale el area. Son fijos porque `cargar_capas_qgis.py` los busca asi.
ARCHIVO_PUNTOS = "puntos_area_estudio.geojson"
ARCHIVO_LINEAS = "hidrografia_area_estudio.geojson"


def leer_geometrias(ruta: str) -> dict:
    """
    Saca TODO lo que trae un KMZ o KML: puntos, lineas y poligonos.

    POR QUE NO BASTABA CON LOS POLIGONOS
    ------------------------------------
    La primera version solo leia poligonos, porque lo que hacia falta era el
    limite del area. Pero el mismo archivo traia, sin que nadie lo mirara, 20
    puntos y 15 lineas: trece veredas con su coordenada, el casco urbano, el
    embalse, y la red hidrica con el Rio San Carlos de 4.134 vertices.

    Se midio lo que costaba ignorarlo: el indice entero -- 377 fichas -- no
    contiene ni una sola coordenada. Las tablas del proyecto nombran veredas y
    quebradas, con sus areas y sus longitudes, pero sin geometria. Las unicas
    coordenadas del proyecto estaban aqui.

    Args:
        ruta: Archivo .kmz o .kml.

    Returns:
        Diccionario con `puntos`, `lineas` y `poligonos`. Cada elemento lleva
        `nombre` y `coordenadas` (lista de pares lon/lat).

    Raises:
        ValueError: Si el formato no se reconoce.
    """
    extension = os.path.splitext(ruta)[1].lower()
    if extension == ".kmz":
        with zipfile.ZipFile(ruta) as z:
            interno = next((n for n in z.namelist()
                            if n.lower().endswith(".kml")), None)
            if interno is None:
                raise ValueError("el KMZ no contiene ningun archivo KML")
            texto = z.read(interno)
    elif extension == ".kml":
        with open(ruta, "rb") as fh:
            texto = fh.read()
    else:
        raise ValueError(f"solo se extraen geometrias de .kmz y .kml, no de "
                         f"'{extension}'")

    raiz = ET.fromstring(texto)
    salida = {"puntos": [], "lineas": [], "poligonos": []}
    etiquetas = {"Point": "puntos", "LineString": "lineas",
                 "Polygon": "poligonos"}

    for lugar in raiz.findall(".//k:Placemark", NS_KML):
        marca = lugar.find("k:name", NS_KML)
        nombre = (marca.text if marca is not None and marca.text
                  else "(sin nombre)").strip()
        for etiqueta, destino in etiquetas.items():
            for geometria in lugar.findall(f".//k:{etiqueta}", NS_KML):
                coords = geometria.find(".//k:coordinates", NS_KML)
                if coords is None or not coords.text:
                    continue
                puntos = []
                for trozo in coords.text.split():
                    partes = trozo.split(",")
                    if len(partes) >= 2:
                        try:
                            puntos.append((float(partes[0]), float(partes[1])))
                        except ValueError:
                            continue
                if puntos:
                    salida[destino].append({"nombre": nombre,
                                            "coordenadas": puntos})
    return salida


def exportar_referencias(ruta_origen: str) -> dict:
    """
    Escribe los puntos y las lineas del archivo como capas que lee QGIS.

    El limite del area se fija aparte, con `fijar()`. Esto es lo demas: las
    veredas, el casco urbano, los sitios del proyecto y la red hidrica. Sin
    ellas el mapa sale con el contorno del municipio y nada dentro.

    Args:
        ruta_origen: El .kmz o .kml del que salen.

    Returns:
        Diccionario con `puntos`, `lineas` y las rutas escritas. Si el archivo
        no trae ninguna de las dos cosas, el archivo correspondiente no se
        escribe y su cuenta va a cero.
    """
    geometrias = leer_geometrias(ruta_origen)
    carpeta = os.path.dirname(settings.ruta_municipios_salida())
    os.makedirs(carpeta, exist_ok=True)
    origen = os.path.basename(ruta_origen)
    escritos = {"puntos": 0, "lineas": 0, "archivos": []}

    bloques = (
        ("puntos", ARCHIVO_PUNTOS, "Point",
         lambda c: list(c[0])),
        ("lineas", ARCHIVO_LINEAS, "LineString",
         lambda c: [list(p) for p in c]),
    )
    for clave, archivo, tipo, forma in bloques:
        elementos = geometrias[clave]
        if not elementos:
            continue
        capa = {
            "type": "FeatureCollection",
            "features": [{
                "type": "Feature",
                "properties": {"nombre": e["nombre"], "origen": origen,
                               "vertices": len(e["coordenadas"])},
                "geometry": {"type": tipo,
                             "coordinates": forma(e["coordenadas"])},
            } for e in elementos],
        }
        destino = os.path.join(carpeta, archivo)
        temporal = destino + ".escribiendo"
        with open(temporal, "w", encoding="utf-8") as fh:
            json.dump(capa, fh, ensure_ascii=False)
        os.replace(temporal, destino)
        escritos[clave] = len(elementos)
        escritos["archivos"].append(destino)
    return escritos


def leer_poligonos(ruta: str) -> list:
    """
    Lee los poligonos de un KMZ, un KML o un GeoJSON.

    Args:
        ruta: Archivo a leer.

    Returns:
        Lista de diccionarios con `nombre`, `km2` y `anillo`, ordenados de
        mayor a menor superficie. Los recortes de dibujo, por debajo de
        `MINIMO_KM2`, se omiten.

    Raises:
        ValueError: Si el formato no se reconoce o no hay ningun poligono.
    """
    extension = os.path.splitext(ruta)[1].lower()

    if extension in (".geojson", ".json"):
        return _leer_geojson(ruta)

    if extension == ".kmz":
        with zipfile.ZipFile(ruta) as z:
            interno = next((n for n in z.namelist()
                            if n.lower().endswith(".kml")), None)
            if interno is None:
                raise ValueError("el KMZ no contiene ningun archivo KML")
            texto = z.read(interno)
    elif extension == ".kml":
        with open(ruta, "rb") as fh:
            texto = fh.read()
    else:
        raise ValueError(
            f"no se sabe leer '{extension}'. Se admiten .kmz, .kml y .geojson")

    raiz = ET.fromstring(texto)
    salida = []
    for lugar in raiz.findall(".//k:Placemark", NS_KML):
        etiqueta = lugar.find("k:name", NS_KML)
        nombre = (etiqueta.text if etiqueta is not None and etiqueta.text
                  else "(sin nombre)")
        for anillo in _anillos_de_placemark(lugar):
            superficie = area_km2(anillo)
            if superficie >= MINIMO_KM2:
                salida.append({"nombre": nombre.strip(),
                               "km2": round(superficie, 2),
                               "anillo": anillo})
    if not salida:
        raise ValueError("el archivo no trae ningun poligono utilizable")
    return sorted(salida, key=lambda x: -x["km2"])


def _leer_geojson(ruta: str) -> list:
    """
    Lee los poligonos de un GeoJSON.

    Args:
        ruta: Archivo GeoJSON.

    Returns:
        La misma forma que `leer_poligonos`.

    Raises:
        ValueError: Si no hay poligonos dentro.
    """
    with open(ruta, "r", encoding="utf-8") as fh:
        datos = json.load(fh)

    rasgos = datos.get("features") or ([datos] if datos.get("geometry") else [])
    salida = []
    for rasgo in rasgos:
        geometria = rasgo.get("geometry") or {}
        props = rasgo.get("properties") or {}
        nombre = (props.get("MPIO_CNMBR") or props.get("nombre")
                  or props.get("name") or "(sin nombre)")
        tipo = geometria.get("type")
        if tipo == "Polygon":
            bloques = [geometria.get("coordinates", [[]])[0]]
        elif tipo == "MultiPolygon":
            bloques = [p[0] for p in geometria.get("coordinates", [])]
        else:
            continue
        for bloque in bloques:
            anillo = [(float(c[0]), float(c[1])) for c in bloque if len(c) >= 2]
            if len(anillo) < 3:
                continue
            superficie = area_km2(anillo)
            if superficie >= MINIMO_KM2:
                salida.append({"nombre": str(nombre).strip(),
                               "km2": round(superficie, 2),
                               "anillo": anillo})
    if not salida:
        raise ValueError("el GeoJSON no trae ningun poligono utilizable")
    return sorted(salida, key=lambda x: -x["km2"])


# ==============================================================================
# FIJAR Y CONSULTAR EL AREA
# ==============================================================================

def fijar(poligono: dict, origen: str = "") -> dict:
    """
    Deja un poligono como area de estudio del proyecto.

    Escribe el GeoJSON que lee QGIS y recuerda la eleccion, para que la
    proxima sesion no tenga que repetirla.

    Args:
        poligono: Uno de los que devuelve `leer_poligonos`.
        origen: De que archivo salio, para poder rastrearlo despues.

    Returns:
        Diccionario con `nombre`, `km2`, `vertices` y `archivo`.

    Raises:
        OSError: Si no se puede escribir el archivo de salida.
    """
    anillo = poligono["anillo"]
    destino = settings.ruta_municipios_salida()

    capa = {
        "type": "FeatureCollection",
        "features": [{
            "type": "Feature",
            "properties": {
                "nombre": poligono["nombre"],
                "km2": poligono["km2"],
                "origen": origen,
                "MPIO_CNMBR": poligono["nombre"],
            },
            "geometry": {
                "type": "Polygon",
                "coordinates": [[list(v) for v in anillo]],
            },
        }],
    }
    # Se escribe a un temporal y se mueve: si falla a mitad, no queda un area
    # de estudio rota que parezca buena.
    temporal = destino + ".escribiendo"
    with open(temporal, "w", encoding="utf-8") as fh:
        json.dump(capa, fh, ensure_ascii=False)
    os.replace(temporal, destino)

    eleccion = {
        "nombre": poligono["nombre"],
        "km2": poligono["km2"],
        "vertices": len(anillo),
        "origen": origen,
        "archivo": destino,
    }
    try:
        with open(ruta_eleccion(), "w", encoding="utf-8") as fh:
            json.dump(eleccion, fh, ensure_ascii=False, indent=2)
    except OSError:
        pass  # el area ya esta escrita; recordar la eleccion es un extra
    return eleccion


def actual() -> dict:
    """
    Describe el area de estudio vigente.

    Returns:
        Diccionario con `hay` y, si lo hay, `nombre`, `km2`, `vertices` y
        `archivo`. Nunca levanta: se consulta al pintar la interfaz.
    """
    destino = settings.ruta_municipios_salida()
    if not destino or not os.path.exists(destino):
        return {"hay": False,
                "motivo": "todavia no se ha fijado ningun area de estudio"}
    datos = {}
    try:
        with open(ruta_eleccion(), "r", encoding="utf-8") as fh:
            datos = json.load(fh)
    except (OSError, ValueError):
        pass
    return {"hay": True, "archivo": destino,
            "nombre": datos.get("nombre", "(sin nombre)"),
            "km2": datos.get("km2"), "vertices": datos.get("vertices"),
            "origen": datos.get("origen", "")}


def _anillo_vigente():
    """
    Carga el anillo del area de estudio fijada.

    Returns:
        El anillo, o None si no hay area o no se pudo leer.
    """
    destino = settings.ruta_municipios_salida()
    if not destino or not os.path.exists(destino):
        return None
    try:
        with open(destino, "r", encoding="utf-8") as fh:
            capa = json.load(fh)
        coords = capa["features"][0]["geometry"]["coordinates"][0]
        return [(float(c[0]), float(c[1])) for c in coords]
    except (OSError, ValueError, KeyError, IndexError, TypeError):
        return None


def revisar_puntos(puntos: list) -> dict:
    """
    Dice cuales de unos puntos caen dentro del area de estudio.

    Args:
        puntos: Lista de diccionarios con al menos `lon` y `lat`.

    Returns:
        Diccionario con `hay_area`, `dentro`, `fuera`, `total` y `detalle`.
        Cada entrada de `detalle` lleva `dentro` y, cuando esta fuera,
        `km_fuera`: a cuanto se quedo. Si no hay area fijada, `hay_area` es
        False y no se inventa ningun veredicto.
    """
    anillo = _anillo_vigente()
    if anillo is None:
        return {"hay_area": False, "dentro": 0, "fuera": 0,
                "total": len(puntos), "detalle": [],
                "motivo": "no hay area de estudio fijada, asi que no hay "
                          "con que comparar"}

    detalle, dentro = [], 0
    for punto in puntos:
        par = (punto["lon"], punto["lat"])
        esta = _dentro_del_anillo(par, anillo)
        fila = dict(punto)
        fila["dentro"] = esta
        if esta:
            dentro += 1
        else:
            fila["km_fuera"] = round(_borde_mas_cercano(par, anillo), 1)
        detalle.append(fila)

    return {"hay_area": True, "dentro": dentro,
            "fuera": len(puntos) - dentro, "total": len(puntos),
            "detalle": detalle, "motivo": ""}
