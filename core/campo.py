"""
Lectura y revision de informacion levantada en campo.
===============================================================================

QUE RESUELVE
------------
La informacion de campo llega en tres formas, y hasta ahora el sistema no
sabia leer ninguna de las tres de cara a validarla:

    .zip   exportacion de QField: GeoPackages con notas y recorridos
    .xlsx  planilla con coordenadas en columnas
    .csv   lo mismo, en texto plano

Lo que importa no es abrirlas, que es facil, sino decir si lo que traen SIRVE.
Se midio con una exportacion real: cuatro notas, todas a 166 km del area de
estudio, dos en coordenadas identicas, un adjunto vacio y un recorrido sin
titulo. Nada de eso saltaba por ningun lado.

POR QUE LAS COMPROBACIONES NO LAS HACE EL AGENTE
------------------------------------------------
Porque son aritmetica y geometria, y tienen que dar el mismo resultado
siempre. Se probo lo contrario: preguntado por esas coordenadas, el agente
contesto que estaban en Bogota, barrio Usaquen. Acerto, pero lo saco de su
propio conocimiento, no de la cartografia del proyecto -- la herramienta del
sistema, preguntada por la misma coordenada, respondia "no determinado".

Para un estudio de impacto ambiental, acertar de memoria y verificar contra
una capa no son lo mismo. Aqui se decide con el poligono; el agente interpreta
y redacta, que es donde si aporta.
"""

import csv
import os
import sqlite3
import struct
import zipfile

from core import area_estudio

#: Nombres de columna que suelen traer la latitud y la longitud. Se comparan
#: en minusculas y sin espacios. La lista es de PRESENTACION: si falta un
#: nombre, el archivo no se rechaza, se avisa de que no se hallaron columnas.
COLUMNAS_LAT = ("lat", "latitud", "latitude", "y", "coord_y", "decimallatitude")
COLUMNAS_LON = ("lon", "lng", "long", "longitud", "longitude", "x", "coord_x",
                "decimallongitude")

#: Limites de una coordenada geografica valida en grados decimales.
LIMITE_LAT = 90.0
LIMITE_LON = 180.0

#: Dos puntos mas cerca que esto se consideran el mismo sitio. Diez metros es
#: menos que la precision tipica de un GPS de telefono, asi que por debajo no
#: se puede afirmar que sean lugares distintos.
METROS_DUPLICADO = 10.0


# ==============================================================================
# LECTURA
# ==============================================================================

def _punto_de_gpkg(blob: bytes) -> tuple:
    """
    Decodifica la geometria de un punto en formato GeoPackage.

    El encabezado del GeoPackage lleva una bandera que dice si hay envolvente
    y de que tamano; hay que saltarla para llegar al WKB.

    Args:
        blob: El contenido binario de la columna de geometria.

    Returns:
        Par `(lon, lat)`, o None si no se pudo decodificar.
    """
    try:
        bandera = blob[3]
        envolvente = (bandera >> 1) & 0x07
        saltos = {0: 0, 1: 32, 2: 48, 3: 48, 4: 64}[envolvente]
        wkb = blob[8 + saltos:]
        orden = "<" if wkb[0] == 1 else ">"
        tipo = struct.unpack(orden + "I", wkb[1:5])[0] % 1000

        # HAY QUE MIRAR EL TIPO. Un recorrido de QField es una LINEA, no un
        # punto, y leer sus bytes como si fuera un punto no da error: da
        # coordenadas basura. Se midio: un recorrido real salio "a 8.318 km
        # del area de estudio", que es mas que el ancho de Colombia y venia
        # de interpretar como latitud el numero de vertices de la linea.
        if tipo == 1:                                   # Point
            lon, lat = struct.unpack(orden + "dd", wkb[5:21])
            return lon, lat
        if tipo == 2:                                   # LineString
            # De un recorrido se toma el primer vertice: es donde empezo. Un
            # recorrido entero no se puede reducir a un punto, pero su inicio
            # si dice si la salida ocurrio dentro del area.
            cuantos = struct.unpack(orden + "I", wkb[5:9])[0]
            if cuantos < 1:
                return None
            lon, lat = struct.unpack(orden + "dd", wkb[9:25])
            return lon, lat
        return None                                     # poligonos y demas
    except (IndexError, KeyError, struct.error, TypeError):
        return None


