"""
Lee una geodatabase de Esri (.gdb) sin instalar GDAL.

QUE ES UNA .gdb Y POR QUE EL PROYECTO LA SALTABA
------------------------------------------------
No es un archivo: es una CARPETA llena de binarios con nombres como
`a00000004.gdbtable`. Dentro estan las capas y las tablas de un proyecto de
ArcGIS, que es como entrega su cartografia media administracion publica
colombiana.

El sistema ya sabia abrir el .zip que la trae, pero dentro no reconocia ni
una extension, asi que el archivo entraba, se descomprimia, no daba texto y
se marcaba "pendiente". En el informe se leia como un fallo temporal cuando
en realidad era una carencia permanente: por mucho que se reintentara,
nunca iba a dar nada. El caso concreto fueron los 21 MB del Atlas Geologico
2020.

La via habitual seria GDAL. No esta en el contenedor y meterlo supone
cientos de megas de dependencias nativas para leer unos pocos archivos, asi
que se lee el formato directamente. Esta publicado y es estable.

COMO ESTA ORGANIZADA POR DENTRO
-------------------------------
    a00000001.gdbtable   el catalogo: que numero le toca a cada tabla
    a00000004.gdbtable   GDB_Items: los nombres de verdad de las capas
    aXXXXXXXX.gdbtable   una tabla: cabecera con los campos, luego filas
    aXXXXXXXX.gdbtablx   indice: en que byte empieza cada fila

LO QUE SE LEE Y LO QUE NO
-------------------------
Se leen los nombres de las tablas, sus campos con su tipo, cuantas filas
tienen y el contenido de esas filas. Eso es lo que hace falta para que la
geodatabase se pueda buscar y citar.

NO se decodifica la geometria comprimida de dentro. Un poligono en .gdb va
en enteros de longitud variable con deltas sobre un origen, que es otro
formato entero. Cuando una capa tiene geometria se DICE que la tiene, con
su tipo y su sistema de referencia, en vez de dejar creer que se dibujo.
"""

import os
import struct

#: Tipos de campo de una geodatabase, por su codigo.
TIPOS_CAMPO = {
    0: "entero corto", 1: "entero", 2: "decimal", 3: "decimal largo",
    4: "texto", 5: "fecha", 6: "identificador", 7: "geometria",
    8: "binario", 9: "raster", 10: "uuid", 11: "uuid", 12: "xml",
}

#: Campos que no ocupan sitio en la fila.
#:
#: El identificador de objeto no se guarda con los datos: ES la posicion de
#: la fila en el indice. Intentar leerlo de la fila desplaza todo lo demas.
NO_SE_GUARDAN = {6}

#: Cuantas filas se leen como mucho de una misma tabla.
MAXIMO_FILAS = 20000

#: Cuantas tablas se recorren como mucho.
MAXIMO_TABLAS = 200


def _varuint(datos: bytes, pos: int) -> tuple:
    """
    Lee un entero de longitud variable, base 128.

    Es como guarda la geodatabase las longitudes: siete bits utiles por
    byte y el octavo diciendo si sigue. Un texto de 20 caracteres gasta un
    byte de longitud en vez de cuatro.

    Args:
        datos: Bytes.
        pos: Desde donde leer.

    Returns:
        Tupla `(valor, nueva_posicion)`.
    """
    valor = 0
    desplazamiento = 0
    while pos < len(datos):
        byte = datos[pos]
        pos += 1
        valor |= (byte & 0x7F) << desplazamiento
        if not byte & 0x80:
            break
        desplazamiento += 7
    return valor, pos


def _texto_utf16(datos: bytes, pos: int) -> tuple:
    """
    Lee un nombre: un byte de longitud en caracteres y luego UTF-16.

    Args:
        datos: Bytes de la cabecera.
        pos: Desde donde leer.

    Returns:
        Tupla `(texto, nueva_posicion)`.
    """
    largo = datos[pos]
    pos += 1
    crudo = datos[pos:pos + largo * 2]
    return crudo.decode("utf-16-le", "replace"), pos + largo * 2


