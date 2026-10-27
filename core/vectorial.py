"""
Indice vectorial del corpus sobre Qdrant, con embeddings de Gemini.
===============================================================================

QUE ANADE Y QUE NO SUSTITUYE
----------------------------
`core/corpus.py` busca por PALABRAS (BM25) y por COINCIDENCIA LITERAL. Este
modulo anade una tercera forma de buscar: por SIGNIFICADO. Las tres conviven,
y el orden importa:

    buscar_binomio()   -> literal. NO SE TOCA y no pasa por aqui.
    buscar()           -> BM25. Sigue igual.
    buscar_hibrido()   -> mezcla BM25 y vectores. Es el camino nuevo.

POR QUE LA LITERAL NO PASA POR AQUI
-----------------------------------
Un vector no distingue `Polites otho` de `Polites vibex`: son dos nombres de
mariposa casi identicos y el modelo los coloca practicamente en el mismo punto
del espacio. Ya paso una vez en este proyecto, con la busqueda aproximada: el
agente cito una fuente que nunca habia nombrado esa especie. Por eso la
busqueda de binomios sigue siendo literal, palabra por palabra y en orden, y
los vectores solo se usan para preguntas en prosa.

COMO SE GUARDA
--------------
Un punto de Qdrant por ficha. En el `payload` viaja todo lo que hace falta para
citar (documento, categoria, pagina) y la `huella` del documento del que salio.
Esa huella es lo que permite reindexar SOLO lo que cambio: si el documento
sigue igual, sus fichas no se vuelven a embeber.

SIN CONTENEDOR TAMBIEN FUNCIONA
-------------------------------
Si `QDRANT_URL` esta vacia se usa el modo embebido, que guarda en una carpeta
del disco. Asi el proyecto arranca sin Docker para la practica, y en despliegue
basta apuntar la variable al contenedor.
"""

import atexit
import hashlib
import random
import time
import uuid

from config import settings
from core import corpus, fuentes


#: Espacios de reintento ante un 429 de cuota, en segundos. Son la red de
#: seguridad: con el regulador de ritmo de mas abajo casi nunca se usan.
ESPERAS_REINTENTO = [2, 5, 15, 40, 70]

#: Tokens por minuto que se asume disponibles.
#:
#: MEDIDO, NO SUPUESTO: enviando lotes de 32 fichas de ~900 caracteres sin
#: pausa, la API acepta 3 y devuelve 429 en el cuarto. Eso son unos 21.000
#: tokens antes de cortar, asi que el limite real de la capa gratuita ronda esa
#: cifra por minuto. Se deja algo por debajo para no rozar el techo.
#:
#: El cuello NO son las peticiones sino los tokens: por eso agrandar el lote no
#: acelera nada, solo hace que el 429 llegue antes.
TOKENS_POR_MINUTO = 20000

#: Estimacion de tokens a partir de caracteres. Para texto en espanol, cuatro
#: caracteres por token se queda corto por poco, que es el lado seguro.
CARACTERES_POR_TOKEN = 4

#: Modelo de embeddings de Gemini. Con cuota disponible y dimension 768.
MODELO_GEMINI_EMBEDDINGS = "gemini-embedding-2"


def modelo_embeddings() -> str:
    """
    Nombre del modelo que genera los vectores.

    Es el unico que hay. Se mantiene como funcion, y no como constante suelta,
    porque la firma de la coleccion y la clave de cache la llaman y asi un
    cambio de modelo se propaga solo.

    Returns:
        El identificador del modelo en uso.
    """
    return MODELO_GEMINI_EMBEDDINGS


#: Compatibilidad: codigo y pruebas antiguas esperaban esta constante.
MODELO_EMBEDDINGS = MODELO_GEMINI_EMBEDDINGS

#: Nombre del vector dentro de la coleccion. Qdrant admite varios por punto;
#: se nombra para poder anadir otro mas adelante sin migrar lo existente.
NOMBRE_VECTOR = "texto"

#: Version de COMO se construye el vector, al margen del modelo.
#:
#: Hay cambios que no tocan el modelo ni el tamano y aun asi producen vectores
#: distintos: anadir los prefijos `search_document:` y `search_query:` fue uno.
#: Sin este numero, la firma seguia igual (`modelo@768`) y ni la
#: coleccion se rehacia ni la cache se invalidaba, de modo que convivian
#: vectores de dos esquemas sin que nada avisara.
#:
#: Subir este numero obliga a reindexar. Hay que subirlo siempre que cambie el
#: texto que se envia al modelo.
ESQUEMA_EMBEDDINGS = 2


# ==============================================================================
# CLIENTES
# ==============================================================================

#: Cliente unico del proceso. NO es una optimizacion opcional.
#:
#: En modo embebido, `QdrantClient(path=...)` toma el control exclusivo de la
#: carpeta. Abrir un cliente por llamada hacia que `indexar()` escribiera en un
#: sitio y `estado()` leyera de otro: la indexacion decia "450 fichas" y la
#: consulta devolvia cero. Con servidor el efecto es menos grave pero reconectar
#: en cada busqueda tampoco tiene sentido.
_CLIENTE = None
_CLIENTE_DESTINO = None


def _cliente_qdrant():
    """
    Devuelve la conexion con Qdrant, sea servidor o embebido.

    La conexion se abre una sola vez por proceso y se reutiliza. Si la
    configuracion cambia en caliente (por ejemplo al pasar de embebido a
    contenedor), se detecta y se vuelve a abrir.

    Returns:
        Un `QdrantClient` listo para usar.

    Raises:
        RuntimeError: Si la libreria no esta instalada, con la orden para
            instalarla en lugar de un ImportError pelado.
    """
    global _CLIENTE, _CLIENTE_DESTINO

    try:
        from qdrant_client import QdrantClient
    except ImportError as exc:
        raise RuntimeError(
            "Falta la libreria 'qdrant-client'. Instalala con: "
            "pip install qdrant-client"
        ) from exc

    url = settings.qdrant_url()
    destino = url or f"local:{settings.qdrant_ruta_local()}"

    if _CLIENTE is not None and _CLIENTE_DESTINO == destino:
        return _CLIENTE

    if _CLIENTE is not None:
        try:
            _CLIENTE.close()
        except Exception:
            pass

    if url:
        clave = settings.qdrant_api_key()
        _CLIENTE = QdrantClient(url=url, api_key=clave or None, timeout=60)
    else:
        _CLIENTE = QdrantClient(path=settings.qdrant_ruta_local())
    _CLIENTE_DESTINO = destino
    return _CLIENTE


def en_servidor() -> bool:
    """
    Indica si Qdrant corre como servicio o embebido en el proceso.

    Returns:
        True si hay `QDRANT_URL` configurada.
    """
    return bool(settings.qdrant_url())


@atexit.register
def _cerrar_cliente() -> None:
    """
    Cierra Qdrant antes de que Python se apague.

    Sin esto, el recolector de basura llama al cierre del cliente cuando el
    interprete ya desmonto los modulos, y la consola escupe un ImportError de
    'sys.meta_path is None' que parece un fallo del programa sin serlo.
    """
    global _CLIENTE
    if _CLIENTE is not None:
        try:
            _CLIENTE.close()
        except Exception:
            pass
        _CLIENTE = None


def _cliente_gemini():
    """
    Abre el cliente de Gemini para pedir embeddings.

    Returns:
        Un cliente de `google.genai`.

    Raises:
        RuntimeError: Si no hay clave configurada.
    """
    clave = settings.api_key_gemini()
    if not clave:
        raise RuntimeError(
            "Falta GEMINI_API_KEY en el .env. Los embeddings los genera "
            "Gemini, asi que sin clave no se puede construir el indice."
        )
    from google import genai
    return genai.Client(api_key=clave)


# ==============================================================================
# EMBEDDINGS
# ==============================================================================

# ==============================================================================
# REGULADOR DE RITMO
# ==============================================================================