def _leer_gpkg(ruta: str) -> dict:
    """
    Lee las capas de un GeoPackage y saca sus puntos.

    Args:
        ruta: Archivo .gpkg.

    Returns:
        Diccionario con `puntos`, `capas` y `avisos`.
    """
    puntos, capas, avisos = [], [], []
    try:
        con = sqlite3.connect(f"file:{ruta}?mode=ro", uri=True)
    except sqlite3.Error as exc:
        return {"puntos": [], "capas": [],
                "avisos": [f"no se pudo abrir {os.path.basename(ruta)}: {exc}"]}

    try:
        contenidos = list(con.execute(
            "select table_name, data_type, srs_id from gpkg_contents"))
    except sqlite3.Error:
        con.close()
        return {"puntos": [], "capas": [],
                "avisos": [f"{os.path.basename(ruta)} no parece un GeoPackage"]}

    for tabla, tipo, srs in contenidos:
        try:
            cuantos = con.execute(f'select count(*) from "{tabla}"').fetchone()[0]
            columnas = [r[1] for r in con.execute(f'PRAGMA table_info("{tabla}")')]
        except sqlite3.Error:
            continue
        capas.append({"nombre": tabla, "tipo": tipo, "epsg": srs,
                      "registros": cuantos, "columnas": columnas})

        # EPSG:4326 es lo que entrega QField. Otro sistema no es un error,
        # pero leerlo como si fueran grados pondria los puntos en otro
        # continente, asi que se avisa en vez de callar.
        if tipo == "features" and srs not in (4326, 0):
            avisos.append(f"la capa '{tabla}' usa EPSG:{srs} y no EPSG:4326; "
                          f"sus coordenadas no se interpretan")
            continue
        if tipo != "features" or "geom" not in columnas:
            continue

        etiqueta = next((c for c in ("title", "name", "nombre") if c in columnas), None)
        nota = next((c for c in ("note", "nota", "descripcion") if c in columnas), None)
        cuando = next((c for c in ("timestamp", "fecha") if c in columnas), None)
        pedidos = ["geom"] + [c for c in (etiqueta, nota, cuando) if c]
        try:
            filas = con.execute(
                f'select {", ".join(chr(34) + c + chr(34) for c in pedidos)} '
                f'from "{tabla}"')
        except sqlite3.Error:
            continue

        for fila in filas:
            par = _punto_de_gpkg(fila[0]) if fila[0] else None
            if par is None:
                continue
            datos = dict(zip(pedidos[1:], fila[1:]))
            puntos.append({
                "capa": tabla,
                "lon": par[0],
                "lat": par[1],
                "titulo": (datos.get(etiqueta) or "") if etiqueta else "",
                "nota": (datos.get(nota) or "") if nota else "",
                "fecha": (datos.get(cuando) or "") if cuando else "",
            })
    con.close()
    return {"puntos": puntos, "capas": capas, "avisos": avisos}