#: Los unicos caracteres que puede tener el nombre de una columna.
#:
#: La lista es corta a proposito. Una geodatabase colombiana nombra sus
#: columnas en castellano, y cualquier cosa fuera de este juego es senal de
#: que se empezo a leer el texto en el byte equivocado.
ADMITIDOS = ("abcdefghijklmnopqrstuvwxyz"
             "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
             "0123456789_- ."
             "ÁÉÍÓÚÜÑáéíóúüñ")


def _nombre_creible(nombre: str) -> bool:
    """
    Dice si un texto puede ser el nombre de una columna.

    Hace falta porque la busqueda a ciegas del apartado siguiente prueba
    byte a byte, y arrancando en medio de un texto UTF-16 salen cadenas que
    parsean sin protestar pero no significan nada: caracteres chinos,
    simbolos de control, pedazos del sistema de referencia. Sin este filtro,
    el primer sitio que "cuadra" suele ser basura.

    Args:
        nombre: Candidato a nombre de columna.

    Returns:
        True si parece un nombre de verdad.
    """
    if not nombre or len(nombre) > 64:
        return False
    # `isalnum()` da True para cualquier alfabeto del mundo, y lo que sale
    # de empezar a leer UTF-16 un byte tarde son precisamente ideogramas
    # perfectamente alfanumericos. El filtro tiene que decir QUE letras, no
    # "que sean letras".
    permitidos = set(ADMITIDOS)
    if nombre[0] not in permitidos or nombre[0] in "0123456789-. ":
        return False
    return all(c in permitidos for c in nombre)


def _variantes(datos: bytes, pos: int, fin: int):
    """
    Enumera las lecturas posibles de UN descriptor de campo.

    POR QUE HAY MAS DE UNA
    ----------------------
    Porque el formato no siempre dice cuanto ocupa el valor por defecto de
    un campo. La bandera tiene un bit que deberia anunciarlo y no se
    cumple en todos los archivos: en el Atlas Geologico, dos columnas de
    tipo decimal llevan detras un byte que su bandera no declara. Leerlo o
    no leerlo desplaza un byte, y un byte de desfase convierte el resto de
    la cabecera en basura.

    En vez de elegir a ciegas se devuelven las dos lecturas, y quien llama
    se queda con la que hace cuadrar la cabecera entera.

    Args:
        datos: Contenido del .gdbtable.
        pos: Donde empieza el descriptor.
        fin: Donde acaba la cabecera.

    Yields:
        Tuplas `(campo, posicion_siguiente)`.
    """
    if pos >= fin:
        return
    nombre, tras = _texto_utf16(datos, pos)
    if not _nombre_creible(nombre) or tras >= fin:
        return
    _alias, tras = _texto_utf16(datos, tras)
    if tras >= fin:
        return
    codigo = datos[tras]
    tras += 1

    if codigo in (7, 9):
        yield {"nombre": nombre, "codigo": codigo, "variable": True}, tras
        return

    if codigo in (4, 12):                      # texto y xml
        if tras + 5 > fin:
            return
        ancho = struct.unpack_from("<i", datos, tras)[0]
        bandera = datos[tras + 4]
        base = tras + 5
    else:
        if tras + 2 > fin:
            return
        ancho = datos[tras]
        bandera = datos[tras + 1]
        base = tras + 2

    campo = {"nombre": nombre,
             "tipo": TIPOS_CAMPO.get(codigo, f"tipo {codigo}"),
             "codigo": codigo, "nulable": bool(bandera & 1),
             "ancho": ancho, "variable": False}

    # Primero la lectura que dice la bandera, luego la otra. El orden
    # importa poco porque la prueba de abajo es exacta, pero probar antes
    # lo documentado deja los casos normales resueltos en el primer intento.
    if bandera & 4:
        largo, siguiente = _varuint(datos, base)
        if siguiente + largo <= fin:
            yield dict(campo), siguiente + largo
        yield dict(campo), base
    else:
        yield dict(campo), base
        largo, siguiente = _varuint(datos, base)
        if siguiente + largo <= fin:
            yield dict(campo), siguiente + largo


