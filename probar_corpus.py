"""
Pruebas del corpus documental.
===============================================================================

Reproduce, con corpus minimos construidos a proposito, los cinco fallos que
detecto la revision del plan tecnico. Cada prueba fallaba antes de la
correccion y debe pasar ahora.

Se ejecuta con:  python probar_corpus.py

No toca el corpus del proyecto: cada caso monta el suyo en una carpeta
temporal del sistema y la borra al terminar.
"""
import os
import shutil
import sys
import tempfile

AQUI = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, AQUI)
os.chdir(AQUI)

#: Raiz temporal donde se montan los corpus de ensayo. Vive FUERA del
#: proyecto, para que una prueba no pueda ensuciar la bibliografia real.
TEMPORAL = tempfile.mkdtemp(prefix="corpus_pruebas_")

ok = total = 0


def comprobar(nombre, condicion, detalle=""):
    """
    Cuenta e imprime una comprobacion.

    Args:
        nombre: Que se esta comprobando.
        condicion: Resultado de la comprobacion.
        detalle: Valor observado, para poder diagnosticar un fallo sin repetir
            la ejecucion a mano.
    """
    global ok, total
    total += 1
    ok += bool(condicion)
    print(f"  [{'OK ' if condicion else 'FALLO'}] {nombre}: {detalle}")


def montar(nombre, documentos):
    """
    Crea un corpus de ensayo aislado y lo deja activo.

    Args:
        nombre: Identificador del caso; da nombre a la carpeta temporal.
        documentos: Lista de tuplas `(categoria, archivo, texto)`.

    Returns:
        Ruta de la carpeta creada, por si la prueba necesita anadir archivos.
    """
    base = os.path.join(TEMPORAL, nombre)
    shutil.rmtree(base, ignore_errors=True)
    for categoria, archivo, texto in documentos:
        carpeta = os.path.join(base, categoria)
        os.makedirs(carpeta, exist_ok=True)
        with open(os.path.join(carpeta, archivo), "w", encoding="utf-8") as fh:
            fh.write(texto)
    os.environ["RUTA_CORPUS"] = base
    corpus.olvidar()
    return base


from core import corpus, informes  # noqa: E402

# ---------------------------------------------------------------- FALLO 1
print("FALLO 1 - La busqueda exacta no debe aceptar prefijos")
montar("f1", [("Prueba", "mezcla.md",
               "La especie Polites othonis fue descrita para la region andina. "
               "El epiteto otho corresponde a una combinacion distinta de otro "
               "genero de la misma familia. " * 6)])
r = corpus.buscar_binomio("Polites otho")
comprobar("'Polites otho' no se cuela dentro de 'Polites othonis'",
          r == [], f"{len(r)} fichas (debe ser 0)")

montar("f1b", [("Prueba", "real.md",
                "El catalogo registra a Polites otho en el pais. " * 15)])
r = corpus.buscar_binomio("Polites otho")
comprobar("y el binomio real sigue encontrandose", len(r) > 0, f"{len(r)} fichas")
comprobar("'Polites oth' (prefijo) no devuelve nada",
          corpus.buscar_binomio("Polites oth") == [], "vacio")

# ---------------------------------------------------------------- FALLO 2
print("\nFALLO 2 - Las secciones no deben citar fichas de otra especie")
montar("f2", [
    ("Prueba", "catalogo.md",
     "La subfamilia Hesperiinae incluye a Polites otho, registrada en el pais. " * 10),
    ("Prueba", "hospederas_de_morpho.md",
     "La planta hospedera de la oruga de Morpho helenor es una leguminosa del "
     "genero Inga, y la larva se alimenta de sus hojas tiernas. " * 8),
])
ficha = informes.ficha_de_especie("Polites otho")
comprobar("la ficha de campo NO cita el documento de Morpho",
          "hospederas_de_morpho.md" not in ficha, "no aparece")
citadas = [l.split("Fuente: ")[1] for l in ficha.splitlines() if "Fuente: " in l]
comprobar("toda fuente citada en la ficha de campo es ajena a Morpho",
          all("morpho" not in c.lower() for c in citadas),
          f"{len(citadas)} fuentes citadas")

rep = informes.reporte_taxonomico("Polites otho")
comprobar("el reporte tampoco lo cita",
          "hospederas_de_morpho.md" not in rep, "no aparece")
comprobar("buscar() sin debe_contener si lo devuelve (el filtro es lo que actua)",
          any(f["documento"] == "hospederas_de_morpho.md"
              for f in corpus.buscar("planta hospedera oruga larva", limite=5)),
          "confirmado: el filtro es necesario")

# ---------------------------------------------------------------- FALLO 3
print("\nFALLO 3 - La bibliografia debe listar lo que se cito, y solo eso")
montar("f3", [
    ("Catalogos", "si_citado.md",
     "El catalogo nacional registra a Polites otho en los valles interandinos "
     "del pais segun la revision taxonomica. " * 10),
    ("Otros", "no_citado.md",
     "Documento sobre escarabajos estercoleros de los Llanos Orientales, sin "
     "relacion alguna con los lepidopteros del catalogo. " * 10),
])
rep = informes.reporte_taxonomico("Polites otho")
biblio = rep.split("6. FUENTES EFECTIVAMENTE CITADAS")[1]
comprobar("incluye el documento que si se cito",
          "si_citado.md" in biblio, "presente")