def _leer_tabla(ruta: str) -> dict:
    """
    Saca puntos de una planilla Excel o de un CSV.

    Args:
        ruta: Archivo .xlsx, .xls o .csv.

    Returns:
        Diccionario con `puntos`, `capas` y `avisos`.
    """
    extension = os.path.splitext(ruta)[1].lower()
    filas, cabecera = [], []

    if extension in (".xlsx", ".xls"):
        try:
            import openpyxl
            libro = openpyxl.load_workbook(ruta, read_only=True, data_only=True)
            hoja = libro[libro.sheetnames[0]]
            for i, fila in enumerate(hoja.iter_rows(values_only=True)):
                if i == 0:
                    cabecera = [str(c or "").strip() for c in fila]
                else:
                    filas.append(fila)
            libro.close()
        except Exception as exc:
            return {"puntos": [], "capas": [],
                    "avisos": [f"no se pudo leer la planilla: {exc}"]}
    else:
        try:
            with open(ruta, encoding="utf-8-sig", errors="replace",
                      newline="") as fh:
                muestra = fh.read(16384)
                fh.seek(0)
                try:
                    dialecto = csv.Sniffer().sniff(muestra, delimiters=",;\t|")
                    delim = dialecto.delimiter
                except csv.Error:
                    delim = ","
                lector = csv.reader(fh, delimiter=delim)
                for i, fila in enumerate(lector):
                    if i == 0:
                        cabecera = [c.strip() for c in fila]
                    else:
                        filas.append(fila)
        except OSError as exc:
            return {"puntos": [], "capas": [],
                    "avisos": [f"no se pudo leer el archivo: {exc}"]}

    normal = [c.strip().lower().replace(" ", "") for c in cabecera]
    i_lat = next((i for i, c in enumerate(normal) if c in COLUMNAS_LAT), None)
    i_lon = next((i for i, c in enumerate(normal) if c in COLUMNAS_LON), None)
    if i_lat is None or i_lon is None:
        return {
            "puntos": [], "capas": [{"nombre": os.path.basename(ruta),
                                     "tipo": "tabla", "epsg": None,
                                     "registros": len(filas),
                                     "columnas": cabecera}],
            "avisos": ["no se hallaron columnas de latitud y longitud. "
                       "Se buscaron nombres como lat/latitud y lon/longitud."],
        }

    # La primera columna de texto que no sea una coordenada se toma como la
    # observacion. Sin esto, una planilla con su columna "observacion" bien
    # rellenada salia avisada de "no traen ni titulo ni nota", que es falso y
    # resta credibilidad al resto de los avisos.
    i_nota = next((i for i, c in enumerate(normal)
                   if i not in (i_lat, i_lon)
                   and any(p in c for p in ("nota", "observ", "descrip",
                                            "comentario", "detalle"))), None)
    if i_nota is None:
        i_nota = next((i for i in range(len(cabecera))
                       if i not in (i_lat, i_lon)), None)

    puntos, malos = [], 0
    for fila in filas:
        try:
            lat = float(str(fila[i_lat]).replace(",", "."))
            lon = float(str(fila[i_lon]).replace(",", "."))
        except (TypeError, ValueError, IndexError):
            malos += 1
            continue
        nota = ""
        if i_nota is not None and i_nota < len(fila):
            nota = str(fila[i_nota] or "").strip()
        puntos.append({
            "capa": os.path.basename(ruta), "lon": lon, "lat": lat,
            "titulo": "", "nota": nota, "fecha": "",
        })

    avisos = []
    if malos:
        avisos.append(f"{malos} fila(s) no traian coordenadas legibles")
    return {
        "puntos": puntos,
        "capas": [{"nombre": os.path.basename(ruta), "tipo": "tabla",
                   "epsg": 4326, "registros": len(filas),
                   "columnas": cabecera}],
        "avisos": avisos,
    }


def leer(ruta: str) -> dict:
    """
    Lee un archivo de campo, sea cual sea de los tres formatos.

    Args:
        ruta: El archivo a leer. Un .zip se abre y se leen los GeoPackage
            que contenga.

    Returns:
        Diccionario con `puntos`, `capas`, `avisos` y `formato`.
    """
    extension = os.path.splitext(ruta)[1].lower()

    if extension == ".zip":
        import tempfile

        carpeta = tempfile.mkdtemp(prefix="campo_")
        puntos, capas, avisos = [], [], []
        try:
            with zipfile.ZipFile(ruta) as z:
                # Se extrae solo lo que se va a leer. Un .zip de fuera es
                # material no confiable: no se vuelca entero sin mirar.
                nombres = [n for n in z.namelist()
                           if n.lower().endswith((".gpkg", ".csv", ".xlsx"))
                           and not n.startswith(("/", "..")) and ".." not in n]
                if not nombres:
                    return {"puntos": [], "capas": [], "formato": "zip",
                            "avisos": ["el .zip no trae GeoPackage, CSV ni "
                                       "planilla que se pueda revisar"]}
                for nombre in nombres:
                    destino = os.path.join(carpeta, os.path.basename(nombre))
                    with z.open(nombre) as origen, open(destino, "wb") as salida:
                        salida.write(origen.read())
                    leido = leer(destino)
                    puntos.extend(leido["puntos"])
                    capas.extend(leido["capas"])
                    avisos.extend(leido["avisos"])
        except zipfile.BadZipFile:
            return {"puntos": [], "capas": [], "formato": "zip",
                    "avisos": ["el archivo no es un .zip valido"]}
        finally:
            import shutil
            shutil.rmtree(carpeta, ignore_errors=True)
        return {"puntos": puntos, "capas": capas, "avisos": avisos,
                "formato": "zip"}

    if extension == ".gpkg":
        return {**_leer_gpkg(ruta), "formato": "gpkg"}
    if extension in (".xlsx", ".xls", ".csv"):
        return {**_leer_tabla(ruta), "formato": extension.lstrip(".")}

    return {"puntos": [], "capas": [], "formato": extension.lstrip("."),
            "avisos": [f"no se sabe leer '{extension}'. Se admiten .zip, "
                       f".gpkg, .xlsx y .csv"]}


# ==============================================================================
# REVISION
# ==============================================================================

