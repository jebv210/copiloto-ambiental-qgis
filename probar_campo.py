"""
Comprobaciones del area de estudio y de la revision de datos de campo.
===============================================================================

Cada comprobacion de aqui existe porque algo fallo de esa manera. Las que
hablan de kilometros concretos salen de archivos reales del proyecto: la
exportacion de QField `Prueba_.zip` y el `AREA PROYECTO SAN CARLOS.kmz`.

Se ejecuta dentro del contenedor:

    docker compose exec api python probar_campo.py
"""

import csv
import os
import shutil
import sys
import time
import tempfile

from core import ajustes as _ajustes_guardados

_ajustes_guardados.aplicar()

from core import area_estudio, campo
from config import settings

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


BANCO = tempfile.mkdtemp(prefix="probar_campo_")


def tabla(nombre, cabecera, filas):
    """
    Escribe un CSV de prueba y devuelve su ruta.

    Args:
        nombre: Nombre del archivo.
        cabecera: Lista de nombres de columna.
        filas: Filas de datos.

    Returns:
        La ruta del archivo escrito.
    """
    ruta = os.path.join(BANCO, nombre)
    with open(ruta, "w", encoding="utf-8", newline="") as fh:
        escritor = csv.writer(fh)
        escritor.writerow(cabecera)
        for fila in filas:
            escritor.writerow(fila)
    return ruta


# ==============================================================================
print("\nAREA DE ESTUDIO - medir y decidir")

# Un cuadrado de un grado en el ecuador mide unos 12.363 km2. Lo que se
# comprueba no es la cifra exacta, sino que la medida sea del orden correcto:
# con una formula mal puesta salen numeros absurdos, no numeros con decimales
# distintos.
_cuadrado = [(0.0, 0.0), (1.0, 0.0), (1.0, 1.0), (0.0, 1.0)]
_medido = area_estudio.area_km2(_cuadrado)
comprobar("medir un area da el orden de magnitud correcto",
          12000 < _medido < 12500, f"{_medido:.0f} km2 para un grado cuadrado")

comprobar("un anillo de menos de tres puntos no es un area",
          area_estudio.area_km2([(0, 0), (1, 1)]) == 0.0, "devuelve 0")

# Bogota a San Carlos son del orden de 180 km en linea recta.
_d = area_estudio.distancia_km((-74.10, 4.76), (-74.99, 6.19))
comprobar("la distancia entre dos puntos es creible",
          150 < _d < 220, f"{_d:.0f} km de Bogota a San Carlos")

_centro = [(-1.0, -1.0), (1.0, -1.0), (1.0, 1.0), (-1.0, 1.0)]
comprobar("un punto dentro del poligono se reconoce",
          area_estudio._dentro_del_anillo((0.0, 0.0), _centro), "el centro")
comprobar("un punto fuera del poligono se reconoce",
          not area_estudio._dentro_del_anillo((5.0, 5.0), _centro), "lejos")


# ==============================================================================
print("\nAREA DE ESTUDIO - leer y fijar")

_KMZ = settings.ruta_proyecto("data", "capas_fuente",
                              "area_proyecto_san_carlos.kmz")
if os.path.exists(_KMZ):
    _poligonos = area_estudio.leer_poligonos(_KMZ)
    comprobar("se leen los poligonos del KMZ del proyecto",
              len(_poligonos) >= 2, f"{len(_poligonos)} poligonos utilizables")
    comprobar("vienen ordenados de mayor a menor",
              all(_poligonos[i]["km2"] >= _poligonos[i + 1]["km2"]
                  for i in range(len(_poligonos) - 1)),
              f"el mayor son {_poligonos[0]['km2']} km2")

    # El limite municipal de San Carlos son 717,81 km2 segun el DANE. La
    # medida esferica no tiene por que dar lo mismo al decimal, pero si tiene
    # que parecerse: si se desvia mas de un 2% hay un error de formula.
    _sc = next((p for p in _poligonos
                if "san carlos" in p["nombre"].lower()), None)
    if _sc:
        _error = abs(_sc["km2"] - 717.81) / 717.81
        comprobar("el area de San Carlos coincide con el dato del DANE",
                  _error < 0.02,
                  f"{_sc['km2']} km2 frente a 717,81 ({_error*100:.1f}%)")
    else:
        omitir("el area de San Carlos coincide con el dato del DANE",
               "el KMZ no trae un poligono llamado San Carlos")