comprobar("no incluye documentos que no aparecen en ninguna seccion",
          "no_citado.md" not in biblio, "ausente")
cuerpo = rep.split("6. FUENTES EFECTIVAMENTE CITADAS")[0]
citados = {l.split("Fuente: ")[1].split(" [")[0]
           for l in cuerpo.split("\n") if "Fuente: " in l}
en_biblio = {l.split("] ")[1].split("  -  ")[0]
             for l in biblio.split("\n") if "] " in l and "  -  " in l}
comprobar("coinciden exactamente las dos listas", citados == en_biblio,
          f"cuerpo={sorted(citados)} biblio={sorted(en_biblio)}")

# ---------------------------------------------------------------- FALLO 4
print("\nFALLO 4 - La pagina 1 de un PDF debe citarse")
base = montar("f4", [("Prueba", "nota.md", "Texto de prueba sin paginas. " * 40)])
try:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    fig = plt.figure(figsize=(8.5, 11))
    fig.text(0.1, 0.9, "Polites otho aparece citada en la pagina uno de este "
                       "documento de prueba sobre el catalogo nacional de "
                       "mariposas. El registro corresponde a los valles "
                       "interandinos y se incluye en la revision taxonomica "
                       "mas reciente de la familia Hesperiidae publicada para "
                       "el territorio colombiano.", wrap=True)
    fig.savefig(os.path.join(base, "Prueba", "unapagina.pdf"))
    plt.close(fig)
    corpus.olvidar()
    datos = corpus.cargar(forzar=True)
    pdf = [f for f in datos["fichas"] if f["documento"] == "unapagina.pdf"]
    md = [f for f in datos["fichas"] if f["documento"] == "nota.md"]
    if pdf:
        comprobar("el PDF cita su pagina 1", ", p. 1" in informes.cita(pdf[0]),
                  informes.cita(pdf[0]))
    if md:
        comprobar("el .md no inventa pagina", ", p." not in informes.cita(md[0]),
                  informes.cita(md[0]))
except ImportError:
    print("  (matplotlib no disponible; prueba de PDF omitida)")

# ---------------------------------------------------------------- FALLO 5
print("\nFALLO 5 - La version debe cambiar si un documento cambia de categoria")
base = montar("f5", [("Guias de Campo", "doc.md", "Contenido estable. " * 40)])
v1 = corpus.version_corpus()
destino = os.path.join(base, "Catalogos Nacionales")
os.makedirs(destino, exist_ok=True)
shutil.move(os.path.join(base, "Guias de Campo", "doc.md"),
            os.path.join(destino, "doc.md"))
v2 = corpus.version_corpus()
comprobar("cambia al mover el documento de carpeta", v1 != v2, f"{v1} -> {v2}")
comprobar("y la categoria de la ficha cambia con el",
          corpus.inventariar()[0]["categoria"] == "Catalogos Nacionales",
          corpus.inventariar()[0]["categoria"])

# ---------------------------------------------------------------- FALLO 6
print("\nFALLO 6 - El troceado no debe estancarse con frases cortas")
patologico = "Es corto. Muy corto. Otra frase. " * 200
trozos = corpus.trocear(patologico)
esperado = len(patologico) / (corpus.TAMANO_FICHA - corpus.SOLAPE_FICHA)
comprobar("no genera fichas de mas", len(trozos) < esperado * 1.6,
          f"{len(trozos)} fichas (razonable ~{esperado:.0f})")