def revisar(lectura: dict) -> dict:
    """
    Decide si la informacion de campo sirve, y por que no cuando no sirve.

    Cada comprobacion es determinista: misma entrada, mismo veredicto. El
    agente no interviene aqui.

    Args:
        lectura: Lo que devuelve `leer()`.

    Returns:
        Diccionario con `sirve`, `problemas`, `avisos`, `resumen` y `puntos`.
        `problemas` impide guardar; `avisos` no, pero se muestra.
    """
    puntos = lectura.get("puntos") or []
    problemas = []
    avisos = list(lectura.get("avisos") or [])

    if not puntos:
        problemas.append("no se halló ni una coordenada legible")
        return {"sirve": False, "problemas": problemas, "avisos": avisos,
                "resumen": {"puntos": 0}, "puntos": []}

    # --- Coordenadas imposibles -------------------------------------------
    # Se comprueba ANTES que nada: un punto en 999 de latitud no esta "fuera
    # del area", esta mal escrito, y decir lo primero despista.
    imposibles = [p for p in puntos
                  if abs(p["lat"]) > LIMITE_LAT or abs(p["lon"]) > LIMITE_LON]
    if imposibles:
        problemas.append(
            f"{len(imposibles)} punto(s) con coordenadas imposibles "
            f"(fuera de ±90 / ±180). Revisa si se invirtieron latitud y "
            f"longitud, o si el archivo no está en grados decimales")

    # --- Dentro del area de estudio ---------------------------------------
    validos = [p for p in puntos if p not in imposibles]
    veredicto = area_estudio.revisar_puntos(validos)
    if not veredicto["hay_area"]:
        avisos.append(veredicto["motivo"] + ". No se pudo comprobar la "
                                            "ubicación")
    elif veredicto["fuera"]:
        lejos = max((d.get("km_fuera", 0) for d in veredicto["detalle"]
                     if not d["dentro"]), default=0)
        problemas.append(
            f"{veredicto['fuera']} de {veredicto['total']} punto(s) caen "
            f"FUERA del área de estudio, hasta {lejos} km del borde")

        # Antes de dejarlo ahí, se comprueba la equivocación más común: haber
        # puesto la latitud donde va la longitud. Decir "está a 10.402 km" es
        # cierto pero no ayuda; decir "parece que están invertidas" sí.
        girados = [{**p, "lat": p["lon"], "lon": p["lat"]} for p in validos
                   if abs(p["lon"]) <= LIMITE_LAT]
        if len(girados) == len(validos):
            prueba = area_estudio.revisar_puntos(girados)
            if prueba["hay_area"] and prueba["dentro"] == len(girados):
                problemas.append(
                    "PERO si se intercambian latitud y longitud, TODOS caen "
                    "dentro del área: lo más probable es que las columnas "
                    "estén cambiadas en el archivo de origen")

    # --- Puntos repetidos --------------------------------------------------
    repetidos = 0
    for i, uno in enumerate(validos):
        for otro in validos[i + 1:]:
            metros = area_estudio.distancia_km(
                (uno["lon"], uno["lat"]), (otro["lon"], otro["lat"])) * 1000
            if metros < METROS_DUPLICADO:
                repetidos += 1
                break
    if repetidos:
        avisos.append(f"{repetidos} punto(s) están a menos de "
                      f"{METROS_DUPLICADO:.0f} m de otro: pueden ser registros "
                      f"repetidos del mismo sitio")

    # --- Registros sin contenido ------------------------------------------
    sin_nota = sum(1 for p in validos
                   if not (p.get("nota") or "").strip()
                   and not (p.get("titulo") or "").strip())
    if sin_nota:
        avisos.append(f"{sin_nota} registro(s) no traen ni título ni nota")

    # --- Capas vacias o sin adjunto ---------------------------------------
    for capa in lectura.get("capas") or []:
        if capa.get("registros") == 0:
            avisos.append(f"la capa '{capa['nombre']}' llegó vacía")

    resumen = {
        "puntos": len(puntos),
        "validos": len(validos),
        "dentro": veredicto.get("dentro", 0),
        "fuera": veredicto.get("fuera", 0),
        "hay_area": veredicto.get("hay_area", False),
        "capas": len(lectura.get("capas") or []),
        "formato": lectura.get("formato", ""),
    }
    return {"sirve": not problemas, "problemas": problemas, "avisos": avisos,
            "resumen": resumen, "puntos": veredicto.get("detalle") or validos}
