"""
Comprobaciones de la geometria: shapefiles, geodatabases y capas.
===============================================================================

Esta bateria nacio de un archivo concreto. En la carpeta de Drive habia un
.zip con el Registro Unico Nacional de Areas Protegidas -- 1.882 poligonos
reales de Colombia -- y el sistema lo leia entero, lo indexaba como texto y
tiraba la geometria. Al preguntarle por el, el agente contestaba que no lo
tenia analizado.

Cada comprobacion de aqui corresponde a un punto donde eso se rompia:
la proyeccion que no se sabia invertir, el poligono con agujero que se
dibujaba como dos poligonos, la geodatabase que no daba texto, el byte de
mas en la cabecera que convertia seis columnas en dos.

Las pruebas no dependen de los archivos reales: cuando no estan, se
construyen a mano con los mismos formatos. Las que SI necesitan un archivo
real se omiten diciendolo.

Se ejecuta dentro del contenedor:

    docker compose exec api python probar_geometria.py
"""

import json
import math
import os
import shutil
import struct
import sys
import tempfile
import zipfile

from core import ajustes as _ajustes_guardados

_ajustes_guardados.aplicar()

from core import capas, corpus, geodatabase, geometria

ACIERTOS = 0
FALLOS = []
OMITIDAS = 0


def comprobar(titulo, condicion, detalle=""):
    """
    Anota el resultado de una comprobacion.

    Args:
        titulo: Que se esta comprobando.
        condicion: Resultado de la comprobacion.
        detalle: Texto que acompana al resultado.
    """
    global ACIERTOS
    if condicion:
        ACIERTOS += 1
        print(f"  [OK ] {titulo}: {detalle}")
    else:
        FALLOS.append(f"{titulo} -> {detalle}")
        print(f"  [FALLA] {titulo}: {detalle}")


def omitir(titulo, motivo):
    """
    Marca una comprobacion como no aplicable.

    Args:
        titulo: Que se iba a comprobar.
        motivo: Por que no se pudo.
    """
    global OMITIDAS
    OMITIDAS += 1
    print(f"  [--- ] {titulo}: omitido ({motivo})")


# ==============================================================================
# UN SHAPEFILE DE VERDAD, ESCRITO A MANO
# ==============================================================================
# Se escriben los bytes del formato en vez de depender de un archivo que
# puede no estar. Asi la bateria corre en una maquina recien clonada y, de
# paso, deja escrito que estructura tiene un shapefile.

WKT_WEB_MERCATOR = (
    'PROJCS["WGS_1984_Web_Mercator_Auxiliary_Sphere",'
    'GEOGCS["GCS_WGS_1984",DATUM["D_WGS_1984",'
    'SPHEROID["WGS_1984",6378137.0,298.257223563]],'
    'PRIMEM["Greenwich",0.0],UNIT["Degree",0.0174532925199433]],'
    'PROJECTION["Mercator_Auxiliary_Sphere"],'
    'PARAMETER["False_Easting",0.0],PARAMETER["False_Northing",0.0],'
    'PARAMETER["Central_Meridian",0.0],'
    'PARAMETER["Standard_Parallel_1",0.0],'
    'PARAMETER["Auxiliary_Sphere_Type",0.0],UNIT["Meter",1.0]]'
)

WKT_UTM18N = (
    'PROJCS["WGS_1984_UTM_Zone_18N",'
    'GEOGCS["GCS_WGS_1984",DATUM["D_WGS_1984",'
    'SPHEROID["WGS_1984",6378137.0,298.257223563]],'
    'PRIMEM["Greenwich",0.0],UNIT["Degree",0.0174532925199433]],'
    'PROJECTION["Transverse_Mercator"],'
    'PARAMETER["False_Easting",500000.0],'
    'PARAMETER["False_Northing",0.0],'
    'PARAMETER["Central_Meridian",-75.0],'
    'PARAMETER["Scale_Factor",0.9996],'
    'PARAMETER["Latitude_Of_Origin",0.0],UNIT["Meter",1.0]]'
)


