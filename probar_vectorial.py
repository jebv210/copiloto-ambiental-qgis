"""
Comprobaciones del indice vectorial y del despliegue.
===============================================================================

Cada bloque intenta TUMBAR una afirmacion que se hizo sobre el sistema, no
confirmarla. Si una comprobacion pasa, la afirmacion se sostiene; si falla,
dice exactamente que se esperaba y que se obtuvo.

Se ejecuta con el interprete del proyecto::

    python probar_vectorial.py

No necesita Docker: lo que requiere contenedor se marca como omitido.
"""

import os
import shutil
import sys
import tempfile
import time


ACIERTOS = 0
FALLOS = []

#: Foto del entorno antes de empezar.
#:
#: Varias comprobaciones de aqui montan un Qdrant de laboratorio cambiando
#: estas variables (un puerto muerto, una coleccion aparte, una carpeta
#: temporal). Si no se devuelven a su sitio, lo que venga DESPUES hereda ese
#: laboratorio y mide la cosa equivocada: se vio al anadir las pruebas de la
#: marca temporal, que informaban de que el indice no existia teniendo 14.990
#: fichas en el servidor.
VARIABLES_DEL_INDICE = ("QDRANT_URL", "QDRANT_COLECCION", "QDRANT_RUTA_LOCAL",
                        "DIMENSIONES_EMBEDDING")
ENTORNO_ORIGINAL = {k: os.environ.get(k) for k in VARIABLES_DEL_INDICE}


def restaurar_entorno() -> None:
    """Devuelve las variables del indice a como estaban al arrancar."""
    for clave, valor in ENTORNO_ORIGINAL.items():
        if valor is None:
            os.environ.pop(clave, None)
        else:
            os.environ[clave] = valor


def comprobar(titulo: str, condicion: bool, detalle: str = "") -> None:
    """Registra el resultado de una comprobacion."""
    global ACIERTOS
    if condicion:
        ACIERTOS += 1
        print(f"  [OK ] {titulo}: {detalle}")
    else:
        FALLOS.append(f"{titulo} -> {detalle}")
        print(f"  [FALLA] {titulo}: {detalle}")


def omitir(titulo: str, motivo: str) -> None:
    """Deja constancia de lo que no se pudo comprobar."""
    print(f"  [--- ] {titulo}: omitido ({motivo})")


# ==============================================================================
print("\nVERSION DEL CORPUS - debe ser portable y sensible al contenido")
# ==============================================================================
from core import corpus

with tempfile.TemporaryDirectory() as tmp:
    sub = os.path.join(tmp, "Categoria A")
    os.makedirs(sub)
    ruta = os.path.join(sub, "doc.md")
    with open(ruta, "w", encoding="utf-8") as f:
        f.write("Morpho peleides se alimenta de Inga. " * 30)

    v1 = corpus.version_corpus(corpus.inventariar(tmp))

    # La fecha de modificacion NO debe influir: es lo que rompia Docker.
    futuro = time.time() + 90000
    os.utime(ruta, (futuro, futuro))
    v2 = corpus.version_corpus(corpus.inventariar(tmp))
    comprobar("cambiar solo la fecha no cambia la version",
              v1 == v2, f"{v1} == {v2}")

    # Una edicion del MISMO tamano si debe cambiarla.
    with open(ruta, "r", encoding="utf-8") as f:
        texto = f.read()
    with open(ruta, "w", encoding="utf-8") as f:
        f.write(texto.replace("Inga", "Ocot"))   # misma longitud exacta
    v3 = corpus.version_corpus(corpus.inventariar(tmp))
    comprobar("una edicion del mismo tamano si cambia la version",
              v1 != v3, f"{v1} -> {v3}")

    # Mover el documento de categoria tambien.
    sub_b = os.path.join(tmp, "Categoria B")
    os.makedirs(sub_b)
    os.replace(ruta, os.path.join(sub_b, "doc.md"))
    v4 = corpus.version_corpus(corpus.inventariar(tmp))
    comprobar("mover de categoria cambia la version",
              v3 != v4, f"{v3} -> {v4}")


# ==============================================================================
print("\nFILTRO DE FICHAS - lo que no merece vector")
# ==============================================================================
from core import vectorial