def _leer_campos(datos: bytes, pos: int, fin: int, cuantos: int,
                 fallidos: set = None):
    """
    Lee los campos de la cabecera probando lecturas hasta que cuadren.

    LA PRUEBA QUE DECIDE
    --------------------
    La cabecera declara CUANTOS campos hay y CUANTO mide. Solo se acepta
    una lectura que saque exactamente ese numero de campos y acabe en el
    ultimo byte declarado. Es una condicion que una lectura equivocada no
    cumple por casualidad, asi que no hace falta acertar a la primera: basta
    con poder comprobar.

    El campo de geometria es el unico que no se puede recorrer, porque
    detras lleva un bloque de origenes y escalas de tamano variable. Ahi se
    prueba byte a byte donde sigue la cabecera, con la misma condicion.

    Args:
        datos: Contenido del .gdbtable.
        pos: Donde empieza el primer campo que queda.
        fin: Donde acaba la cabecera.
        cuantos: Cuantos campos faltan.
        fallidos: Posiciones ya descartadas, para no repetir trabajo.

    Returns:
        Lista de campos, o None si no hay lectura que cuadre.
    """
    if fallidos is None:
        fallidos = set()
    if cuantos <= 0:
        # Algunos archivos dejan un byte de relleno al final. Uno se
        # perdona; dos harian que la prueba dejase de distinguir.
        return [] if fin - 1 <= pos <= fin else None
    if (pos, cuantos) in fallidos:
        return None

    for campo, siguiente in _variantes(datos, pos, fin):
        if campo.get("variable"):
            for arranque in range(siguiente, fin):
                resto = _leer_campos(datos, arranque, fin, cuantos - 1,
                                     fallidos)
                if resto is not None:
                    campo["inicio_cola"] = arranque
                    return [campo] + resto
            break
        resto = _leer_campos(datos, siguiente, fin, cuantos - 1, fallidos)
        if resto is not None:
            return [campo] + resto

    fallidos.add((pos, cuantos))
    return None


def _campos_de(datos: bytes) -> dict:
    """
    Lee la cabecera de un .gdbtable: que campos tiene y de que tipo.

    Args:
        datos: Contenido completo del .gdbtable.

    Returns:
        Diccionario con `campos`, `filas`, `desde` y `geometria`. `campos`
        es una lista de diccionarios con `nombre`, `tipo`, `codigo`,
        `nulable` y `ancho`.
    """
    if len(datos) < 40 or struct.unpack_from("<i", datos, 0)[0] != 3:
        return {"error": "no parece una tabla de geodatabase"}

    filas = struct.unpack_from("<i", datos, 4)[0]
    inicio = struct.unpack_from("<q", datos, 32)[0]
    if inicio <= 0 or inicio + 14 > len(datos):
        return {"error": "la cabecera de campos cae fuera del archivo"}

    largo_cabecera = struct.unpack_from("<i", datos, inicio)[0]
    fin_cabecera = inicio + 4 + largo_cabecera
    if fin_cabecera > len(datos):
        return {"error": "la cabecera declara mas tamano del que hay"}

    pos = inicio + 8                      # saltamos largo y version
    codigo_geometria = datos[pos]
    pos += 4
    cuantos = struct.unpack_from("<h", datos, pos)[0]
    pos += 2

    campos = _leer_campos(datos, pos, fin_cabecera, max(cuantos, 0))
    if campos is None:
        return {"error": f"la cabecera declara {cuantos} campos y no hay "
                         f"forma de leerlos que cuadre con su tamano"}

    geometria = {}
    for campo in campos:
        if campo.get("codigo") != 7:
            continue
        campo["tipo"] = "geometria"
        campo["ancho"] = 0
        campo.setdefault("nulable", True)
        # El sistema de referencia va en texto justo detras del tipo. Se
        # lee hasta donde la busqueda dijo que vuelve la cabecera.
        cola = campo.pop("inicio_cola", fin_cabecera)
        crudo = datos[:cola - (cola % 2)].decode("utf-16-le", "replace")
        corte = crudo.rfind("]]")
        texto = crudo[:corte + 2] if corte > 0 else ""
        # El PRIMER PROJCS, no el ultimo: dentro de el hay un GEOGCS y
        # buscando hacia atras se devolvia solo esa parte, que no lleva la
        # proyeccion. La capa salia entonces como si ya estuviera en grados
        # y sus coordenadas, que son metros, no caian en ningun sitio.
        inicio = texto.find("PROJCS[")
        if inicio < 0:
            inicio = texto.find("GEOGCS[")
        geometria = {"campo": campo["nombre"],
                     "wkt": texto[inicio:] if inicio > 0 else texto[-400:],
                     # Donde acaba el texto y donde vuelve la cabecera: con
                     # esos dos numeros se deduce el bloque de origenes y
                     # escalas que hay en medio, que es lo que hace falta
                     # para descomprimir las coordenadas.
                     "fin_wkt": (corte + 2) * 2 if corte > 0 else 0,
                     "cola": cola}
        geometria["srs"] = (geometria["wkt"].split('"')[1]
                            if '"' in geometria["wkt"] else "")
        campo.pop("variable", None)
    for campo in campos:
        campo.pop("variable", None)
        campo.pop("inicio_cola", None)
        if campo.get("codigo") == 9:
            campo["tipo"] = "raster"

    return {"campos": campos, "filas": filas, "desde": fin_cabecera,
            "geometria": geometria, "codigo_geometria": codigo_geometria,
            "declarados": cuantos}


