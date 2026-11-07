"""
Bateria de Drive como UNICA fuente: que nada de la carpeta quede invisible.

QUE SE PROTEGE AQUI
-------------------
Una sola promesa, y es la que importa: **ningun archivo de la carpeta de
Drive puede acabar sin rastro en el indice**. No que todo se indexe entero --
eso es imposible y ademas no haria falta --, sino que de cada archivo quede
algo que permita al agente decir que existe y de que trata.

Hay exactamente tres finales validos para un archivo:

  ENTERO      se indexa fragmento a fragmento, como un documento normal
  RESUMIDO    se recorre completo y se indexa su agregado (tablas grandes)
  DECLARADO   no se pudo leer o no cabe, pero queda dicho con su motivo

Y uno invalido, que es el que esta bateria persigue:

  INVISIBLE   el archivo estuvo en Drive y el indice no sabe nada de el

El final invisible no es teorico. Se han encontrado cuatro formas distintas
de llegar a el en este proyecto: un Excel grande que se bajaba y se tiraba,
un PDF que se pasaba del presupuesto y desaparecia, una foto que no se podia
transcribir y se esfumaba, y un formato sin lector que se saltaba con un
`continue` pelado.

POR QUE UN DRIVE DE MENTIRA
---------------------------
Porque hace falta provocar situaciones que en el Drive real no se pueden
pedir a voluntad: un nativo de Google, un formato sin lector, un archivo
enorme. El Drive de mentira responde igual que la biblioteca de Google pero
leyendo de un arbol que se describe aqui.

Los pesos y los archivos SI son reales donde se puede: la capa de
ecosistemas de 418 MB se sirve desde el disco, no se inventa.

NO GASTA CUOTA
--------------
El embebedor y el lector de imagenes se sustituyen por dobles que cuentan.
Lo que se mide es el camino, no la calidad del modelo: de eso se encarga
`probar_imagenes.py`, que si llama al modelo de verdad.

COMO SE EJECUTA
---------------
    python probar_drive_completo.py
"""

import io
import os
import shutil
import sys
import tempfile
import time
import zipfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from core import corpus as _cp         # noqa: E402
from core import fuentes as _fu        # noqa: E402
from core import vectorial as _vc      # noqa: E402

ACIERTOS = 0
FALLOS = []

CARPETA_GOOGLE = "application/vnd.google-apps.folder"
DOC_NATIVO = "application/vnd.google-apps.document"


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


# ==============================================================================
# 1. UN DRIVE DE MENTIRA QUE SIRVE ARCHIVOS DE VERDAD
# ==============================================================================

class DriveFalso:
    """
    Responde como la biblioteca de Google, leyendo de un arbol descrito aqui.

    Cada entrada puede traer `datos` (bytes) o `origen` (una ruta del disco).
    `origen` existe para la capa de 418 MB: tenerla en memoria como bytes
    seria absurdo, y lo que se quiere medir es justamente que el sistema NO
    la carga entera.
    """

    def __init__(self, arbol):
        """
        Args:
            arbol: Diccionario {id_de_carpeta: lista_de_entradas}.
        """
        self.arbol = arbol
        self.exportaciones = []
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
        carpeta = q.split("'")[1] if "'" in q else ""
        self._resultado = {"files": []}
        for entrada in self.arbol.get(carpeta, []):
            fila = {k: v for k, v in entrada.items()
                    if k not in ("datos", "origen")}
            if "size" not in fila:
                if "datos" in entrada:
                    fila["size"] = str(len(entrada["datos"]))
                elif "origen" in entrada:
                    fila["size"] = str(os.path.getsize(entrada["origen"]))
            self._resultado["files"].append(fila)
        return self

    def execute(self):
        """
        Devuelve el resultado del `list()` anterior.

        Returns:
            Diccionario con la clave `files`.
        """
        return self._resultado

    def entrada(self, archivo_id):
        """
        Busca una entrada del arbol por su identificador.

        Args:
            archivo_id: Identificador buscado.

        Returns:
            El diccionario de la entrada, o None.
        """
        for entradas in self.arbol.values():
            for e in entradas:
                if e.get("id") == archivo_id:
                    return e
        return None