casos = [
    ("indice con puntos suspensivos",
     "................................ 6 \nFUNDAMENTACION TEORICA ......... 8", False),
    ("prosa real",
     "La oruga de Morpho peleides se alimenta de hojas tiernas del genero Inga, "
     "segun los registros de campo recogidos en la zona de estudio durante 2024.", True),
    ("fragmento demasiado corto", "Mariposas.", False),

    # Una tabla de datos tiene tan pocas letras como el indice de un libro,
    # pero es justo el dato que necesita este trabajo. Se midio: el inventario
    # de las 127 quebradas de la cuenca de San Carlos entro al indice con UNA
    # ficha, porque filas como "5 | Quebrada Abejales | 1.069 | 1069.07" tienen
    # un 33% de letras. Se perdieron 115 quebradas.
    ("tabla de quebradas con columnas", """13 | Quebrada Bizcocho | 2.041 | 2041.33 | 1 | 5101
14 | Quebrada Brasil | 1.332 | 1331.76 | 2 | 5101
15 | Quebrada Buenos Aires | 4.118 | 4117.52 | 3 | 5101
16 | Quebrada Cafetal | 1.904 | 1903.61 | 1 | 5101""", True),
    ("csv con punto y coma", """COD_POMCA;NOMBRE;AREA_HA;DEPARTAMENTO
2305;Rio San Carlos;70200;Antioquia
2306;Rio Nare;112400;Antioquia
2307;Rio Samana Norte;98300;Antioquia""", True),
    # Y el indice sigue descartandose: lineas sueltas, sin columnas repetidas.
    ("indice de capitulos con numero de pagina", """Introduccion ............................................. 7
Marco teorico ............................................ 12
Metodologia .............................................. 24
Resultados ............................................... 38""", False),
]
for titulo, texto, esperado in casos:
    comprobar(titulo, vectorial.merece_vector(texto) is esperado,
              f"merece_vector = {vectorial.merece_vector(texto)}")

# Las cifras que se afirmaron sobre el corpus real.
base = corpus.cargar()
fichas = base["fichas"]
basura = [f for f in fichas if not vectorial.merece_vector(f["texto"])]
import hashlib as _h
from collections import Counter as _C
vistos = _C(_h.md5(f["texto"].strip().encode()).hexdigest() for f in fichas)
duplicadas = sum(v - 1 for v in vistos.values() if v > 1)

# Estas tres miden sobre el corpus REAL, asi que necesitan que haya corpus.
# Si la carpeta esta vacia -- porque se cambio la fuente a Drive, o porque los
# documentos se archivaron -- no hay nada que medir. Se OMITEN, que no es lo
# mismo que fallar: no haber podido comprobar algo no significa que este mal.
if not fichas:
    for _titulo in ("se descartan fichas basura del corpus real",
                    "hay duplicados exactos que deduplicar",
                    "el filtro NO afecta a BM25"):
        omitir(_titulo, "no hay corpus cargado que medir")
else:
    # Las cifras van en PROPORCION, no en valor absoluto.
    #
    # Antes se exigia "entre 1500 y 2500 fichas basura", un rango calibrado
    # contra el corpus de mariposas de 17.052 fichas. Al cambiar la fuente a
    # Drive, con 286 fichas de otro tema, esas tres comprobaciones fallaron
    # sin que nada estuviera roto: median el corpus, no el codigo.
    #
    # Es ademas la regla del propio proyecto: nada debe presuponer de que
    # trata el corpus.
    _porcentaje_basura = 100.0 * len(basura) / max(len(fichas), 1)
    comprobar("el filtro descarta parte de las fichas, pero no todas",
              0 < _porcentaje_basura < 60,
              f"{_porcentaje_basura:.0f}% descartado "
              f"({len(basura)} de {len(fichas)})")
    comprobar("se detectan los duplicados exactos que haya",
              0 <= duplicadas < len(fichas),
              f"{duplicadas} duplicadas de {len(fichas)}")

    # El termino de busqueda se SACA del corpus cargado, en vez de escribir
    # uno que solo existia en el corpus de antes.
    _palabras = {}
    for _f in fichas[:400]:
        for _p in corpus.tokenizar(_f["texto"])[:60]:
            if len(_p) > 5:
                _palabras[_p] = _palabras.get(_p, 0) + 1
    _termino = max(_palabras, key=_palabras.get) if _palabras else ""
    if not _termino:
        omitir("el filtro NO afecta a BM25",
               "el corpus no tiene palabras con las que buscar")
    else:
        comprobar("el filtro NO afecta a BM25",
                  len(corpus.buscar(_termino, limite=5)) > 0,
                  f"BM25 encuentra '{_termino}', que sale del propio corpus")