def _posiciones(ruta_tablx: str) -> list:
    """
    Lee el indice que dice en que byte empieza cada fila.

    POR QUE NO SE RECORRE LA TABLA DE PRINCIPIO A FIN
    -------------------------------------------------
    Porque una fila borrada deja su hueco con la longitud en negativo, y
    sumando longitudes se acaba leyendo basura en cuanto hay un borrado. El
    indice dice exactamente donde empieza cada fila viva, y pone cero en las
    que ya no estan.

    Args:
        ruta_tablx: Ruta del .gdbtablx.

    Returns:
        Lista de posiciones. Vacia si no se pudo leer.
    """
    try:
        with open(ruta_tablx, "rb") as fh:
            cabecera = fh.read(16)
            if len(cabecera) < 16:
                return []
            bloques, _filas, ancho = struct.unpack_from("<iii", cabecera, 4)
            if ancho not in (4, 5, 6):
                return []
            crudo = fh.read(bloques * 1024 * ancho)
    except OSError:
        return []

    posiciones = []
    for i in range(0, len(crudo) - ancho + 1, ancho):
        posiciones.append(int.from_bytes(crudo[i:i + ancho], "little"))
    return posiciones


def _valor(codigo: int, datos: bytes, pos: int) -> tuple:
    """
    Lee el valor de un campo segun su tipo.

    Args:
        codigo: Codigo de tipo del campo.
        datos: Bytes de la fila.
        pos: Desde donde leer.

    Returns:
        Tupla `(valor, nueva_posicion)`.
    """
    if codigo == 0:
        return struct.unpack_from("<h", datos, pos)[0], pos + 2
    if codigo == 1:
        return struct.unpack_from("<i", datos, pos)[0], pos + 4
    if codigo == 2:
        return round(struct.unpack_from("<f", datos, pos)[0], 6), pos + 4
    if codigo == 3:
        return round(struct.unpack_from("<d", datos, pos)[0], 6), pos + 8
    if codigo == 5:
        import datetime
        dias = struct.unpack_from("<d", datos, pos)[0]
        base = datetime.datetime(1899, 12, 30)
        try:
            fecha = base + datetime.timedelta(days=dias)
            return fecha.strftime("%Y-%m-%d"), pos + 8
        except (OverflowError, ValueError):
            return "", pos + 8
    if codigo in (4, 12):
        largo, pos = _varuint(datos, pos)
        return datos[pos:pos + largo].decode("utf-8", "replace"), pos + largo
    if codigo in (10, 11):
        return datos[pos:pos + 16].hex(), pos + 16
    # Geometria y binario: se mide y se salta. Cuanto ocupa si se dice.
    largo, pos = _varuint(datos, pos)
    return f"<{largo} bytes>", pos + largo


