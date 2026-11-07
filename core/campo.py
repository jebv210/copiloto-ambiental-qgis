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
            return lon, lat, False
        if tipo == 2:                                   # LineString
            # De un recorrido se toma el primer vertice: es donde empezo. Un
            # recorrido entero no se puede reducir a un punto, pero su inicio
            # si dice si la salida ocurrio dentro del area.
            cuantos = struct.unpack(orden + "I", wkb[5:9])[0]
            if cuantos < 1:
                return None
            lon, lat = struct.unpack(orden + "dd", wkb[9:25])
            # El tercer valor dice que esto NO es un punto propio sino el
            # arranque de un recorrido. Importa despues: un recorrido que
            # empieza fuera del area es normal -- una quebrada nace donde
            # nace -- y no puede invalidar los datos como lo haria una
            # ficha de campo tomada fuera.
            return lon, lat, True
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
                "recorrido": par[2],
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


#: Columnas del .dbf de las que sale el titulo de un registro, por orden de
#: preferencia. Se comparan en minusculas y por su PRINCIPIO, porque el
#: formato shapefile corta los nombres a 10 caracteres: en el formulario de
#: mariposas del equipo, "Nombre cientifico" quedo como "Nombre cie".
CAMPOS_TITULO = ("nombre cie", "nombre com", "especie", "nombre", "title",
                 "name", "titulo")

#: De donde sale la nota u observacion.
CAMPOS_NOTA = ("notas", "nota", "observ", "obser", "note", "descripcion",
               "comentario", "habitat")

#: De donde sale la fecha.
CAMPOS_FECHA = ("fecha", "date", "timestamp", "hora")


def _buscar_campos(nombres: list, preferencias: tuple) -> list:
    """
    Ordena las columnas que encajan, de la mas preferida a la menos.

    POR QUE DEVUELVE VARIAS Y NO UNA
    --------------------------------
    Porque una ficha de campo reparte lo mismo en columnas distintas segun
    quien la rellene. Se midio en el formulario del equipo: 19 registros
    traen "Nombre cientifico" y 23 traen "Nombre comun", pero solo 26
    traen alguno de los dos. Quedandose con la primera columna, el
    validador avisaba de 35 registros "sin identificar" cuando en realidad
    eran 31, y culpaba a la ingeniera de un hueco que no existia.

    De las columnas que encajan se toma, para cada registro, el primer
    valor que no venga vacio.

    Args:
        nombres: Nombres de columna tal como vienen del .dbf.
        preferencias: Principios de nombre buscados, por orden.

    Returns:
        Lista de nombres de columna, en orden de preferencia.
    """
    bajos = [(n.lower().strip(), n) for n in nombres]
    elegidas = []
    for quiero in preferencias:
        for bajo, original in bajos:
            if bajo.startswith(quiero) and original not in elegidas:
                elegidas.append(original)
    return elegidas


def _primer_valor(atributos: dict, columnas: list) -> str:
    """
    Devuelve el primer valor con contenido de una lista de columnas.

    Args:
        atributos: Los valores del registro.
        columnas: Columnas candidatas, en orden de preferencia.

    Returns:
        El valor encontrado, o cadena vacia.
    """
    for columna in columnas:
        valor = (atributos.get(columna) or "").strip()
        if valor:
            return valor
    return ""