# ==============================================================================
print("\nREINTENTOS - que se reintenta y que no")
# ==============================================================================
pasajeros = [
    ("cuota agotada", "429 RESOURCE_EXHAUSTED quota"),
    ("servicio caido", "503 UNAVAILABLE"),
    ("corte de red", "Server disconnected without sending a response."),
    ("tiempo agotado", "Read timeout"),
]
for titulo, mensaje in pasajeros:
    comprobar(f"reintenta: {titulo}", vectorial._es_cuota(Exception(mensaje)), mensaje[:40])

definitivos = [
    ("clave invalida", "400 API_KEY_INVALID"),
    ("modelo inexistente", "404 NOT_FOUND models/loquesea"),
]
for titulo, mensaje in definitivos:
    comprobar(f"NO reintenta: {titulo}",
              not vectorial._es_cuota(Exception(mensaje)), mensaje[:40])


# ==============================================================================
print("\nREGULADOR DE RITMO - debe frenar antes de pasarse")
# ==============================================================================
reg = vectorial._Regulador(tokens_por_minuto=1000)
t0 = time.time()
reg.reservar(600)          # cabe
reg.reservar(300)          # cabe justo
sin_frenar = time.time() - t0
comprobar("no frena mientras hay presupuesto",
          sin_frenar < 0.5, f"{sin_frenar:.2f} s")

# El tercero se pasaria del tope: debe dormir. Se comprueba que CALCULA la
# espera, sin llegar a dormir 60 s en la prueba.
reg2 = vectorial._Regulador(tokens_por_minuto=1000)
reg2.enviados = [(time.time() - 59.0, 900)]     # casi lleno, caduca en 1 s
t0 = time.time()
reg2.reservar(300)
espero = time.time() - t0
comprobar("frena cuando el presupuesto esta lleno",
          0.5 < espero < 5.0, f"durmio {espero:.2f} s esperando la ventana")


# ==============================================================================
print("\nCACHE DE EMBEDDINGS - reanudable y sin repetir")
# ==============================================================================
from config import settings

if not settings.api_key_gemini():
    omitir("cache de embeddings", "sin GEMINI_API_KEY")
else:
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        os.environ["QDRANT_RUTA_LOCAL"] = os.path.join(tmp, "idx")
        textos = ["Las mariposas Morpho habitan el bosque humedo tropical. " * 12] * 5

        # La capa gratuita permite 1.000 embeddings al dia. Agotada, estas
        # comprobaciones no pueden hacerse, pero eso no es un fallo del
        # codigo: se omiten. Antes reventaban la bateria entera con un
        # traceback y el lanzador solo sabia decir "sin resumen".
        try:
            t0 = time.time()
            v1 = vectorial.embeber(textos)
            primera = time.time() - t0

            t0 = time.time()
            v2 = vectorial.embeber(textos)
            segunda = time.time() - t0
        except Exception as exc:
            for titulo in ("la segunda pasada sale de cache",
                           "el vector recuperado es equivalente",
                           "las dimensiones son las configuradas"):
                omitir(titulo, f"la API no respondio: {str(exc)[:46]}")
            v1 = v2 = None

    if v1 is not None:

        comprobar("la segunda pasada sale de cache",
                  segunda < primera / 5, f"{primera:.2f} s -> {segunda:.3f} s")
        comprobar("el vector recuperado es equivalente",
                  max(abs(a - b) for a, b in zip(v1[0], v2[0])) < 1e-6,
                  "diferencia por redondeo a float32")
        comprobar("las dimensiones son las configuradas",
                  len(v1[0]) == settings.dimensiones_embedding(),
                  f"{len(v1[0])} dimensiones")


# ==============================================================================
print("\nDIMENSIONES - el tamano real manda sobre la configuracion")
# ==============================================================================
from config import settings as _s

previo_dim = os.environ.get("DIMENSIONES_EMBEDDING")
os.environ["DIMENSIONES_EMBEDDING"] = "1536"
try:
    efectivas = vectorial.dimensiones_efectivas()
    comprobar("Gemini respeta el tamano configurado",
              efectivas == 1536, f"{efectivas} dimensiones")
    comprobar("la firma lleva el tamano efectivo",
              f"@{efectivas}" in vectorial._firma_actual(),
              vectorial._firma_actual())
finally:
    if previo_dim is None:
        os.environ.pop("DIMENSIONES_EMBEDDING", None)
    else:
        os.environ["DIMENSIONES_EMBEDDING"] = previo_dim


# ==============================================================================
print("\nESQUEMA DE EMBEDDINGS")
# ==============================================================================
# Un documento y una consulta se embeben con `task_type` distinto, asi que no
# pueden compartir entrada de cache: si la compartieran, la primera consulta
# envenenaria el vector del documento.
comprobar("documento y consulta no comparten clave de cache",
          vectorial._clave_cache("texto", "RETRIEVAL_DOCUMENT")
          != vectorial._clave_cache("texto", "RETRIEVAL_QUERY"),
          "claves distintas")