else:
    omitir("se leen los poligonos del KMZ del proyecto", "no esta el KMZ")
    omitir("vienen ordenados de mayor a menor", "no esta el KMZ")
    omitir("el area de San Carlos coincide con el dato del DANE", "no esta")
    _poligonos, _sc = [], None

try:
    area_estudio.leer_poligonos(os.path.join(BANCO, "cosa.docx"))
    comprobar("un formato desconocido levanta un error claro", False,
              "no levanto nada")
except ValueError as exc:
    comprobar("un formato desconocido levanta un error claro",
              "no se sabe leer" in str(exc), str(exc)[:60])
except Exception as exc:
    comprobar("un formato desconocido levanta un error claro", False,
              f"levanto {type(exc).__name__}")


# ==============================================================================
print("\nAREA DE ESTUDIO - las referencias que trae el mismo archivo")
# El indice documental no tiene NI UNA coordenada: se reviso entero, 377
# fichas, y las tablas nombran veredas y quebradas con sus areas y longitudes
# pero sin geometria. Las unicas coordenadas del proyecto estaban en el KMZ, y
# la primera version del lector solo miraba los poligonos, asi que el mapa
# salia con el contorno del municipio y nada dentro.
_KMZ_REF = settings.ruta_proyecto("data", "capas_fuente",
                                  "area_proyecto_san_carlos.kmz")
if os.path.exists(_KMZ_REF):
    _geo = area_estudio.leer_geometrias(_KMZ_REF)
    comprobar("del archivo del area salen tambien puntos",
              len(_geo["puntos"]) > 0,
              f"{len(_geo['puntos'])} puntos (veredas, sitios)")
    comprobar("y lineas",
              len(_geo["lineas"]) > 0,
              f"{len(_geo['lineas'])} lineas (quebradas, rutas)")

    _veredas = [p for p in _geo["puntos"]
                if "vda" in p["nombre"].lower()
                or "vereda" in p["nombre"].lower()]
    comprobar("entre los puntos hay veredas con coordenada",
              len(_veredas) >= 5,
              f"{len(_veredas)} veredas localizadas")

    # Un rio de miles de vertices leido como un punto daria una coordenada
    # cualquiera. Se comprueba que las lineas conservan su recorrido entero.
    _mayor = max(_geo["lineas"], key=lambda l: len(l["coordenadas"]))
    comprobar("una linea conserva todos sus vertices",
              len(_mayor["coordenadas"]) > 100,
              f"'{_mayor['nombre'][:26]}' con "
              f"{len(_mayor['coordenadas'])} vertices")

    _ubicacion = area_estudio.revisar_puntos(
        [{"lon": p["coordenadas"][0][0], "lat": p["coordenadas"][0][1]}
         for p in _geo["puntos"]])
    if _ubicacion["hay_area"]:
        comprobar("la mayoria de esos puntos caen dentro del area",
                  _ubicacion["dentro"] >= _ubicacion["total"] * 0.6,
                  f"{_ubicacion['dentro']} de {_ubicacion['total']} dentro")
    else:
        omitir("la mayoria de esos puntos caen dentro del area", "sin area")
else:
    for _titulo in ("del archivo del area salen tambien puntos", "y lineas",
                    "entre los puntos hay veredas con coordenada",
                    "una linea conserva todos sus vertices",
                    "la mayoria de esos puntos caen dentro del area"):
        omitir(_titulo, "no esta el KMZ del proyecto")


print("\nCAMPO - lo que llega de una salida real")

_vigente = area_estudio.actual()
if not _vigente["hay"]:
    omitir("la revision usa el area fijada", "no hay area de estudio fijada")
else:
    comprobar("hay un area de estudio contra la que comparar",
              bool(_vigente.get("km2")),
              f"{_vigente['nombre']}, {_vigente['km2']} km2")