def _anillo_bytes(anillos):
    """
    Serializa un poligono con sus anillos como contenido de registro.

    Args:
        anillos: Lista de anillos, cada uno lista de pares.

    Returns:
        Bytes del contenido, empezando por el codigo de forma.
    """
    puntos = [p for anillo in anillos for p in anillo]
    xs = [p[0] for p in puntos]
    ys = [p[1] for p in puntos]
    partes, acumulado = [], 0
    for anillo in anillos:
        partes.append(acumulado)
        acumulado += len(anillo)
    cuerpo = struct.pack("<i", 5)
    cuerpo += struct.pack("<dddd", min(xs), min(ys), max(xs), max(ys))
    cuerpo += struct.pack("<ii", len(anillos), len(puntos))
    cuerpo += struct.pack(f"<{len(partes)}i", *partes)
    for x, y in puntos:
        cuerpo += struct.pack("<dd", x, y)
    return cuerpo


def escribir_shapefile(carpeta, nombre, geometrias, filas, wkt):
    """
    Escribe un shapefile completo con sus cinco archivos.

    Args:
        carpeta: Donde dejarlo.
        nombre: Nombre sin extension.
        geometrias: Lista de listas de anillos.
        filas: Lista de diccionarios con un campo `NOMBRE`.
        wkt: Contenido del .prj.

    Returns:
        Ruta del .shp.
    """
    base = os.path.join(carpeta, nombre)
    registros = b""
    for numero, anillos in enumerate(geometrias, start=1):
        contenido = _anillo_bytes(anillos)
        registros += struct.pack(">ii", numero, len(contenido) // 2)
        registros += contenido

    todos = [p for g in geometrias for a in g for p in a]
    xs = [p[0] for p in todos]
    ys = [p[1] for p in todos]
    cabecera = struct.pack(">iiiiiii", 9994, 0, 0, 0, 0, 0,
                           (100 + len(registros)) // 2)
    cabecera += struct.pack("<ii", 1000, 5)
    cabecera += struct.pack("<dddd", min(xs), min(ys), max(xs), max(ys))
    cabecera += struct.pack("<dddd", 0, 0, 0, 0)
    with open(base + ".shp", "wb") as fh:
        fh.write(cabecera + registros)

    ancho = 40
    dbf = struct.pack("<BBBBIHH", 3, 125, 1, 1, len(filas), 32 + 32 + 1,
                      1 + ancho)
    dbf += b"\x00" * 20
    dbf += b"NOMBRE".ljust(11, b"\x00") + b"C" + b"\x00" * 4
    dbf += struct.pack("<BB", ancho, 0) + b"\x00" * 14
    dbf += b"\x0d"
    for fila in filas:
        dbf += b" " + str(fila.get("NOMBRE", "")).encode(
            "utf-8")[:ancho].ljust(ancho, b" ")
    dbf += b"\x1a"
    with open(base + ".dbf", "wb") as fh:
        fh.write(dbf)
    with open(base + ".prj", "w", encoding="utf-8") as fh:
        fh.write(wkt)
    with open(base + ".cpg", "w", encoding="ascii") as fh:
        fh.write("UTF-8")
    with open(base + ".shx", "wb") as fh:
        fh.write(cabecera[:100])
    return base + ".shp"


# ==============================================================================
print("PROYECCIONES - convertir a grados sin GDAL")
# ------------------------------------------------------------------------------
# Los valores de referencia no se inventan: salen de la definicion de cada
# proyeccion. Web Mercator pone el meridiano cero en x=0, y una vuelta
# completa en 2*pi*R. UTM pone su meridiano central en x=500.000.

_mercator = geometria.conversion_de_wkt(WKT_WEB_MERCATOR)
comprobar("Web Mercator se reconoce y se sabe invertir",
          _mercator["convertir"] is not None and not _mercator["grados"],
          _mercator["nombre"])

if _mercator["convertir"]:
    _lon, _lat = _mercator["convertir"](0.0, 0.0)
    comprobar("el origen de Web Mercator cae en 0, 0",
              abs(_lon) < 1e-9 and abs(_lat) < 1e-9,
              f"{_lon:.6f}, {_lat:.6f}")

    _medio = math.pi * geometria.RADIO_WEB_MERCATOR
    _lon, _lat = _mercator["convertir"](_medio / 2, 0.0)
    comprobar("media vuelta de Mercator son 90 grados de longitud",
              abs(_lon - 90.0) < 1e-6, f"{_lon:.6f}")

    # El punto real de la cabecera del RUNAP, que es lo que se leia como si
    # fuera latitud: 1.693.576 metros. En grados son 15, no 1.693.576.
    _lon, _lat = _mercator["convertir"](-8400000.0, 1693576.4684)
    comprobar("el extremo norte del RUNAP cae donde tiene que caer",
              14.9 < _lat < 15.1 and -76 < _lon < -75,
              f"latitud {_lat:.4f}, longitud {_lon:.4f}")

_utm = geometria.conversion_de_wkt(WKT_UTM18N)
comprobar("una zona UTM se reconoce como Transversa de Mercator",
          _utm["convertir"] is not None, _utm["nombre"])

if _utm["convertir"]:
    _lon, _lat = _utm["convertir"](500000.0, 0.0)
    comprobar("el meridiano central de la zona 18N es -75",
              abs(_lon + 75.0) < 1e-6 and abs(_lat) < 1e-6,
              f"{_lon:.6f}, {_lat:.6f}")

    # El centro del area de estudio -- 6.19 N, 74.92 O -- en UTM 18N. La
    # zona 18 tiene su meridiano central en -75, asi que un punto a 0,08
    # grados al este cae unos 8,8 km pasado el falso este de 500.000.
    _lon, _lat = _utm["convertir"](508853.0, 684300.0)
    comprobar("un punto del area de estudio vuelve a su sitio",
              6.0 < _lat < 6.4 and -75.2 < _lon < -74.7,
              f"latitud {_lat:.4f}, longitud {_lon:.4f}")

_geo = geometria.conversion_de_wkt('GEOGCS["GCS_WGS_1984"]')
comprobar("una capa que ya viene en grados no se toca",
          _geo["grados"] and _geo["convertir"] is None, "sin conversion")

_raro = geometria.conversion_de_wkt(
    'PROJCS["Lo_Que_Sea",PROJECTION["Krovak"]]')
comprobar("una proyeccion desconocida se declara, no se adivina",
          _raro["convertir"] is None and not _raro["grados"]
          and bool(_raro["aviso"]),
          _raro["aviso"][:60])

_sin = geometria.leer_prj("/no/existe.prj")
comprobar("sin .prj se dice que no se sabe donde esta la capa",
          _sin["convertir"] is None and "no viene el .prj" in _sin["aviso"],
          "no se da por hecho que sean grados")

# ==============================================================================
print("\nSHAPEFILE - la geometria, no solo la tabla")
_banco = tempfile.mkdtemp(prefix="_geom_")
try:
    # Un cuadrado con un agujero dentro, y otro cuadrado lejos. En metros de
    # Web Mercator, alrededor de San Carlos.
    _x, _y = -8340000.0, 690000.0
    _contorno = [(_x, _y), (_x + 20000, _y), (_x + 20000, _y + 20000),
                 (_x, _y + 20000), (_x, _y)]
    _agujero = [(_x + 5000, _y + 5000), (_x + 5000, _y + 10000),
                (_x + 10000, _y + 10000), (_x + 10000, _y + 5000),
                (_x + 5000, _y + 5000)]
    _lejos = [[(0.0, 0.0), (1000.0, 0.0), (1000.0, 1000.0),
               (0.0, 1000.0), (0.0, 0.0)]]
    _ruta = escribir_shapefile(
        _banco, "prueba",
        [[_contorno, _agujero], _lejos],
        [{"NOMBRE": "Reserva del Peñol"}, {"NOMBRE": "Lejos de aqui"}],
        WKT_WEB_MERCATOR)

    _leido = geometria.leer_shp(_ruta)
    comprobar("se leen todas las entidades del shapefile",
              _leido.get("total") == 2 and len(_leido["entidades"]) == 2,
              f"{_leido.get('total')} registros")
    comprobar("la capa se declara situable porque su .prj se entiende",
              _leido.get("situable") is True, _leido.get("crs", ""))

    _primera = _leido["entidades"][0]
    comprobar("un poligono con agujero sigue siendo UN poligono",
              _primera["geometry"]["type"] == "Polygon"
              and len(_primera["geometry"]["coordinates"]) == 2,
              "contorno y agujero, no dos poligonos sueltos")

    _caja = geometria._recuadro(_primera["geometry"])
    comprobar("las coordenadas salen en grados, no en metros",
              all(-180 <= c <= 180 for c in _caja),
              f"de {_caja[1]:.4f} a {_caja[3]:.4f} de latitud")

    comprobar("la tabla de atributos viaja con su geometria",
              _primera["properties"].get("NOMBRE") == "Reserva del Peñol",
              _primera["properties"].get("NOMBRE", ""))
    comprobar("los acentos del .dbf sobreviven a la codificacion",
              "ñ" in _primera["properties"].get("NOMBRE", ""),
              "se respeta el UTF-8 declarado en el .cpg")

    # El recorte: solo lo que cae cerca del area.
    _recorte = geometria.leer_shp(_ruta, recuadro=(-75.2, 6.0, -74.7, 6.4))
    comprobar("recortar por el area deja fuera lo que esta lejos",
              len(_recorte["entidades"]) == 1
              and _recorte["descartadas"] == 1,
              f"{len(_recorte['entidades'])} de {_recorte['total']}")

    # Sin .prj no se puede situar, y eso tiene que notarse.
    os.remove(os.path.join(_banco, "prueba.prj"))
    _ciego = geometria.leer_shp(_ruta)
    comprobar("sin sistema de referencia la capa NO se da por situada",
              _ciego.get("situable") is False and bool(_ciego.get("aviso")),
              "se prefiere no dibujarla a dibujarla donde no es")
finally:
    shutil.rmtree(_banco, ignore_errors=True)

# ==============================================================================
print("\nCONTACTO CON EL AREA - tocar no es lo mismo que estar cerca")
_cuadrado = {"type": "FeatureCollection", "features": [{
    "type": "Feature",
    "geometry": {"type": "Polygon", "coordinates": [[
        [-75.0, 6.0], [-74.8, 6.0], [-74.8, 6.2], [-75.0, 6.2],
        [-75.0, 6.0]]]},
    "properties": {}}]}
_anillos = geometria.anillos_de(_cuadrado)
comprobar("el area de estudio se reduce a sus anillos",
          len(_anillos) == 1 and len(_anillos[0]) == 5,
          f"{len(_anillos[0])} vertices")

_entidades = [
    {"type": "Feature", "properties": {"n": "dentro"},
     "geometry": {"type": "Polygon", "coordinates": [[
         [-74.95, 6.05], [-74.90, 6.05], [-74.90, 6.10],
         [-74.95, 6.10], [-74.95, 6.05]]]}},
    {"type": "Feature", "properties": {"n": "solapa"},
     "geometry": {"type": "Polygon", "coordinates": [[
         [-74.85, 6.15], [-74.70, 6.15], [-74.70, 6.30],
         [-74.85, 6.30], [-74.85, 6.15]]]}},
    {"type": "Feature", "properties": {"n": "lejos"},
     "geometry": {"type": "Polygon", "coordinates": [[
         [-74.0, 5.0], [-73.9, 5.0], [-73.9, 5.1],
         [-74.0, 5.1], [-74.0, 5.0]]]}},
]
_tocan = geometria.marcar_contacto(_entidades, _anillos)
comprobar("se cuentan las que tocan el area y solo esas",
          _tocan == 2, f"{_tocan} de {len(_entidades)}")
comprobar("la que esta dentro se marca como dentro",
          _entidades[0]["properties"]["en_el_area"] == "si", "si")
comprobar("la que solo se acerca se marca como cerca",
          _entidades[2]["properties"]["en_el_area"] == "cerca", "cerca")

# ==============================================================================
print("\nGEODATABASE - leer lo que no tiene lector")
comprobar("un entero de longitud variable se lee igual que se escribe",
          geodatabase._varuint(bytes([0x9a, 0x03]), 0) == (410, 2),
          "410 en dos bytes")
comprobar("el signo del primer byte no se confunde con un bit de dato",
          geodatabase._varint(bytes([0x41]), 0) == (-1, 1)
          and geodatabase._varint(bytes([0x01]), 0) == (1, 1),
          "0x41 es -1, no 65")

comprobar("un nombre de columna de verdad se acepta",
          geodatabase._nombre_creible("NombreFalla")
          and geodatabase._nombre_creible("Unidades Cronoestratigráficas"),
          "letras, numeros y poco mas")
comprobar("lo que sale de leer UTF-16 a destiempo se rechaza",
          not geodatabase._nombre_creible("开伀爀椀最椀渀")
          and not geodatabase._nombre_creible("")
          and not geodatabase._nombre_creible("\x00\x07"),
          "es el filtro que evita que la busqueda acepte basura")

_FORMAS = geodatabase.FORMAS_GDB
comprobar("las variantes Z y M de una forma se tratan como la forma",
          _FORMAS[5] == _FORMAS[19] == _FORMAS[25] == "Polygon"
          and _FORMAS[3] == _FORMAS[13] == "LineString",
          "para dibujar en planta basta X e Y")

# ==============================================================================
print("\nRESUMIR UNA TABLA - que cabe en el indice y que no")
_filas = ([{"Tipo": "Falla", "Nombre": f"Falla {i}"} for i in range(40)]
          + [{"Tipo": "Lineamiento", "Nombre": f"Falla {i}"}
             for i in range(40, 50)])
_resumen = capas.resumen_de_filas(_filas)
_texto = "\n".join(_resumen)
comprobar("una columna repetida se cuenta, no se repite",
          "Falla (40)" in _texto and "Lineamiento (10)" in _texto,
          "50 filas en una linea")
comprobar("una columna con un valor por fila se describe, no se vuelca",
          "valores distintos" in _texto,
          "un identificador no se enumera entero")
comprobar("el resumen cabe donde las filas no caben",
          len(_texto) < 600 and len(_filas) == 50,
          f"{len(_texto)} caracteres para 50 filas")

_igual = capas.resumen_de_filas([{"Pais": "Colombia"}] * 30)
comprobar("una columna con un solo valor lo dice en una linea",
          len(_igual) == 1 and "siempre" in _igual[0], _igual[0])

comprobar("sin filas no se inventa un resumen",
          capas.resumen_de_filas([]) == [], "lista vacia")

# ==============================================================================
print("\nVERSION DE LECTURA - mejorar el codigo rehace el indice")
comprobar("los formatos cuya lectura cambio llevan marca de version",
          corpus.sufijo_de_lectura("algo.zip") == "/l2"
          and corpus.sufijo_de_lectura("algo.shp") == "/l2",
          "su huella cambia aunque el archivo no")
comprobar("los demas formatos no se rehacen por nada",
          corpus.sufijo_de_lectura("algo.pdf") == ""
          and corpus.sufijo_de_lectura("algo.xlsx") == "",
          "13.000 fichas de PDF no se tocan")
comprobar("la marca no depende de las mayusculas del nombre",
          corpus.sufijo_de_lectura("ALGO.ZIP") == "/l2", "/l2")

# ==============================================================================
print("\nDE UN .zip A UNA CAPA DE QGIS, DE PUNTA A PUNTA")
_banco = tempfile.mkdtemp(prefix="_zipgeo_")
_capas_real = capas.carpeta
try:
    _destino = os.path.join(_banco, "salida")
    os.makedirs(_destino, exist_ok=True)
    capas.carpeta = lambda: _destino

    _dentro = os.path.join(_banco, "dentro")
    os.makedirs(_dentro, exist_ok=True)
    _x, _y = -8340000.0, 690000.0
    escribir_shapefile(
        _dentro, "areas",
        [[[(_x, _y), (_x + 20000, _y), (_x + 20000, _y + 20000),
           (_x, _y + 20000), (_x, _y)]]],
        [{"NOMBRE": "Parque de prueba"}], WKT_WEB_MERCATOR)

    _zip = os.path.join(_banco, "paquete.zip")
    with zipfile.ZipFile(_zip, "w") as _z:
        for _f in sorted(os.listdir(_dentro)):
            _z.write(os.path.join(_dentro, _f), _f)

    _paginas = corpus.leer_documento(_zip)
    _todo = "\n".join(p[1] for p in _paginas)
    comprobar("el .zip con un shapefile dentro da texto",
              bool(_paginas), f"{len(_paginas)} paginas")
    comprobar("el texto dice que la capa quedo lista para QGIS",
              "listas para QGIS" in _todo or "YA ESTA en el proyecto"
              in _todo, "el mapa se entera de lo que entro")
    comprobar("los atributos del shapefile entran al indice",
              "Parque de prueba" in _todo, "se puede citar")

    _escritas = [f for f in os.listdir(_destino) if f.endswith(".geojson")]
    comprobar("y la capa existe de verdad en el disco",
              len(_escritas) == 1, ", ".join(_escritas))

    if _escritas:
        with open(os.path.join(_destino, _escritas[0]),
                  encoding="utf-8") as _fh:
            _capa = json.load(_fh)
        _caja = geometria._recuadro(_capa["features"][0]["geometry"])
        comprobar("la capa guardada esta en grados, lista para dibujar",
                  all(-180 <= c <= 180 for c in _caja),
                  f"longitud {_caja[0]:.3f}, latitud {_caja[1]:.3f}")

    comprobar("la tabla de atributos NO se indexa dos veces",
              _todo.count("Parque de prueba") <= 2,
              f"{_todo.count('Parque de prueba')} apariciones: el .dbf no "
              f"se cuenta aparte del .shp que lo lee")
finally:
    capas.carpeta = _capas_real
    shutil.rmtree(_banco, ignore_errors=True)

# ==============================================================================
print("\nARCHIVOS REALES - si estan, se usan")
_REALES = "/app/temp_uploads/_zips"
_zips = (sorted(os.path.join(_REALES, f) for f in os.listdir(_REALES)
                if f.lower().endswith(".zip"))
         if os.path.isdir(_REALES) else [])
if not _zips:
    omitir("lectura de los .zip reales de Drive",
           "no hay copias bajadas en temp_uploads/_zips")
else:
    for _ruta in _zips:
        _nombre = os.path.basename(_ruta)
        _fichas = corpus.contar_fichas_de(_ruta)
        comprobar(f"{_nombre[:34]} da contenido indexable",
                  _fichas > 0, f"{_fichas} fichas")
        comprobar(f"{_nombre[:34]} cabe en el presupuesto",
                  _fichas <= corpus.PRESUPUESTO_FICHAS,
                  f"{_fichas} de {corpus.PRESUPUESTO_FICHAS}: se indexa "
                  f"entero, no como una ficha de 'existe'")

# ==============================================================================
print("\n" + "=" * 70)
total = ACIERTOS + len(FALLOS)
if FALLOS:
    print(f"{ACIERTOS} de {total} superadas, {len(FALLOS)} FALLIDAS"
          f"{f', {OMITIDAS} omitidas' if OMITIDAS else ''}:\n")
    for f in FALLOS:
        print("  -", f)
    sys.exit(1)
print(f"{ACIERTOS} de {total} comprobaciones superadas"
      f"{f', {OMITIDAS} omitidas' if OMITIDAS else ''}")