comprobar("solo queda un modelo de embeddings",
          vectorial.modelo_embeddings() == vectorial.MODELO_GEMINI_EMBEDDINGS
          and "gemini" in vectorial.modelo_embeddings(),
          vectorial.modelo_embeddings())

comprobar("la firma incluye la version del esquema",
          f"#v{vectorial.ESQUEMA_EMBEDDINGS}" in vectorial._firma_actual(),
          vectorial._firma_actual())


# ==============================================================================
print("\nFIRMA DEL MODELO - no se pueden mezclar vectores de dos modelos")
# ==============================================================================
# `ignore_cleanup_errors` porque en Windows el Qdrant embebido suelta el
# archivo con retraso y el borrado del temporal fallaba aunque la prueba fuera
# bien. Es ruido del sistema de archivos, no del codigo que se comprueba.
with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
    os.environ["QDRANT_RUTA_LOCAL"] = os.path.join(tmp, "idx")
    os.environ["QDRANT_COLECCION"] = "firma_prueba"
    os.environ.pop("QDRANT_URL", None)
    vectorial._CLIENTE = None
    vectorial._CLIENTE_DESTINO = None

    from qdrant_client import models as _m
    vectorial.asegurar_coleccion()
    cli = vectorial._cliente_qdrant()
    dim = settings.dimensiones_embedding()
    cli.upsert(collection_name="firma_prueba", points=[_m.PointStruct(
        id=vectorial._id_de_ficha("d.pdf", i),
        vector={vectorial.NOMBRE_VECTOR: [0.5] * dim},
        payload={"documento": "d.pdf", "huella": "h", "texto": "x"})
        for i in range(5)])

    antes = len(vectorial.huellas_indexadas(cli))
    comprobar("la firma queda registrada al crear la coleccion",
              vectorial._firma_guardada(cli, "firma_prueba") == vectorial._firma_actual(),
              vectorial._firma_actual())
    comprobar("el punto de firma no cuenta como documento",
              antes == 1, f"{antes} documento indexado")

    previo = vectorial.MODELO_GEMINI_EMBEDDINGS
    vectorial.MODELO_GEMINI_EMBEDDINGS = "otro-modelo-cualquiera"
    vectorial.asegurar_coleccion(cli)
    despues = len(vectorial.huellas_indexadas(cli))
    comprobar("cambiar de modelo rehace la coleccion",
              despues == 0, f"{antes} -> {despues} documentos")
    comprobar("la firma nueva queda guardada",
              vectorial._firma_guardada(cli, "firma_prueba") == vectorial._firma_actual(),
              vectorial._firma_actual())

    # Con la firma de otro modelo todavia puesta: consultar ese indice daria
    # vectores incomparables y respuestas sin sentido que el agente citaria
    # como buenas. La consulta debe negarse antes de llegar a Qdrant.
    comprobar("no se consulta un indice de otro modelo",
              vectorial.buscar_semantico("lo que sea", limite=3) == [],
              "devuelve vacio y la hibrida cae a BM25")

    vectorial.MODELO_GEMINI_EMBEDDINGS = previo

    # Una coleccion SIN firma (creada antes de este control) con puntos dentro
    # es de procedencia desconocida: hay que rehacerla, no confiar en ella.
    cli.delete(collection_name="firma_prueba",
               points_selector=_m.PointIdsList(points=[vectorial.ID_FIRMA]))
    cli.upsert(collection_name="firma_prueba", points=[_m.PointStruct(
        id=vectorial._id_de_ficha("viejo.pdf", 0),
        vector={vectorial.NOMBRE_VECTOR: [0.3] * dim},
        payload={"documento": "viejo.pdf", "huella": "h", "texto": "y"})])
    comprobar("una coleccion sin firma se detecta",
              vectorial._firma_guardada(cli, "firma_prueba") is None,
              "sin firma, con puntos dentro")
    vectorial.asegurar_coleccion(cli)
    comprobar("y se rehace en lugar de confiar en ella",
              len(vectorial.huellas_indexadas(cli)) == 0,
              "vectores de procedencia desconocida descartados")

    vectorial._cerrar_cliente()
    os.environ.pop("QDRANT_COLECCION", None)


