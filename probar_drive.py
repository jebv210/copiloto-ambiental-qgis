# -*- coding: utf-8 -*-
"""
Ensayo de la fuente de Google Drive, sin Google.
===============================================================================

PARA QUE SIRVE
--------------
Comprobar que TODO el camino de Drive funciona antes de que existan las
credenciales: recorrido de carpetas, categorias, filtro de formatos, descarga
incremental, imagenes, y el reparto por categoria que se ve en la interfaz.

Lo unico que no se comprueba aqui es la conexion con Google, que es justo lo
que depende de que compartan la carpeta. Cuando lleguen las credenciales, lo
que falle sera la conexion o los permisos; la logica ya estara probada.

COMO LO HACE
------------
Levanta un Drive de mentira: un objeto que responde a `files().list()` y
`files().get_media()` igual que la biblioteca de Google, leyendo de un arbol de
carpetas creado en un temporal. `FuenteDrive` no se entera y se ejecuta tal
cual, sin tocar ni una linea del codigo que ira a produccion.

POR QUE ESTA BATERIA EXISTE
---------------------------
Se encontro un fallo real que solo se veia recorriendo el camino entero: Drive
deja lo que baja en `corpus/_drive/<categoria>/`, y como la categoria es la
subcarpeta de PRIMER nivel, TODOS los documentos de Drive caian en una sola
categoria llamada "_drive". El reparto por categoria dejaba de informar de
nada. Aqui se vigila que no vuelva a pasar.

COMO SE USA
-----------
    python probar_drive.py

No necesita Docker, ni red, ni credenciales.

===============================================================================
"""

import hashlib
import io
import os
import shutil
import sys
import tempfile

AQUI = os.path.dirname(os.path.abspath(__file__))
if AQUI not in sys.path:
    sys.path.insert(0, AQUI)

ACIERTOS = 0
FALLOS = []


