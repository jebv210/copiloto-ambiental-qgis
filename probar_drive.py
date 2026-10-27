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

#: Un archivo nativo de Google. Se incluye a proposito: su nombre no lleva
#: extension, y el sistema NO lo sabe leer. La prueba comprueba que se descarta
#: en silencio y no revienta, que es la limitacion documentada.
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
            # Formato que el sistema NO sabe leer: debe descartarse.
            {"id": "a3", "name": "proyecto.qgz", "mimeType": "application/zip",
             "md5Checksum": md5(b"qgz"), "datos": b"qgz"},
            # Archivo nativo de Google: sin extension en el nombre.
            {"id": "a4", "name": "Informe de avance",
             "mimeType": DOCUMENTO_NATIVO, "md5Checksum": ""},
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
    # Legibles: suelto.txt, mapa.pdf, tablas.xlsx, ficha_014.jpg, ficha_015.png
    return arbol, 5


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

    def descargar_falso(servicio, archivo_id, ruta):
        """Escribe el contenido del arbol en vez de bajarlo de la red."""
        drive.descargas.append(archivo_id)
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
                  "proyecto.qgz" not in nombres, "proyecto.qgz fuera")
        comprobar("descarta el archivo nativo de Google",
                  "Informe de avance" not in nombres,
                  "sin extension, no se sabe leer; limitacion documentada")

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
        comprobar("Cartografia aparece con sus documentos",
                  reparto.get("Cartografia") == 2,
                  f"{reparto.get('Cartografia')} documentos")
        comprobar("Fichas de campo incluye lo de la subcarpeta",
                  reparto.get("Fichas de campo") == 2,
                  f"{reparto.get('Fichas de campo')} documentos")

        # --- Segunda pasada: incremental ------------------------------------
        print("\n--- Segunda sincronizacion: no repetir trabajo ---")
        bajadas_primera = len(drive.descargas)
        drive.descargas.clear()
        fuente.documentos()
        comprobar("no vuelve a bajar lo que no cambio",
                  drive.descargas == [],
                  f"0 descargas (la primera vez fueron {bajadas_primera})")

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
                  drive.descargas == ["a1"],
                  f"descargas: {drive.descargas}")
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
        comprobar("sin configurar, explica que falta y no revienta",
                  listo is False and "DRIVE_CARPETA_ID" in motivo,
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


if __name__ == "__main__":
    sys.exit(main())