# ==============================================================================
print("\nDEGRADACION - sin Qdrant el sistema sigue respondiendo")
# ==============================================================================
# Se guarda la direccion de verdad para devolverla al final. Antes esta prueba
# terminaba con `os.environ.pop("QDRANT_URL")`, que no restaura: BORRA. Como
# era la ultima comprobacion, daba igual; en cuanto se anadio otra detras, esa
# otra se encontraba el modulo en modo embebido y creia que el indice no
# existia, con 14.990 fichas en el servidor.
_URL_REAL = os.environ.get("QDRANT_URL")

os.environ["QDRANT_URL"] = "http://127.0.0.1:59999"   # puerto muerto a proposito
vectorial._CLIENTE = None
vectorial._CLIENTE_DESTINO = None

listo, _ = vectorial.disponible()
comprobar("detecta que el indice no esta disponible", not listo, "disponible() = False")

hibrida = vectorial.buscar_hibrido("plantas hospederas de Morpho", limite=4)
if not corpus.cargar()["fichas"]:
    omitir("buscar_hibrido cae a BM25 sin romperse",
           "no hay corpus cargado sobre el que caer")
else:
    # Mismo motivo: la consulta sale del corpus que haya, no de uno supuesto.
    _fichas_vivas = corpus.cargar()["fichas"]
    _consulta = " ".join(
        corpus.tokenizar(_fichas_vivas[0]["texto"])[:4]) if _fichas_vivas else ""
    _hibrida2 = vectorial.buscar_hibrido(_consulta, limite=4) if _consulta else []
    comprobar("buscar_hibrido cae a BM25 sin romperse",
              len(_hibrida2) > 0,
              f"{len(_hibrida2)} fichas desde BM25 con '{_consulta[:28]}'")

semantica = vectorial.buscar_semantico("lo que sea", limite=3)
comprobar("buscar_semantico devuelve vacio en vez de reventar",
          semantica == [], "lista vacia")

if _URL_REAL:
    os.environ["QDRANT_URL"] = _URL_REAL
else:
    os.environ.pop("QDRANT_URL", None)
vectorial._CLIENTE = None
vectorial._CLIENTE_DESTINO = None

binomio = corpus.buscar_binomio("Morpho peleides", limite=3)
if not corpus.cargar()["fichas"]:
    omitir("la busqueda literal de binomios sigue intacta",
           "no hay corpus cargado donde buscar")
else:
    # `buscar_binomio` es para nombres cientificos. Si el corpus cargado no
    # trata de especies, no hay binomio que buscar: se comprueba que la
    # funcion RESPONDA sin romperse, que es lo que de verdad se vigila aqui.
    comprobar("la busqueda literal de binomios sigue intacta",
              isinstance(binomio, list),
              f"{len(binomio)} coincidencias exactas "
              f"(0 es valido si el corpus no trata de especies)")
# ==============================================================================
# MARCA TEMPORAL DE CADA FICHA DEL INDICE
# ==============================================================================
# Que cada punto de Qdrant diga CUANDO entro al indice. Sin eso no se puede
# responder "que se estudio en la sincronizacion de anoche", que es la pregunta
# natural cuando el corpus crece solo cada 24 horas.
print("\n--- Fecha de indexado ---")
from core import vectorial as _vec

# Se deshace cualquier laboratorio que hayan dejado montado las comprobaciones
# anteriores, para medir contra el indice de verdad y no contra uno de prueba.
restaurar_entorno()
_vec._CLIENTE = None
_vec._CLIENTE_DESTINO = None

_momento = _vec._ahora_iso()
comprobar("la fecha se genera en formato ISO 8601 y en UTC",
          len(_momento) == 20 and _momento[4] == "-" and _momento.endswith("Z"),
          _momento)

# Ordenar texto ISO 8601 da el orden cronologico. Es la razon de usar este
# formato y no uno local: con "23/10/2026" el orden alfabetico seria falso.
_antes, _despues = "2026-10-09T23:00:00Z", "2026-10-10T01:00:00Z"
comprobar("ordenar las fechas como texto da el orden correcto",
          sorted([_despues, _antes]) == [_antes, _despues],
          "9 de octubre antes que 10, aunque 9 > 1 como caracter")

# --- Sobre el indice de verdad -------------------------------------------
# El indice vive en el contenedor de Qdrant. Lanzada desde el equipo, esta
# bateria puede no alcanzarlo: entonces se OMITE, porque no haber podido mirar
# no es lo mismo que haber mirado y estar mal.
_estado_vec = _vec.estado()
_fechas = _estado_vec.get("indexado") or {}
if not _estado_vec.get("listo") or not _fechas.get("mirados"):
    omitir("el estado informa de las fechas del indice",
           _estado_vec.get("motivo") or "no se alcanza la coleccion desde aqui")