def comprobar(nombre, condicion, detalle=""):
    """
    Anota el resultado de una comprobacion.

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


# ==============================================================================
# 1. UN DRIVE DE MENTIRA
# ==============================================================================
# Imita solo lo que `FuenteDrive` usa de verdad. Si manana la fuente empieza a
# pedir otro campo, esta clase falla y avisa, que es lo que se quiere.

CARPETA = "application/vnd.google-apps.folder"

#: Un archivo nativo de Google. Se incluye a proposito porque es el UNICO
#: puente oficial entre Gemini Notebook y este proyecto: "Exportar a
#: Documentos" deja la nota como Documento de Google en Drive.
#:
#: Antes se descartaba, porque su nombre no lleva extension y el filtro por
#: `EXTENSIONES_LEIBLES` lo tiraba. Ahora se exporta a .docx al vuelo, y esta
#: prueba vigila que se pida por `export_media` y no por `get_media`, que es
#: lo que falla con los nativos.
DOCUMENTO_NATIVO = "application/vnd.google-apps.document"


class DriveFalso:
    """
    Responde como la biblioteca de Google, leyendo de un diccionario.

    El arbol se describe asi::

        {"id_carpeta": [{"id":..., "name":..., "mimeType":..., "datos": b"..."}]}
    """

    def __init__(self, arbol):
        """
        Args:
            arbol: Diccionario {id_de_carpeta: lista_de_entradas}.
        """
        self.arbol = arbol
        self.listados = 0
        self.descargas = []
        self.exportaciones = []

    def files(self):
        """Devuelve el propio objeto, que hace de coleccion de archivos."""
        return self

    def list(self, q="", fields="", pageSize=200, pageToken=None, **extra):
        """
        Imita `files().list()`. El identificador sale de la consulta `q`.

        Args:
            q: Consulta de Drive, con la forma "'<id>' in parents and ...".
            fields: Se ignora; esta para que la firma coincida.
            pageSize: Se ignora.
            pageToken: Se ignora; este Drive no pagina.
            **extra: `supportsAllDrives` y demas.

        Returns:
            El propio objeto, con el resultado preparado.
        """
        self.listados += 1
        carpeta = q.split("'")[1] if "'" in q else ""
        entradas = self.arbol.get(carpeta, [])
        # Se incluye `size` como hace Google: es lo que permite descartar un
        # archivo enorme SIN bajarlo.
        self._resultado = {"files": []}
        for e in entradas:
            fila = {k: v for k, v in e.items() if k != "datos"}
            if "size" not in fila and "datos" in e:
                fila["size"] = str(len(e["datos"]))
            self._resultado["files"].append(fila)
        return self

    def get_media(self, fileId=None, **extra):
        """
        Imita `files().get_media()`.

        Args:
            fileId: Identificador del archivo.
            **extra: `supportsAllDrives` y demas.

        Returns:
            El propio objeto, recordando que archivo se pidio.
        """
        self._pedido = fileId
        return self

    def export_media(self, fileId=None, mimeType=None, **extra):
        """
        Imita `files().export_media()`, que es como se bajan los nativos.

        Un Documento de Google no tiene bytes propios, asi que `get_media`
        falla con el y hay que pedirle a Google que lo convierta. Se anota el
        formato pedido para poder comprobar que se eligio el correcto.

        Args:
            fileId: Identificador del archivo.
            mimeType: Formato al que se pide la conversion.
            **extra: Lo que venga.

        Returns:
            El propio objeto, recordando que se pidio y en que formato.
        """
        self._pedido = fileId
        self.exportaciones.append((fileId, mimeType))
        return self

    def execute(self):
        """
        Devuelve el resultado del `list()` anterior.

        Returns:
            Diccionario con la clave `files`.
        """
        return self._resultado

    def contenido(self, archivo_id):
        """
        Da los bytes de un archivo del arbol.

        Args:
            archivo_id: Identificador buscado.

        Returns:
            Los bytes del archivo, o b"" si no esta.
        """
        for entradas in self.arbol.values():
            for e in entradas:
                if e["id"] == archivo_id:
                    return e.get("datos", b"")
        return b""


def construir_arbol():
    """
    Arma un Drive de ejemplo con las situaciones que importan.

    Returns:
        Tupla `(arbol, cuantos_legibles)`.
    """
    def md5(datos):
        """Huella del contenido, como la que da Drive."""
        return hashlib.md5(datos).hexdigest()

    pdf = b"%PDF-1.4 contenido de prueba"
    jpg = b"\xff\xd8\xff\xe0" + b"imagen de prueba" * 4
    xls = b"PK\x03\x04" + b"hoja" * 8

    # El .docx SI se construye de verdad, no con bytes inventados: es lo que
    # sale de exportar un Documento de Google, y si el lector no lo pudiera
    # abrir el puente con Notebook no serviria de nada. `_leer_docx` busca
    # `word/document.xml` dentro del ZIP, asi que hay que ponerlo.
    import io as _io
    import zipfile as _zip

    _buf = _io.BytesIO()
    with _zip.ZipFile(_buf, "w") as _z:
        _z.writestr("word/document.xml",
                    '<?xml version="1.0"?>'
                    '<w:document xmlns:w="http://schemas.openxmlformats.org/'
                    'wordprocessingml/2006/main"><w:body><w:p><w:r><w:t>'
                    'Nota exportada desde Gemini Notebook sobre la cuenca de '
                    'San Carlos.</w:t></w:r></w:p></w:body></w:document>')
    docx = _buf.getvalue()

    arbol = {
        "RAIZ": [
            {"id": "c1", "name": "Cartografia", "mimeType": CARPETA},
            {"id": "c2", "name": "Fichas de campo", "mimeType": CARPETA},
            {"id": "suelto", "name": "suelto.txt", "mimeType": "text/plain",
             "md5Checksum": md5(b"en la raiz"), "datos": b"en la raiz"},
        ],
        "c1": [
            {"id": "a1", "name": "mapa.pdf", "mimeType": "application/pdf",
             "md5Checksum": md5(pdf), "datos": pdf},
            {"id": "a2", "name": "tablas.xlsx", "mimeType": "application/xlsx",
             "md5Checksum": md5(xls), "datos": xls},
            # Formato que el sistema NO sabe leer: debe quedar declarado.
            #
            # Antes aqui habia un "proyecto.qgz", y dejo de servir cuando se
            # anadio el lector de proyectos de QGIS: paso a ser legible y la
            # prueba quedo comprobando lo contrario de lo que decia. Un .dwg
            # de AutoCAD sigue sin tener lector, que es lo que hace falta.
            {"id": "a3", "name": "plano_red_hidrica.dwg",
             "mimeType": "application/dwg",
             "md5Checksum": md5(b"dwg"), "datos": b"dwg"},
            # Nativo de Google: sin extension y sin `size`, como los
            # devuelve Drive de verdad. Sus "datos" son los del .docx al que
            # se exporta.
            {"id": "a4", "name": "Nota de Notebook",
             "mimeType": DOCUMENTO_NATIVO, "md5Checksum": "",
             "modifiedTime": "2026-11-01T10:00:00Z", "datos": docx},
            # Un CSV enorme, del tamano de los que llegaron de verdad. NO se
            # declara `datos`: la gracia es que ni siquiera se intente bajar.
            {"id": "a5", "name": "capa_nacional_enorme.csv",
             "mimeType": "text/csv", "md5Checksum": "x",
             "size": str(450 * 1024 * 1024)},
        ],
        "c2": [
            {"id": "b1", "name": "ficha_014.jpg", "mimeType": "image/jpeg",
             "md5Checksum": md5(jpg), "datos": jpg},
            # Subcarpeta de SEGUNDO nivel: hereda la categoria del primero.
            {"id": "c2a", "name": "Escaneos", "mimeType": CARPETA},
        ],
        "c2a": [
            {"id": "b2", "name": "ficha_015.png", "mimeType": "image/png",
             "md5Checksum": md5(jpg), "datos": jpg},
        ],
    }
    # Legibles: suelto.txt, mapa.pdf, tablas.xlsx, ficha_014.jpg,
    # ficha_015.png y la nota de Notebook, que ahora se exporta a .docx.
    return arbol, 6


# ==============================================================================
# 2. LAS COMPROBACIONES
# ==============================================================================

def main():
    """
    Ejecuta el ensayo completo.

    Returns:
        0 si todo paso, 1 si algo fallo.
    """
    print("=" * 70)
    print("  Fuente de Google Drive: ensayo sin Google")
    print("=" * 70)

    from core import corpus, fuentes

    trabajo = tempfile.mkdtemp(prefix="probar_drive_")
    corpus_falso = os.path.join(trabajo, "corpus")
    os.makedirs(corpus_falso)

    arbol, legibles = construir_arbol()
    drive = DriveFalso(arbol)

    # Se sustituyen solo las dos puertas que hablan con Google. Todo lo demas
    # de FuenteDrive se ejecuta de verdad.
    fuente = fuentes.FuenteDrive(carpeta_id="RAIZ", credenciales="(falsas)")
    fuente._servicio = lambda: drive

    def descargar_falso(servicio, archivo_id, ruta, exportar_como=""):
        """
        Escribe el contenido del arbol en vez de bajarlo de la red.

        Acepta `exportar_como` a proposito: si manana la fuente deja de
        pasarlo, esta firma deja de encajar y la prueba avisa en vez de que
        los nativos se bajen por el camino equivocado.
        """
        drive.descargas.append(archivo_id)
        if exportar_como:
            drive.exportaciones.append((archivo_id, exportar_como))
        with open(ruta, "wb") as f:
            f.write(drive.contenido(archivo_id))

    fuente._descargar = staticmethod(descargar_falso)
    fuente._descargar = descargar_falso

    # Drive ya NO baja dentro de `corpus/`: lo hace en una carpeta temporal
    # que se borra al terminar, para no dejar copias en el equipo. La prueba
    # apunta esa carpeta al temporal de este ensayo.
    carpeta_original = corpus.carpeta_corpus
    corpus.carpeta_corpus = lambda: corpus_falso
    fuente.carpeta_temporal = lambda: os.path.join(corpus_falso, "_drive")

    try:
        # --- Primera pasada -------------------------------------------------
        print("\n--- Primera sincronizacion ---")
        documentos = fuente.documentos()

        comprobar("baja solo los formatos que sabe leer",
                  len(documentos) == legibles,
                  f"{len(documentos)} de {len(arbol['c1']) + len(arbol['c2']) + 1} "
                  f"candidatos ({legibles} legibles)")

        nombres = {d["nombre"] for d in documentos}
        comprobar("descarta el formato desconocido",
                  "plano_red_hidrica.dwg" not in nombres,
                  "el .dwg no entra como bibliografia")
        # EL PUENTE CON GEMINI NOTEBOOK
        # -----------------------------
        # Notebook no tiene API publica, pero su boton "Exportar a
        # Documentos" deja la nota como Documento de Google en Drive. Si el
        # proyecto no sabe leer los nativos, ese puente no existe: la nota se
        # ve en la carpeta y el sistema la salta.
        comprobar("un nativo de Google entra, con la extension puesta",
                  "Nota de Notebook.docx" in nombres,
                  "el Documento se nombra .docx y pasa el filtro")
        comprobar("y se pide por export_media, no por get_media",
                  any(ident == "a4" for ident, _ in drive.exportaciones),
                  f"exportado como "
                  f"{[m.split('.')[-1] for i, m in drive.exportaciones if i == 'a4']}"
                  if drive.exportaciones else "no se pidio exportacion")

        # Lo que NO se sabe leer tampoco desaparece: antes era un `continue`
        # pelado y el archivo se esfumaba sin dejar constancia, asi que el
        # agente no podia ni decir que existia.
        _motivos = {d["nombre"]: d["motivo"] for d in fuente.descartados}
        comprobar("un formato sin lector queda declarado, no se esfuma",
                  "plano_red_hidrica.dwg" in _motivos
                  and "dwg" in _motivos["plano_red_hidrica.dwg"],
                  _motivos.get("plano_red_hidrica.dwg",
                               "no quedo constancia"))

        # --- El tope de tamano, ANTES de descargar ---------------------------
        # El limite existia, pero solo en el inventario de la carpeta local:
        # la fuente de Drive no lo miraba. Una carpeta con cuatro CSV de
        # 450 MB se bajaba entera y se troceaba entera, y la indexacion se
        # quedaba atascada horas en un solo archivo.
        print("\n--- Archivos demasiado grandes ---")
        comprobar("un archivo enorme NO entra al indice",
                  "capa_nacional_enorme.csv" not in nombres,
                  "450 MB quedan fuera")
        # El diseno cambio a proposito. Antes se descartaba por el tamano que
        # declaraba Google, sin tocar el archivo; el problema es que el tamano
        # no predice el coste, y el aviso quedaba en "pesa mucho", que no
        # ayuda a decidir nada. Ahora se baja una MUESTRA de 4 MB para poder
        # decir cuantas fichas costaria. Lo que sigue sin ocurrir, que es lo
        # que importa, es bajarse los 450 MB enteros.
        _bajado = os.path.getsize(os.path.join(
            fuente.carpeta_temporal(), "capa_nacional_enorme.csv")) \
            if os.path.exists(os.path.join(fuente.carpeta_temporal(),
                                           "capa_nacional_enorme.csv")) else 0
        comprobar("no se baja el archivo entero, como mucho una muestra",
                  _bajado == 0,
                  "no queda ni la muestra en la carpeta de trabajo")
        _grandes = [d for d in getattr(fuente, "descartados", [])
                    if "enorme" in d["nombre"]]
        comprobar("se dice cual se descarto y por que",
                  bool(_grandes),
                  f"{_grandes[0]['nombre']} ({_grandes[0]['mb']} MB): "
                  f"{_grandes[0]['motivo']}" if _grandes
                  else "no quedo constancia")
        comprobar("el motivo habla de lo que cuesta, no solo de lo que pesa",
                  bool(_grandes) and ("fichas" in _grandes[0]["motivo"]
                                      or "descarga" in _grandes[0]["motivo"]),
                  _grandes[0]["motivo"] if _grandes else "sin motivo")

        # --- Categorias -----------------------------------------------------
        print("\n--- Categorias ---")
        por_nombre = {d["nombre"]: d["categoria"] for d in documentos}
        comprobar("la carpeta de primer nivel ES la categoria",
                  por_nombre.get("mapa.pdf") == "Cartografia",
                  f"mapa.pdf -> {por_nombre.get('mapa.pdf')}")
        comprobar("la subcarpeta hereda la categoria del primer nivel",
                  por_nombre.get("ficha_015.png") == "Fichas de campo",
                  f"ficha_015.png (en Escaneos) -> "
                  f"{por_nombre.get('ficha_015.png')}")
        # La etiqueta dice DONDE esta, no que falte algo. "Sin categoria" se
        # leia como un fallo de lectura: con los ocho archivos sueltos en la
        # raiz, la interfaz parecia estar diciendo que no supo clasificarlos.
        comprobar("un archivo en la raiz se etiqueta por donde esta",
                  por_nombre.get("suelto.txt") == fuentes.RAIZ,
                  f"suelto.txt -> {por_nombre.get('suelto.txt')}")

        # --- Imagenes -------------------------------------------------------
        print("\n--- Imagenes ---")
        imagenes = [d for d in documentos
                    if os.path.splitext(d["nombre"])[1].lower()
                    in corpus.TIPOS_IMAGEN]
        comprobar("baja las imagenes como un documento mas",
                  len(imagenes) == 2, f"{len(imagenes)} imagenes")
        comprobar("el corpus tiene un lector para esas imagenes",
                  all(corpus.LECTORES.get(
                      os.path.splitext(d["nombre"])[1].lower())
                      is corpus._leer_imagen for d in imagenes),
                  "se transcriben con el modelo, no se descartan")

        # --- El reparto que se ve en la interfaz ----------------------------
        # Esta es la comprobacion por la que existe la bateria: antes TODO
        # caia en una categoria llamada "_drive".
        print("\n--- Reparto por categoria (lo que ve el usuario) ---")
        reparto = dict(corpus.categorias(corpus.inventariar(corpus_falso)))
        comprobar("el reparto usa las carpetas de Drive",
                  "_drive" not in reparto,
                  f"categorias: {sorted(reparto)}")
        # Tres: mapa.pdf, tablas.xlsx y la nota de Notebook exportada a
        # .docx. El .qgz no, que no hay lector.
        comprobar("Cartografia aparece con sus documentos",
                  reparto.get("Cartografia") == 3,
                  f"{reparto.get('Cartografia')} documentos")
        comprobar("Fichas de campo incluye lo de la subcarpeta",
                  reparto.get("Fichas de campo") == 2,
                  f"{reparto.get('Fichas de campo')} documentos")

        # --- Segunda pasada: incremental ------------------------------------
        print("\n--- Segunda sincronizacion: no repetir trabajo ---")
        bajadas_primera = len(drive.descargas)
        drive.descargas.clear()
        fuente.documentos()
        # LOS NATIVOS SON LA EXCEPCION, Y ES A PROPOSITO
        # ----------------------------------------------
        # `_ya_esta` compara el MD5 del archivo local con el `md5Checksum`
        # que da Drive. Un nativo de Google NO tiene md5Checksum -- no es un
        # archivo, es una fila en una base de datos suya --, asi que no hay
        # con que comparar y se vuelve a exportar.
        #
        # Se deja asi a proposito: entre volver a convertir un Documento, que
        # pesa kilobytes, y arriesgarse a servir una version vieja, se
        # prefiere lo primero. En produccion da igual, porque lo descargado
        # se borra despues de cada sincronizacion y nada se conserva en el
        # equipo.
        comprobar("no vuelve a bajar lo que no cambio",
                  drive.descargas == ["a4"],
                  f"solo se reexporta el nativo (la primera vez fueron "
                  f"{bajadas_primera} descargas)")

        # --- Un documento cambia en Drive -----------------------------------
        print("\n--- Un documento cambia en Drive ---")
        nuevo = b"%PDF-1.4 contenido CORREGIDO"
        for e in arbol["c1"]:
            if e["id"] == "a1":
                e["datos"] = nuevo
                e["md5Checksum"] = hashlib.md5(nuevo).hexdigest()
        drive.descargas.clear()
        fuente.documentos()
        comprobar("baja solo el que cambio",
                  drive.descargas == ["a1", "a4"],
                  f"el .pdf corregido y el nativo, que no se puede verificar: "
                  f"{drive.descargas}")
        with open(os.path.join(corpus_falso, "_drive", "Cartografia",
                               "mapa.pdf"), "rb") as f:
            comprobar("el archivo local queda actualizado",
                      f.read() == nuevo, "contenido nuevo en disco")

        # --- NO SE CONSERVA NADA EN EL EQUIPO -------------------------------
        # Es la regla que pidio quien dirige el proyecto: el agente estudia de
        # Drive, y lo que baja para leerlo se borra. Lo aprendido vive en el
        # indice vectorial, que son numeros, no copias de los documentos.
        print("\n--- Lo descargado se borra ---")
        _temporal = fuente.carpeta_temporal()
        comprobar("la descarga NO va dentro de la carpeta del corpus",
                  "_drive" in _temporal,
                  "va a una carpeta aparte, de paso")

        _antes = sum(len(f) for _, _, f in os.walk(_temporal))
        _limpieza = fuente.limpiar()
        _despues = sum(len(f) for _, _, f in os.walk(_temporal))
        comprobar("limpiar() borra todo lo bajado",
                  _despues == 0 and _antes > 0,
                  f"{_antes} archivos antes, {_despues} despues")
        comprobar("y dice cuanto libero",
                  _limpieza["archivos"] == _antes,
                  f"{_limpieza['archivos']} archivos, {_limpieza['megas']} MB")

        # --- Diagnostico cuando falta configuracion -------------------------
        print("\n--- Diagnostico ---")
        vacia = fuentes.FuenteDrive(carpeta_id="", credenciales="")
        listo, motivo = vacia.disponible()
        # Se comprueba el COMPORTAMIENTO, no la redaccion. Antes esta
        # linea exigia que el motivo dijera "DRIVE_CARPETA_ID", y eso ataba
        # la prueba a un nombre de variable de entorno que no deberia
        # aparecer en lo que lee una persona. Lo que importa es que no
        # reviente, que diga que no esta listo, que explique algo, y que
        # mande a la pestana en vez de a un archivo de configuracion.
        comprobar("sin configurar, explica que falta y no revienta",
                  listo is False and len(motivo) > 25
                  and "pestana" in motivo.lower()
                  and ".env" not in motivo,
                  motivo[:66])

    finally:
        corpus.carpeta_corpus = carpeta_original
        shutil.rmtree(trabajo, ignore_errors=True)

    print("\n" + "=" * 70)
    total = ACIERTOS + len(FALLOS)
    if FALLOS:
        print(f"{ACIERTOS} de {total} superadas, {len(FALLOS)} FALLIDAS:\n")
        for f in FALLOS:
            print("  -", f)
        return 1
    print(f"{ACIERTOS} de {total} comprobaciones superadas")
    return 0



# ==============================================================================
# DESCONECTAR NO ES VOLVER A LA CARPETA LOCAL
# ==============================================================================
print("\n--- Desconectar Drive ---")

import json as _json_des

from core import ajustes as _ajustes_des
from core import conexion_drive as _conexion

_archivo_ajustes = _ajustes_des.ruta()
_respaldo = None
if os.path.exists(_archivo_ajustes):
    with open(_archivo_ajustes, encoding="utf-8") as _fh:
        _respaldo = _fh.read()
try:
    _conexion.desconectar()
    with open(_archivo_ajustes, encoding="utf-8") as _fh:
        _tras = _json_des.load(_fh)
    # Dejar la fuente en "local" era el camino por el que el sistema
    # acababa estudiando lo que hubiera en el disco sin que nadie lo
    # pidiera. Al desconectar, la fuente sigue siendo Drive pero sin
    # carpeta: el sistema se niega a sincronizar y lo dice.
    comprobar("desconectar NO reactiva la carpeta local",
              _tras.get("FUENTE_CORPUS") == "drive",
              f"la fuente queda en '{_tras.get('FUENTE_CORPUS')}', sin "
              f"carpeta configurada")
    comprobar("y olvida la carpeta y la credencial",
              _tras.get("DRIVE_CARPETA_ID") is None
              and _tras.get("GOOGLE_CREDENCIALES") is None,
              "no queda rastro de la conexion anterior")
finally:
    if _respaldo is not None:
        with open(_archivo_ajustes, "w", encoding="utf-8") as _fh:
            _fh.write(_respaldo)



# ==============================================================================
# EL RESUMEN GUARDADO CADUCA CUANDO CAMBIA EL RESUMIDOR
# ==============================================================================
#
# El resumen de una capa de 418 MB se guarda en disco para no volver a
# bajarla cada noche. Pero el archivo guardado no decia de que version era,
# asi que sobrevivia a cualquier arreglo: las cuatro capas del proyecto
# seguian sirviendo un texto del 25 de octubre, sin tres correcciones
# posteriores. Un cache sin version es un cache que miente despacio.
print("\n--- Caducidad del resumen guardado ---")

import json as _json_ver

from core import catalogo as _cat_ver
from core import fuentes as _fu_ver

_carpeta_ver = tempfile.mkdtemp(prefix="probar_version_")
_fuente_ver = _fu_ver.FuenteDrive(carpeta_id="x", credenciales="y")
_fuente_ver._carpeta_resumenes = staticmethod(lambda: _carpeta_ver)
try:
    _nombre_ver = "capa_vieja.csv"
    _viejo = {"lineas": ["[Resumen de capa_vieja.csv]", "texto de antes"],
              "filas": 1, "columnas_resumidas": [], "segundos": 0.1,
              "error": ""}          # sin la clave "version"
    with open(os.path.join(_carpeta_ver, _nombre_ver + ".json"), "w",
              encoding="utf-8") as _fh:
        _json_ver.dump(_viejo, _fh)

    _bajadas = {"n": 0}

    def _bajar_ver(_servicio, _ident, ruta_destino, exportar_como=""):
        """Escribe una tabla pequena en vez de bajar 418 MB."""
        _bajadas["n"] += 1
        with open(ruta_destino, "w", encoding="utf-8") as _fh:
            _fh.write("vereda,cobertura,area_ha\n")
            for _i in range(40):
                _fh.write(f"El Jordan,Bosque,{1.5 + _i}\n")

    _fuente_ver._descargar = _bajar_ver
    _rehecho = _fuente_ver._resumir_capa(None, "id", _nombre_ver,
                                         _carpeta_ver)
    comprobar("un resumen sin version se descarta y se rehace",
              _bajadas["n"] == 1
              and _rehecho.get("version") == _cat_ver.VERSION_RESUMEN,
              f"se volvio a calcular, ahora en version "
              f"{_rehecho.get('version')}")

    comprobar("y el texto nuevo reemplaza al viejo",
              not any("texto de antes" in l for l in _rehecho["lineas"])
              and any("COMPLETA" in l for l in _rehecho["lineas"]),
              "el resumen guardado ya no es el del 25 de octubre")

    # Y con la version al dia NO se vuelve a bajar: el cache tiene que
    # seguir sirviendo para lo que existe, que es ahorrar 418 MB por noche.
    _antes_ver = _bajadas["n"]
    _otra_vez = _fuente_ver._resumir_capa(None, "id", _nombre_ver,
                                          _carpeta_ver)
    comprobar("pero un resumen al dia se reutiliza, sin volver a bajar nada",
              _bajadas["n"] == _antes_ver
              and _otra_vez["filas"] == _rehecho["filas"],
              "el cache sigue ahorrando la descarga")
finally:
    shutil.rmtree(_carpeta_ver, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