def leer_tabla(ruta: str, maximo: int = MAXIMO_FILAS) -> dict:
    """
    Lee una tabla de la geodatabase con sus campos y sus filas.

    Args:
        ruta: Ruta del .gdbtable.
        maximo: Tope de filas a leer.

    Returns:
        Diccionario con `campos`, `filas` (lista de diccionarios),
        `total`, `geometria` y, si fallo, `error`.
    """
    try:
        with open(ruta, "rb") as fh:
            datos = fh.read()
    except OSError as exc:
        return {"error": f"no se pudo abrir: {str(exc)[:80]}"}

    cabecera = _campos_de(datos)
    if cabecera.get("error"):
        return cabecera

    campos = [c for c in cabecera["campos"]]
    nulables = [c for c in campos if c["nulable"]]
    bytes_nulos = (len(nulables) + 7) // 8

    posiciones = _posiciones(os.path.splitext(ruta)[0] + ".gdbtablx")
    if not posiciones:
        return {"campos": campos, "filas": [], "total": cabecera["filas"],
                "geometria": cabecera["geometria"],
                "error": "no se pudo leer el indice de filas"}

    filas = []
    for posicion in posiciones:
        if len(filas) >= maximo:
            break
        if posicion <= 0 or posicion + 4 > len(datos):
            continue
        largo = struct.unpack_from("<i", datos, posicion)[0]
        if largo <= 0 or posicion + 4 + largo > len(datos):
            continue
        cuerpo = datos[posicion + 4:posicion + 4 + largo]

        nulos = set()
        pos = 0
        if bytes_nulos:
            mapa = cuerpo[:bytes_nulos]
            pos = bytes_nulos
            for i, campo in enumerate(nulables):
                if i // 8 < len(mapa) and mapa[i // 8] & (1 << (i % 8)):
                    nulos.add(campo["nombre"])

        fila = {}
        try:
            for campo in campos:
                if campo["codigo"] in NO_SE_GUARDAN:
                    continue
                if campo["nombre"] in nulos:
                    continue
                valor, pos = _valor(campo["codigo"], cuerpo, pos)
                if valor not in ("", None):
                    fila[campo["nombre"]] = valor
        except (struct.error, IndexError, UnicodeDecodeError):
            # Una fila que no se deja leer no invalida la tabla. Se cuenta
            # como leida a medias y se sigue: perder una fila es mucho mejor
            # que perder el archivo.
            pass
        if fila:
            filas.append(fila)

    return {"campos": campos, "filas": filas, "total": cabecera["filas"],
            "geometria": cabecera["geometria"]}