comprobar("ninguna ficha es diminuta",
          all(len(t) > corpus.TAMANO_FICHA // 2 for t in trozos[:-1]),
          f"minimo {min((len(t) for t in trozos[:-1]), default=0)} caracteres")
inflado = sum(len(t) for t in trozos) / len(patologico)
comprobar("el texto no se infla", inflado < 1.4, f"{inflado:.2f}x")

# ---------------------------------------------------------------- FALLO 7
print("\nFALLO 7 - La cabecera de procedencia no debe indexarse")
base = montar("f7", [])
from core import importar
largo = "El genero Polites se registra en los valles interandinos del pais. " * 18
importar.importar_fuente("Fuente con procedencia", largo, "Catalogos",
                         "6. Catalogo", "https://notebook.google.com/notebook/x",
                         carpeta=base)
corpus.olvidar()
datos = corpus.cargar(forzar=True)
comprobar("ninguna ficha contiene la huella",
          not any("huella:" in f["texto"] for f in datos["fichas"]),
          f"{len(datos['fichas'])} fichas revisadas")
comprobar("ninguna ficha contiene la url del cuaderno",
          not any("notebook.google.com" in f["texto"] for f in datos["fichas"]),
          "limpias")
comprobar("pero el contenido real si esta",
          any("valles interandinos" in f["texto"] for f in datos["fichas"]),
          "texto conservado")

# ---------------------------------------------------------------- FALLO 8
print("\nFALLO 8 - El importador debe contar bien el lote")
base = montar("f8", [])
from core import importar as imp
largo = "Contenido suficiente para pasar la longitud minima exigida. " * 12
lote = imp.importar_lote(
    [{"titulo": "Valida", "texto": largo, "categoria": "Cat A"},
     {"titulo": "Corta", "texto": "muy poco", "categoria": "Cat B"},
     {"titulo": "Repetida", "texto": largo, "categoria": "Cat C"}],
    "6. Catalogo", "https://notebook.google.com/notebook/x", carpeta=base)
comprobar("una importada, una rechazada, una duplicada",
          (lote["importadas"], lote["rechazadas"], lote["duplicadas"]) == (1, 1, 1),
          f"imp={lote['importadas']} rech={lote['rechazadas']} dup={lote['duplicadas']}")
comprobar("rechaza por longitud y lo explica",
          any(d["estado"] == "rechazada" and "minimo" in d["motivo"]
              for d in lote["detalle"]), "motivo claro")
comprobar("detecta el duplicado aunque el titulo sea otro",
          any(d["estado"] == "duplicada" for d in lote["detalle"]),
          "por contenido, no por nombre")
comprobar("sanea nombres de archivo invalidos en Windows",
          "?" not in imp.nombre_seguro("Morpho / Siproeta: que come?"),
          imp.nombre_seguro("Morpho / Siproeta: que come?"))

# ==============================================================================
# INVENTARIO GEOGRAFICO: QUE DEL CORPUS SIRVE PARA UN MAPA
# ==============================================================================
# Lo que se vigila aqui es que NO mienta. La version ingenua de este
# inventario contaba 70 fichas con coordenadas y 710 con codigos DANE, y no
# habia ninguna de las dos cosas: eran indices de libro, tablas de mortalidad
# con coma decimal, ISBN, rangos de pagina y DOI. Decir "tengo coordenadas"
# cuando no las hay es peor que decir que no hay, porque el mapa sale con
# puntos y parece correcto.
print("\n--- Inventario geografico del corpus ---")
from core import geografia

# Los textos de prueba son inventados a proposito: lo que se comprueba es la
# FORMA que el inventario sabe reconocer, no un lugar concreto.
RECHAZAR = [
    ("indice de libro", "120, 124, 136,  146, 181  Eueides isabella em 20, 21"),
    ("tabla con coma decimal", "ASD 0,102 mas menos 0,001 0,159 0,258 0,428"),
    ("mortalidad acumulada", "Mortalidad acumulativa 0,107 0,127 0,127 0,148"),
]
for nombre, texto in RECHAZAR:
    comprobar(f"no toma por coordenada un {nombre}",
              geografia.coordenadas_en(texto) == [],
              "0 coordenadas, como debe ser")

#: Coordenadas de ejemplo. Llevan comilla simple (minutos) y doble (segundos),
#: asi que se escriben con el caracter escapado para no pelearse con el
#: delimitador de la cadena.
LATITUD_GMS = "6°21'46\" N"
LONGITUD_GMS = "75°36'18\" W"

ACEPTAR = [
    ("grados con hemisferio",
     "ubicada en 5°19'24.3\"N, 72°17'33.5\"W", 2),
    ("decimales con la palabra latitud",
     "latitud 6.1850, -74.9900 del registro", 1),
]
for nombre, texto, cuantas in ACEPTAR:
    halladas = geografia.coordenadas_en(texto)
    comprobar(f"reconoce {nombre}", len(halladas) == cuantas,
              f"{len(halladas)} de {cuantas}")

comprobar("un par de decimales SIN marca no cuenta",
          geografia.coordenadas_en("los valores 6.1850, -74.9900 del ensayo") == [],
          "sin la palabra que lo respalde, se descarta")

lat, eje = geografia.a_decimal(LATITUD_GMS)
comprobar("convierte grados a decimales",
          eje == "lat" and abs(lat - 6.3628) < 0.001, f"{lat:.4f} {eje}")
lon, eje = geografia.a_decimal(LONGITUD_GMS)
comprobar("aplica el signo del hemisferio oeste", eje == "lon" and lon < 0,
          f"{lon:.4f} {eje}")

puntos = geografia.emparejar([
    {"texto": LATITUD_GMS, "fuente": "a.pdf", "categoria": "x"},
    {"texto": LONGITUD_GMS, "fuente": "a.pdf", "categoria": "x"},
    {"texto": LATITUD_GMS, "fuente": "b.pdf", "categoria": "x"},
])
comprobar("empareja latitud con longitud del mismo documento",
          len(puntos) == 1, f"{len(puntos)} punto; la coordenada huerfana se descarta")

comprobar("el inventario no inventa toponimos",
          geografia.inventario([{"texto": "sin nada geografico", "fuente": "x",
                                 "categoria": "y"}])["cuencas"] == [],
          "texto sin lugares -> lista vacia")

# ==============================================================================
# ARCHIVOS .ZIP: LA COPIA DE SEGURIDAD AUTOMATICA QUE LLEGA A DRIVE
# ==============================================================================
#
# POR QUE IMPORTA ESTE FORMATO
# ----------------------------
# Gemini Notebook no tiene API publica, asi que no hay forma oficial de sacar
# sus notas hacia Drive de forma continua. Lo que SI existe es la exportacion
# programada de Google (Takeout), que cada dos meses puede dejar un .zip en
# Drive con las notas del cuaderno.
#
# Sin lector de .zip ese plan no funciona: el archivo se ve en la carpeta y el
# proyecto lo salta. Con el, el circuito se cierra entero con piezas
# oficiales: Google exporta -> cae en Drive -> el cron de las 2:00 lo indexa.
print("\n--- Un .zip, como el de una copia de seguridad programada ---")

import io as _io_zip
import zipfile as _zip

_carpeta_zip = tempfile.mkdtemp(prefix="probar_zip_")
try:
    # Un .docx de verdad dentro del paquete: es lo que sale de exportar una
    # nota del cuaderno a Documentos y luego respaldarla.
    _buf = _io_zip.BytesIO()
    with _zip.ZipFile(_buf, "w") as _z:
        _z.writestr("word/document.xml",
                    '<?xml version="1.0"?><w:document xmlns:w='
                    '"http://schemas.openxmlformats.org/wordprocessingml/'
                    '2006/main"><w:body><w:p><w:r><w:t>La cuenca del rio San '
                    'Carlos abarca 702 km2.</w:t></w:r></w:p></w:body>'
                    '</w:document>')
    _docx_bytes = _buf.getvalue()

    _paquete = os.path.join(_carpeta_zip, "takeout-gemininotebook.zip")
    with _zip.ZipFile(_paquete, "w") as _z:
        _z.writestr("Takeout/Gemini Notebook/Cuenca/notas/sintesis.txt",
                    "Sintesis: 127 quebradas inventariadas en la cuenca.")
        _z.writestr("Takeout/Gemini Notebook/Cuenca/notas/avance.docx",
                    _docx_bytes)
        _z.writestr("Takeout/Gemini Notebook/Cuenca/fuentes/datos.csv",
                    "vereda,quebradas\nEl Jordan,14\nPalmichal,9")
        # Dos que NO se saben leer: no deben romper la lectura del resto.
        _z.writestr("Takeout/Gemini Notebook/plano.dwg", b"binario")
        _z.writestr("Takeout/README", "sin extension")

    _dentro = corpus.leer_documento(_paquete)
    comprobar("un .zip se abre y se lee lo que trae dentro",
              len(_dentro) == 3,
              f"{len(_dentro)} archivos legibles de 5")

    _todo = " ".join(t for _p, t in _dentro)
    comprobar("el contenido de las notas llega al indice",
              "127 quebradas" in _todo and "702 km2" in _todo,
              "el .txt y el .docx de dentro se leyeron")

    # Sin esto una cita diria "pagina 2 de takeout.zip", que no se puede
    # comprobar. Con el nombre interno se sabe de que archivo salio el dato.
    comprobar("cada trozo dice de que archivo interno salio",
              all("[Dentro de" in t and "Takeout/" in t for _p, t in _dentro),
              "la cita apunta al archivo, no al paquete")

    comprobar("lo que no se sabe leer se salta sin romper el resto",
              not any(".dwg" in t for _p, t in _dentro) and len(_dentro) == 3,
              "el .dwg y el README quedan fuera, los otros tres entran")

    # --- La defensa contra una bomba de descompresion --------------------
    # Un .zip puede venir de una exportacion automatica que nadie mira antes
    # de que entre. Unos pocos kilobytes pueden volverse gigabytes al abrirse
    # y llenar el disco del servidor.
    _bomba = os.path.join(_carpeta_zip, "bomba.zip")
    with _zip.ZipFile(_bomba, "w", _zip.ZIP_DEFLATED) as _z:
        _z.writestr("enorme.txt", b"\0" * (4 * 1024 * 1024))
    _tope_real = corpus.TOPE_DESCOMPRIMIDO
    try:
        # Se baja el tope a 1 MB para no tener que fabricar un zip de 2 GB.
        corpus.TOPE_DESCOMPRIMIDO = 1024 * 1024
        _resultado = corpus.leer_documento(_bomba)
        comprobar("un .zip que se expande demasiado no se abre",
                  _resultado == [],
                  f"{os.path.getsize(_bomba) // 1024} kB comprimidos que "
                  f"darian 4 MB: se rechaza antes de extraer")
    finally:
        corpus.TOPE_DESCOMPRIMIDO = _tope_real

    # --- Nada de .zip dentro de .zip -------------------------------------
    _anidado = os.path.join(_carpeta_zip, "anidado.zip")
    with _zip.ZipFile(_anidado, "w") as _z:
        _z.writestr("dentro.zip", open(_paquete, "rb").read())
        _z.writestr("nota.txt", "esto si se lee")
    _r = corpus.leer_documento(_anidado)
    comprobar("un .zip dentro de otro NO se abre en cadena",
              len(_r) == 1 and "esto si se lee" in _r[0][1],
              "el anidamiento es la forma clasica de la bomba; se ignora")

    # --- Un paquete corrupto ---------------------------------------------
    _roto = os.path.join(_carpeta_zip, "roto.zip")
    with open(_roto, "wb") as _fh:
        _fh.write(b"PK\x03\x04" + b"basura" * 50)
    comprobar("un .zip corrupto se declara, no revienta",
              corpus.leer_documento(_roto) == [],
              "devuelve vacio y queda anotado en los fallos de lectura")
finally:
    shutil.rmtree(_carpeta_zip, ignore_errors=True)

# ==============================================================================
# FORMATOS SIG: SHAPEFILE, PROYECTO DE QGIS Y RASTER
# ==============================================================================
#
# POR QUE ENTRAN ESTOS FORMATOS
# -----------------------------
# Porque es lo que llega de una salida de campo. El proyecto de mariposas
# del equipo, hecho con QField, trae un shapefile de 58 registros con 37
# campos, un poligono de monitoreo, un mapa base georreferenciado y el
# proyecto de QGIS que los junta. Sin estos lectores, de todo eso el sistema
# solo veria las fotos sueltas.
print("\n--- Formatos SIG ---")

import struct as _struct_sig

_carpeta_sig = tempfile.mkdtemp(prefix="probar_sig_")
try:
    def _escribir_shapefile(base, campos, registros, lon, lat, cpg="UTF-8"):
        """
        Escribe un shapefile de un punto, con sus archivos hermanos.

        Se construye a mano porque en el contenedor no hay GDAL, que es la
        misma razon por la que el lector tambien se escribio a mano.

        Args:
            base: Ruta sin extension.
            campos: Lista de nombres de campo.
            registros: Lista de listas de valores, en el orden de `campos`.
            lon: Longitud del punto.
            lat: Latitud del punto.
            cpg: Codificacion a declarar, o None para no escribir el .cpg.
        """
        # --- .shp: cabecera de 100 bytes y un registro de punto ----------
        contenido = _struct_sig.pack("<idd", 1, lon, lat)
        registro = _struct_sig.pack(">ii", 1, len(contenido) // 2) + contenido
        cabecera = bytearray(100)
        cabecera[0:4] = _struct_sig.pack(">i", 9994)
        cabecera[24:28] = _struct_sig.pack(">i", (100 + len(registro)) // 2)
        cabecera[28:32] = _struct_sig.pack("<i", 1000)
        cabecera[32:36] = _struct_sig.pack("<i", 1)
        cabecera[36:68] = _struct_sig.pack("<dddd", lon, lat, lon, lat)
        with open(base + ".shp", "wb") as fh:
            fh.write(bytes(cabecera) + registro)

        # --- .dbf: cabecera, descriptores y registros --------------------
        largo = 80
        descriptores = b""
        for nombre in campos:
            bruto = nombre.encode(cpg or "latin-1")[:11]
            descriptores += (bruto.ljust(11, b"\x00") + b"C"
                             + b"\x00" * 4 + bytes([largo]) + b"\x00" * 15)
        tam_cabecera = 32 + len(descriptores) + 1
        tam_registro = 1 + largo * len(campos)
        cab = bytearray(32)
        cab[0] = 0x03
        cab[4:8] = _struct_sig.pack("<I", len(registros))
        cab[8:10] = _struct_sig.pack("<H", tam_cabecera)
        cab[10:12] = _struct_sig.pack("<H", tam_registro)
        cuerpo = b""
        for fila in registros:
            cuerpo += b" "
            for valor in fila:
                cuerpo += str(valor).encode(cpg or "latin-1")[:largo].ljust(
                    largo, b" ")
        with open(base + ".dbf", "wb") as fh:
            fh.write(bytes(cab) + descriptores + b"\x0d" + cuerpo + b"\x1a")

        with open(base + ".prj", "w", encoding="latin-1") as fh:
            fh.write('GEOGCS["GCS_WGS_1984",DATUM["D_WGS_1984"]]')
        if cpg:
            with open(base + ".cpg", "w", encoding="ascii") as fh:
                fh.write(cpg)

    # --- Un shapefile completo, con acentos y nombres largos -------------
    _base = os.path.join(_carpeta_sig, "Formulario Mariposas")
    _campos = ["ID único", "Nombre cie", "Hábitat", "Número in"]
    _escribir_shapefile(_base, _campos,
                        [["01", "Danaus plexippus", "Bosque húmedo", "3"]],
                        -75.0413, 6.1714)

    _paginas = corpus.leer_documento(_base + ".shp")
    _todo = " ".join(t for _p, t in _paginas)
    comprobar("un shapefile se lee con su geometria y su tabla",
              len(_paginas) == 2 and "Punto" in _todo,
              f"{len(_paginas)} paginas: la capa y su tabla de atributos")

    comprobar("los atributos llegan, que es lo que se puede citar",
              "Danaus plexippus" in _todo and "Bosque húmedo" in _todo,
              "la especie y el habitat entran al indice")

    # El .cpg dice la codificacion y hay que hacerle caso TAMBIEN en los
    # nombres de campo. Leyendolos como latin-1 fijo, "ID único" salia como
    # "ID Ãºnico" en los datos reales del equipo.
    comprobar("los acentos de los nombres de campo sobreviven",
              "ID único" in _todo and "Número in" in _todo,
              "se respeta la codificacion declarada en el .cpg")

    comprobar("se dice en que sistema de referencia esta",
              "GCS_WGS_1984" in _todo,
              "sale del .prj que acompana al shapefile")

    # --- Un shapefile al que le falta un hermano -------------------------
    # Pasa de verdad: alguien sube solo el .shp a Drive y se deja los otros
    # cuatro. La capa entra, pero hay que DECIR que llega muda.
    _suelto = os.path.join(_carpeta_sig, "suelta")
    _escribir_shapefile(_suelto, ["campo"], [["valor"]], -75.0, 6.1)
    os.remove(_suelto + ".dbf")
    _r = corpus.leer_documento(_suelto + ".shp")
    comprobar("un shapefile sin su .dbf avisa de que no trae atributos",
              bool(_r) and "sin" in _r[0][1] and "atributos" in _r[0][1],
              "entra la geometria y se declara lo que falta")

    # --- Dentro de un .zip los hermanos tienen que seguir juntos ---------
    # Es el caso real: el proyecto llega comprimido. El lector de .zip
    # extraia cada archivo a una carpeta propia, lo que separaba el .shp de
    # su .dbf y dejaba la capa sin atributos sin que nadie se enterara.
    _comprimido = os.path.join(_carpeta_sig, "salida_de_campo.zip")
    import zipfile as _zip_sig
    with _zip_sig.ZipFile(_comprimido, "w") as _z:
        for _ext in (".shp", ".dbf", ".prj", ".cpg"):
            _z.write(_base + _ext, "capas/Formulario Mariposas" + _ext)
    _en_zip = " ".join(t for _p, t in corpus.leer_documento(_comprimido))
    comprobar("un shapefile dentro de un .zip conserva sus atributos",
              "Danaus plexippus" in _en_zip,
              "los cinco archivos se extraen juntos, no por separado")

    # --- Un proyecto de QGIS ---------------------------------------------
    _qgs = os.path.join(_carpeta_sig, "proyecto_campo.qgs")
    with open(_qgs, "w", encoding="utf-8") as fh:
        fh.write(
            '<qgis version="3.40.8"><title>Mariposas</title>'
            '<projectCrs><spatialrefsys><authid>EPSG:4326</authid>'
            '</spatialrefsys></projectCrs>'
            '<projectlayers>'
            '<maplayer type="vector" geometry="Point">'
            '<layername>Mariposas</layername>'
            '<datasource>./Formulario Mariposas.shp</datasource></maplayer>'
            '<maplayer type="raster"><layername>Mapa base</layername>'
            '<datasource>./MAPA.tif</datasource></maplayer>'
            '</projectlayers>'
            '<properties><Paths><Absolute type="bool">false</Absolute>'
            '</Paths></properties></qgis>')
    _texto_qgs = corpus.leer_documento(_qgs)[0][1]
    comprobar("un proyecto de QGIS se lee como el indice que es",
              "2 capas" in _texto_qgs and "Mapa base" in _texto_qgs
              and "EPSG:4326" in _texto_qgs,
              "dice que capas trae y de donde salen")
    comprobar("y avisa si las rutas fueran absolutas",
              "relativas" in _texto_qgs,
              "aqui son relativas, asi que el proyecto se puede mover")

    # --- Un raster proyectado --------------------------------------------
    # Se escribe un TIFF minimo con las etiquetas de GeoTIFF en metros, que
    # es como venia el mapa base del equipo: Pseudo-Mercator, no grados.
    def _escribir_tif(ruta, ancho, alto, escala, amarre):
        """Escribe un TIFF minimo con etiquetas de georreferenciacion."""
        entradas = [(256, 4, 1, ancho), (257, 4, 1, alto)]
        extra = b""
        posicion_extra = 8 + 2 + 12 * 4 + 4
        datos_escala = _struct_sig.pack("<ddd", escala[0], escala[1], 0.0)
        datos_amarre = _struct_sig.pack("<dddddd", *amarre)
        entradas.append((33550, 12, 3, posicion_extra))
        extra += datos_escala
        entradas.append((33922, 12, 6, posicion_extra + len(datos_escala)))
        extra += datos_amarre

        cuerpo = _struct_sig.pack("<H", len(entradas))
        for etiqueta, tipo, cantidad, valor in entradas:
            cuerpo += _struct_sig.pack("<HHI", etiqueta, tipo, cantidad)
            cuerpo += _struct_sig.pack("<I", valor)
        cuerpo += _struct_sig.pack("<I", 0)
        with open(ruta, "wb") as fh:
            fh.write(b"II" + _struct_sig.pack("<HI", 42, 8) + cuerpo + extra)

    _tif = os.path.join(_carpeta_sig, "mapa_base.tif")
    _escribir_tif(_tif, 3072, 2048, (10.0, 10.0),
                  (0, 0, 0, -8359244.11, 700740.31, 0))
    _texto_tif = corpus.leer_documento(_tif)[0][1]
    comprobar("un raster declara su tamano sin abrir los pixeles",
              "3072" in _texto_tif and "2048" in _texto_tif,
              "millones de pixeles no dicen nada; su tamano y su sitio si")

    # Decir "latitud 680260" seria falso, y ademas imposible: una latitud no
    # pasa de 90. El mapa base del equipo esta en metros, no en grados.
    comprobar("no se llama latitud a lo que son metros",
              "PROYECTADO" in _texto_tif and "no son grados" in _texto_tif,
              "se avisa de que hay que reproyectarlo para compararlo")
finally:
    shutil.rmtree(_carpeta_sig, ignore_errors=True)


# ==============================================================================
print("")
print("FORMATOS QUE ANTES SE APARTABAN")
# ------------------------------------------------------------------------------
# En la carpeta de Drive habia siete archivos que el sistema veia y no leia, y
# los listaba como "no hay lector para '.ris'". Dos de ellos resultaron ser
# documentos de 157 y 255 paginas: un manual para crear mariposarios y una
# tesis entera. Se iban a la basura por como estaba escrito su nombre.
import zipfile as _zp_f

_banco_f = tempfile.mkdtemp(prefix="formatos_")
try:
    # --- Una presentacion -----------------------------------------------
    # Se arma a mano: un .pptx es un ZIP con un XML por diapositiva, igual
    # que un .docx. Por eso no hace falta python-pptx.
    _NS = 'xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main"'

    def _diapositiva(*parrafos):
        """
        Arma el XML de una diapositiva con los parrafos dados.

        Args:
            *parrafos: Textos, uno por parrafo.

        Returns:
            El XML como cadena.
        """
        cuerpo = "".join(
            f"<a:p><a:r><a:t>{t}</a:t></a:r></a:p>" for t in parrafos)
        return f"<sld {_NS}><a:txBody>{cuerpo}</a:txBody></sld>"

    _pptx = os.path.join(_banco_f, "catalogo.pptx")
    with _zp_f.ZipFile(_pptx, "w") as _z:
        _z.writestr("ppt/slides/slide1.xml",
                    _diapositiva("Catalogo de mariposas", "Morpho peleides"))
        # La 10 antes que la 2, a proposito: es el orden en que un ZIP
        # devuelve los nombres y el que haria citar mal la diapositiva.
        _z.writestr("ppt/slides/slide10.xml", _diapositiva("Decima lamina"))
        _z.writestr("ppt/slides/slide2.xml",
                    _diapositiva("Planta hospedera: Passiflora"))
        _z.writestr("ppt/notesSlides/notesSlide2.xml",
                    _diapositiva("Nota: confirmado en la vereda El Jardin"))

    _paginas = corpus.leer_documento(_pptx)
    comprobar("una presentacion .pptx se lee sin instalar nada",
              len(_paginas) == 3,
              f"{len(_paginas)} diapositivas")
    comprobar("cada diapositiva es su propia pagina, para poder citarla",
              [p[0] for p in _paginas] == [1, 2, 10],
              "ordenadas 1, 2, 10: el ZIP las devuelve 1, 10, 2")
    _todo = chr(10).join(p[1] for p in _paginas)
    comprobar("el texto de las laminas entra entero",
              "Morpho peleides" in _todo and "Passiflora" in _todo,
              "titulo y contenido")
    comprobar("y las notas del ponente tambien, marcadas aparte",
              "Notas de la diapositiva 2" in _todo
              and "vereda El Jardin" in _todo,
              "ahi suele estar el nombre cientifico que no cabe en la lamina")

    # --- Una referencia bibliografica -----------------------------------
    _ris = os.path.join(_banco_f, "citation-1.ris")
    with open(_ris, "w", encoding="utf-8") as _fh:
        _fh.write(chr(10).join([
            "TY  - JOUR",
            "AU  - Andrade, Miguel",
            "TI  - Biodiversidad de las mariposas de Colombia",
            "PY  - 2011",
            "AB  - Se revisa el estado del conocimiento de los",
            "      lepidopteros diurnos del pais.",
            "DO  - 10.1000/ejemplo",
            "ER  - ",
        ]))
    _paginas = corpus.leer_documento(_ris)
    _texto = chr(10).join(p[1] for p in _paginas)
    comprobar("un .ris deja de ser un archivo de cero kilobytes ilegible",
              len(_paginas) == 1 and "Andrade" in _texto,
              "es la ficha completa de un articulo")
    comprobar("las etiquetas se traducen a palabras que alguien buscaria",
              "Titulo:" in _texto and "Ano: 2011" in _texto
              and "TY  -" not in _texto,
              "'Titulo', no 'TI'")
    comprobar("un resumen partido en varias lineas se junta",
              "lepidopteros diurnos del pais" in _texto,
              "la continuacion no se pierde")

    # --- Un archivo cuyo nombre no dice lo que es -----------------------
    # Es lo que deja Chrome cuando una descarga no se cierra del todo. En
    # la carpeta habia dos, de 8 MB y 26,8 MB, y los dos eran PDF enteros.
    _docx = os.path.join(_banco_f, "informe.docx")
    _NW = ('xmlns:w="http://schemas.openxmlformats.org/'
           'wordprocessingml/2006/main"')
    with _zp_f.ZipFile(_docx, "w") as _z:
        _z.writestr("word/document.xml",
                    f"<document {_NW}><w:body><w:p><w:r><w:t>"
                    f"Resolucion 1402 de 2018</w:t></w:r></w:p>"
                    f"</w:body></document>")
    _disfrazado = os.path.join(_banco_f, "descarga_a_medias.crdownload")
    shutil.copy(_docx, _disfrazado)

    comprobar("se reconoce el formato por dentro, no por el nombre",
              corpus._formato_por_dentro(_disfrazado) == ".docx",
              "mirando los primeros bytes y lo que lleva el ZIP")

    _paginas = corpus.leer_documento(_disfrazado)
    _texto = chr(10).join(p[1] for p in _paginas)
    comprobar("y se lee igual, aunque se llame .crdownload",
              "Resolucion 1402 de 2018" in _texto,
              "el contenido estaba entero; lo raro era el nombre")
    comprobar("pero se DICE que el nombre no correspondia",
              "no lleva extension util" in _texto,
              "quien lea la cita vera un .crdownload y tiene que saber por que")

    _basura = os.path.join(_banco_f, "ni_idea.crdownload")
    with open(_basura, "wb") as _fh:
        _fh.write(b"\x00\x01\x02 esto no es ningun formato conocido")
    comprobar("lo que no se reconoce ni por dentro se declara, no se traga",
              corpus.leer_documento(_basura) == []
              and any("ni_idea" in k for k in corpus.FALLOS_DE_LECTURA),
              "queda anotado en los fallos de lectura")

    comprobar("un nombre sin extension tambien se olfatea",
              corpus.LECTORES.get("") is corpus._leer_lo_que_sea,
              "el filtro de la fuente ya no lo aparta antes de bajarlo")
finally:
    shutil.rmtree(_banco_f, ignore_errors=True)

# ---------------------------------------------------------------- ESTADO
# Preguntar como va el corpus no puede costar leerselo entero. Durante un
# estudio con Drive la carpeta tiene 3,26 GB y cambia cada pocos segundos:
# `resumen()` reconstruia el indice en cada llamada, la API dejaba de
# contestar y la interfaz se quedaba cargando con la pagina en blanco.
print("")
print("ESTADO - informar del corpus no puede costar leerlo entero")

_base_e = montar("estado", [
    ("Leyes", "ley.md", "El articulo primero regula el uso del suelo. " * 20),
    ("Leyes", "decreto.md", "El decreto desarrolla la ley anterior. " * 20),
    ("Guias", "guia.md", "La guia describe el muestreo en campo. " * 20),
])

_abiertos = []
_abrir_de_verdad = open


def _abrir_contando(ruta, *a, **k):
    """Abre como siempre, dejando constancia de que documento se leyo."""
    if str(ruta).endswith((".md", ".pdf", ".docx", ".xlsx")):
        _abiertos.append(os.path.basename(str(ruta)))
    return _abrir_de_verdad(ruta, *a, **k)


import builtins  # noqa: E402

builtins.open = _abrir_contando
try:
    corpus.olvidar()
    _abiertos.clear()
    _r = corpus.resumen()
    comprobar("resumen() no abre ni un documento",
              _abiertos == [], f"abrio {len(_abiertos)}")
    comprobar("y aun asi dice cuantos hay y como se reparten",
              _r["documentos"] == 3 and dict(_r["categorias"])["Leyes"] == 2,
              f"{_r['documentos']} documentos")
    comprobar("las fichas que no se han contado se declaran sin contar",
              _r["fichas"] == 0 and _r["fichas_contadas"] is False,
              "cero fichas y 'no se han contado' no son lo mismo")

    _abiertos.clear()
    _r = corpus.resumen(reconstruir=True)
    comprobar("quien necesita las fichas las pide, y entonces si se lee",
              _r["fichas"] > 0 and _r["fichas_contadas"] is True
              and set(_abiertos) == {"ley.md", "decreto.md", "guia.md"},
              f"{_r['fichas']} fichas tras abrir "
              f"{len(set(_abiertos))} documentos distintos")

    _r = corpus.resumen()
    comprobar("y una vez leido, el resumen barato ya sabe el numero",
              _r["fichas"] > 0 and _r["fichas_contadas"] is True,
              "sale de lo que ya esta en memoria")

    # --- La huella no se recalcula sobre archivos que no han cambiado ---
    corpus._HUELLAS.clear()
    _abiertos.clear()
    _v1 = corpus.version_corpus()
    _leidos_la_primera = len(_abiertos)
    _abiertos.clear()
    _v2 = corpus.version_corpus()
    comprobar("la huella se calcula leyendo los documentos la primera vez",
              _v1 == _v2 and _leidos_la_primera == 3,
              f"{_leidos_la_primera} leidos, huella {_v1}")
    comprobar("y la segunda vez no se vuelve a leer ninguno",
              _abiertos == [], f"abrio {len(_abiertos)}")

    with _abrir_de_verdad(os.path.join(_base_e, "Guias", "guia.md"),
                          "a", encoding="utf-8") as _fh:
        _fh.write("Se anade una linea que cambia el archivo. " * 20)
    _abiertos.clear()
    comprobar("pero si un archivo cambia, la huella cambia con el",
              corpus.version_corpus() != _v1 and _abiertos,
              "el que cambio se vuelve a leer")
finally:
    builtins.open = _abrir_de_verdad

# ---------------------------------------------------------------- limpieza
shutil.rmtree(TEMPORAL, ignore_errors=True)

print(f"\n{ok} de {total} comprobaciones superadas")
sys.exit(0 if ok == total else 1)