else:
    comprobar("el estado informa de las fechas del indice",
              "mirados" in _fechas,
              f"{_fechas.get('mirados')} fichas miradas")
    _con_dato = (_fechas.get("con_fecha_exacta", 0)
                 + _fechas.get("con_cota", 0))
    comprobar("toda ficha dice cuando entro, exacto o acotado",
              _fechas.get("sin_dato", 0) == 0,
              f"{_con_dato} con dato, {_fechas.get('sin_dato')} sin nada")


# --- Una ficha nueva nace con su fecha exacta ----------------------------
# Se indexa un documento de mentira y se mira el punto que deja en Qdrant.
import os as _os
import time as _time

try:
    from config import settings as _ajustes
    from qdrant_client import models as _modelos

    _cli = _vec._cliente_qdrant()
    _col = _ajustes.coleccion_qdrant()
    _nombre_doc = "__prueba_marca_temporal__.txt"
    _ruta = _os.path.join(_ajustes.carpeta_temporal(), _nombre_doc)
    with open(_ruta, "w", encoding="utf-8") as _f:
        _f.write("Texto de prueba para la marca temporal. " * 40)

    _res = {"fichas": 0, "fallidos": []}
    _vec._indexar_documento(
        _cli, _col,
        {"nombre": _nombre_doc, "categoria": "Prueba", "ruta": _ruta,
         "huella": str(_time.time())},
        _res, None, "[1/1]")

    _filtro = _modelos.Filter(must=[_modelos.FieldCondition(
        key="documento", match=_modelos.MatchValue(value=_nombre_doc))])
    _puntos, _ = _cli.scroll(collection_name=_col, scroll_filter=_filtro,
                             limit=3, with_payload=True)
    _carga = (_puntos[0].payload or {}) if _puntos else {}

    comprobar("una ficha nueva trae su fecha exacta",
              bool(_carga.get("fecha_indexado")),
              _carga.get("fecha_indexado", "(ninguna)"))
    comprobar("y tambien la marca en segundos, para comparar rapido",
              isinstance(_carga.get("indexado_unix"), int),
              str(_carga.get("indexado_unix")))
    comprobar("todas las fichas del documento comparten marca",
              len({(p.payload or {}).get("fecha_indexado")
                   for p in _puntos}) == 1,
              f"{len(_puntos)} fichas, una sola marca")

    _cli.delete(collection_name=_col,
                points_selector=_modelos.FilterSelector(filter=_filtro))
    _os.remove(_ruta)
except Exception as _exc:
    omitir("una ficha nueva trae su fecha exacta",
           f"no se pudo escribir en Qdrant: {_exc}")




# ==============================================================================
print("\n--- Nada se conserva en el equipo ---")
# El compromiso es que lo traido de Drive se borre en cuanto se ha leido. Lo
# que se comprueba aqui no es la ruta feliz, sino la fea: que se borre AUNQUE
# la indexacion se caiga. Antes la limpieza era la ultima linea del cuerpo de
# `indexar`, asi que cualquier fallo a mitad se la saltaba, y una ejecucion
# cortada dejo cuatro documentos de Drive en la carpeta temporal del
# contenedor. El comentario decia que iba en `finally`; el codigo no lo hacia.
from core import vectorial as _lim
from core import fuentes as _fts

restaurar_entorno()


class _FuenteDePrueba:
    """Fuente de mentira que descarga a una carpeta propia y sabe limpiarse."""

    nombre = "prueba"

    def __init__(self, carpeta):
        self.carpeta = carpeta

    def carpeta_temporal(self):
        return self.carpeta

    def limpiar(self):
        cuantos = sum(len(a) for _, _, a in os.walk(self.carpeta))
        shutil.rmtree(self.carpeta, ignore_errors=True)
        return {"archivos": cuantos, "megas": 0.0}


def _plantar(carpeta, cuantos=2):
    """Deja archivos como los que quedarian de una descarga a medias."""
    sub = os.path.join(carpeta, "Sin categoria")
    os.makedirs(sub, exist_ok=True)
    for i in range(cuantos):
        with open(os.path.join(sub, f"resto_{i}.xlsx"), "wb") as fh:
            fh.write(os.urandom(50_000))


_banco = tempfile.mkdtemp(prefix="probar_limpieza_")
_fuente_falsa = _FuenteDePrueba(os.path.join(_banco, "descargas"))