# Puntos dentro de San Carlos: tienen que pasar limpios.
_buena = tabla("dentro.csv", ["latitud", "longitud", "observacion"],
               [[6.1878, -74.9936, "Cabecera municipal, punto de control"],
                [6.2050, -74.9700, "Quebrada La Chorrera, bosque denso"],
                [6.1600, -75.0200, "Vereda El Jordan, pastos limpios"]])
_r = campo.revisar(campo.leer(_buena))
comprobar("una salida dentro del area se da por valida",
          _r["sirve"] and _r["resumen"]["dentro"] == 3,
          f"{_r['resumen']['dentro']} de 3 dentro")

# Se midio: una planilla CON su columna de observacion salia avisada de "no
# traen ni titulo ni nota", que era falso y restaba credibilidad al resto.
comprobar("la columna de observacion se recoge, y no se avisa de mas",
          not any("título ni nota" in a for a in _r["avisos"]),
          "sin avisos falsos" if not _r["avisos"] else str(_r["avisos"])[:60])

# Puntos de Bogota: el caso real del Prueba_.zip.
_fuera = tabla("fuera.csv", ["lat", "lon", "nota"],
               [[4.76139, -74.10657, "Prueba 1"],
                [4.74548, -74.10830, "Prueba 3"]])
_rf = campo.revisar(campo.leer(_fuera))
comprobar("una salida fuera del area NO se da por valida",
          not _rf["sirve"], "rechazada")
comprobar("y se dice a cuanto se quedo, no solo que esta fuera",
          any("km del borde" in p for p in _rf["problemas"]),
          _rf["problemas"][0][:72] if _rf["problemas"] else "sin motivo")

# El error mas comun de campo: columnas cambiadas.
_girado = tabla("girado.csv", ["lat", "lon", "nota"],
                [[-74.9936, 6.1878, "columnas al reves"],
                 [-74.9700, 6.2050, "idem"]])
_rg = campo.revisar(campo.leer(_girado))
comprobar("si las coordenadas estan invertidas, se dice",
          any("intercambian" in p for p in _rg["problemas"]),
          "se sugiere el intercambio en vez de solo informar la distancia")

# Coordenadas imposibles: no es "fuera del area", es un archivo mal escrito.
_imposible = tabla("imposible.csv", ["lat", "lon"], [[999.0, -74.0]])
_ri = campo.revisar(campo.leer(_imposible))
comprobar("una coordenada imposible se distingue de una lejana",
          any("imposibles" in p for p in _ri["problemas"]),
          _ri["problemas"][0][:70] if _ri["problemas"] else "sin motivo")

# Un archivo sin columnas de coordenadas no se puede revisar: hay que decirlo.
_sin = tabla("sin_coordenadas.csv", ["codigo", "especie"], [["A1", "Inga"]])
_rs = campo.revisar(campo.leer(_sin))
comprobar("un archivo sin coordenadas se rechaza explicando que falta",
          not _rs["sirve"] and any("latitud" in a for a in _rs["avisos"]),
          "dice que columnas buscaba")


# ==============================================================================
print("\nCAMPO - exportacion de QField")

# El ejemplo vive en el proyecto, no en /tmp: una bateria que depende
# de que alguien haya dejado un archivo suelto en el equipo no prueba
# nada en un contenedor recien creado. Se omitia sola.
_ZIP = settings.ruta_proyecto("data", "ejemplos_campo",
                              "qfield_fuera_del_area.zip")
if os.path.exists(_ZIP):
    _lectura = campo.leer(_ZIP)
    comprobar("se abre el .zip y se leen sus GeoPackage",
              len(_lectura["capas"]) >= 2,
              f"{len(_lectura['capas'])} capas, {len(_lectura['puntos'])} puntos")

    # Se midio: el recorrido es una LINEA, y leerlo como punto daba
    # coordenadas basura -- un punto "a 8.318 km del area", mas que el ancho
    # de Colombia.
    _todos_creibles = all(abs(p["lat"]) <= 90 and abs(p["lon"]) <= 180
                          for p in _lectura["puntos"])
    comprobar("un recorrido no se lee como si fuera un punto",
              _todos_creibles,
              "todas las coordenadas estan en rango")

    _traza = [p for p in _lectura["puntos"] if p["capa"] == "tracks"]
    if _traza:
        _cerca = min(area_estudio.distancia_km(
            (_traza[0]["lon"], _traza[0]["lat"]), (p["lon"], p["lat"]))
            for p in _lectura["puntos"] if p["capa"] != "tracks")
        comprobar("el inicio del recorrido cae junto a las notas",
                  _cerca < 5, f"a {_cerca:.1f} km de la nota mas cercana")
    else:
        omitir("el inicio del recorrido cae junto a las notas", "sin traza")