class _Regulador:
    """
    Frena antes de chocar con la cuota, en vez de chocar y esperar el castigo.

    POR QUE HACE FALTA
    ------------------
    Reaccionar al 429 sale carisimo. En la primera prueba, indexar 450 fichas
    tardo 247 segundos, y la API solo estuvo trabajando unos 10: el resto fue
    esperando los reintentos de 40 y 70 segundos. Llevando la cuenta de los
    tokens enviados en el ultimo minuto y durmiendo lo justo ANTES de pasarse,
    ese castigo no llega a producirse.
    """

    def __init__(self, tokens_por_minuto: int = TOKENS_POR_MINUTO):
        """
        Args:
            tokens_por_minuto: Presupuesto por ventana de 60 segundos.
        """
        self.tope = max(1000, int(tokens_por_minuto))
        self.enviados = []          # pares (instante, tokens)

    def _limpiar(self, ahora: float) -> None:
        """
        Descarta lo que ya salio de la ventana de un minuto.

        Args:
            ahora: Momento actual, en segundos desde la epoca.
        """
        self.enviados = [(t, n) for t, n in self.enviados if ahora - t < 60.0]

    def reservar(self, tokens: int) -> float:
        """
        Espera lo necesario para poder enviar `tokens` sin pasarse.

        Args:
            tokens: Tokens del lote que se va a enviar.

        Returns:
            Segundos que se durmio, para poder informarlo.
        """
        dormido = 0.0
        while True:
            ahora = time.time()
            self._limpiar(ahora)
            usados = sum(n for _, n in self.enviados)
            if usados + tokens <= self.tope or not self.enviados:
                self.enviados.append((ahora, tokens))
                return dormido
            # Dormir hasta que caduque la peticion mas antigua.
            espera = 60.0 - (ahora - self.enviados[0][0]) + 0.2
            time.sleep(max(espera, 0.2))
            dormido += max(espera, 0.2)