def _punto_de_shp(contenido: bytes) -> tuple:
    """
    Saca una coordenada del contenido de un registro de .shp.

    Igual que con el GeoPackage, HAY QUE MIRAR EL TIPO antes de leer los
    bytes: un recorrido es una linea y leerlo como punto no da error, da
    coordenadas basura. De una linea o un poligono se toma su primer
    vertice, que es donde empezo.

    Args:
        contenido: Los bytes del registro, sin su cabecera.

    Returns:
        Par `(lon, lat)`, o None si no se pudo decodificar.
    """
    try:
        tipo = struct.unpack("<i", contenido[:4])[0]
        if tipo == 0:                       # registro nulo
            return None
        if tipo in (1, 11, 21):             # Point, PointZ, PointM
            return struct.unpack("<dd", contenido[4:20]) + (False,)
        if tipo in (3, 5, 13, 15, 23, 25):  # lineas y poligonos
            # Tras la envolvente (32 bytes) vienen el numero de partes y de
            # puntos, los indices de parte, y por fin las coordenadas.
            partes, puntos = struct.unpack("<ii", contenido[36:44])
            if puntos < 1:
                return None
            inicio = 44 + partes * 4
            # El True dice que viene de un recorrido, no de un punto.
            return struct.unpack(
                "<dd", contenido[inicio:inicio + 16]) + (True,)
        if tipo in (8, 18, 28):             # Multipunto
            puntos = struct.unpack("<i", contenido[36:40])[0]
            if puntos < 1:
                return None
            return struct.unpack("<dd", contenido[40:56]) + (False,)
    except (struct.error, IndexError):
        return None
    return None


def _leer_shp(ruta: str) -> dict:
    """
    Lee un shapefile de campo: sus puntos y los atributos de cada uno.

    POR QUE HACE FALTA
    ------------------
    Porque es lo que exporta QField. El paquete real del equipo
    -- PROYECTO_MARIPOSAS_SAN_CARLOS.zip, 584 MB -- no trae ningun
    GeoPackage: trae un shapefile de 58 registros con 37 campos, un
    poligono de monitoreo, un raster y 229 fotografias. Antes de esto, el
    validador lo abria y respondia que no habia nada que revisar.

    UN SHAPEFILE SON CINCO ARCHIVOS
    -------------------------------
    El .shp guarda la geometria y el .dbf los atributos, emparejados por
    posicion: el registro 7 del .shp le corresponde al 7 del .dbf. El .prj
    dice el sistema de referencia y el .cpg la codificacion. Si falta
    alguno se lee lo que haya y se DICE lo que falta.

    Args:
        ruta: Ruta del .shp.

    Returns:
        Diccionario con `puntos`, `capas` y `avisos`.
    """
    from core import corpus

    base = os.path.splitext(ruta)[0]
    capa = os.path.basename(base)
    avisos = []

    # --- El sistema de referencia manda ----------------------------------
    # Un shapefile en metros no se puede comparar con un area de estudio en
    # grados. Decir que un punto "esta a 8.000 km" porque nadie miro el
    # .prj seria peor que no revisarlo.
    prj = base + ".prj"
    if os.path.exists(prj):
        try:
            with open(prj, encoding="latin-1") as fh:
                definicion = fh.read(400)
        except OSError:
            definicion = ""
        if not definicion.lstrip().upper().startswith("GEOGCS"):
            avisos.append(
                f"la capa '{capa}' no esta en coordenadas geograficas: su "
                f".prj declara un sistema proyectado, y sus coordenadas no "
                f"se pueden comparar con el area de estudio sin "
                f"reproyectarlas")
            return {"puntos": [],
                    "capas": [{"nombre": capa, "tipo": "features",
                               "epsg": 0, "registros": 0, "columnas": []}],
                    "avisos": avisos}
    else:
        avisos.append(f"la capa '{capa}' no trae .prj: se asume que sus "
                      f"coordenadas estan en grados, pero no se pudo "
                      f"comprobar")

    # --- Los atributos ----------------------------------------------------
    filas, nombres = [], []
    dbf = base + ".dbf"
    if os.path.exists(dbf):
        codificacion = corpus._codificacion_de(dbf)
        try:
            with open(dbf, "rb") as fh:
                registros, tam_cab, tam_reg, campos = corpus._campos_dbf(
                    fh, codificacion)
                nombres = [c[0] for c in campos]
                fh.seek(tam_cab)
                for _ in range(registros):
                    bruto = fh.read(tam_reg)
                    if len(bruto) < tam_reg:
                        break
                    posicion, valores = 1, {}
                    for nombre, _tipo, largo in campos:
                        valores[nombre] = bruto[
                            posicion:posicion + largo].decode(
                                codificacion, "replace").strip()
                        posicion += largo
                    filas.append(None if bruto[0:1] == b"*" else valores)
        except (OSError, struct.error) as exc:
            avisos.append(f"no se pudo leer la tabla de '{capa}': "
                          f"{str(exc)[:80]}")
    else:
        avisos.append(f"la capa '{capa}' no trae .dbf: sus puntos entran "
                      f"sin titulo ni nota")

    cols_titulo = _buscar_campos(nombres, CAMPOS_TITULO)
    cols_nota = _buscar_campos(nombres, CAMPOS_NOTA)
    cols_fecha = _buscar_campos(nombres, CAMPOS_FECHA)

    # --- La geometria, registro a registro --------------------------------
    puntos = []
    try:
        with open(ruta, "rb") as fh:
            cabecera = fh.read(100)
            if len(cabecera) < 100 or struct.unpack(
                    ">i", cabecera[:4])[0] != 9994:
                return {"puntos": [],
                        "capas": [{"nombre": capa, "tipo": "features",
                                   "epsg": 4326, "registros": 0,
                                   "columnas": nombres}],
                        "avisos": avisos + [f"'{capa}' no es un shapefile "
                                            f"valido"]}
            indice, sin_geometria = 0, 0
            while True:
                encabezado = fh.read(8)
                if len(encabezado) < 8:
                    break
                _numero, palabras = struct.unpack(">ii", encabezado)
                contenido = fh.read(palabras * 2)
                par = _punto_de_shp(contenido)
                atributos = (filas[indice]
                             if indice < len(filas) else None) or {}
                indice += 1
                if par is None:
                    # NO se descarta en silencio. Un registro sin geometria
                    # tiene atributos pero no coordenada: alguien lleno la
                    # ficha y el GPS no fijo posicion. Se midio en el
                    # paquete real del equipo: de sus 58 registros, uno
                    # esta asi. Si no se dice, el recuento baja de 58 a 57
                    # y nadie sabe por que.
                    sin_geometria += 1
                    continue
                puntos.append({
                    "capa": capa,
                    "lon": par[0],
                    "lat": par[1],
                    "recorrido": par[2],
                    "titulo": _primer_valor(atributos, cols_titulo),
                    "nota": _primer_valor(atributos, cols_nota),
                    "fecha": _primer_valor(atributos, cols_fecha),
                })
            if sin_geometria:
                avisos.append(
                    f"{sin_geometria} registro(s) de '{capa}' traen datos "
                    f"pero NO traen coordenada: se rellenó la ficha y el "
                    f"GPS no fijó posición. Entran {len(puntos)} de "
                    f"{indice}")
    except (OSError, struct.error) as exc:
        avisos.append(f"no se pudo leer la geometria de '{capa}': "
                      f"{str(exc)[:80]}")

    # La capa se describe igual que las de un GeoPackage: `revisar` las
    # recorre todas por igual y espera un diccionario, no un nombre suelto.
    return {"puntos": puntos,
            "capas": [{"nombre": capa, "tipo": "features", "epsg": 4326,
                       "registros": len(puntos), "columnas": nombres}],
            "avisos": avisos}