else:
    for titulo in ("se abre el .zip y se leen sus GeoPackage",
                   "un recorrido no se lee como si fuera un punto",
                   "el inicio del recorrido cae junto a las notas"):
        omitir(titulo, "falta el ejemplo en data/ejemplos_campo")

_raro = os.path.join(BANCO, "cosa.rtf")
open(_raro, "w").close()
_rr = campo.leer(_raro)
comprobar("un formato que no se sabe leer se declara",
          not _rr["puntos"] and any("no se sabe leer" in a
                                    for a in _rr["avisos"]),
          _rr["avisos"][0][:60] if _rr["avisos"] else "sin aviso")


shutil.rmtree(BANCO, ignore_errors=True)

# ==============================================================================
# SHAPEFILE: LO QUE DE VERDAD EXPORTA QFIELD
# ==============================================================================
#
# El paquete real del equipo, PROYECTO_MARIPOSAS_SAN_CARLOS.zip, pesa 584 MB
# y NO trae ningun GeoPackage: trae un shapefile de 58 registros con 37
# campos, un poligono de monitoreo, un raster y 229 fotografias. Antes de
# estas comprobaciones el validador lo abria y contestaba que no habia nada
# que revisar, que es la peor respuesta posible: no es que los datos esten
# mal, es que no se miraron.
print("\n--- Shapefile: el formato que llega de campo ---")

import struct as _st_shp
import zipfile as _zip_shp