def abrir(carpeta: str) -> dict:
    """
    Abre una geodatabase y devuelve que tablas tiene y como se llaman.

    LOS NOMBRES ESTAN EN DOS SITIOS
    -------------------------------
    El catalogo (`a00000001`) da el nombre fisico de cada tabla, que para
    una capa de usuario es un numero. Los nombres que una persona reconoce
    -- "Unidades_Geologicas" -- estan en `GDB_Items`, la tabla 4. Se leen
    los dos y se cruza, porque con el catalogo solo el informe diria
    "tabla 12" y eso no le sirve a nadie.

    Args:
        carpeta: Ruta de la carpeta .gdb.

    Returns:
        Diccionario con `tablas`, lista de diccionarios con `archivo`,
        `nombre` y `numero`. Con `error` si no es una geodatabase.
    """
    catalogo = os.path.join(carpeta, "a00000001.gdbtable")
    if not os.path.exists(catalogo):
        return {"error": "la carpeta no tiene catalogo de geodatabase"}

    sistema = leer_tabla(catalogo, maximo=MAXIMO_TABLAS * 4)
    if sistema.get("error") and not sistema.get("filas"):
        return {"error": sistema["error"]}

    tablas = []
    for numero, fila in enumerate(sistema.get("filas") or [], start=1):
        nombre = fila.get("Name") or ""
        if not nombre or nombre.startswith("GDB_"):
            continue
        archivo = os.path.join(carpeta, f"a{numero:08x}.gdbtable")
        if os.path.exists(archivo):
            tablas.append({"archivo": archivo, "nombre": nombre,
                           "numero": numero})

    return {"tablas": tablas[:MAXIMO_TABLAS],
            "internas": len(sistema.get("filas") or []) - len(tablas)}


# ==============================================================================
# LA GEOMETRIA
# ==============================================================================
#
# En una geodatabase un poligono no se guarda como una lista de coordenadas.
# Se guarda como enteros de longitud variable que cuentan la DIFERENCIA con
# el vertice anterior, sobre un origen y una escala que estan declarados en
# la cabecera de la capa. Es muy compacto -- el Atlas Geologico mete 18.061
# geometrias de Colombia en 21 MB -- y es por eso que ningun lector de texto
# iba a sacar nada de el.
#
# Las tres piezas que hacen falta son el origen, la escala y el sistema de
# referencia. Las tres estan en la cabecera, y con ellas la cuenta es
# directa: coordenada = origen + acumulado / escala, y luego la inversa de
# la proyeccion para llegar a grados.

#: Tipo de geometria de cada codigo de forma de una geodatabase.
#:
#: Son los mismos codigos del shapefile, con sus variantes Z y M. Para
#: dibujar en planta basta X e Y, asi que las variantes se tratan igual y lo
#: que va detras se ignora.
FORMAS_GDB = {
    1: "Point", 9: "Point", 11: "Point", 21: "Point", 52: "Point",
    8: "MultiPoint", 18: "MultiPoint", 20: "MultiPoint",
    28: "MultiPoint", 53: "MultiPoint",
    3: "LineString", 10: "LineString", 13: "LineString",
    23: "LineString", 50: "LineString",
    5: "Polygon", 15: "Polygon", 19: "Polygon", 25: "Polygon",
    51: "Polygon",
}


def _varint(datos: bytes, pos: int) -> tuple:
    """
    Lee un entero CON SIGNO de longitud variable.

    Se diferencia del sin signo en el primer byte: ahi solo hay seis bits
    utiles porque el septimo lleva el signo. Confundir los dos no da error,
    da coordenadas en otro continente.

    Args:
        datos: Bytes.
        pos: Desde donde leer.

    Returns:
        Tupla `(valor, nueva_posicion)`.
    """
    byte = datos[pos]
    pos += 1
    negativo = bool(byte & 0x40)
    valor = byte & 0x3F
    desplazamiento = 6
    while byte & 0x80:
        byte = datos[pos]
        pos += 1
        valor |= (byte & 0x7F) << desplazamiento
        desplazamiento += 7
    return (-valor if negativo else valor), pos