def leer(ruta: str) -> dict:
    """
    Lee un archivo de campo, sea cual sea de los tres formatos.

    Args:
        ruta: El archivo a leer. Un .zip se abre y se leen los
            GeoPackage, shapefiles, CSV y planillas que contenga.

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
                # Se extrae SOLO lo que se va a leer. Un .zip de fuera es
                # material no confiable y ademas enorme: el paquete real
                # del equipo pesa 584 MB, de los que 570 son fotografias y
                # videos. Lo que se revisa son 400 kB.
                #
                # Los hermanos del shapefile entran aunque no se lean por
                # si mismos: sin el .dbf la capa no tiene atributos y sin
                # el .prj no se sabe en que sistema estan sus coordenadas.
                acompanan = (".dbf", ".prj", ".cpg", ".shx")
                se_leen = (".gpkg", ".csv", ".xlsx", ".shp")
                dentro = [n for n in z.namelist()
                          if n.lower().endswith(se_leen + acompanan)
                          and not n.startswith(("/", "..")) and ".." not in n]
                nombres = [n for n in dentro if n.lower().endswith(se_leen)]
                if not nombres:
                    return {"puntos": [], "capas": [], "formato": "zip",
                            "avisos": ["el .zip no trae GeoPackage, "
                                       "shapefile, CSV ni planilla que se "
                                       "pueda revisar"]}

                # Se extrae todo a la MISMA carpeta y conservando el nombre,
                # para que cada .shp siga encontrando a sus hermanos.
                extraidos = {}
                for nombre in dentro:
                    destino = os.path.join(carpeta, os.path.basename(nombre))
                    with z.open(nombre) as origen,                             open(destino, "wb") as salida:
                        salida.write(origen.read())
                    extraidos[nombre] = destino

                # SE RECONOCE EL PAQUETE QUE GENERA EL PROPIO SISTEMA
                # ---------------------------------------------------
                # Esta pestana es para lo que VUELVE del campo. Si alguien
                # sube aqui el paquete de QGIS que entrega el agente, la
                # revision funciona -- son geometrias validas -- pero
                # compara el sistema consigo mismo, y eso no dice nada de
                # la calidad de ningun dato de campo. Conviene decirlo en
                # vez de dejar que parezca una validacion de verdad.
                internos = {n.rsplit("/", 1)[-1].lower() for n in z.namelist()}
                if {"proyecto.qgs", "capas.gpkg"} <= internos:
                    avisos.append(
                        "Esto es el paquete de QGIS que genera el propio "
                        "sistema, no datos que vengan de campo. Se revisa "
                        "igual, pero lo que sale es el sistema comparandose "
                        "consigo mismo: para validar una salida, sube el "
                        ".zip que exporta QField")

                for nombre in nombres:
                    leido = leer(extraidos[nombre])
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
    if extension == ".shp":
        return {**_leer_shp(ruta), "formato": "shp"}
    if extension in (".xlsx", ".xls", ".csv"):
        return {**_leer_tabla(ruta), "formato": extension.lstrip(".")}

    return {"puntos": [], "capas": [], "formato": extension.lstrip("."),
            "avisos": [f"no se sabe leer '{extension}'. Se admiten .zip, "
                       f".gpkg, .shp, .xlsx y .csv"]}


#: Carpeta donde el script de QGIS recoge las capas que anade el usuario.
#:
#: POR QUE AQUI Y NO EN `data/`
#: ----------------------------
#: `cargar_capas_qgis.py` carga solo tres archivos con nombre fijo, y es
#: zona intocable del proyecto. Pero ya trae una puerta abierta: todo lo
#: que haya en `capas_extra/` se carga sin que nadie toque su codigo.
#: Escribiendo ahi, los datos de campo aparecen en QGIS sin modificar el
#: script.
CARPETA_CAPAS_EXTRA = "capas_extra"


def carpeta_capas_extra() -> str:
    """
    Ruta de la carpeta de capas propias del usuario.

    Returns:
        Ruta absoluta. Se crea si no existe.
    """
    base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    carpeta = os.path.join(base, CARPETA_CAPAS_EXTRA)
    os.makedirs(carpeta, exist_ok=True)
    return carpeta


def _nombre_de_capa(origen: str) -> str:
    """
    Convierte el nombre del archivo de origen en nombre de capa.

    Args:
        origen: Nombre o ruta del paquete que se reviso.

    Returns:
        Un nombre de archivo sin acentos ni espacios, con su extension.
    """
    import unicodedata

    base = os.path.splitext(os.path.basename(origen or "campo"))[0]
    plano = unicodedata.normalize("NFKD", base)
    plano = "".join(c for c in plano if not unicodedata.combining(c))
    limpio = "".join(c if c.isalnum() else "_" for c in plano.lower())
    while "__" in limpio:
        limpio = limpio.replace("__", "_")
    return f"campo_{limpio.strip('_') or 'sin_nombre'}.geojson"


def exportar_capa(veredicto: dict, origen: str = "") -> dict:
    """
    Escribe los puntos revisados como una capa que QGIS puede abrir.

    POR QUE HACIA FALTA
    -------------------
    Estudiar y cartografiar eran dos caminos que no se cruzaban. El agente
    indexaba el paquete de campo y podia responder preguntas sobre el, pero
    el mapa seguia mostrando solo el area de estudio: las tres capas salian
    del KMZ y nadie las volvia a tocar. Se subia un Excel, se revisaba un
    .zip con 57 registros de mariposas, y el mapa salia identico. Visto
    desde fuera parecia que el sistema no habia hecho nada.

    QUE SE ESCRIBE Y QUE NO
    -----------------------
    Solo los puntos con coordenada valida. Los registros sin geometria -- en
    el paquete real del equipo habia uno -- no pueden dibujarse y no se
    inventan: ya quedan declarados en los avisos de la revision.

    Cada punto lleva si cayo DENTRO o FUERA del area de estudio, porque es
    lo primero que se mira en un mapa de validacion.

    Args:
        veredicto: Lo que devuelve `revisar()`.
        origen: Nombre del archivo revisado, para nombrar la capa.

    Returns:
        Diccionario con `ruta`, `nombre` y `puntos`, o con `error` si no
        habia nada que escribir.
    """
    import json

    puntos = [p for p in (veredicto.get("puntos") or [])
              if p.get("lon") is not None and p.get("lat") is not None]
    if not puntos:
        return {"error": "no hay ningun punto con coordenada que dibujar"}

    entidades = []
    for punto in puntos:
        entidades.append({
            "type": "Feature",
            "geometry": {"type": "Point",
                         "coordinates": [float(punto["lon"]),
                                         float(punto["lat"])]},
            "properties": {
                "titulo": punto.get("titulo") or "",
                "nota": punto.get("nota") or "",
                "fecha": punto.get("fecha") or "",
                "capa": punto.get("capa") or "",
                # `dentro` puede no venir si no habia area fijada. Se
                # distingue "fuera" de "no se pudo comprobar": decir que un
                # punto esta fuera cuando nadie lo midio seria inventar.
                "en_el_area": ("si" if punto.get("dentro") else
                               ("no" if "dentro" in punto else "sin comprobar")),
                "origen": os.path.basename(origen) if origen else "",
            },
        })

    nombre = _nombre_de_capa(origen)
    ruta = os.path.join(carpeta_capas_extra(), nombre)
    try:
        with open(ruta, "w", encoding="utf-8") as fh:
            json.dump({"type": "FeatureCollection", "features": entidades},
                      fh, ensure_ascii=False)
    except OSError as exc:
        return {"error": f"no se pudo escribir la capa: {str(exc)[:90]}"}

    return {"ruta": ruta, "nombre": nombre, "puntos": len(entidades)}


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
        # UN RECORRIDO QUE EMPIEZA FUERA NO ES UN ERROR
        # ---------------------------------------------
        # De una linea se toma su primer vertice, y una quebrada nace
        # donde nace: que arranque fuera del area y entre despues es lo
        # normal. Lo que si es sospechoso es una FICHA tomada fuera.
        #
        # Se vio al revisar el propio paquete del sistema: "Quebrada El
        # Jardin" arrancaba 2,4 km fuera del limite municipal y eso
        # bastaba para declarar todo el archivo no valido. 111 puntos
        # correctos invalidados por un rio que cruza una frontera.
        fuera_de_verdad = [d for d in veredicto["detalle"]
                           if not d["dentro"] and not d.get("recorrido")]
        recorridos_fuera = veredicto["fuera"] - len(fuera_de_verdad)

        if fuera_de_verdad:
            problemas.append(
                f"{len(fuera_de_verdad)} de {veredicto['total']} punto(s) "
                f"caen FUERA del área de estudio, hasta {lejos} km del "
                f"borde")
        if recorridos_fuera:
            avisos.append(
                f"{recorridos_fuera} recorrido(s) empiezan fuera del área y "
                f"entran después. Es normal en un cauce: no invalida nada, "
                f"pero conviene mirarlo si no esperabas líneas que crucen "
                f"el límite")

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