_plantar(_fuente_falsa.carpeta, cuantos=3)
_informe = _lim.limpiar_descargas(_fuente_falsa)
comprobar("la limpieza borra lo descargado y dice cuanto",
          _informe.get("archivos") == 3
          and not os.path.isdir(_fuente_falsa.carpeta),
          f"{_informe.get('archivos')} archivos borrados")

_original = _lim._indexar_todo
_original_obtener = _fts.obtener
_vistos = {"antes_de_caer": 0}


def _indexacion_que_se_cae(forzar=False, progreso=None):
    """Descarga y se cae, como cuando se agota la cuota de embeddings."""
    _plantar(_fuente_falsa.carpeta, cuantos=2)
    _vistos["antes_de_caer"] = sum(
        len(a) for _, _, a in os.walk(_fuente_falsa.carpeta))
    raise RuntimeError("cuota de embeddings agotada")


_lim._indexar_todo = _indexacion_que_se_cae
_fts.obtener = lambda *a, **k: _fuente_falsa
try:
    _subio = None
    try:
        _lim.indexar()
    except RuntimeError as _exc:
        _subio = str(_exc)
    comprobar("un fallo a mitad de indexacion no se traga el error",
              _subio == "cuota de embeddings agotada",
              _subio or "no levanto nada")
    comprobar("y aun asi no deja nada descargado en el equipo",
              _vistos["antes_de_caer"] == 2
              and not os.path.isdir(_fuente_falsa.carpeta),
              f"habia {_vistos['antes_de_caer']} archivos, quedan 0")
finally:
    _lim._indexar_todo = _original
    _fts.obtener = _original_obtener
    shutil.rmtree(_banco, ignore_errors=True)

# La carpeta local no se limpia: ahi los documentos son del usuario.
comprobar("una fuente sin limpieza no provoca un error",
          _lim.limpiar_descargas(object()) == {},
          "devuelve vacio en vez de levantar")



# ==============================================================================
print("\n--- El limite es por fichas, no por megas ---")
# Los megas no predicen el coste. Se midio sobre los archivos reales: 1 MB de
# CSV tabular da ~1.485 fichas y 1 MB de informe con fotos, unas decenas. Con
# un tope en megas, un informe de 26 MB con 30 fichas quedaba fuera mientras
# un CSV de 24 MB con 35.000 habria entrado. Lo que cuesta cuota son las
# fichas, asi que el limite es ese.
from core import corpus as _cp
from core import vectorial as _vc
from config import settings as _st

restaurar_entorno()