def _origen_y_escala(datos: bytes, desde: int, hasta: int) -> dict:
    """
    Saca el origen y la escala con que estan guardadas las coordenadas.

    COMO SE SABE CUANTOS NUMEROS HAY
    --------------------------------
    Detras del sistema de referencia van, en este orden: una bandera, el
    origen y la escala de X e Y, los de M y los de Z si la capa los tiene,
    las tolerancias, el rectangulo de la capa, y al final una rejilla
    espacial. Cuantos numeros son depende de si hay M y Z, y esos bits no se
    leen igual en todas las versiones del formato.

    No hace falta interpretarlos: el hueco entre el final del texto y el
    principio del campo siguiente es conocido, y solo una de las tres
    combinaciones posibles lo llena exactamente. Es la misma idea que con
    los campos -- comprobar en vez de adivinar.

    Args:
        datos: Contenido del .gdbtable.
        desde: Primer byte despues del texto del sistema de referencia.
        hasta: Donde empieza el campo siguiente.

    Returns:
        Diccionario con `xorigen`, `yorigen` y `escala`, o vacio.
    """
    hueco = hasta - desde
    for extra in (2, 1, 0):
        cuantos = 8 + 3 * extra
        sobra = hueco - (1 + 8 * cuantos + 1 + 4)
        if sobra < 0 or sobra % 8:
            continue
        try:
            rejillas = struct.unpack_from("<i", datos,
                                          desde + 1 + 8 * cuantos + 1)[0]
        except struct.error:
            continue
        if rejillas != sobra // 8:
            continue
        xorigen, yorigen, escala = struct.unpack_from("<ddd", datos, desde + 1)
        if escala > 0:
            return {"xorigen": xorigen, "yorigen": yorigen, "escala": escala}
    return {}


def _geometria_del_blob(blob: bytes, marco: dict):
    """
    Convierte el binario comprimido de una geometria en GeoJSON.

    Args:
        blob: Bytes de la geometria, tal como vienen en la fila.
        marco: Diccionario con `xorigen`, `yorigen` y `escala`.

    Returns:
        Geometria GeoJSON en coordenadas proyectadas, o None.
    """
    if not blob or not marco:
        return None
    xorigen = marco["xorigen"]
    yorigen = marco["yorigen"]
    escala = marco["escala"]

    try:
        codigo, pos = _varuint(blob, 0)
        tipo = FORMAS_GDB.get(codigo & 0xFF)
        if tipo is None:
            return None

        if tipo == "Point":
            x, pos = _varint(blob, pos)
            y, pos = _varint(blob, pos)
            return {"type": "Point",
                    "coordinates": [xorigen + x / escala,
                                    yorigen + y / escala]}

        cuantos, pos = _varuint(blob, pos)
        if not cuantos:
            return None

        if tipo == "MultiPoint":
            partes = [cuantos]
        else:
            n_partes, pos = _varuint(blob, pos)
            partes = []
            for _ in range(max(n_partes - 1, 0)):
                valor, pos = _varuint(blob, pos)
                partes.append(valor)
            partes.append(cuantos - sum(partes))

        # El rectangulo de la geometria va delante de los vertices. No se
        # usa -- se recalcula al dibujar -- pero hay que saltarlo entero o
        # el primer vertice sale del sitio equivocado.
        for _ in range(4):
            _valor, pos = _varuint(blob, pos)

        x = y = 0
        anillos = []
        for cuantos_en_parte in partes:
            puntos = []
            for _ in range(cuantos_en_parte):
                dx, pos = _varint(blob, pos)
                dy, pos = _varint(blob, pos)
                x += dx
                y += dy
                puntos.append((xorigen + x / escala, yorigen + y / escala))
            anillos.append(puntos)
    except (IndexError, struct.error):
        return None

    if tipo == "MultiPoint":
        return {"type": "MultiPoint",
                "coordinates": [list(p) for p in anillos[0]]}
    if tipo == "LineString":
        if len(anillos) == 1:
            return {"type": "LineString",
                    "coordinates": [list(p) for p in anillos[0]]}
        return {"type": "MultiLineString",
                "coordinates": [[list(p) for p in a] for a in anillos]}

    from core.geometria import agrupar_anillos
    return agrupar_anillos(anillos)


