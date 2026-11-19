"""
Bateria del paquete de QGIS: que el archivo entregado ABRA de verdad.

POR QUE ESTA BATERIA ES DISTINTA
--------------------------------
Las demas comprueban el sistema contra si mismo. Esta lo comprueba contra
QGIS: construye el paquete, lo descomprime y se lo da a QGIS para que lo
abra, con el mismo motor que usara la persona que lo reciba.

Es la unica prueba que vale para un entregable. Un GeoPackage se puede
escribir con las tablas en su sitio y aun asi no abrirse: basta que falte el
numero magico del archivo, que una fila del catalogo no cuadre, o que el
encabezado binario de la geometria declare mal su envolvente. Nada de eso se
ve leyendo el codigo; se ve cuando QGIS dice "capa no valida".

QUE SE EXIGE
------------
  - que el proyecto abra y traiga sus capas
  - que cada capa sea VALIDA, no solo que aparezca en la lista
  - que los recuentos y los tipos de geometria coincidan con el origen
  - que el sistema de referencia sea EPSG:4326
  - que los nombres de campo lleguen ENTEROS, que es la razon de haber
    elegido GeoPackage y no shapefile
  - que las rutas sean relativas, porque de eso depende que funcione en
    otro ordenador

SE EJECUTA CON EL PYTHON DE QGIS
--------------------------------
    "C:\\Program Files\\QGIS 3.40.4\\apps\\Python312\\python.exe" probar_paquete_qgis.py

Con el interprete normal no hay `qgis.core` y la bateria lo dice en vez de
fingir que paso.
"""

import glob
import json
import os
import re
import shutil
import sys
import tempfile
import zipfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

ACIERTOS = 0
FALLOS = []


def comprobar(nombre: str, condicion: bool, detalle: str = "") -> None:
    """
    Apunta una comprobacion y la imprime.

    Args:
        nombre: Que se comprobaba.
        condicion: Resultado; verdadero es exito.
        detalle: Texto explicativo que se imprime al lado.
    """
    global ACIERTOS
    if condicion:
        ACIERTOS += 1
        print(f"  [OK ] {nombre}: {detalle}")
    else:
        FALLOS.append(f"{nombre} -> {detalle}")
        print(f"  [FALLA] {nombre}: {detalle}")


def arrancar_qgis():
    """
    Pone en marcha un QGIS sin ventana.

    Returns:
        Par `(aplicacion, motivo)`. La aplicacion es None si QGIS no esta.
    """
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

    raiz = os.path.abspath(os.path.join(sys.prefix, "..", ".."))
    for sufijo in ("apps/qgis-ltr/python", "apps/qgis/python"):
        ruta = os.path.join(raiz, *sufijo.split("/"))
        if os.path.isdir(ruta) and ruta not in sys.path:
            sys.path.insert(0, ruta)

    try:
        from qgis.core import QgsApplication
    except Exception as exc:
        return None, f"no se pudo importar qgis.core ({type(exc).__name__})"

    for sufijo in ("apps/qgis-ltr", "apps/qgis"):
        prefijo = os.path.join(raiz, *sufijo.split("/"))
        if os.path.isdir(prefijo):
            QgsApplication.setPrefixPath(prefijo, True)
            break

    app = QgsApplication([], False)
    app.initQgis()
    return app, ""


print("=" * 70)
print("  Paquete de QGIS: que lo que se entrega se pueda abrir")
print("=" * 70)

BASE = os.path.dirname(os.path.abspath(__file__))
_fuentes = sorted(glob.glob(os.path.join(BASE, "data", "*.geojson")))
if not _fuentes:
    print("\n  No hay capas en data/. Genera primero el area de estudio.")
    sys.exit(1)

_app, _motivo = arrancar_qgis()
if _app is None:
    print(f"\n  QGIS no esta disponible: {_motivo}")
    print("  Esta bateria necesita el interprete de QGIS; con el normal no")
    print("  probaria lo unico que tiene que probar.")
    sys.exit(1)