def construir_drive(banco, capa_real):
    """
    Arma la carpeta de Drive con un caso de cada final posible.

    Args:
        banco: Carpeta temporal donde dejar los archivos generados.
        capa_real: Ruta a la capa de 418 MB, o None si no esta.

    Returns:
        Tupla `(arbol, esperado)`, donde `esperado` dice que final debe
        tener cada archivo.
    """
    import openpyxl

    # --- Un Excel grande: el caso que antes se bajaba y se tiraba ---------
    gordo = os.path.join(banco, "Censo_Predial.xlsx")
    libro = openpyxl.Workbook(write_only=True)
    hoja = libro.create_sheet("datos")
    hoja.append(["vereda", "cobertura", "area_ha"])
    # 300.000 filas, no 120.000: con 120.000 el libro se quedaba en 1,56 MB,
    # por debajo del umbral de desvio (1,72 MB), y se indexaba entero. La
    # prueba decia estar probando el camino del resumen y probaba el otro.
    for i in range(300000):
        hoja.append([["El Jordan", "Palmichal", "La Hondita"][i % 3],
                     ["Bosque denso", "Pastos limpios"][i % 2],
                     round(1.5 + (i % 90), 2)])
    libro.save(gordo)

    # --- Un Excel pequeno: el inventario de quebradas ---------------------
    chico = os.path.join(banco, "Inventario_127_Quebradas.xlsx")
    libro = openpyxl.Workbook()
    hoja = libro.active
    hoja.append(["quebrada", "vereda", "longitud_km"])
    for i in range(127):
        hoja.append([f"Quebrada La {i:03d}", "El Jordan", round(1.2 + i * .07, 2)])
    libro.save(chico)

    # --- Un PDF con capa de texto -----------------------------------------
    # Se escribe a mano porque en el contenedor no hay libreria que los
    # genere, y un PDF de imagen no serviria: probaria otra cosa.
    # El texto tiene que dar para una ficha de verdad. Con una sola frase,
    # `merece_vector` lo descartaba por corto -- con razon -- y la prueba
    # acusaba al lector de PDF de no leer, cuando lo que pasaba era que el
    # PDF de prueba no tenia contenido suficiente para significar nada.
    _frases = [
        "POMCA del rio San Carlos: la cuenca abarca 702 kilometros "
        "cuadrados y reune 127 quebradas inventariadas en campo.",
        "Las coberturas dominantes son bosque denso y pastos limpios, "
        "con rastrojo alto en las laderas de mayor pendiente.",
        "El regimen hidrologico es bimodal, con maximos en abril y "
        "octubre y estiaje marcado entre junio y agosto.",
        "Las veredas de El Jordan, Palmichal y La Hondita concentran la "
        "mayor densidad de drenajes de la cuenca.",
    ]
    texto = "BT /F1 11 Tf 60 740 Td 14 TL " + " ".join(
        f"({f}) Tj T*" for f in _frases) + " ET"
    objetos = [
        "<< /Type /Catalog /Pages 2 0 R >>",
        "<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
        "/Resources << /Font << /F1 5 0 R >> >> /Contents 4 0 R >>",
        f"<< /Length {len(texto)} >>\nstream\n{texto}\nendstream",
        "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    pdf = "%PDF-1.4\n"
    posiciones = []
    for numero, cuerpo in enumerate(objetos, start=1):
        posiciones.append(len(pdf))
        pdf += f"{numero} 0 obj\n{cuerpo}\nendobj\n"
    inicio_tabla = len(pdf)
    pdf += f"xref\n0 {len(objetos) + 1}\n0000000000 65535 f \n"
    for posicion in posiciones:
        pdf += f"{posicion:010d} 00000 n \n"
    pdf += (f"trailer\n<< /Size {len(objetos) + 1} /Root 1 0 R >>\n"
            f"startxref\n{inicio_tabla}\n%%EOF\n")
    pdf_bytes = pdf.encode("latin-1")

    # --- La copia de seguridad del cuaderno, como la deja Takeout ---------
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("Takeout/Gemini Notebook/Cuenca/notas/sintesis.txt",
                   "Nota del cuaderno sobre la cuenca del rio San Carlos. "
                   "Se inventariaron 127 quebradas en las veredas de El "
                   "Jordan, Palmichal y La Hondita. El area de estudio "
                   "abarca 702 kilometros cuadrados, que no coinciden con "
                   "los 717,81 del municipio: son dos poligonos distintos y "
                   "la cuenca manda. Las coberturas dominantes son bosque "
                   "denso y pastos limpios.")
        z.writestr("Takeout/Gemini Notebook/Cuenca/fuentes/veredas.csv",
                   "vereda,quebradas\nEl Jordan,14\nPalmichal,9")
    zip_bytes = buf.getvalue()

    # --- Una nota exportada a Documentos: el puente con el cuaderno -------
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("word/document.xml",
                   '<?xml version="1.0"?><w:document xmlns:w='
                   '"http://schemas.openxmlformats.org/wordprocessingml/'
                   '2006/main"><w:body><w:p><w:r><w:t>Nota exportada del '
                   'cuaderno de Gemini sobre el area de estudio de San '
                   'Carlos. La delimitacion se tomo del KMZ del proyecto y '
                   'da 720,77 kilometros cuadrados frente a los 717,81 que '
                   'reporta el DANE para el municipio. La diferencia es '
                   'esperable porque la cuenca y el limite municipal son '
                   'poligonos distintos.</w:t></w:r></w:p>'
                   '</w:body></w:document>')
    docx_bytes = buf.getvalue()

    # --- Una foto de ficha de campo ---------------------------------------
    png = os.path.join(banco, "ficha_campo_042.png")
    try:
        from PIL import Image

        Image.new("RGB", (1200, 800), (250, 248, 242)).save(png)
    except ImportError:
        with open(png, "wb") as fh:
            fh.write(b"\x89PNG\r\n\x1a\n" + b"x" * 4096)

    hijos = [
        {"id": "f1", "name": "Censo_Predial.xlsx", "mimeType": "application/xlsx",
         "md5Checksum": "x1", "origen": gordo, "_final": "RESUMIDO"},
        {"id": "f2", "name": "Inventario_127_Quebradas.xlsx",
         "mimeType": "application/xlsx", "md5Checksum": "x2",
         "origen": chico, "_final": "ENTERO"},
        {"id": "f3", "name": "POMCA_San_Carlos.pdf", "mimeType": "application/pdf",
         "md5Checksum": "x3", "datos": pdf_bytes, "_final": "ENTERO"},
        {"id": "f4", "name": "copia_notebook.zip", "mimeType": "application/zip",
         "md5Checksum": "x4", "datos": zip_bytes, "_final": "ENTERO"},
        {"id": "f5", "name": "Nota del cuaderno", "mimeType": DOC_NATIVO,
         "md5Checksum": "", "modifiedTime": "2026-11-01T10:00:00Z",
         "datos": docx_bytes, "_final": "ENTERO"},
        {"id": "f6", "name": "ficha_campo_042.png", "mimeType": "image/png",
         "md5Checksum": "x6", "origen": png, "_final": "ENTERO"},
        {"id": "f7", "name": "plano_red_hidrica.dwg", "mimeType": "application/dwg",
         "md5Checksum": "x7", "datos": b"binario sin lector",
         "_final": "DECLARADO"},
    ]
    if capa_real:
        hijos.insert(0, {
            "id": "f0", "name": "e_eccmc_100K_2024.csv", "mimeType": "text/csv",
            "md5Checksum": "x0", "origen": capa_real, "_final": "RESUMIDO"})

    arbol = {
        "RAIZ": [{"id": "c1", "name": "Cartografia",
                  "mimeType": CARPETA_GOOGLE}],
        "c1": hijos,
    }
    esperado = {h["name"]: h["_final"] for h in hijos}
    # El nativo entra con la extension puesta.
    esperado["Nota del cuaderno.docx"] = esperado.pop("Nota del cuaderno")
    return arbol, esperado


print("=" * 70)
print("  Drive como unica fuente: que nada quede invisible")
print("=" * 70)

BASE = os.path.dirname(os.path.abspath(__file__))
_capa = os.path.join(BASE, "data", "capas_fuente",
                     "ecosistemas_colombia_100K_2024.csv")
if not os.path.exists(_capa):
    _capa = None
    print("\n  (sin la capa de 418 MB en disco: se prueba sin ella)")

_banco = tempfile.mkdtemp(prefix="probar_drive_completo_")
try:
    # ==========================================================================
    # 2. LA CARPETA ENTERA, POR EL CAMINO DE VERDAD
    # ==========================================================================
    print("\n--- Se recorre la carpeta de Drive ---")

    _arbol, _esperado = construir_drive(_banco, _capa)
    _drive = DriveFalso(_arbol)

    _fuente = _fu.FuenteDrive(carpeta_id="RAIZ", credenciales="noop")
    _fuente._servicio = lambda: _drive
    _fuente.carpeta_temporal = lambda: os.path.join(_banco, "_bajada")

    def _descargar_falso(servicio, archivo_id, ruta, exportar_como=""):
        """Copia del arbol en vez de bajar de la red."""
        _drive.descargas.append(archivo_id)
        if exportar_como:
            _drive.exportaciones.append((archivo_id, exportar_como))
        entrada = _drive.entrada(archivo_id) or {}
        if "origen" in entrada:
            shutil.copyfile(entrada["origen"], ruta)
        else:
            with open(ruta, "wb") as fh:
                fh.write(entrada.get("datos", b""))

    _fuente._descargar = _descargar_falso

    def _asomarse_falso(servicio, archivo_id, ruta, cuantos_bytes=2 << 20):
        """Trae solo el principio, como hace la descarga parcial de verdad."""
        entrada = _drive.entrada(archivo_id) or {}
        try:
            if "origen" in entrada:
                with open(entrada["origen"], "rb") as origen, \
                        open(ruta, "wb") as destino:
                    destino.write(origen.read(cuantos_bytes))
            else:
                with open(ruta, "wb") as destino:
                    destino.write(entrada.get("datos", b"")[:cuantos_bytes])
            return True
        except OSError:
            return False

    _fuente.asomarse = _asomarse_falso

    _t0 = time.time()
    _documentos = _fuente.documentos()
    _tardo_listar = time.time() - _t0

    _entran = {d["nombre"] for d in _documentos}
    _declarados = {d["nombre"]: d["motivo"] for d in _fuente.descartados}
    _apartados = {d["nombre"]: d for d in _fuente.descartados}

    comprobar("se ve la carpeta entera, sin saltarse nada",
              len(_entran) + len(_declarados) == len(_esperado),
              f"{len(_entran)} entran + {len(_declarados)} declarados = "
              f"{len(_esperado)} archivos")

    comprobar("el nativo de Google se exporta al vuelo",
              "Nota del cuaderno.docx" in _entran
              and any(i == "f5" for i, _m in _drive.exportaciones),
              "la nota del cuaderno entra como .docx")

    comprobar("lo que no tiene lector queda DECLARADO, no invisible",
              "plano_red_hidrica.dwg" in _declarados
              and "dwg" in _declarados["plano_red_hidrica.dwg"],
              _declarados.get("plano_red_hidrica.dwg", "sin constancia"))

    # ==========================================================================
    # 3. CADA ARCHIVO TIENE UN FINAL, Y NINGUNO ES "INVISIBLE"
    # ==========================================================================
    print("\n--- Que le pasa a cada archivo ---")

    _embeber_real = _vc.embeber
    _leer_real = _cp.leer_documento
    _gasto = {"n": 0}

    def _contar(textos, *_a, **_k):
        _gasto["n"] += len(textos)
        return [[0.0] * 768 for _ in textos]

    def _leer_sin_modelo(ruta):
        """
        Lee igual que siempre, salvo las imagenes.

        La transcripcion de una foto es una llamada al modelo y cuesta cuota.
        Aqui no se esta midiendo si el modelo lee bien -- eso lo hace
        `probar_imagenes.py` con llamadas de verdad -- sino que la foto
        recorre el camino y deja rastro.
        """
        if os.path.splitext(ruta)[1].lower() in _cp.TIPOS_IMAGEN:
            return [(1, "FICHA DE CAMPO 042. Vereda El Jordan. Quebrada "
                        "La Palmichala. Coordenadas 6.18742 N, -74.99315 W. "
                        "Caudal medido 37.4 litros por segundo. Cobertura "
                        "circundante de bosque denso con rastrojo alto en "
                        "la margen derecha. Levantamiento validado en sitio "
                        "por la brigada 3 el 19 de octubre de 2026.")]
        return _leer_real(ruta)

    class _Mudo:
        def delete(self, **_kw):
            """Acepta el borrado previo de las fichas viejas."""

        def upsert(self, **_kw):
            """Acepta el guardado."""

    _vc.embeber = _contar
    _cp.leer_documento = _leer_sin_modelo
    _finales = {}
    try:
        for _doc in _documentos:
            _acumulado = {"fichas": 0, "fallidos": [], "_fraccion": 0.0}
            _antes = _gasto["n"]
            _vc._indexar_documento(_Mudo(), "prueba", _doc, _acumulado,
                                   None, "[x]")
            _fichas = _gasto["n"] - _antes
            if _acumulado.get("resumidas"):
                _finales[_doc["nombre"]] = "RESUMIDO"
            elif _acumulado.get("fuera_de_presupuesto"):
                _finales[_doc["nombre"]] = "DECLARADO"
            elif _acumulado.get("sin_contenido"):
                _finales[_doc["nombre"]] = "DECLARADO"
            elif _fichas > 0:
                _finales[_doc["nombre"]] = "ENTERO"
            else:
                _finales[_doc["nombre"]] = "INVISIBLE"
    finally:
        _vc.embeber = _embeber_real
        _cp.leer_documento = _leer_real

    # HAY DOS CAMINOS AL RESUMEN, NO UNO
    # ----------------------------------
    # Un archivo apartado por `_decidir` -- porque sus fichas no caben en el
    # presupuesto -- NO se queda en una nota suelta: se le hace una ficha de
    # catalogo y, si es una tabla, se recorre ENTERO y se indexa su
    # agregado. Esa es la via de la capa de 418 MB.
    #
    # La otra via es `_indexar_documento`, para lo que si paso el filtro de
    # descarga pero resulta ser una tabla grande.
    #
    # Las dos acaban en el indice con el contenido agregado, asi que las dos
    # son RESUMIDO. Contar la primera como un simple "declarado" seria no
    # distinguir un archivo analizado de uno que solo se menciona.
    for _nombre, _ficha in _apartados.items():
        _resumen_cat = (_ficha.get("catalogo") or {}).get("resumen") or {}
        if _resumen_cat.get("lineas"):
            _finales[_nombre] = "RESUMIDO"
        else:
            _finales[_nombre] = "DECLARADO"

    print(f"  {'archivo':<34} {'esperado':<11} {'real':<11}")
    for _nombre, _quiere in sorted(_esperado.items()):
        _tiene = _finales.get(_nombre, "INVISIBLE")
        _marca = "  " if _tiene == _quiere else "<-"
        print(f"  {_nombre:<34} {_quiere:<11} {_tiene:<11} {_marca}")

    _invisibles = [n for n, f in _finales.items() if f == "INVISIBLE"]
    comprobar("NINGUN archivo de Drive queda invisible",
              not _invisibles,
              "todos dejan rastro en el indice" if not _invisibles
              else f"se perdieron: {_invisibles}")

    comprobar("cada archivo acaba como debe",
              all(_finales.get(n) == f for n, f in _esperado.items()),
              f"{sum(1 for n, f in _esperado.items() if _finales.get(n) == f)}"
              f"/{len(_esperado)} coinciden")

    # ==========================================================================
    # 4. LOS FORMATOS Y LOS PESOS QUE PEDIA EL ENCARGO
    # ==========================================================================
    print("\n--- Formatos y pesos ---")

    comprobar("csv: la capa pesada se recorre entera y se resume",
              _capa is None
              or _finales.get("e_eccmc_100K_2024.csv") == "RESUMIDO",
              "418 MB -> agregado" if _capa else "omitido, no esta la capa")
    comprobar("excel: el grande se resume y el pequeno entra entero",
              _finales.get("Censo_Predial.xlsx") == "RESUMIDO"
              and _finales.get("Inventario_127_Quebradas.xlsx") == "ENTERO",
              "300.000 filas resumidas, 127 quebradas enteras")
    comprobar("pdf: se lee su texto",
              _finales.get("POMCA_San_Carlos.pdf") == "ENTERO",
              "la capa de texto del PDF entra al indice")
    comprobar("png: la foto deja su transcripcion",
              _finales.get("ficha_campo_042.png") == "ENTERO",
              "la ficha fotografiada se convierte en texto")
    comprobar("zip: la copia del cuaderno se abre y se lee",
              _finales.get("copia_notebook.zip") == "ENTERO",
              "las notas de dentro llegan al indice")

    # ==========================================================================
    # 5. Y NO QUEDA NADA EN EL EQUIPO
    # ==========================================================================
    print("\n--- Nada se conserva en el equipo ---")

    _limpieza = _fuente.limpiar()
    _bajada = os.path.join(_banco, "_bajada")
    _quedan = sum(len(f) for _r, _d, f in os.walk(_bajada)) \
        if os.path.exists(_bajada) else 0
    comprobar("lo descargado de Drive se borra al terminar",
              _quedan == 0,
              f"{_limpieza.get('archivos', 0)} archivos borrados, "
              f"quedan {_quedan}")
finally:
    shutil.rmtree(_banco, ignore_errors=True)

# ==============================================================================
print("\n" + "=" * 70)
if FALLOS:
    print(f"{ACIERTOS} comprobaciones superadas, {len(FALLOS)} FALLIDAS:\n")
    for _f in FALLOS:
        print("  -", _f)
    sys.exit(1)
print(f"{ACIERTOS} de {ACIERTOS} comprobaciones superadas")