def _tokens_de(textos: list) -> int:
    """
    Estima los tokens de un lote.

    Args:
        textos: Fragmentos del lote.

    Returns:
        Numero aproximado de tokens.
    """
    return max(1, sum(len(t) for t in textos) // CARACTERES_POR_TOKEN)


# ==============================================================================
# CACHE EN DISCO
# ==============================================================================

def _ruta_cache() -> str:
    """
    Ruta del archivo donde se guardan los vectores ya calculados.

    Returns:
        Ruta absoluta del sqlite de cache. Vive al lado del indice para que
        borrar uno y conservar el otro no deje el sistema a medias.
    """
    import os
    return os.path.join(settings.qdrant_ruta_local() + "_cache.sqlite")


def _abrir_cache():
    """
    Abre (y crea si hace falta) la cache de embeddings.

    POR QUE UNA CACHE
    -----------------
    Un embedding del mismo texto siempre da el mismo vector, y cuesta cuota.
    Guardarlos convierte un trabajo de dos horas en reanudable: si se corta la
    luz, si el contenedor se reinicia o si alguien lanza una reindexacion
    forzada, lo ya calculado no se vuelve a pagar.

    Returns:
        Conexion sqlite, o None si no se pudo abrir (la cache es un extra y
        nunca debe impedir indexar).
    """
    import os
    import sqlite3
    try:
        os.makedirs(os.path.dirname(_ruta_cache()), exist_ok=True)
        con = sqlite3.connect(_ruta_cache())
        con.execute(
            "CREATE TABLE IF NOT EXISTS vectores ("
            "  clave TEXT PRIMARY KEY,"
            "  vector BLOB NOT NULL)"
        )
        con.commit()
        return con
    except Exception:
        return None


def _clave_cache(texto: str, tarea: str) -> str:
    """
    Identifica un embedding por su contenido y sus condiciones.

    Incluye modelo, dimensiones y tarea: si cambia cualquiera de los tres, el
    vector guardado ya no sirve y debe recalcularse.

    Args:
        texto: Fragmento.
        tarea: Tipo de tarea de embedding.

    Returns:
        Clave hexadecimal.
    """
    crudo = (f"{modelo_embeddings()}|{dimensiones_efectivas()}|v{ESQUEMA_EMBEDDINGS}"
             f"|{tarea}|{texto}")
    return hashlib.sha1(crudo.encode("utf-8")).hexdigest()


def embeber(textos: list, tarea: str = "RETRIEVAL_DOCUMENT",
            progreso=None, reintentar: bool = True) -> list:
    """
    Convierte una lista de textos en vectores.

    Va por lotes y reintenta cuando la API responde que se agoto la cuota, que
    con la capa gratuita de Gemini pasa de verdad: se midio que un lote de 100
    devuelve 429 y uno de 32 pasa sin problema.

    Args:
        textos: Fragmentos a convertir.
        tarea: `RETRIEVAL_DOCUMENT` al indexar y `RETRIEVAL_QUERY` al buscar.
            No es cosmetico: el modelo coloca la pregunta y el documento en
            espacios distintos, y acertar aqui mejora lo que se recupera.
        progreso: Callback opcional `f(hechos, total)`.

    Returns:
        Lista de vectores, en el mismo orden que `textos`.

    Raises:
        RuntimeError: Si la cuota sigue agotada despues de todos los
            reintentos, diciendo cuantos se hicieron.
    """
    if not textos:
        return []

    import array

    from google.genai import types

    dimensiones = settings.dimensiones_embedding()
    config = types.EmbedContentConfig(
        output_dimensionality=dimensiones, task_type=tarea)

    # --- 1. Lo que ya esta en la cache no se vuelve a pedir -------------------
    cache = _abrir_cache()
    resultado = [None] * len(textos)
    faltan = []
    if cache is not None:
        for i, texto in enumerate(textos):
            fila = cache.execute(
                "SELECT vector FROM vectores WHERE clave = ?",
                (_clave_cache(texto, tarea),)).fetchone()
            if fila:
                v = array.array("f")
                v.frombytes(fila[0])
                resultado[i] = list(v)
            else:
                faltan.append(i)
    else:
        faltan = list(range(len(textos)))

    if progreso:
        progreso(len(textos) - len(faltan), len(textos))

    # --- 2. Lo que falta, agrupado para no repetir textos identicos ----------
    # El corpus tiene 478 fichas duplicadas exactas. Pedirlas una sola vez
    # ahorra cuota, que es el recurso escaso.
    unicos = {}
    for i in faltan:
        unicos.setdefault(textos[i], []).append(i)
    pendientes = list(unicos.keys())

    cliente = _cliente_gemini() if pendientes else None
    regulador = _Regulador()
    tamano = settings.lote_embeddings()
    hechos = len(textos) - len(faltan)

    for inicio in range(0, len(pendientes), tamano):
        lote = pendientes[inicio:inicio + tamano]
        regulador.reservar(_tokens_de(lote))
        vectores = _embeber_lote(cliente, lote, config, reintentar=reintentar)

        for texto, vec in zip(lote, vectores):
            for i in unicos[texto]:
                resultado[i] = vec
            if cache is not None:
                try:
                    # Se guarda en float32 ("f"), no en float64. Un vector
                    # recuperado de la cache no es BIT A BIT igual al original:
                    # se midio una diferencia maxima de 3e-9 por componente y
                    # una similitud coseno de 1,0000000000. Es redondeo, no
                    # perdida, y Qdrant almacena float32 de todas formas; a
                    # cambio la cache ocupa la mitad.
                    cache.execute(
                        "INSERT OR REPLACE INTO vectores VALUES (?, ?)",
                        (_clave_cache(texto, tarea),
                         array.array("f", vec).tobytes()))
                except Exception:
                    pass
            hechos += len(unicos[texto])

        if cache is not None:
            try:
                cache.commit()      # se guarda por lote: si se corta, no se pierde todo
            except Exception:
                pass
        if progreso:
            progreso(hechos, len(textos))

    if cache is not None:
        try:
            cache.close()
        except Exception:
            pass

    # Si algo quedo sin vector (fallo raro), se devuelve un vector nulo en vez
    # de None: Qdrant rechazaria el None y tumbaria toda la indexacion.
    return [v if v is not None else [0.0] * dimensiones_efectivas()
            for v in resultado]


def _embeber_lote(cliente, lote: list, config, reintentar: bool = True) -> list:
    """
    Pide los vectores de un lote, reintentando si la cuota se agota.

    Args:
        cliente: Cliente de Gemini.
        lote: Textos del lote.
        config: Configuracion de embeddings ya construida.
        reintentar: Si False, no espera ciclos largos de cuota (para consultas interactivas).

    Returns:
        Lista de vectores del lote.

    Raises:
        RuntimeError: Si no se consiguio despues de agotar los reintentos.
    """
    esperas = ESPERAS_REINTENTO if reintentar else [2]
    ultimo_error = None
    for intento, espera in enumerate(esperas, start=1):
        try:
            r = cliente.models.embed_content(
                model=MODELO_EMBEDDINGS, contents=lote, config=config)
            return [list(e.values) for e in r.embeddings]
        except Exception as exc:
            ultimo_error = exc
            if not _es_cuota(exc):
                raise
            # Se anade una pizca de azar para que varios procesos que reintentan
            # a la vez no vuelvan a chocar en el mismo instante.
            time.sleep(espera + random.uniform(0, 1.5))

    # No se afirma "sin cuota": esta funcion reintenta ante CUALQUIER fallo
    # pasajero, y la mayoria de las veces no es la cuota sino un corte de red.
    # Se midio: uno de cada diez embeddings fallaba con "[SSL:
    # UNEXPECTED_EOF_WHILE_READING]" y el mensaje decia "sigue sin cuota",
    # que mandaba a mirar la facturacion en vez de la conexion.
    raise RuntimeError(
        f"La API de embeddings no respondio tras {len(esperas)} intentos. "
        f"Ultimo error: {ultimo_error}"
    )


#: Senales de un fallo PASAJERO, que se arregla volviendo a intentarlo.
#:
#: Al principio aqui solo estaba la cuota (429). Una indexacion real murio a
#: los tres minutos con "Server disconnected without sending a response": un
#: corte de red del lado de Google, que no es cuota y no se reintentaba. Un
#: trabajo que dura dos horas se topa con varios de esos; si cada uno lo mata,
#: nunca termina.
FALLOS_PASAJEROS = (
    "429", "resource_exhausted", "quota",          # cuota agotada
    "500", "502", "503", "504", "unavailable",     # el servicio flaquea
    "disconnected", "connection", "timeout",       # la red se corta
    "deadline", "reset by peer", "eof",
)


def _es_cuota(exc) -> bool:
    """
    Decide si conviene reintentar el lote.

    Reintentar un 429 o un corte de red tiene sentido; reintentar una clave
    invalida o un texto rechazado solo gasta tiempo y cuota.

    Args:
        exc: Excepcion capturada.

    Returns:
        True si el fallo parece pasajero.
    """
    texto = str(exc).lower()
    return any(senal in texto for senal in FALLOS_PASAJEROS)


# ==============================================================================
# COLECCION
# ==============================================================================

#: Identificador del punto que guarda con que modelo se creo la coleccion.
#: Es un punto normal, con vector nulo, que nunca sale en las busquedas porque
#: su similitud con cualquier consulta real es minima; y aunque saliera, se
#: filtra por `documento`.
ID_FIRMA = "00000000-0000-0000-0000-000000000001"


def dimensiones_efectivas() -> int:
    """
    Tamano real del vector que produce el modelo en uso.

    Gemini respeta el `output_dimensionality` que se le pide, asi que lo
    configurado y lo efectivo coinciden. La funcion se conserva porque el
    resto del modulo la usa y porque otro proveedor podria no respetarlo.

    Returns:
        Numero de dimensiones que tendran los vectores.
    """
    return settings.dimensiones_embedding()


def _firma_actual() -> str:
    """
    Describe con que modelo y tamano se estan generando los vectores.

    Returns:
        Cadena del tipo `gemini-embedding-001@768#v2`.
    """
    return f"{modelo_embeddings()}@{dimensiones_efectivas()}#v{ESQUEMA_EMBEDDINGS}"


def _firma_guardada(cliente, nombre: str):
    """
    Lee con que modelo se creo una coleccion existente.

    Args:
        cliente: Cliente de Qdrant.
        nombre: Nombre de la coleccion.

    Returns:
        La firma guardada, o None si la coleccion es anterior a este control.
    """
    try:
        puntos = cliente.retrieve(collection_name=nombre, ids=[ID_FIRMA],
                                  with_payload=True)
        if puntos:
            return (puntos[0].payload or {}).get("firma")
    except Exception:
        pass
    return None


def _guardar_firma(cliente, nombre: str) -> None:
    """
    Deja constancia del modelo con el que se creo la coleccion.

    Args:
        cliente: Cliente de Qdrant.
        nombre: Nombre de la coleccion.
    """
    from qdrant_client import models
    try:
        cliente.upsert(collection_name=nombre, points=[models.PointStruct(
            id=ID_FIRMA,
            vector={NOMBRE_VECTOR: [0.0] * dimensiones_efectivas()},
            payload={"documento": "__firma__", "firma": _firma_actual()})])
    except Exception:
        pass


def _tirar_coleccion(cliente, nombre: str) -> None:
    """
    Deja la coleccion vacia de verdad, en servidor y en modo embebido.

    POR QUE NO BASTA `delete_collection`
    ------------------------------------
    Se midio: en modo embebido, borrar la coleccion y crearla de nuevo en la
    misma sesion dejaba los puntos antiguos dentro. La firma quedaba
    actualizada y los vectores viejos seguian ahi, que es justo el escenario
    que este control pretende evitar. Asi que despues de borrar se comprueba, y
    si siguen apareciendo puntos se vacian uno a uno.

    Args:
        cliente: Cliente de Qdrant.
        nombre: Nombre de la coleccion.
    """
    from qdrant_client import models

    # Se vacia ANTES de borrar la coleccion. Al reves no servia: borrar la
    # coleccion dejaba el contenido en pie en modo embebido, y como ya no
    # "existia", la limpieza posterior se saltaba y los vectores del modelo
    # anterior sobrevivian a la recreacion.
    try:
        cliente.delete(
            collection_name=nombre,
            points_selector=models.FilterSelector(filter=models.Filter()))
    except Exception:
        pass

    try:
        cliente.delete_collection(nombre)
    except Exception:
        pass


def asegurar_coleccion(cliente=None) -> None:
    """
    Crea la coleccion de Qdrant si todavia no existe.

    Si existe pero se creo con otro modelo de embeddings, la rehace: los
    vectores de dos modelos distintos no son comparables entre si.

    Args:
        cliente: Cliente de Qdrant ya abierto. Si falta, se abre uno.
    """
    from qdrant_client import models

    cliente = cliente or _cliente_qdrant()
    nombre = settings.coleccion_qdrant()

    if cliente.collection_exists(nombre):
        # Un vector solo es comparable con otros del MISMO modelo y del mismo
        # tamano. Mezclar los de un modelo con los de
        # otro modelo no da un error: da resultados sin sentido, que es
        # peor. Si la coleccion se creo con otro modelo, se rehace.
        guardada = _firma_guardada(cliente, nombre)

        if guardada == _firma_actual():
            return

        if guardada is None:
            # Coleccion anterior a este control. Si esta vacia basta con
            # firmarla; si tiene puntos, no hay forma de saber que modelo los
            # produjo, y la sospecha es suficiente para rehacerla: se midio
            # una coleccion con 4.582 fichas mezcladas de dos esquemas
            # precisamente porque este caso salia por la puerta de atras.
            try:
                poblada = cliente.count(nombre, exact=True).count > 0
            except Exception:
                poblada = False
            if not poblada:
                _guardar_firma(cliente, nombre)
                return

        _tirar_coleccion(cliente, nombre)

    if not cliente.collection_exists(nombre):
        cliente.create_collection(
            collection_name=nombre,
            vectors_config={
                NOMBRE_VECTOR: models.VectorParams(
                    size=dimensiones_efectivas(),
                    distance=models.Distance.COSINE,
                )
            },
        )
    _guardar_firma(cliente, nombre)

    # Sin indice sobre `documento` el borrado por documento recorre la
    # coleccion entera, y el reindexado incremental deja de ser incremental.
    # El modo embebido los ignora y avisa por consola, asi que solo se crean
    # cuando hay servidor detras.
    if not en_servidor():
        return
    for campo in ("documento", "huella", "categoria"):
        try:
            cliente.create_payload_index(
                collection_name=nombre, field_name=campo,
                field_schema=models.PayloadSchemaType.KEYWORD)
        except Exception:
            pass  # ya existia

    # La fecha va como DATETIME, no como KEYWORD: `order_by` de Qdrant exige
    # un indice de rango, y con uno de texto devuelve los puntos en un orden
    # cualquiera sin quejarse. Se crea aqui, junto a los demas, porque al
    # vaciar y recrear la coleccion se perdia y nadie lo notaba hasta que una
    # consulta ordenada por fecha devolvia cualquier cosa.
    for campo, tipo in (("fecha_indexado", models.PayloadSchemaType.DATETIME),
                        ("indexado_unix", models.PayloadSchemaType.INTEGER)):
        try:
            cliente.create_payload_index(
                collection_name=nombre, field_name=campo, field_schema=tipo)
        except Exception:
            pass  # ya existia


def _ahora_iso() -> str:
    """
    Momento actual en UTC, en formato ISO 8601.

    POR QUE UTC Y POR QUE ESTE FORMATO
    ----------------------------------
    En UTC porque el indice lo escribe un contenedor y lo consulta una persona
    que puede estar en otro huso: guardando la hora local, dos sincronizaciones
    hechas en maquinas distintas no se podrian ordenar entre si.

    En ISO 8601 (`2026-10-22T04:15:33Z`) porque Qdrant compara las cadenas por
    orden alfabetico, y en este formato el orden alfabetico ES el cronologico.
    Con `22/10/2026` no lo seria, y filtrar "lo indexado desde el lunes"
    daria resultados absurdos.

    Returns:
        La fecha y hora actuales, por ejemplo `2026-10-22T04:15:33Z`.
    """
    import datetime

    return datetime.datetime.now(datetime.timezone.utc).strftime(
        "%Y-%m-%dT%H:%M:%SZ")


def _id_de_ficha(documento: str, indice: int) -> str:
    """
    Calcula un identificador estable para una ficha.

    Tiene que ser estable para que reindexar el mismo documento pise sus
    propios puntos en vez de duplicarlos.

    Args:
        documento: Nombre del documento de origen.
        indice: Posicion de la ficha dentro del documento.

    Returns:
        UUID en texto, que es uno de los formatos que acepta Qdrant.
    """
    semilla = f"{documento}#{indice}"
    return str(uuid.UUID(hashlib.md5(semilla.encode("utf-8")).hexdigest()))


# ==============================================================================
# INDEXADO
# ==============================================================================

def _parece_tabla_de_datos(texto: str) -> bool:
    """
    Distingue una tabla de datos del indice de un libro.

    Las dos cosas tienen pocas letras, pero no se parecen en nada mas. Un
    indice son lineas sueltas con puntos de relleno y un numero de pagina al
    final. Una tabla son filas con la MISMA forma: el mismo separador repetido
    el mismo numero de veces, fila tras fila. Esa regularidad es lo que se
    busca aqui, porque es lo que no puede fingir un indice.

    Args:
        texto: Fragmento ya limpio de espacios en los extremos.

    Returns:
        True si las filas comparten estructura de columnas.
    """
    filas = [f for f in texto.splitlines() if f.strip()]
    if len(filas) < 3:
        return False

    for separador in ("|", "	", ";"):
        cuentas = [f.count(separador) for f in filas]
        # Al menos tres filas con dos o mas separadores, y la forma mas comun
        # repetida en la mayoria de ellas: eso es una tabla, no una lista.
        con_columnas = [c for c in cuentas if c >= 2]
        if len(con_columnas) < 3:
            continue
        mas_comun = max(set(con_columnas), key=con_columnas.count)
        if con_columnas.count(mas_comun) >= max(3, len(filas) * 0.6):
            return True
    return False


def merece_vector(texto: str) -> bool:
    """
    Decide si un fragmento vale la pena embeber.

    POR QUE FILTRAR
    ---------------
    De las 17.032 fichas del corpus, 2.043 son indices y tablas de contenido:
    lineas como `........................ 12  Alimentacion .....`. Embeberlas
    cuesta cuota, que es el recurso escaso, y encima ensucia la busqueda, porque
    un indice menciona todos los temas del libro y acaba pareciendose a
    cualquier pregunta.

    Para BM25 no estorban, asi que `corpus.buscar` las sigue viendo. El filtro
    es solo para la parte vectorial.

    Args:
        texto: Fragmento candidato.

    Returns:
        True si conviene indexarlo.
    """
    limpio = (texto or "").strip()
    if len(limpio) < corpus.TAMANO_MINIMO_FICHA:
        return False

    # Una tabla de datos tampoco llega al 45% de letras, y en este proyecto es
    # justo lo que mas importa: inventarios de quebradas, longitudes, areas,
    # coordenadas. Se comprueba ANTES de aplicar el filtro de letras.
    #
    # Se midio el dano: el inventario de las 127 quebradas de la cuenca de San
    # Carlos entro al indice con UNA ficha. La primera pasaba por el titulo y
    # los encabezados, que son letras; de la 12 en adelante, filas como
    # "5 | Quebrada Abejales | 1.069 | 1069.07 | 1 | 5101" tienen un 33% de
    # letras y se descartaban como si fueran el indice de un libro. Se
    # perdieron 115 quebradas que son el dato cartografico del trabajo.
    if _parece_tabla_de_datos(limpio):
        return True

    # Un indice es sobre todo puntos, guiones y numeros de pagina.
    letras = sum(1 for c in limpio if c.isalpha())
    if letras < len(limpio) * 0.45:
        return False

    # Muy poca variedad de caracteres: relleno, no prosa.
    import re as _re
    if len(set(_re.sub(r"[\s.\-_]", "", limpio))) < 12:
        return False

    return True


def huellas_indexadas(cliente=None) -> dict:
    """
    Lee que documentos estan ya en el indice y con que huella.

    Es lo que permite saltarse lo que no cambio.

    Args:
        cliente: Cliente de Qdrant ya abierto.

    Returns:
        Diccionario `{nombre_documento: huella}`.
    """
    cliente = cliente or _cliente_qdrant()
    nombre = settings.coleccion_qdrant()
    if not cliente.collection_exists(nombre):
        return {}

    encontradas = {}
    siguiente = None
    while True:
        puntos, siguiente = cliente.scroll(
            collection_name=nombre, limit=1000, offset=siguiente,
            with_payload=["documento", "huella"], with_vectors=False)
        for p in puntos:
            carga = p.payload or {}
            doc = carga.get("documento")
            if doc and doc != "__firma__":
                encontradas[doc] = carga.get("huella", "")
        if siguiente is None:
            break
    return encontradas


def limpiar_descargas(fuente=None) -> dict:
    """
    Borra lo que la fuente haya traido al equipo.

    El agente aprende en el indice vectorial, que son numeros. Los archivos
    solo hacen falta mientras se leen. Conservarlos ocuparia espacio con
    copias de algo que ya esta en su sitio y multiplicaria los lugares desde
    donde puede escaparse.

    Args:
        fuente: La fuente a limpiar. Si no se pasa, se pregunta cual esta
            activa.

    Returns:
        Lo que informe la fuente, o `{"error": ...}` si no se pudo. Nunca
        levanta: la limpieza se llama desde sitios donde ya hubo un fallo y
        tapar ese fallo con otro solo esconde el primero.
    """
    try:
        if fuente is None:
            fuente = fuentes.obtener()
        if not hasattr(fuente, "limpiar"):
            return {}
        return fuente.limpiar()
    except Exception as exc:
        return {"error": str(exc)[:120]}


#: Como se marcan en el indice los archivos que NO se leyeron enteros. Va en
#: `categoria` para que se distingan de la bibliografia de un vistazo y para
#: poder filtrarlos.
CATEGORIA_CAPA = "Capa de datos (no leida entera)"


def texto_de_catalogo(descartado: dict) -> str:
    """
    Redacta la ficha de un archivo que no entro al indice por su tamano.

    POR QUE SE INDEXA ALGO DE UN ARCHIVO QUE NO SE LEYO
    ---------------------------------------------------
    Se midio la alternativa: sin esta ficha, preguntar por `e_eccmc_100K_2024`
    daba "No tengo informacion en la bibliografia cargada sobre eso", que se
    lee como "ese archivo no existe". Existe, esta en Drive, pesa 434 MB y es
    una capa de datos. Decir eso, con sus columnas, es una respuesta util;
    callarlo manda a buscar un archivo que ya esta ahi.

    Args:
        descartado: Entrada de `fuente.descartados`, con `nombre`, `mb` y el
            `catalogo` leido de los primeros megas.

    Returns:
        El texto de la ficha, listo para embeber.
    """
    catalogo = descartado.get("catalogo") or {}
    lineas = [
        f"[Capa de datos: {descartado['nombre']}]",
        f"Archivo disponible en la fuente documental, de {descartado['mb']} MB.",
        "No se indexo fila a fila: habria costado cientos de miles de fichas. "
        "En su lugar se recorrio entero y se indexo su RESUMEN agrupado, que "
        "es lo que sigue a esta ficha.",
    ]

    if catalogo.get("error"):
        lineas.append(f"No se pudo leer su cabecera: {catalogo['error']}")
        return "\n".join(lineas)

    columnas = catalogo.get("columnas") or []
    if columnas:
        lineas.append(
            f"Tiene {catalogo.get('total_columnas')} columnas, separadas por "
            f"{catalogo.get('delimitador')}:")
        lineas.append(", ".join(columnas))

    # Lo de las coordenadas no es un detalle: decide si la capa sirve para
    # dibujar un mapa o solo para contar. Una tabla de atributos sin geometria
    # no pinta nada en QGIS por mucho que pese.
    if catalogo.get("apto_para_qgis"):
        lineas.append(
            f"Trae coordenadas ({catalogo.get('columna_latitud')} / "
            f"{catalogo.get('columna_longitud')}), asi que se puede llevar a "
            f"QGIS como capa de puntos.")
    else:
        lineas.append(
            "NO trae columnas de coordenadas ni geometria, asi que por si solo "
            "no dibuja nada en QGIS: es una tabla de atributos. Para cartografia "
            "hace falta el archivo con geometria (shapefile o GeoPackage), o "
            "unirla a una capa que ya la tenga.")

    if catalogo.get("filas_en_la_muestra"):
        lineas.append(
            f"En los primeros megas leidos hay {catalogo['filas_en_la_muestra']} "
            f"filas; el archivo completo tiene muchas mas.")
    return "\n".join(lineas)


def _huella_de_catalogo(descartado: dict) -> str:
    """
    Huella de la ficha de una capa, para saber si hay que rehacerla.

    Args:
        descartado: Entrada de `fuente.descartados`.

    Returns:
        Una cadena que cambia si cambia el archivo en la fuente.
    """
    catalogo = descartado.get("catalogo") or {}
    resumen = catalogo.get("resumen") or {}
    # Las filas recorridas entran en la huella: si la capa cambia en origen, el
    # resumen cambia y sus fichas se rehacen. Sin esto, anadir el resumen a una
    # capa ya catalogada no disparaba nada, porque la huella seguia igual.
    return (f"catalogo-{descartado.get('mb')}-{catalogo.get('total_columnas')}"
            f"-{resumen.get('filas', 0)}")


def _indexar_catalogos(cliente, nombre: str, descartados: list,
                       resumen: dict, ya_estan: dict = None) -> None:
    """
    Mete en el indice una ficha por cada archivo demasiado grande.

    Args:
        cliente: Cliente de Qdrant.
        nombre: Coleccion donde escribir.
        descartados: Lo que la fuente aparto por tamano.
        resumen: Resumen de la indexacion, que se actualiza.
        ya_estan: Huellas ya indexadas, para no rehacer lo que no cambio.
    """
    from qdrant_client import models

    ya_estan = ya_estan or {}
    conservar = [d for d in descartados if d.get("catalogo")]
    # Rehacer una ficha identica cuesta un embedding y no aporta nada. Si el
    # archivo no ha cambiado de tamano ni de columnas, su ficha sigue valiendo.
    conservar = [d for d in conservar
                 if ya_estan.get(d["nombre"]) != _huella_de_catalogo(d)]
    if not conservar:
        return

    # Cada capa aporta su ficha de cabecera MAS las del resumen agregado. El
    # resumen es lo que hace que una capa de 434 MB entre de verdad al indice:
    # no sus 644.936 filas, que son imposibles, sino lo que esas filas dicen
    # juntas. Se trocea igual que cualquier documento.
    textos, duenos = [], []
    for ficha in conservar:
        textos.append(texto_de_catalogo(ficha))
        duenos.append(ficha)
        lineas = ((ficha.get("catalogo") or {}).get("resumen") or {}).get(
            "lineas") or []
        if lineas:
            for trozo in corpus.trocear(chr(10).join(lineas)):
                if merece_vector(trozo):
                    textos.append(trozo)
                    duenos.append(ficha)
    try:
        vectores = embeber(textos, "RETRIEVAL_DOCUMENT")
    except Exception as exc:
        resumen.setdefault("fallidos", []).append(
            {"documento": "fichas de capas de datos", "motivo": str(exc)[:200]})
        return

    momento = _ahora_iso()
    ahora_unix = int(time.time())
    puntos = []
    for numero, (ficha, texto, vector) in enumerate(
            zip(duenos, textos, vectores)):
        if vector is None:
            continue
        puntos.append(models.PointStruct(
            id=_id_de_ficha(ficha["nombre"], numero),
            vector={NOMBRE_VECTOR: vector},
            payload={
                "documento": ficha["nombre"],
                "categoria": CATEGORIA_CAPA,
                "pagina": None,
                # La huella incluye el tamano: si el archivo cambia en Drive,
                # cambia la huella y la ficha se rehace.
                "huella": _huella_de_catalogo(ficha),
                "texto": texto,
                "fecha_indexado": momento,
                "indexado_unix": ahora_unix,
            }))
    if puntos:
        cliente.upsert(collection_name=nombre, points=puntos)
        resumen["fichas"] += len(puntos)
        resumen["capas_catalogadas"] = len(puntos)

def _indexar_todo(forzar: bool = False, progreso=None) -> dict:
    """
    Sincroniza el indice vectorial con la fuente documental.

    Solo procesa los documentos cuya huella cambio, mas los que aun no estaban.
    Los que desaparecieron de la fuente se borran del indice, para que el
    agente no cite un documento que ya no existe.

    Args:
        forzar: True reindexa todo aunque nada haya cambiado.
        progreso: Callback opcional `f(mensaje, fraccion)`.

    Returns:
        Diccionario con `nuevos`, `actualizados`, `sin_cambios`, `borrados`,
        `fichas` y `segundos`.
    """
    from qdrant_client import models

    arranque = time.time()
    cliente = _cliente_qdrant()
    asegurar_coleccion(cliente)
    nombre = settings.coleccion_qdrant()

    fuente = fuentes.obtener()
    listo, motivo = fuente.disponible()
    if not listo:
        raise RuntimeError(f"La fuente documental no esta lista: {motivo}")

    documentos = fuente.documentos()
    ya_estan = {} if forzar else huellas_indexadas(cliente)
    # Desde aqui ya puede haber archivos bajados. Lo que siga va envuelto para
    # que el borrado ocurra tambien si algo falla.

    pendientes = [d for d in documentos
                  if ya_estan.get(d["nombre"]) != d["huella"]]
    nuevos = sum(1 for d in pendientes if d["nombre"] not in ya_estan)
    resumen = {
        "nuevos": nuevos,
        "actualizados": len(pendientes) - nuevos,
        "sin_cambios": len(documentos) - len(pendientes),
        "borrados": 0,
        "fichas": 0,
        "fallidos": [],
        "segundos": 0.0,
    }

    # --- Documentos que ya no estan en la fuente ------------------------------
    # Las capas grandes tambien estan EN la fuente, aunque no se lean enteras:
    # cuentan como vigentes. Sin esto, sus fichas de catalogo se borraban al
    # principio de cada pasada y se volvian a crear al final, y el resumen
    # decia "4 borrados" en todas las sincronizaciones aunque no hubiera
    # cambiado nada. Ademas gastaba cuatro embeddings cada vez.
    vigentes = {d["nombre"] for d in documentos}
    vigentes |= {d["nombre"] for d in getattr(fuente, "descartados", [])}
    sobrantes = [n for n in ya_estan if n not in vigentes]
    if sobrantes:
        cliente.delete(
            collection_name=nombre,
            points_selector=models.FilterSelector(filter=models.Filter(
                must=[models.FieldCondition(
                    key="documento",
                    match=models.MatchAny(any=sobrantes))])))
        resumen["borrados"] = len(sobrantes)

    # --- Documentos nuevos o modificados --------------------------------------
    for n, doc in enumerate(pendientes, start=1):
        resumen["_fraccion"] = n / max(len(pendientes), 1)
        puesto = f"[{n}/{len(pendientes)}]"
        if progreso:
            progreso(f"{puesto} {doc['nombre'][:46]}", resumen["_fraccion"])
        try:
            _indexar_documento(cliente, nombre, doc, resumen, progreso, puesto)
        except Exception as exc:
            # Un documento ilegible, un PDF corrupto o un fallo que no cedio a
            # los reintentos no pueden tumbar un trabajo de dos horas. Se anota
            # y se sigue; al final se informa de cuales quedaron fuera.
            resumen.setdefault("fallidos", []).append(
                {"documento": doc["nombre"], "motivo": str(exc)[:200]})

    # Los archivos que no entraron por tamano dejan constancia: una ficha
    # cada uno con lo que se pudo leer de su cabecera. Cuesta un embedding por
    # archivo y evita que el agente niegue algo que esta ahi.
    try:
        _indexar_catalogos(cliente, nombre, getattr(fuente, "descartados", []),
                           resumen, ya_estan)
    except Exception as exc:
        resumen.setdefault("fallidos", []).append(
            {"documento": "fichas de capas de datos", "motivo": str(exc)[:200]})

    resumen.pop("_fraccion", None)
    resumen["segundos"] = round(time.time() - arranque, 1)
    return resumen


def indexar(forzar: bool = False, progreso=None) -> dict:
    """
    Sincroniza el indice y se asegura de no dejar nada descargado.

    El trabajo lo hace `_indexar_todo`. Esta capa existe solo para una cosa:
    que lo traido al equipo se borre AUNQUE la indexacion se caiga. Antes la
    limpieza era la ultima linea del cuerpo, asi que cualquier fallo a mitad
    -- cuota agotada, Qdrant que se cae, un documento roto que no cedio -- se
    la saltaba.

    Se midio el dano: una ejecucion que no llego al final dejo cuatro
    documentos de Drive en la carpeta temporal del contenedor. El comentario
    del codigo decia que iba en `finally`; el codigo no lo hacia. Ahora si.

    Args:
        forzar: Reindexar todo, aunque la huella no haya cambiado.
        progreso: Funcion a la que avisar del avance.

    Returns:
        El resumen de la indexacion, con `limpieza` anadida.

    Raises:
        Lo que levante la indexacion. La limpieza ocurre igual antes de que
        el error suba.
    """
    limpieza = None
    try:
        resumen = _indexar_todo(forzar=forzar, progreso=progreso)
    finally:
        limpieza = limpiar_descargas()
        # Despues de sincronizar SI ha podido cambiar lo que hay en la fuente,
        # asi que el estado guardado deja de valer.
        try:
            fuentes.olvidar_estado()
        except Exception:
            pass
    resumen["limpieza"] = limpieza
    return resumen


def _indexar_documento(cliente, nombre: str, doc: dict, resumen: dict,
                       progreso=None, puesto: str = "") -> None:
    """
    Trocea, embebe y guarda un documento en el indice.

    Args:
        cliente: Cliente de Qdrant.
        nombre: Nombre de la coleccion.
        doc: Documento de la fuente, con `ruta`, `nombre`, `categoria` y `huella`.
        resumen: Acumulador de cifras; se le suma el numero de fichas.
        progreso: Callback `f(mensaje, fraccion)` para informar por dentro.
        puesto: Posicion del documento en la tanda, del tipo "12/134".
    """
    from qdrant_client import models

    fichas = []
    for pagina, texto in corpus.leer_documento(doc["ruta"]):
        for trozo in corpus.trocear(texto):
            if merece_vector(trozo):
                fichas.append((pagina, trozo))
    if not fichas:
        return

    # GUARDIA EXACTO DEL PRESUPUESTO
    # ------------------------------
    # Aqui ya se sabe cuantas fichas son DE VERDAD, no estimadas. Contarlas no
    # cuesta cuota; convertirlas en vectores si. Asi que este es el ultimo
    # momento para decir que no, y cubre los formatos que no se pueden estimar
    # por muestra (PDF, DOCX, XLSX: truncados no se abren).
    #
    # Se para antes de embeber ni una sola: gastar la mitad del presupuesto
    # diario para luego abandonar el documento a medias seria lo peor de las
    # dos opciones.
    if len(fichas) > corpus.PRESUPUESTO_FICHAS:
        resumen.setdefault("fuera_de_presupuesto", []).append({
            "documento": doc["nombre"],
            "fichas": len(fichas),
            "presupuesto": corpus.PRESUPUESTO_FICHAS,
        })
        if progreso:
            progreso(f"{puesto} {doc['nombre'][:42]} — {len(fichas)} fichas, "
                     f"fuera de presupuesto", resumen.get("_fraccion", 0.0))
        return

    # El documento mas grande del corpus pesa 2,5 MB y da miles de fichas: sin
    # avisar por dentro, la indexacion parecia colgada ocho minutos seguidos
    # sobre el mismo nombre. Se informa ficha a ficha.
    def _dentro(hechas, total):
        if progreso:
            progreso(f"{puesto} {doc['nombre'][:42]} — {hechas}/{total} fichas",
                     resumen.get("_fraccion", 0.0))

    vectores = embeber([t for _, t in fichas], "RETRIEVAL_DOCUMENT",
                       progreso=_dentro)

    # Se borran primero los puntos viejos del documento: si la version nueva
    # tiene menos fichas, las sobrantes quedarian huerfanas apuntando a un
    # texto que ya no existe.
    cliente.delete(
        collection_name=nombre,
        points_selector=models.FilterSelector(filter=models.Filter(
            must=[models.FieldCondition(
                key="documento",
                match=models.MatchValue(value=doc["nombre"]))])))

    # Momento en que esta ficha entra al indice. Se calcula UNA vez para todo
    # el documento: asi todas sus fichas comparten marca y se puede responder
    # "que entro en la sincronizacion de anoche" con una sola comparacion.
    #
    # En UTC y en formato ISO 8601 a proposito. Qdrant ordena y filtra cadenas
    # por orden alfabetico, y en ese formato el orden alfabetico coincide con
    # el cronologico. Guardando "22/10/2026" no coincidiria.
    momento = _ahora_iso()

    cliente.upsert(
        collection_name=nombre,
        points=[
            models.PointStruct(
                id=_id_de_ficha(doc["nombre"], i),
                vector={NOMBRE_VECTOR: vec},
                payload={
                    "documento": doc["nombre"],
                    "categoria": doc["categoria"],
                    "pagina": pagina,
                    "huella": doc["huella"],
                    "texto": texto,
                    # Cuando entro al indice. OJO: no es la fecha del
                    # documento original, es la de su indexacion. Si un
                    # documento cambia, sus fichas se reescriben y la fecha
                    # pasa a ser la del cambio, que es justo lo que interesa
                    # para saber que se estudio y cuando.
                    "fecha_indexado": momento,
                    "indexado_unix": int(time.time()),
                },
            )
            for i, ((pagina, texto), vec) in enumerate(zip(fichas, vectores))
        ],
    )
    resumen["fichas"] += len(fichas)


# ==============================================================================
# BUSQUEDA
# ==============================================================================

#: La marca de firma es un punto normal de la coleccion, pero no es una ficha:
#: solo guarda con que modelo se construyo el indice. Contarla engana, y
#: engana justo cuando mas importa: con el indice recien vaciado, el sistema
#: decia "1 ficha indexada" y daba el indice por listo, cuando no habia ni un
#: documento dentro.
_SIN_LA_FIRMA = None


def _filtro_sin_firma():
    """
    Filtro que excluye la marca de firma al contar fichas.

    Returns:
        Un filtro de Qdrant, construido una sola vez.
    """
    global _SIN_LA_FIRMA
    if _SIN_LA_FIRMA is None:
        from qdrant_client import models
        _SIN_LA_FIRMA = models.Filter(must_not=[models.FieldCondition(
            key="documento", match=models.MatchValue(value="__firma__"))])
    return _SIN_LA_FIRMA


def contar_fichas(cliente=None, nombre: str = None) -> int:
    """
    Cuenta las fichas de documentos que hay en el indice.

    Args:
        cliente: Cliente de Qdrant ya abierto.
        nombre: Coleccion a contar. Por defecto, la configurada.

    Returns:
        Cuantas fichas hay, sin contar la marca de firma. 0 si la coleccion
        todavia no existe.
    """
    cliente = cliente or _cliente_qdrant()
    nombre = nombre or settings.coleccion_qdrant()
    if not cliente.collection_exists(nombre):
        return 0
    return cliente.count(nombre, exact=True,
                         count_filter=_filtro_sin_firma()).count


def disponible() -> tuple:
    """
    Comprueba si el indice vectorial se puede consultar ahora mismo.

    Returns:
        Tupla `(listo, motivo)`.
    """
    try:
        cliente = _cliente_qdrant()
        nombre = settings.coleccion_qdrant()
        if not cliente.collection_exists(nombre):
            return False, ("El indice vectorial aun no existe. Lanza una "
                           "sincronizacion para construirlo.")
        cuantos = contar_fichas(cliente, nombre)
        if not cuantos:
            return False, ("El indice vectorial esta vacio. Lanza una "
                           "sincronizacion para cargarlo desde la fuente.")

        # Tener fichas guardadas no es lo mismo que poder consultarlas. Para
        # buscar hay que convertir la PREGUNTA en vector, y eso lo hace
        # Gemini: sin clave, Qdrant responde pero la busqueda devuelve cero
        # sin explicar por que.
        #
        # Se midio el dano: el contenedor arranco sin GEMINI_API_KEY (un
        # espacio de mas en el .env y el env_file se lee al crear el
        # contenedor, no al reiniciarlo). El estado decia "10 fichas
        # indexadas, listo" y toda consulta del agente volvia vacia. Parecia
        # que el corpus no tenia nada; lo que faltaba era la clave.
        if not settings.api_key_gemini():
            return False, (f"Hay {cuantos} fichas indexadas, pero falta "
                           "GEMINI_API_KEY: sin ella no se puede convertir la "
                           "pregunta en vector y toda busqueda saldria vacia.")
        return True, f"{cuantos} fichas indexadas."
    except Exception as exc:
        return False, f"No se pudo consultar Qdrant: {exc}"


# Ultimo motivo por el que la busqueda semantica se fue en vacio. No es un
# contador de errores: es la respuesta a "pregunte y no me dijo nada, por que".
ULTIMO_FALLO_DE_BUSQUEDA = {"motivo": "", "cuando": ""}


def _anotar_fallo_de_busqueda(exc: Exception) -> None:
    """
    Guarda por que la busqueda semantica no pudo responder.

    Args:
        exc: El error que la dejo en vacio.
    """
    ULTIMO_FALLO_DE_BUSQUEDA["motivo"] = f"{type(exc).__name__}: {str(exc)[:200]}"
    ULTIMO_FALLO_DE_BUSQUEDA["cuando"] = _ahora_iso()


def buscar_semantico(consulta: str, limite: int = 8,
                     categoria: str = None) -> list:
    """
    Busca fichas por significado, no por palabras.

    Args:
        consulta: Pregunta en prosa.
        limite: Cuantas fichas devolver.
        categoria: Restringe a una coleccion tematica.

    Returns:
        Lista de fichas con la misma forma que devuelve `corpus.buscar`, mas
        `puntaje`. Si algo falla, devuelve lista vacia: la busqueda semantica
        es un extra y nunca debe tumbar una consulta.
    """
    try:
        from qdrant_client import models

        cliente = _cliente_qdrant()
        nombre = settings.coleccion_qdrant()

        # El indice puede haberse construido con OTRO modelo. Preguntarle con
        # vectores de un modelo distinto no da error: da resultados sin
        # sentido, y el agente los citaria como buenos. Si no coinciden, se
        # devuelve vacio y la busqueda hibrida cae a BM25, que siempre acierta
        # lo que encuentra aunque encuentre menos.
        guardada = _firma_guardada(cliente, nombre)
        if guardada is not None and guardada != _firma_actual():
            return []

        vector = embeber([consulta], "RETRIEVAL_QUERY", reintentar=False)[0]

        # El punto de firma es un punto normal de la coleccion, asi que sale
        # en las busquedas como cualquier otro. Se midio: sobre una coleccion
        # recien creada era el UNICO resultado, y llegaba al agente como una
        # fuente con documento "__firma__" y texto vacio. Se excluye siempre.
        condiciones = [models.FieldCondition(
            key="documento", match=models.MatchValue(value="__firma__"))]
        filtro = models.Filter(must_not=condiciones)
        if categoria:
            filtro = models.Filter(
                must=[models.FieldCondition(
                    key="categoria", match=models.MatchValue(value=categoria))],
                must_not=condiciones)

        encontrados = cliente.query_points(
            collection_name=nombre,
            query=vector, using=NOMBRE_VECTOR, limit=limite,
            query_filter=filtro, with_payload=True).points

        return [{
            "documento": p.payload.get("documento", ""),
            "categoria": p.payload.get("categoria", ""),
            "pagina": p.payload.get("pagina"),
            "paginada": p.payload.get("pagina") is not None,
            "texto": p.payload.get("texto", ""),
            # Cuando se estudio esta ficha. Las fichas indexadas antes de que
            # se empezara a guardar la fecha devuelven cadena vacia, no se
            # inventa una: no saber cuando entro algo es un dato en si mismo.
            "fecha_indexado": p.payload.get("fecha_indexado", ""),
            "puntaje": float(p.score),
        } for p in encontrados]
    except Exception as exc:
        # Devolver [] esta bien para un fallo pasajero: la hibrida cae a BM25.
        # Pero un fallo de CONFIGURACION no se arregla reintentando, y callarlo
        # lo vuelve indistinguible de "el corpus no dice nada de eso". Se deja
        # anotado para que el estado y el diagnostico lo puedan contar.
        _anotar_fallo_de_busqueda(exc)
        return []


def buscar_hibrido(consulta: str, limite: int = 8,
                   categoria: str = None) -> list:
    """
    Combina la busqueda por palabras con la busqueda por significado.

    COMO SE MEZCLAN
    ---------------
    Las dos listas vienen con puntajes que no son comparables entre si: BM25 da
    numeros sin tope y el coseno va de -1 a 1. Normalizarlos exigiria calibrar
    a ojo, asi que se usa fusion por posicion (RRF): cada ficha suma
    `1 / (60 + puesto)` por cada lista en la que aparece. Lo que sale primero en
    las dos gana, y no hay ningun numero que ajustar a mano.

    Args:
        consulta: Pregunta del usuario.
        limite: Cuantas fichas devolver.
        categoria: Restringe a una coleccion tematica.

    Returns:
        Lista de fichas ordenadas. Si el indice vectorial no esta disponible,
        devuelve exactamente lo que daria `corpus.buscar`, de modo que el
        sistema siga funcionando sin Qdrant.
    """
    lexicas = corpus.buscar(consulta, limite=limite * 3, categoria=categoria)

    # Las fichas de indice ("Morpho peleides . . . . . 834") son coincidencias
    # literales perfectas para BM25 y una cita inservible: no afirman nada. Se
    # descartan de lo que se DEVUELVE, no del indice, de modo que
    # `corpus.buscar` y la busqueda por binomio siguen viendolas.
    utiles = [f for f in lexicas if merece_vector(f.get("texto", ""))]
    lexicas = utiles or lexicas          # nunca devolver vacio si habia algo

    semanticas = buscar_semantico(consulta, limite=limite * 2,
                                  categoria=categoria)
    if not semanticas:
        return lexicas[:limite]

    K = 60  # amortiguador habitual del metodo; evita que el primer puesto arrase
    puntos, fichas = {}, {}
    for lista in (lexicas, semanticas):
        for puesto, ficha in enumerate(lista):
            clave = (ficha.get("documento"), ficha.get("texto", "")[:80])
            puntos[clave] = puntos.get(clave, 0.0) + 1.0 / (K + puesto + 1)
            fichas.setdefault(clave, ficha)

    mejores = sorted(puntos, key=puntos.get, reverse=True)[:limite]
    return [fichas[c] for c in mejores]


def estado() -> dict:
    """
    Describe el indice vectorial para la interfaz y para n8n.

    Returns:
        Diccionario con `motor`, `listo`, `motivo`, `coleccion`, `fichas`,
        `dimensiones` y `modelo`.
    """
    listo, motivo = disponible()
    cuantas = 0
    try:
        cuantas = contar_fichas()
    except Exception:
        pass
    return {
        "motor": settings.qdrant_url() or "embebido",
        "listo": listo,
        "motivo": motivo,
        "coleccion": settings.coleccion_qdrant(),
        "fichas": cuantas,
        "dimensiones": dimensiones_efectivas(),
        "modelo": modelo_embeddings(),
        "indexado": fechas_de_indexado() if listo else {},
        "ultimo_fallo_de_busqueda": dict(ULTIMO_FALLO_DE_BUSQUEDA),
    }


def sellar_fichas_sin_fecha(limite_iso: str = None) -> dict:
    """
    Pone una cota temporal a las fichas que se indexaron antes de que hubiera fecha.

    POR QUE NO SE REINDEXA Y POR QUE NO SE INVENTA UNA FECHA
    --------------------------------------------------------
    Las fichas que ya estaban en el indice no saben cuando entraron: el dato
    no se guardaba. Hay tres caminos y solo uno es honesto:

      - Reindexarlo todo: daria la fecha de HOY, que es falsa, y ademas cuesta
        un embedding por ficha. Con 15.000 fichas y 1.000 al dia de plan
        gratuito son dos semanas.
      - Ponerles la fecha del archivo original: tampoco es cuando se
        estudiaron, es cuando se escribio el documento. Mezclar las dos cosas
        en el mismo campo haria que ninguna fuera fiable.
      - Decir lo unico que se sabe con certeza: que entraron ANTES de este
        momento. Es lo que hace esta funcion.

    Escribe solo en el payload, no toca los vectores, asi que no consume
    cuota del modelo y tarda segundos.

    Args:
        limite_iso: Momento hasta el que se sabe que ya estaban. Por defecto,
            ahora.

    Returns:
        Diccionario con `selladas` y `limite`.
    """
    from qdrant_client import models

    limite = limite_iso or _ahora_iso()
    cliente = _cliente_qdrant()
    nombre = settings.coleccion_qdrant()

    # Solo las que NO tienen fecha: las que ya la traen no se tocan.
    filtro = models.Filter(must=[
        models.IsEmptyCondition(
            is_empty=models.PayloadField(key="fecha_indexado")),
    ])

    selladas = 0
    siguiente = None
    while True:
        lote, siguiente = cliente.scroll(
            collection_name=nombre, scroll_filter=filtro, limit=512,
            offset=siguiente, with_payload=False, with_vectors=False)
        if not lote:
            break
        cliente.set_payload(
            collection_name=nombre,
            payload={"indexado_antes_de": limite},
            points=[p.id for p in lote])
        selladas += len(lote)
        if siguiente is None:
            break
    return {"selladas": selladas, "limite": limite}


def fechas_de_indexado(muestra: int = 4000) -> dict:
    """
    Resume cuando se estudio lo que hay en el indice.

    POR QUE SOBRE UNA MUESTRA Y NO SOBRE TODO
    -----------------------------------------
    Qdrant no sabe calcular un minimo ni un maximo de un campo del payload:
    habria que traerse los puntos y mirarlos uno a uno. Con decenas de miles
    de fichas eso es mover muchos megas cada vez que alguien abre la interfaz,
    para un dato que es informativo. Se mira una muestra y se dice claramente
    que lo es.

    Args:
        muestra: Cuantos puntos mirar como maximo.

    Returns:
        Diccionario con `primera`, `ultima`, `sin_fecha` (cuantos de la
        muestra se indexaron antes de que se guardara la fecha) y `mirados`.
        Vacio si no se pudo consultar.
    """
    try:
        cliente = _cliente_qdrant()
        nombre = settings.coleccion_qdrant()
        fechas = []
        cotas = []
        sin_nada = 0
        mirados = 0
        siguiente = None
        while mirados < muestra:
            lote, siguiente = cliente.scroll(
                collection_name=nombre,
                limit=min(512, muestra - mirados),
                offset=siguiente,
                with_payload=["fecha_indexado", "indexado_antes_de",
                              "documento"],
                with_vectors=False)
            if not lote:
                break
            for p in lote:
                carga = p.payload or {}
                if carga.get("documento") == "__firma__":
                    continue
                mirados += 1
                if carga.get("fecha_indexado"):
                    fechas.append(carga["fecha_indexado"])
                elif carga.get("indexado_antes_de"):
                    # No se sabe cuando entro, solo que fue antes de esto.
                    cotas.append(carga["indexado_antes_de"])
                else:
                    sin_nada += 1
            if siguiente is None:
                break
        return {
            # Las fechas van en ISO 8601, asi que ordenarlas como texto da el
            # orden cronologico correcto.
            "primera": min(fechas) if fechas else "",
            "ultima": max(fechas) if fechas else "",
            "con_fecha_exacta": len(fechas),
            # Fichas de antes de que se guardara la fecha. De ellas solo se
            # sabe una cota: estaban indexadas antes de este momento.
            "con_cota": len(cotas),
            "cota": max(cotas) if cotas else "",
            "sin_dato": sin_nada,
            "mirados": mirados,
        }
    except Exception:
        return {}