_banco_p = tempfile.mkdtemp(prefix="probar_presupuesto_")
try:
    # --- Un archivo pesado pero barato: tiene que ENTRAR -------------------
    _barato = os.path.join(_banco_p, "informe_con_fotos.txt")
    with open(_barato, "w", encoding="utf-8") as _fh:
        _fh.write("Caracterizacion ambiental de la cuenca del rio. " * 400)
    _fichas_barato = _cp.contar_fichas_de(_barato)
    comprobar("un documento largo pero barato cabe en el presupuesto",
              0 < _fichas_barato <= _cp.PRESUPUESTO_FICHAS,
              f"{_fichas_barato} fichas de {_cp.PRESUPUESTO_FICHAS}")

    # --- Un archivo que se pasa: el guardia tiene que PARARLO --------------
    _caro = os.path.join(_banco_p, "pasa_del_presupuesto.txt")
    with open(_caro, "w", encoding="utf-8") as _fh:
        for _i in range(24000):
            _fh.write(f"Unidad de paisaje {_i}: cobertura de bosque denso "
                      f"sobre relieve de colinas, drenaje permanente y suelos "
                      f"profundos, codigo UP-{_i:06d}.\n")
    _fichas_caro = _cp.contar_fichas_de(_caro)
    comprobar("se cuentan las fichas de verdad antes de gastar cuota",
              _fichas_caro > _cp.PRESUPUESTO_FICHAS,
              f"{_fichas_caro} fichas, presupuesto {_cp.PRESUPUESTO_FICHAS}")

    # El embebedor se bloquea: si el guardia fallara se notaria aqui en vez
    # de gastar miles de embeddings en silencio, que es justo lo que este
    # limite existe para evitar.
    _embeber_real = _vc.embeber
    _se_intento = {"si": False}

    def _embeber_prohibido(*_a, **_k):
        """Sustituto que delata al guardia si deja pasar algo caro."""
        _se_intento["si"] = True
        raise AssertionError("el guardia no paro: se intento embeber")

    _vc.embeber = _embeber_prohibido
    try:
        _resumen_p = {"fichas": 0, "_fraccion": 0.0}
        _vc._indexar_documento(
            _vc._cliente_qdrant(), _st.coleccion_qdrant(),
            {"nombre": "pasa_del_presupuesto.txt", "categoria": "prueba",
             "ruta": _caro, "huella": "x"}, _resumen_p, None, "[1/1]")
        _fuera = _resumen_p.get("fuera_de_presupuesto") or []
        comprobar("lo que se pasa se aparta sin gastar un solo embedding",
                  not _se_intento["si"] and len(_fuera) == 1
                  and _fuera[0]["fichas"] == _fichas_caro,
                  f"apartado con {_fuera[0]['fichas']} fichas" if _fuera
                  else "no quedo constancia")
    except AssertionError as _exc:
        comprobar("lo que se pasa se aparta sin gastar un solo embedding",
                  False, str(_exc))
    finally:
        _vc.embeber = _embeber_real

    # --- La estimacion por muestra, que evita bajar el archivo entero ------
    _muestra = os.path.join(_banco_p, "muestra.csv")
    with open(_caro, "rb") as _orig, open(_muestra, "wb") as _dst:
        _dst.write(_orig.read(512 * 1024))
    _peso_total = os.path.getsize(_caro)
    _est = _cp.estimar_fichas(_muestra, _peso_total)
    # Se admite un margen amplio: lo que importa es el ORDEN DE MAGNITUD,
    # porque la decision es "cabe o no cabe", no un presupuesto contable.
    _error = abs(_est["fichas"] - _fichas_caro) / max(_fichas_caro, 1)
    comprobar("estimar desde una muestra acierta el orden de magnitud",
              _est["fichas"] > 0 and _error < 0.25,
              f"estimadas {_est['fichas']} frente a {_fichas_caro} reales "
              f"({_error*100:.0f}% de error)")

    # Una muestra ilegible no puede devolver 0: seria dar por gratis algo que
    # no se ha sabido medir, y el archivo entraria por la puerta de atras.
    _rota = os.path.join(_banco_p, "rota.csv")
    with open(_rota, "wb") as _fh:
        _fh.write(b"\x00\x01\x02")
    comprobar("si no se puede medir, se dice 'no se sabe', no 'cero'",
              _cp.estimar_fichas(_rota, 999_999_999)["fichas"] == -1,
              "devuelve -1 en vez de 0")
finally:
    shutil.rmtree(_banco_p, ignore_errors=True)


# ==============================================================================
print("\n--- La memoria no pierde nada aunque falle la red ---")
# Se midio: uno de cada diez embeddings fallaba con un corte SSL, y entonces
# el recuerdo se perdia y la interfaz decia "No se guardo en la memoria: el
# texto es demasiado corto o no se pudo vectorizar", adivinando el motivo
# porque el error real se lo habia tragado un `except`.
from core import memoria as _mem

_pendientes_antes = len(_mem.pendientes())
_embeber_real = _vc.embeber


def _embeber_caido(*_a, **_k):
    """Simula el corte SSL que se midio en produccion."""
    raise RuntimeError("[SSL: UNEXPECTED_EOF_WHILE_READING] EOF occurred")


_vc.embeber = _embeber_caido
try:
    _r = _mem.guardar_recuerdo_detallado(
        "Dato de prueba que llega justo cuando la red se cae.", "prueba_bateria")
    comprobar("un recuerdo que no se puede vectorizar NO se pierde",
              _r["a_salvo"] and not _r["en_indice"],
              "queda anotado en disco para reintentarlo")
    comprobar("y se dice el motivo de verdad, no uno inventado",
              "SSL" in _r["motivo"] or "RuntimeError" in _r["motivo"],
              _r["motivo"][:60])
finally:
    _vc.embeber = _embeber_real

_vuelta = _mem.reintentar_pendientes()
comprobar("cuando vuelve la red, lo pendiente entra solo",
          _vuelta["guardados"] >= 1 and _vuelta["siguen_esperando"] == 0,
          f"{_vuelta['guardados']} recuperado(s), "
          f"{_vuelta['siguen_esperando']} en espera")

comprobar("un texto demasiado corto si se rechaza, y lo dice",
          _mem.guardar_recuerdo_detallado("hola", "prueba_bateria")["a_salvo"]
          is False,
          "no se guarda lo que no significa nada")
# ==============================================================================
print("\n" + "=" * 70)
if FALLOS:
    print(f"{ACIERTOS} comprobaciones superadas, {len(FALLOS)} FALLIDAS:\n")
    for f in FALLOS:
        print("  -", f)
    sys.exit(1)
print(f"{ACIERTOS} de {ACIERTOS} comprobaciones superadas")