def leer_capa(ruta: str, recuadro: tuple = None,
              maximo: int = MAXIMO_FILAS) -> dict:
    """
    Lee una capa de la geodatabase como entidades de GeoJSON en grados.

    Args:
        ruta: Ruta del .gdbtable de la capa.
        recuadro: `(lonmin, latmin, lonmax, latmax)` en grados. Si se da,
            solo se quedan las entidades que lo tocan.
        maximo: Tope de entidades.

    Returns:
        Diccionario con `entidades`, `tipo`, `crs`, `situable`, `total`,
        `descartadas` y `aviso`. Con `error` si no se pudo leer.
    """
    from core import geometria as _geo

    try:
        with open(ruta, "rb") as fh:
            datos = fh.read()
    except OSError as exc:
        return {"error": f"no se pudo abrir: {str(exc)[:80]}"}

    cabecera = _campos_de(datos)
    if cabecera.get("error"):
        return cabecera
    info = cabecera.get("geometria") or {}
    if not info:
        return {"error": "esta tabla no tiene columna de geometria"}

    marco = _origen_y_escala(datos, info.get("fin_wkt", 0),
                             info.get("cola", 0))
    sistema = _geo.conversion_de_wkt(info.get("wkt") or "")
    convertir = sistema["convertir"]
    situable = bool(marco) and bool(sistema["grados"] or convertir)
    if not marco:
        return {"error": "no se pudo averiguar el origen y la escala con "
                         "que estan guardadas las coordenadas",
                "crs": sistema["nombre"], "total": cabecera["filas"]}

    campos = cabecera["campos"]
    nulables = [c for c in campos if c["nulable"]]
    bytes_nulos = (len(nulables) + 7) // 8
    posiciones = _posiciones(os.path.splitext(ruta)[0] + ".gdbtablx")

    entidades = []
    todas = []
    total = descartadas = 0
    for posicion in posiciones:
        if len(entidades) >= maximo:
            break
        if posicion <= 0 or posicion + 4 > len(datos):
            continue
        largo = struct.unpack_from("<i", datos, posicion)[0]
        if largo <= 0 or posicion + 4 + largo > len(datos):
            continue
        cuerpo = datos[posicion + 4:posicion + 4 + largo]
        total += 1

        nulos = set()
        pos = 0
        if bytes_nulos:
            mapa = cuerpo[:bytes_nulos]
            pos = bytes_nulos
            for i, campo in enumerate(nulables):
                if i // 8 < len(mapa) and mapa[i // 8] & (1 << (i % 8)):
                    nulos.add(campo["nombre"])

        propiedades = {}
        forma = None
        try:
            for campo in campos:
                if campo["codigo"] in NO_SE_GUARDAN:
                    continue
                if campo["nombre"] in nulos:
                    continue
                if campo["codigo"] == 7:
                    cuanto, pos = _varuint(cuerpo, pos)
                    forma = _geometria_del_blob(cuerpo[pos:pos + cuanto],
                                                marco)
                    pos += cuanto
                    continue
                valor, pos = _valor(campo["codigo"], cuerpo, pos)
                if valor not in ("", None) and str(valor).strip():
                    propiedades[campo["nombre"]] = valor
        except (struct.error, IndexError, UnicodeDecodeError):
            descartadas += 1
            continue

        if propiedades:
            todas.append(dict(propiedades))
        if forma is None:
            descartadas += 1
            continue

        if recuadro and situable:
            xmin, ymin, xmax, ymax = _geo._recuadro(forma)
            if convertir:
                xmin, ymin = convertir(xmin, ymin)
                xmax, ymax = convertir(xmax, ymax)
            if (xmax < recuadro[0] - _geo.MARGEN_GRADOS
                    or xmin > recuadro[2] + _geo.MARGEN_GRADOS
                    or ymax < recuadro[1] - _geo.MARGEN_GRADOS
                    or ymin > recuadro[3] + _geo.MARGEN_GRADOS):
                descartadas += 1
                continue

        if convertir:
            forma = _geo._mover(forma, convertir)
        entidades.append({"type": "Feature", "geometry": forma,
                          "properties": propiedades})

    return {"entidades": entidades,
            "tipo": (entidades[0]["geometry"]["type"] if entidades else ""),
            "crs": sistema["nombre"], "situable": situable,
            "total": cabecera["filas"], "descartadas": descartadas,
            "aviso": sistema["aviso"], "todas": todas}