from qgis.core import QgsProject, QgsVectorLayer  # noqa: E402

from core import paquete_qgis as _pq  # noqa: E402

_banco = tempfile.mkdtemp(prefix="probar_paquete_")
try:
    # ==========================================================================
    # 1. SE CONSTRUYE EL PAQUETE
    # ==========================================================================
    print("\n--- Se arma el paquete ---")

    _zip = os.path.join(_banco, "Area_estudio_QGIS.zip")
    _hecho = _pq.construir(_zip, _fuentes)

    comprobar("el paquete se arma y pesa algo razonable",
              0 < _hecho["bytes"] < 50 * 1024 * 1024,
              f"{_hecho['bytes'] / 1024:.0f} kB con {_hecho['entidades']} "
              f"entidades")

    _dentro = zipfile.ZipFile(_zip).namelist()
    comprobar("trae proyecto, capas y un LEEME",
              {"proyecto.qgs", "capas.gpkg", "LEEME.txt"} <= set(_dentro),
              ", ".join(sorted(_dentro)))

    _carpeta = os.path.join(_banco, "abierto")
    zipfile.ZipFile(_zip).extractall(_carpeta)

    # ==========================================================================
    # 2. QGIS ABRE EL PROYECTO
    # ==========================================================================
    print("\n--- QGIS lo abre ---")

    _proyecto = QgsProject.instance()
    _abrio = _proyecto.read(os.path.join(_carpeta, "proyecto.qgs"))
    comprobar("QGIS abre el proyecto sin rechazarlo",
              _abrio, "read() devolvio verdadero")

    _capas = list(_proyecto.mapLayers().values())
    # El proyecto lleva las capas de datos MAS el mapa base de satelite,
    # que es un raster de teselas y no sale en `_hecho["capas"]`.
    _vectoriales = [c for c in _capas if isinstance(c, QgsVectorLayer)]
    _rasters = [c for c in _capas if not isinstance(c, QgsVectorLayer)]
    comprobar("aparecen todas las capas que se metieron",
              len(_vectoriales) == len(_hecho["capas"]),
              f"{len(_vectoriales)} capas de datos de "
              f"{len(_hecho['capas'])} esperadas")

    # El satelite da contexto -- relieve, cultivos, el casco urbano -- que
    # un poligono verde sobre fondo blanco no da. Va de fondo, nunca
    # encima: taparia justo lo que se quiere ver.
    comprobar("el proyecto trae su mapa base de satelite",
              len(_rasters) == 1,
              _rasters[0].name() if _rasters else "no hay mapa base")

    _orden = [c.name() for c in _proyecto.layerTreeRoot().layerOrder()]
    # EL SISTEMA DE REFERENCIA DEL MAPA BASE TIENE QUE ESTAR DECLARADO
    # -----------------------------------------------------------------
    # La primera version no lo declaraba. `isValid()` devolvia True -- por
    # eso la bateria la daba por buena -- pero en QGIS la capa salia con un
    # interrogante y el mapa, gris: las teselas van en Pseudo-Mercator y el
    # proyecto en grados, y sin saberlo QGIS no puede reproyectarlas.
    #
    # "Valida" no significa "se ve". Esta comprobacion mira lo segundo.
    # EL PROYECTO TIENE QUE TENER SISTEMA DE REFERENCIA
    # --------------------------------------------------
    # Parece obvio y no lo era: QGIS lee el CRS del proyecto de
    # `<properties><SpatialRefSys>`, no del bloque `<projectCrs>`. Con solo
    # ese bloque, el proyecto abria SIN sistema de referencia.
    #
    # Los vectores se veian igual -- ya estaban en grados -- asi que el
    # fallo parecia ser solo del mapa base, que viene en metros y no se
    # podia reproyectar. Fondo gris y un interrogante en la capa.
    comprobar("el proyecto declara su sistema de referencia",
              _proyecto.crs().isValid()
              and _proyecto.crs().authid() == "EPSG:4326",
              _proyecto.crs().authid() or "SIN sistema de referencia")

    comprobar("el mapa base declara en que sistema estan sus teselas",
              bool(_rasters) and _rasters[0].crs().authid() == "EPSG:3857",
              _rasters[0].crs().authid() or "sin sistema de referencia"
              if _rasters else "no hay mapa base")

    comprobar("y va debajo de todas las demas",
              bool(_rasters) and _orden and _orden[-1] == _rasters[0].name(),
              f"ultimo del orden de dibujo: {_orden[-1] if _orden else '-'}")

    # Que una capa aparezca en la lista NO significa que se pueda usar: una
    # capa rota tambien aparece, con el nombre en rojo y sin un solo
    # registro. `isValid()` es lo que distingue las dos cosas.
    # El mapa base se valida contra internet: sin conexion sale invalido y
    # eso no es un defecto del paquete. Se comprueban las de datos.
    _invalidas = [c.name() for c in _vectoriales if not c.isValid()]
    comprobar("todas las capas son VALIDAS, no solo visibles",
              not _invalidas,
              "QGIS puede leerlas todas" if not _invalidas
              else f"rotas: {_invalidas}")

    # ==========================================================================
    # 3. LOS DATOS LLEGAN COMPLETOS
    # ==========================================================================
    print("\n--- Los datos llegan completos ---")

    _esperado = {c["nombre"]: c for c in _hecho["capas"]}
    _por_nombre = {c.name(): c for c in _vectoriales}

    _cuentas_ok, _detalle_cuentas = True, []
    for _nombre, _quiere in _esperado.items():
        _capa = _por_nombre.get(_nombre)
        if _capa is None:
            _cuentas_ok = False
            _detalle_cuentas.append(f"{_nombre}: no esta")
            continue
        _tiene = _capa.featureCount()
        if _tiene != _quiere["entidades"]:
            _cuentas_ok = False
            _detalle_cuentas.append(
                f"{_nombre}: {_tiene} de {_quiere['entidades']}")
    comprobar("no se pierde ni una entidad por el camino",
              _cuentas_ok,
              f"{_hecho['entidades']} entidades, todas presentes"
              if _cuentas_ok else "; ".join(_detalle_cuentas))

    _tipos = {"Point": 0, "LineString": 1, "Polygon": 2}
    _geom_ok = all(
        _por_nombre[n].geometryType() == _tipos[c["tipo"]]
        for n, c in _esperado.items() if n in _por_nombre)
    comprobar("cada capa conserva su tipo de geometria",
              _geom_ok,
              ", ".join(f"{c['tipo']}" for c in _hecho["capas"]))

    _crs = {c.crs().authid() for c in _vectoriales}
    comprobar("todo viene en EPSG:4326",
              _crs == {"EPSG:4326"},
              f"sistemas encontrados: {sorted(_crs)}")

    # ==========================================================================
    # 4. LOS NOMBRES DE CAMPO, ENTEROS
    # ==========================================================================
    print("\n--- Los nombres de campo llegan enteros ---")

    # Esta es LA razon de haber elegido GeoPackage. En el proyecto de
    # mariposas del equipo, hecho con shapefile, los campos llegaron
    # cortados a 10 caracteres: "Comportami", "Nombre cie", "Numero in". No
    # se puede deshacer, y quien lo reciba no sabe que decian.
    _perdidos = []
    for _nombre, _quiere in _esperado.items():
        _capa = _por_nombre.get(_nombre)
        if _capa is None:
            continue
        _hay = {f.name() for f in _capa.fields()}
        for _campo in _quiere["columnas"]:
            if _campo not in _hay:
                _perdidos.append(f"{_nombre}.{_campo}")
    comprobar("ningun nombre de campo se corta ni se pierde",
              not _perdidos,
              "todos enteros, sin el tope de 10 caracteres del shapefile"
              if not _perdidos else f"faltan: {_perdidos}")

    # Las capas que genera hoy el proyecto tienen nombres cortos -- nombre,
    # origen, vertices --, asi que con ellas la comprobacion de arriba se
    # cumple sin demostrar nada. Para que demuestre algo hay que escribir
    # campos que un shapefile SI cortaria.
    #
    # Se usan los del formulario de mariposas del equipo, que es el caso
    # real: en su .dbf quedaron como "Comportami", "Nombre cie" y
    # "Nivel de c", y esos nombres ya no se pueden recuperar.
    _nombres_largos = ["Comportamiento", "Nombre cientifico",
                       "Nivel de confianza", "Numero individuos",
                       "Lugar especifico"]
    _prueba = [{
        "nombre": "Formulario de campo",
        "tipo": "Point",
        "entidades": [{
            "propiedades": {c: f"valor de {c}" for c in _nombres_largos},
            "coordenadas": [-75.0413, 6.1714],
        }],
    }]
    _gpkg_largo = os.path.join(_banco, "campos_largos.gpkg")
    _pq.escribir_geopaquete(_gpkg_largo, _prueba)
    _capa_larga = QgsVectorLayer(
        f"{_gpkg_largo}|layername=formulario_de_campo", "prueba", "ogr")
    _leidos = [f.name() for f in _capa_larga.fields()]
    _sobreviven = [c for c in _nombres_largos if c in _leidos]
    comprobar("un nombre de 18 caracteres sobrevive entero",
              _capa_larga.isValid() and len(_sobreviven) == len(_nombres_largos),
              f"{len(_sobreviven)}/{len(_nombres_largos)} enteros, incluido "
              f"'Nivel de confianza'; un shapefile lo habria dejado en "
              f"'Nivel de c'")

    # ==========================================================================
    # 5. EL PAQUETE FUNCIONA EN OTRO SITIO
    # ==========================================================================
    print("\n--- Funciona movido de carpeta ---")

    # Las rutas relativas son lo que hace que el paquete sirva para
    # entregarselo a alguien. Con rutas absolutas el proyecto buscaria las
    # capas en la carpeta del servidor que lo genero, y quien lo abriera
    # veria tres capas rotas sin entender por que.
    _texto_qgs = open(os.path.join(_carpeta, "proyecto.qgs"),
                      encoding="utf-8").read()
    comprobar("el proyecto declara rutas relativas",
              "<Absolute type=\"bool\">false</Absolute>" in _texto_qgs,
              "Absolute=false, como el proyecto de campo del equipo")
    comprobar("y no se cuela ninguna ruta del servidor",
              "/app/" not in _texto_qgs and ":\\" not in _texto_qgs,
              "ninguna ruta absoluta dentro del .qgs")

    _mudado = os.path.join(_banco, "otra", "carpeta", "distinta")
    os.makedirs(os.path.dirname(_mudado), exist_ok=True)
    shutil.copytree(_carpeta, _mudado)
    _proyecto.clear()
    _abrio2 = _proyecto.read(os.path.join(_mudado, "proyecto.qgs"))
    _capas2 = list(_proyecto.mapLayers().values())
    _vect2 = [c for c in _capas2 if isinstance(c, QgsVectorLayer)]
    comprobar("abre igual desde otra carpeta, con las capas vivas",
              _abrio2 and len(_vect2) == len(_hecho["capas"])
              and all(c.isValid() for c in _vect2),
              f"{len(_vect2)} capas de datos validas tras mover el paquete")

    # ==========================================================================
    # 6. LO QUE SE ESCRIBE, ESTE PROYECTO LO SABE VOLVER A LEER
    # ==========================================================================
    print("\n--- El sistema entiende lo que el mismo escribe ---")

    # `core/campo.py` lee GeoPackages para validar lo que vuelve de QField.
    # Escribe y lee el mismo formato binario, asi que tienen que entenderse:
    # si uno cambia el encabezado y el otro no, dejan de hacerlo y nadie se
    # entera hasta que una validacion de campo da coordenadas absurdas.
    from core import campo as _campo

    _gpkg = os.path.join(_carpeta, "capas.gpkg")
    _leido = _campo.leer(_gpkg)
    _puntos = _leido.get("puntos") or []
    _esperados_punto = sum(c["entidades"] for c in _hecho["capas"]
                           if c["tipo"] == "Point")
    # Recupera MAS que los puntos sueltos, y es correcto: de cada linea
    # toma su primer vertice, porque para saber si una salida de campo
    # ocurrio dentro del area basta con donde empezo el recorrido.
    _lineas = sum(c["entidades"] for c in _hecho["capas"]
                  if c["tipo"] == "LineString")
    comprobar("core/campo.py vuelve a leer el GeoPackage que se genero",
              len(_puntos) == _esperados_punto + _lineas > 0,
              f"{len(_puntos)} puntos: los {_esperados_punto} sueltos mas el "
              f"primer vertice de cada una de las {_lineas} lineas")

    if _puntos:
        _dentro_rango = all(-82 < p["lon"] < -66 and -5 < p["lat"] < 13
                            for p in _puntos if p.get("lon") is not None)
        comprobar("y las coordenadas caen donde deben, no en bytes basura",
                  _dentro_rango,
                  "todas dentro de Colombia")

    # ==========================================================================
    # 6b. CADA CAPA CON SU COLOR
    # ==========================================================================
    #
    # Con tres capas de puntos sobre el mismo mapa -- los del area, los de
    # campo y las veredas -- todas del mismo rojo, no habia forma de saber
    # cual era cual sin ir apagandolas una a una.
    print("\n--- Cada capa con su color ---")

    _xml = open(os.path.join(_carpeta, "proyecto.qgs"), encoding="utf-8").read()
    _colores = {}
    for _bloque in _xml.split("<maplayer")[1:]:
        _n = _bloque.split("<layername>")[1].split("</layername>")[0]             if "<layername>" in _bloque else "?"
        _c = re.findall(r'value="(\d+,\d+,\d+,\d+)"', _bloque)
        if _c:
            _colores[_n] = _c[0]

    _de_puntos = {n: c for n, c in _colores.items()
                  if n in {x["nombre"] for x in _hecho["capas"]
                           if x["tipo"] == "Point"}}
    comprobar("dos capas de puntos nunca comparten color",
              len(set(_de_puntos.values())) == len(_de_puntos),
              f"{len(_de_puntos)} capas de puntos, "
              f"{len(set(_de_puntos.values()))} colores distintos")

    # El agua se lee en azul. Con una paleta unica numerando todas las
    # capas seguidas, las quebradas salian del mismo rojo que el borde del
    # municipio, y un rio rojo no se lee como un rio.
    _lineas_capas = [x["nombre"] for x in _hecho["capas"]
                     if x["tipo"] == "LineString"]
    if _lineas_capas:
        _azules = set(_pq.PALETA_POR_TIPO["LineString"])
        comprobar("las lineas van en la gama del agua",
                  all(_colores.get(n) in _azules for n in _lineas_capas),
                  f"{[_colores.get(n) for n in _lineas_capas]}")

    # Un poligono con relleno opaco taparia el mapa base, que es justo lo
    # que se acaba de anadir.
    _poli = [x["nombre"] for x in _hecho["capas"] if x["tipo"] == "Polygon"]
    if _poli:
        _alfa = int(_colores.get(_poli[0], "0,0,0,255").split(",")[3])
        comprobar("el relleno del area deja ver el mapa base",
                  _alfa < 80,
                  f"opacidad {_alfa} de 255")

    # ==========================================================================
    # 7. Y SE COMPRUEBA MIRANDO LOS PIXELES, NO PREGUNTANDO
    # ==========================================================================
    #
    # Esta es la comprobacion que faltaba. Antes se daba por bueno el mapa
    # base porque `isValid()` devolvia True. Devolvia True con el proyecto
    # sin sistema de referencia, con el recuadro en las unidades
    # equivocadas y sin renderizador: tres fallos a la vez, y los tres
    # invisibles a esa pregunta.
    #
    # "Valida" no significa "se ve". Lo unico que lo demuestra es dibujar.
    print("\n--- Se dibuja de verdad ---")

    from qgis.core import (QgsMapRendererParallelJob, QgsMapSettings,
                           QgsRectangle)
    from qgis.PyQt.QtCore import QEventLoop, QSize
    from qgis.PyQt.QtGui import QColor

    _proyecto.clear()
    _proyecto.read(os.path.join(_carpeta, "proyecto.qgs"))
    _ajustes = QgsMapSettings()
    _ajustes.setLayers(_proyecto.layerTreeRoot().layerOrder())
    # Un fondo que no aparece en ningun mapa: lo que siga siendo magenta es
    # lo que nadie dibujo.
    _ajustes.setBackgroundColor(QColor(255, 0, 255))
    _ajustes.setOutputSize(QSize(300, 200))
    _ajustes.setDestinationCrs(_proyecto.crs())
    _l = [c["limites"] for c in _hecho["capas"] if c.get("limites")]
    _ajustes.setExtent(QgsRectangle(
        min(x[0] for x in _l), min(x[1] for x in _l),
        max(x[2] for x in _l), max(x[3] for x in _l)))
    _trabajo = QgsMapRendererParallelJob(_ajustes)
    _bucle = QEventLoop()
    _trabajo.finished.connect(_bucle.quit)
    _trabajo.start()
    _bucle.exec_()
    _imagen = _trabajo.renderedImage()
    _muestras = [(x, y) for x in range(0, 300, 5) for y in range(0, 200, 5)]
    _sin_pintar = sum(1 for x, y in _muestras
                      if _imagen.pixelColor(x, y).name() == "#ff00ff")

    comprobar("el mapa se dibuja: no queda fondo sin pintar",
              _sin_pintar == 0,
              f"{len(_muestras) - _sin_pintar}/{len(_muestras)} puntos "
              f"muestreados llevan algo dibujado")

    # Con mapa base, hasta las esquinas -- fuera del area de estudio --
    # tienen imagen. Sin el, serian fondo liso.
    _esquinas = [(4, 4), (295, 4), (4, 195), (295, 195)]
    _con_fondo = sum(1 for x, y in _esquinas
                     if _imagen.pixelColor(x, y).name() != "#ff00ff")
    comprobar("el mapa base llega hasta las esquinas",
              _con_fondo == 4,
              f"{_con_fondo}/4 esquinas con imagen de satelite")


    # ==========================================================================
    # EL AREA DE ESTUDIO, POR ENCIMA DE TODO
    # ==========================================================================
    # Este apartado nacio de una pantalla. El mapa traia doce capas -- areas
    # protegidas, fallas, unidades geologicas, puntos de campo -- y no habia
    # forma de saber de donde era: el limite del municipio era un poligono
    # mas, dibujado en blanco, debajo de los otros 444 poligonos y fuera del
    # encuadre, que abarcaba las capas nacionales recortadas con holgura.
    #
    # Un mapa de un area de estudio tiene que decir de que area es.
    print("\n--- El area de estudio manda ---")

    # El proyecto se reabre: los apartados anteriores lo movieron de carpeta
    # y lo volvieron a leer, asi que las capas de mas arriba ya no existen.
    _proyecto.clear()
    _proyecto.read(os.path.join(_carpeta, "proyecto.qgs"))
    _vivas = [c for c in _proyecto.mapLayers().values()
              if isinstance(c, QgsVectorLayer)]
    _orden = [c.name() for c in _proyecto.layerTreeRoot().layerOrder()]

    _ficha = _pq.ficha_del_area()
    comprobar("se sabe como se llama el area de estudio",
              bool(_ficha.get("nombre")),
              _ficha.get("nombre") or "no hay area fijada")

    _nombre_area = _ficha.get("nombre", "")
    _del_area = [c for c in _hecho["capas"] if c.get("destacada")]
    comprobar("el paquete trae marcado el limite del area",
              len(_del_area) == 1,
              _del_area[0]["nombre"] if _del_area else "ninguna destacada")

    if _del_area:
        comprobar("la capa se llama como el area, no como el archivo",
                  _nombre_area and _del_area[0]["nombre"].startswith(
                      _nombre_area),
                  f"{_del_area[0]['nombre']!r}, no 'Municipios filtrados'")

        comprobar("va la PRIMERA del arbol, encima de todas las demas",
                  _orden and _orden[0] == _del_area[0]["nombre"],
                  f"arriba esta {_orden[0]!r}" if _orden else "arbol vacio")

        _capa_area = next((c for c in _vivas
                           if c.name() == _del_area[0]["nombre"]), None)
        comprobar("QGIS la abre como capa valida",
                  _capa_area is not None and _capa_area.isValid(),
                  _del_area[0]["nombre"])

        if _capa_area is not None:
            _simbolo = _capa_area.renderer().symbol()
            _capa_simbolo = _simbolo.symbolLayer(0)
            _relleno = _capa_simbolo.color()
            comprobar("no lleva relleno: estando arriba, taparia el resto",
                      _relleno.alpha() == 0
                      or _capa_simbolo.brushStyle() == 0,
                      f"opacidad del relleno {_relleno.alpha()} de 255")

            _borde = _capa_simbolo.strokeColor().name()
            _otros = []
            for _c in _vivas:
                if _c is _capa_area:
                    continue
                try:
                    _otros.append(_c.renderer().symbol().color().name())
                    _otros.append(
                        _c.renderer().symbol().symbolLayer(0)
                        .strokeColor().name())
                except Exception:
                    pass
            comprobar("su color no lo usa ninguna otra capa",
                      _borde not in _otros,
                      f"{_borde} frente a {len(set(_otros))} colores mas")

            comprobar("lleva etiqueta, que es lo que DICE donde queda",
                      _capa_area.labelsEnabled(),
                      "el nombre se escribe sobre el mapa")

    # El encuadre de salida: el area, no la suma de todo lo que haya.
    with open(os.path.join(_carpeta, "proyecto.qgs"),
              encoding="utf-8") as _fh:
        _xml = _fh.read()
    _caja = re.search(r"<mapcanvas.*?<extent>(.*?)</extent>", _xml, re.S)
    _nums = [float(v) for v in re.findall(r">(-?[\d.]+)<", _caja.group(1))]
    _ancho_vista = _nums[2] - _nums[0]
    if _del_area:
        _l = _del_area[0]["limites"]
        _ancho_area = _l[2] - _l[0]
        comprobar("el mapa se abre sobre el area, no sobre todo el pais",
                  _ancho_vista < _ancho_area * 1.5,
                  f"{_ancho_vista:.3f} grados de vista para un area de "
                  f"{_ancho_area:.3f}")

    comprobar("el titulo del proyecto nombra el area, y no va escrito a mano",
              _nombre_area and _nombre_area in _xml,
              f"el .qgs dice {_nombre_area!r}")

    # Y entra aunque no se pida: un mapa que no dice de donde es obliga a
    # adivinarlo por el relieve.
    _sin_area = [r for r in _fuentes
                 if os.path.basename(r) != _pq.ARCHIVO_AREA]
    if _sin_area and len(_sin_area) < len(_fuentes):
        _otro = _pq.construir(os.path.join(_banco, "sin_pedirla.zip"),
                              _sin_area)
        comprobar("el area entra aunque no se incluya en la lista",
                  any(c.get("destacada") for c in _otro["capas"]),
                  "se anade sola")

    _proyecto.clear()
finally:
    shutil.rmtree(_banco, ignore_errors=True)

# ==============================================================================
print("\n" + "=" * 70)
if FALLOS:
    print(f"{ACIERTOS} comprobaciones superadas, {len(FALLOS)} FALLIDAS:\n")
    for _f in FALLOS:
        print("  -", _f)
    # `os._exit` evita el fallo de segmentacion que da Qt al desmontarse.
    sys.stdout.flush()
    os._exit(1)
print(f"{ACIERTOS} de {ACIERTOS} comprobaciones superadas")
sys.stdout.flush()
os._exit(0)
