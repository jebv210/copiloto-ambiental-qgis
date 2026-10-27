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

# ---------------------------------------------------------------- limpieza
shutil.rmtree(TEMPORAL, ignore_errors=True)

print(f"\n{ok} de {total} comprobaciones superadas")
sys.exit(0 if ok == total else 1)