_banco_shp = tempfile.mkdtemp(prefix="probar_campo_shp_")
try:
    def _shapefile(base, campos, filas, puntos, geogcs=True, cpg="UTF-8"):
        """
        Escribe un shapefile con sus hermanos.

        Args:
            base: Ruta sin extension.
            campos: Nombres de columna.
            filas: Valores, una lista por registro.
            puntos: Lista de `(lon, lat)`, o None para geometria nula.
            geogcs: Si el .prj declara coordenadas geograficas.
            cpg: Codificacion declarada.
        """
        cuerpo = b""
        for numero, par in enumerate(puntos, start=1):
            if par is None:
                contenido = _st_shp.pack("<i", 0)        # registro nulo
            else:
                contenido = _st_shp.pack("<idd", 1, par[0], par[1])
            cuerpo += (_st_shp.pack(">ii", numero, len(contenido) // 2)
                       + contenido)
        reales = [p for p in puntos if p]
        cabecera = bytearray(100)
        cabecera[0:4] = _st_shp.pack(">i", 9994)
        cabecera[24:28] = _st_shp.pack(">i", (100 + len(cuerpo)) // 2)
        cabecera[28:32] = _st_shp.pack("<i", 1000)
        cabecera[32:36] = _st_shp.pack("<i", 1)
        if reales:
            cabecera[36:68] = _st_shp.pack(
                "<dddd", min(p[0] for p in reales), min(p[1] for p in reales),
                max(p[0] for p in reales), max(p[1] for p in reales))
        with open(base + ".shp", "wb") as fh:
            fh.write(bytes(cabecera) + cuerpo)

        largo = 80
        descriptores = b""
        for nombre in campos:
            bruto = nombre.encode(cpg)[:11]
            descriptores += (bruto.ljust(11, b"\x00") + b"C" + b"\x00" * 4
                             + bytes([largo]) + b"\x00" * 15)
        cab = bytearray(32)
        cab[0] = 0x03
        cab[4:8] = _st_shp.pack("<I", len(filas))
        cab[8:10] = _st_shp.pack("<H", 32 + len(descriptores) + 1)
        cab[10:12] = _st_shp.pack("<H", 1 + largo * len(campos))
        datos = b""
        for fila in filas:
            datos += b" " + b"".join(
                str(v).encode(cpg)[:largo].ljust(largo, b" ") for v in fila)
        with open(base + ".dbf", "wb") as fh:
            fh.write(bytes(cab) + descriptores + b"\x0d" + datos + b"\x1a")

        with open(base + ".prj", "w", encoding="latin-1") as fh:
            fh.write('GEOGCS["GCS_WGS_1984",DATUM["D_WGS_1984"]]' if geogcs
                     else 'PROJCS["WGS 84 / Pseudo-Mercator",PROJECTION["x"]]')
        with open(base + ".cpg", "w", encoding="ascii") as fh:
            fh.write(cpg)

    # Dentro del area de estudio de San Carlos, que es la que esta fijada.
    _DENTRO = [(-75.0061, 6.1827), (-75.0008, 6.1870), (-75.0010, 6.1869)]

    # --- Un shapefile suelto ---------------------------------------------
    _base = os.path.join(_banco_shp, "Formulario Mariposas")
    _shapefile(_base, ["Nombre cie", "Notas de c", "Fecha"],
               [["Hamadryas", "", "20260823"],
                ["Anartia amathea", "sobre flor", "20260913"],
                ["", "", "20260926"]],
               _DENTRO)
    _lec = campo.leer(_base + ".shp")
    comprobar("se lee un shapefile de campo",
              _lec["formato"] == "shp" and len(_lec["puntos"]) == 3,
              f"{len(_lec['puntos'])} puntos leidos del .shp")

    comprobar("los atributos acompanan a cada punto",
              _lec["puntos"][0]["titulo"] == "Hamadryas"
              and _lec["puntos"][1]["nota"] == "sobre flor",
              "la especie y la nota viajan con su coordenada")

    # --- Un registro sin coordenada se DECLARA ---------------------------
    # Se midio en el paquete real: de sus 58 registros, uno trae la ficha
    # llena y la geometria vacia. Si se descarta callando, el recuento baja
    # de 58 a 57 y nadie sabe por que.
    _nulo = os.path.join(_banco_shp, "Con hueco")
    _shapefile(_nulo, ["Nombre cie"], [["Morpho"], ["Heliconius"]],
               [_DENTRO[0], None])
    _lec_nulo = campo.leer(_nulo + ".shp")
    comprobar("un registro sin coordenada no desaparece en silencio",
              len(_lec_nulo["puntos"]) == 1
              and any("NO traen coordenada" in a
                      for a in _lec_nulo["avisos"]),
              _lec_nulo["avisos"][0][:72] if _lec_nulo["avisos"]
              else "no quedo constancia")

    # --- Un sistema proyectado no se interpreta como grados --------------
    # Leer metros como si fueran grados no da error: pone los puntos en
    # otro continente y el validador los declara "fuera del area".
    _metros = os.path.join(_banco_shp, "En metros")
    _shapefile(_metros, ["Nombre cie"], [["Morpho"]],
               [(-8359244.11, 680260.31)], geogcs=False)
    _lec_m = campo.leer(_metros + ".shp")
    comprobar("un shapefile en metros se rechaza en vez de malinterpretarse",
              not _lec_m["puntos"]
              and any("proyectado" in a for a in _lec_m["avisos"]),
              "se avisa de que hay que reproyectarlo")

    # --- El titulo cae a la siguiente columna cuando la primera esta vacia
    # Se midio en el formulario del equipo: 19 registros traen nombre
    # cientifico y 23 traen nombre comun, pero solo 26 traen alguno. Con una
    # sola columna, el validador acusaba de "sin identificar" a siete
    # registros que si lo estaban.
    _dos = os.path.join(_banco_shp, "Dos nombres")
    _shapefile(_dos, ["Nombre cie", "Nombre com"],
               [["", "Mariposa monarca"], ["Morpho helenor", ""]],
               _DENTRO[:2])
    _lec_dos = campo.leer(_dos + ".shp")
    _titulos = [p["titulo"] for p in _lec_dos["puntos"]]
    comprobar("si falta el nombre cientifico se usa el comun",
              _titulos == ["Mariposa monarca", "Morpho helenor"],
              "ningun registro identificado se cuenta como anonimo")

    # --- Dentro de un .zip, con los hermanos juntos ----------------------
    # Es como llega de verdad. Extraer cada archivo por su lado separaria
    # el .shp de su .dbf y la capa entraria muda.
    _paquete = os.path.join(_banco_shp, "salida_de_campo.zip")
    with _zip_shp.ZipFile(_paquete, "w") as _z:
        for _ext in (".shp", ".dbf", ".prj", ".cpg"):
            _z.write(_base + _ext, "PROYECTO/Formulario Mariposas" + _ext)
        # Una foto, para comprobar que NO se extrae lo que no se revisa.
        _z.writestr("PROYECTO/DCIM/foto.jpg", b"\xff\xd8\xff\xe0" + b"x" * 5000)
    _lec_zip = campo.leer(_paquete)
    comprobar("un shapefile dentro de un .zip conserva sus atributos",
              len(_lec_zip["puntos"]) == 3
              and _lec_zip["puntos"][0]["titulo"] == "Hamadryas",
              "los cinco archivos se extraen juntos")

    # --- El veredicto completo -------------------------------------------
    _v = campo.revisar(_lec_zip)
    comprobar("el veredicto dice si sirve y con que recuento",
              _v["sirve"] is True and _v["resumen"]["puntos"] == 3,
              f"sirve={_v['sirve']}, {_v['resumen']['puntos']} puntos, "
              f"{_v['resumen']['dentro']} dentro del area")

    # --- Lo que no se sabe leer se declara --------------------------------
    comprobar("el mensaje de formato desconocido nombra el shapefile",
              ".shp" in campo.leer(os.path.join(
                  _banco_shp, "x.rtf"))["avisos"][0],
              "se admiten .zip, .gpkg, .shp, .xlsx y .csv")
finally:
    shutil.rmtree(_banco_shp, ignore_errors=True)


# ==============================================================================
# LO REVISADO SE CONVIERTE EN CAPA DE MAPA
# ==============================================================================
#
# Estudiar y cartografiar eran dos caminos que no se cruzaban. El agente
# indexaba el paquete de campo y sabia responder sobre el, pero el mapa
# seguia saliendo del KMZ del area: se subia un .zip con 57 registros de
# mariposas y la cartografia salia identica, como si no se hubiera hecho
# nada. Visto desde fuera, el sistema parecia roto.
print("\n--- Los datos de campo acaban en el mapa ---")

import json as _json_capa

_banco_capa = tempfile.mkdtemp(prefix="probar_capa_campo_")
_extra_real = campo.carpeta_capas_extra
campo.carpeta_capas_extra = lambda: _banco_capa
try:
    _veredicto = {
        "sirve": True,
        "puntos": [
            {"lon": -75.0061, "lat": 6.1827, "titulo": "Hamadryas",
             "nota": "sobre tronco", "fecha": "20260823",
             "capa": "Formulario", "dentro": True},
            {"lon": -75.0008, "lat": 6.1870, "titulo": "Anartia amathea",
             "nota": "", "fecha": "20260913", "capa": "Formulario",
             "dentro": False},
        ],
        "resumen": {}, "problemas": [], "avisos": [],
    }
    _hecho = campo.exportar_capa(_veredicto, "PROYECTO_MARIPOSAS.zip")
    comprobar("revisar un paquete escribe una capa de mapa",
              _hecho.get("puntos") == 2 and os.path.exists(_hecho["ruta"]),
              _hecho.get("nombre", _hecho.get("error", "")))

    # Va a `capas_extra/` y no a `data/` a proposito: el script de QGIS
    # carga esa carpeta entera sin que nadie toque su codigo, y ese script
    # es zona intocable del proyecto.
    comprobar("la capa va donde QGIS la recoge sin tocar su script",
              os.path.dirname(_hecho["ruta"]) == _banco_capa,
              "escrita en la carpeta de capas propias")

    with open(_hecho["ruta"], encoding="utf-8") as _fh:
        _capa = _json_capa.load(_fh)
    comprobar("es un GeoJSON de puntos con sus coordenadas",
              _capa["type"] == "FeatureCollection"
              and _capa["features"][0]["geometry"]["type"] == "Point"
              and _capa["features"][0]["geometry"]["coordinates"][1] == 6.1827,
              f"{len(_capa['features'])} entidades de tipo Point")

    _props = [f["properties"] for f in _capa["features"]]
    comprobar("cada punto lleva su especie, su nota y su fecha",
              _props[0]["titulo"] == "Hamadryas"
              and _props[0]["fecha"] == "20260823",
              "los atributos viajan con la geometria")

    # Lo primero que se mira en un mapa de validacion es quien cayo fuera.
    comprobar("y si cayo dentro o fuera del area de estudio",
              _props[0]["en_el_area"] == "si"
              and _props[1]["en_el_area"] == "no",
              "dentro y fuera se distinguen en el mapa")

    # NO se inventa una posicion para quien no la tiene. En el paquete real
    # del equipo habia un registro con la ficha llena y el GPS sin fijar.
    _sin_coord = {"sirve": True, "puntos": [
        {"lon": None, "lat": None, "titulo": "Morpho", "nota": "",
         "fecha": "", "capa": "x"}], "resumen": {}, "problemas": [],
        "avisos": []}
    _nada = campo.exportar_capa(_sin_coord, "vacio.zip")
    comprobar("un registro sin coordenada no se dibuja ni se inventa",
              "error" in _nada and not _nada.get("ruta"),
              _nada.get("error", "")[:60])

    # Dos paquetes distintos no se pisan: cada uno es su propia capa.
    _otro = campo.exportar_capa(_veredicto, "SALIDA_OCTUBRE.zip")
    comprobar("dos paquetes distintos dan dos capas distintas",
              _otro["nombre"] != _hecho["nombre"]
              and os.path.exists(_otro["ruta"]),
              f"{_hecho['nombre']} y {_otro['nombre']}")
finally:
    campo.carpeta_capas_extra = _extra_real
    shutil.rmtree(_banco_capa, ignore_errors=True)

# ==============================================================================
print("")
print("EL PAQUETE DEL PROPIO SISTEMA NO SE APRUEBA NI SE SUSPENDE")
# ------------------------------------------------------------------------------
# Pasaba esto: se le pedia al agente el .zip de QGIS, se subia a esta misma
# pestana y salia en rojo, "la informacion NO se puede dar por buena". El
# archivo estaba perfecto. Lo que fallaba era la pregunta: un paquete que
# escribio este sistema no es una salida de campo, asi que no hay nada que
# aprobar, y suspenderlo tapaba los veredictos que si significan algo.
_banco_p = tempfile.mkdtemp(prefix="propio_")
try:
    import zipfile as _zp

    _falso = os.path.join(_banco_p, "Area_estudio_QGIS.zip")
    with _zp.ZipFile(_falso, "w") as _z:
        _z.writestr("proyecto.qgs", "<qgis/>")
        _z.writestr("LEEME.txt", "paquete de prueba")
        # Un GeoPackage de verdad, escrito por el propio proyecto: asi la
        # prueba no depende de un archivo inventado que el lector rechace.
        from core import paquete_qgis as _pq
        _gpkg = os.path.join(_banco_p, "capas.gpkg")
        _pq.escribir_geopaquete(_gpkg, [{
            "nombre": "sitios", "tipo": "Point",
            "entidades": [
                {"propiedades": {"nombre": "uno"},
                 "coordenadas": [-74.9, 6.2]},
                {"propiedades": {"nombre": "lejos"},
                 "coordenadas": [-70.0, 2.0]},
            ]}])
        _z.write(_gpkg, "capas.gpkg")

    _lec_p = campo.leer(_falso)
    comprobar("se reconoce el paquete que genera el propio sistema",
              _lec_p.get("propio") is True,
              "por el proyecto.qgs y el capas.gpkg que lleva dentro")

    _rev_p = campo.revisar(_lec_p)
    comprobar("no se le pone veredicto: ni aprobado ni suspenso",
              _rev_p["sirve"] is None and not _rev_p["problemas"],
              f"sirve={_rev_p['sirve']!r}, {len(_rev_p['problemas'])} "
              f"problemas")
    comprobar("y se dice QUE es y que habria que subir en su lugar",
              "QField" in _rev_p.get("explicacion", "")
              and "no viene del campo" in _rev_p.get("explicacion", ""),
              "en vez de un rojo que no significa nada")
    comprobar("el punto que cae lejos ya no invalida nada",
              not _rev_p["problemas"],
              "aunque el paquete lleve geometria nacional fuera del area")

    # Un .zip de campo normal SIGUE revisandose como siempre.
    _normal = os.path.join(_banco_p, "salida.zip")
    with _zp.ZipFile(_normal, "w") as _z:
        _z.write(_gpkg, "datos.gpkg")
    _rev_n = campo.revisar(campo.leer(_normal))
    comprobar("un .zip que no es el paquete del sistema si se juzga",
              _rev_n["sirve"] is not None,
              f"sirve={_rev_n['sirve']!r}: el cambio no desactiva la "
              f"revision de verdad")
finally:
    shutil.rmtree(_banco_p, ignore_errors=True)

# ==============================================================================
print("")
print("LAS CIFRAS DEL VEREDICTO TIENEN QUE CUADRAR ENTRE SI")
# ------------------------------------------------------------------------------
# El panel decia "Fuera del area: 361" justo encima de "1 punto cae FUERA", y
# la frase remataba "hasta 135,3 km del borde" cuando ese unico punto estaba a
# 2,4 km: los 135 km eran de una linea que la misma frase acababa de excluir.
# Tres numeros ciertos que juntos contaban una mentira.
_ejemplo = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        "data", "ejemplos_campo",
                        "qfield_fuera_del_area.zip")
if not os.path.exists(_ejemplo):
    omitir("cifras coherentes en una salida real", "falta el ejemplo de campo")
else:
    _rev_e = campo.revisar(campo.leer(_ejemplo))
    _re = _rev_e["resumen"]
    _suma = (_re.get("dentro", 0) + _re.get("recorridos_fuera", 0)
             + _re.get("fuera", 0))
    comprobar("dentro + recorridos + fuera suma los puntos leidos",
              _suma == _re.get("puntos"),
              f"{_re.get('dentro')} + {_re.get('recorridos_fuera')} + "
              f"{_re.get('fuera')} = {_suma} de {_re.get('puntos')}")

    _fuera_real = [d for d in _rev_e["puntos"]
                   if not d.get("dentro") and not d.get("recorrido")]
    if _fuera_real and _rev_e["problemas"]:
        _peor = max(d.get("km_fuera", 0) for d in _fuera_real)
        _dicho = _rev_e["problemas"][0]
        comprobar("la distancia que se nombra es la del punto que falla",
                  f"{_peor} km" in _dicho,
                  f"dice '{_peor} km', que es el peor de los que cuentan, "
                  f"no el de un recorrido excluido")
    else:
        omitir("la distancia que se nombra es la del punto que falla",
               "el ejemplo no tiene fichas fuera del area")

# ==============================================================================
print("")
print("BUSCAR REPETIDOS NO PUEDE CRECER AL CUADRADO")
# ------------------------------------------------------------------------------
# Comparar todos contra todos son 200 millones de distancias con 20.000
# puntos, y varios minutos de "Leyendo y revisando..." que desde fuera no se
# distinguen de un cuelgue. Con casillas de 10 m el resultado es el mismo.
_muchos = [{"lat": 6.1 + (i % 200) * 0.001,
            "lon": -74.9 + (i // 200) * 0.001,
            "titulo": f"p{i}", "nota": "", "fecha": "", "capa": "x"}
           for i in range(4000)]
# Dos pegados a proposito, para que el detector tenga algo que encontrar.
_muchos.append({**_muchos[0], "titulo": "gemelo"})
_arranque = time.time()
_rev_m = campo.revisar({"puntos": _muchos, "capas": [], "avisos": [],
                        "formato": "csv"})
_tardo = time.time() - _arranque
comprobar("4.001 puntos se revisan en un tiempo razonable",
          _tardo < 20,
          f"{_tardo:.1f} s")
comprobar("y los pegados se siguen detectando",
          any("menos de" in a for a in _rev_m["avisos"]),
          next((a for a in _rev_m["avisos"] if "menos de" in a),
               "no se detecto ninguno")[:70])


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
