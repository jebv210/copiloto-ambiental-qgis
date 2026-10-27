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
