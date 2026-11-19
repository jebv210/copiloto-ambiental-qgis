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
import os
import hashlib
import random
import time
import uuid

from config import settings
from core import corpus, fuentes


#: Espacios de reintento ante un 429 de cuota, en segundos. Son la red de
#: seguridad: con el regulador de ritmo de mas abajo casi nunca se usan.
ESPERAS_REINTENTO = [2, 5, 15, 40, 70]

#: Cuantos lotes seguidos tienen que chocar con la cuota para dar el dia por
#: perdido y dejar de esperarla.
#:
#: POR QUE HAY QUE RENDIRSE, Y PRONTO
#: -----------------------------------
#: Cada lote que choca con la cuota cuesta 2+5+15+40+70 = 132 segundos de
#: espera antes de rendirse. Con una cuota diaria agotada y cuatrocientos
#: documentos por delante, eso son HORAS de no hacer nada, documento a
#: documento, para acabar fallando igual.
#:
#: Y mientras tanto no se indexa lo que SI es gratis: los vectores que ya
#: estan en la cache local de dias anteriores, que no necesitan la API para
#: nada. Se midio en vivo: el indice llevaba 45 segundos clavado en 1.865
#: fichas, esperando, con miles de vectores ya pagados esperando turno.
#:
#: Tres lotes seguidos no es mala suerte: es que hoy ya no hay cuota. A
#: partir de ahi se sirve solo de la cache, se declara lo que falta y la
#: pasada termina en minutos en vez de en horas. Manana hay cuota nueva y
#: se empieza con el contador a cero.
LOTES_PARA_RENDIRSE = 3

#: Cuantos lotes seguidos han chocado con la cuota en esta pasada.
_CUOTA = {"seguidos": 0}


def olvidar_el_mal_dia() -> None:
    """Vuelve a dar por buena la cuota. Se llama al empezar cada pasada."""
    _CUOTA["seguidos"] = 0


def _es_de_cuota(error) -> bool:
    """
    Dice si un error es "se acabo la cuota" y no otra cosa.

    Args:
        error: La excepcion que levanto la llamada.

    Returns:
        True si el mensaje habla de cuota. Un fallo de red NO cuenta: ese se
        reintenta, porque la red vuelve y la cuota diaria no.
    """
    texto = str(error).upper()
    return "429" in texto or "RESOURCE_EXHAUSTED" in texto or "QUOTA" in texto

#: Tokens por minuto que se asume disponibles.
#:
#: MEDIDO, NO SUPUESTO: enviando lotes de 32 fichas de ~900 caracteres sin
#: pausa, la API acepta 3 y devuelve 429 en el cuarto. Eso son unos 21.000
#: tokens antes de cortar, asi que el limite real de la capa gratuita ronda esa
#: cifra por minuto. Se deja algo por debajo para no rozar el techo.
#:
#: El cuello NO son las peticiones sino los tokens: por eso agrandar el lote no
#: acelera nada, solo hace que el 429 llegue antes.
#:
#: ESTE NUMERO ES EL QUE MANDA EN LO QUE TARDA TODO
#: ------------------------------------------------
#: Indexar no esta limitado por el disco ni por la CPU. Recorrer 418 MB tarda
#: 27 segundos; vectorizar 2.000 fichas tarda media hora. La diferencia es
#: este freno, y solo este.
#:
#: Con 20.000 tokens por minuto y fichas de ~225 tokens salen unas 89 fichas
#: por minuto, o sea 1,5 por segundo. De ahi se saca cuanto va a tardar
#: cualquier sincronizacion: fichas / 89 = minutos.
#:
#: SE PUEDE SUBIR, Y CONVIENE COMPROBARLO
#: --------------------------------------
#: La documentacion publica de Google da 30.000 tokens por minuto para la
#: capa gratuita de embeddings, un 50% mas de lo que se midio aqui. La
#: diferencia puede ser del modelo, del momento, o de que el limite sea por
#: proyecto y no por clave. No se sube a ciegas: la medida propia es prueba y
#: la cifra de internet no.
#:
#: Lo honesto es dejarlo ajustable y que se compruebe contra el limite real
#: del proyecto, que aparece en la consola de AI Studio. Subirlo a 28.000
#: acortaria las sincronizaciones un 40% sin pagar nada; pasarse solo
#: provoca 429 y reintentos de 40 y 70 segundos, que es MAS lento que ir
#: frenado.
TOKENS_POR_MINUTO = int(os.environ.get("TOKENS_POR_MINUTO") or 40000)

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
    cliente = corpus.cliente_gemini()
    if cliente is None:
        raise RuntimeError(
            "Falta GEMINI_API_KEY en el .env. Los embeddings los genera "
            "Gemini, asi que sin clave no se puede construir el indice."
        )
    # Se pide a `corpus`, que lo guarda, en vez de construir uno aqui. Antes
    # habia dos sitios creando clientes y ninguno lo reutilizaba: se midio
    # que construirlo cuesta entre 1,5 y 9 segundos, cada vez.
    return cliente


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
            progreso=None, reintentar: bool = True,
            permitir_huecos: bool = False) -> list:
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
        permitir_huecos: Si True, devuelve `None` en los fragmentos que no se
            pudieron vectorizar en vez de levantar. Quien llama se queda con
            lo que si hay y TIENE QUE declarar lo que falta. Ver el motivo en
            el cuerpo de la funcion.

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
        # Si hoy ya no queda cuota, no se vuelve a preguntar: cada intento
        # cuesta mas de dos minutos de espera para acabar en el mismo sitio.
        if permitir_huecos and _CUOTA["seguidos"] >= LOTES_PARA_RENDIRSE:
            continue
        try:
            vectores = _embeber_lote(cliente, lote, config,
                                     reintentar=reintentar)
            _CUOTA["seguidos"] = 0
        except Exception as exc:
            # Con huecos permitidos, un lote que no sale adelante deja sus
            # fragmentos sin vector y se sigue con los demas. Sin huecos,
            # el error sube como siempre.
            if _es_de_cuota(exc):
                _CUOTA["seguidos"] += 1
            if not permitir_huecos:
                raise
            continue

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

    # ANTES AQUI SE RELLENABA CON CEROS, Y ESO ERA EL DESASTRE
    # --------------------------------------------------------
    # El razonamiento parecia prudente: Qdrant rechaza un None y tumbaria la
    # indexacion entera, asi que se ponia un vector de ceros y se seguia. El
    # problema es lo que significa un vector de ceros en una busqueda por
    # significado: no se parece a NADA. La ficha entra al indice, cuenta en
    # los totales, se ve en el panel... y no aparece nunca en una respuesta.
    #
    # Un documento que falla y lo dice se arregla. Un documento que falla en
    # silencio se descubre meses despues, preguntandose por que el agente no
    # encuentra lo que tiene delante.
    # PERO QUEDARSE CON LO QUE SI HAY NO ES LO MISMO QUE RELLENAR CON CEROS
    # ----------------------------------------------------------------------
    # Un vector de ceros MIENTE: la ficha entra, cuenta en los totales y no
    # aparece jamas en una busqueda. Omitirla no miente: esa ficha no esta, y
    # quien llama lo dice en el informe.
    #
    # La diferencia importa el dia que la cuota se agota a mitad. Un
    # documento de 100 fichas con 90 ya en la cache fallaba ENTERO por las 10
    # que faltaban, y se perdian tambien las 90 que no costaban nada. Con el
    # indice recien reconstruido eso es la diferencia entre recuperar lo que
    # el sistema ya sabia y no recuperarlo.
    faltan_ahora = [i for i, v in enumerate(resultado) if v is None]
    if faltan_ahora and permitir_huecos and len(faltan_ahora) < len(textos):
        return resultado
    if faltan_ahora:
        raise RuntimeError(
            f"{len(faltan_ahora)} de {len(textos)} fragmentos se quedaron "
            f"sin vector. No se indexan en ceros: una ficha con vector nulo "
            f"no sale jamas en una busqueda y nadie se entera")
    return resultado


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
                model=MODELO_EMBEDDINGS, contents=_como_documentos(lote),
                config=config)
            vectores = [list(e.values) for e in (r.embeddings or [])]
            # EL FALLO QUE NADIE VEIA
            # -----------------------
            # Si la API devuelve menos vectores de los que se pidieron, los
            # textos que se quedaron sin vector acababan mas abajo con un
            # vector de ceros, y un vector de ceros no se parece a nada: la
            # ficha entra al indice y NUNCA sale en una busqueda. El agente
            # decia "no tengo informacion" sobre documentos que estaban
            # perfectamente indexados.
            #
            # Se midio cuando ya habia pasado: 12.218 de 14.532 fichas, el
            # 84% del indice, guardadas en ceros. Nada lo avisaba porque
            # nada lo comprobaba. Esta linea es esa comprobacion.
            if len(vectores) != len(lote):
                raise RuntimeError(
                    f"se pidieron {len(lote)} vectores y la API devolvio "
                    f"{len(vectores)}")
            return vectores
        except Exception as exc:
            ultimo_error = exc
            if not _es_cuota(exc):
                raise
            if intento < len(esperas):
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


def _como_documentos(lote: list) -> list:
    """
    Envuelve cada texto para que la API lo trate como un documento aparte.

    POR QUE NO SE MANDAN LAS CADENAS A SECAS
    ----------------------------------------
    Porque el cliente de Gemini NO interpreta una lista de cadenas como una
    lista de documentos: la interpreta como UN documento partido en trozos.
    Se le mandaban 32 fichas y devolvia un vector -- el de las 32 pegadas --
    sin dar ningun error.

    El efecto era doble y los dos lados eran silenciosos. La ficha que si
    recibia vector recibia el equivocado, y las otras 31 acababan con un
    vector de ceros, que no se parece a ninguna consulta y por tanto no
    aparece jamas en una busqueda. El indice crecia, los numeros cuadraban y
    el agente seguia diciendo que no encontraba nada.

    Envolviendo cada texto en su propio `Content` la API devuelve tantos
    vectores como textos. Se comprueba ademas en `_embeber_lote`, porque de
    esto no se vuelve a uno fiar.

    Args:
        lote: Textos del lote.

    Returns:
        Lista de contenidos, uno por texto.
    """
    from google.genai import types

    return [types.Content(parts=[types.Part(text=t)]) for t in lote]


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


def _asegurar_indices(cliente, nombre: str) -> None:
    """
    Crea los indices de payload que necesita la coleccion.

    Se llama SIEMPRE, exista ya la coleccion o no: crear un indice
    que ya esta no cuesta nada, y no crearlo cuesta la busqueda.

    Args:
        cliente: Cliente de Qdrant.
        nombre: Nombre de la coleccion.
    """
    from qdrant_client import models

    # El modo embebido los ignora y avisa por consola, asi que solo se
    # crean cuando hay servidor detras.
    if not en_servidor():
        return

    # UN INDICE DE TEXTO SOBRE `texto`, QUE ES LO QUE SALVA LOS DIAS SIN CUOTA
    # -------------------------------------------------------------------------
    # Buscar por significado cuesta una llamada a la API por pregunta. Cuando
    # la cuota diaria se agota, el agente deja de poder consultar su propio
    # indice: puede tener miles de fichas dentro y contestar "no se encontro
    # nada". Con este indice se puede buscar por palabras, que no cuesta nada
    # y no depende de nadie.
    try:
        cliente.create_payload_index(
            collection_name=nombre, field_name="texto",
            field_schema=models.TextIndexParams(
                type=models.TextIndexType.TEXT,
                tokenizer=models.TokenizerType.MULTILINGUAL,
                min_token_len=3, max_token_len=30, lowercase=True))
    except Exception:
        # Si esta version de Qdrant no admite el tokenizador multilingue, se
        # intenta con el sencillo antes de rendirse.
        try:
            cliente.create_payload_index(
                collection_name=nombre, field_name="texto",
                field_schema=models.TextIndexParams(
                    type=models.TextIndexType.TEXT,
                    tokenizer=models.TokenizerType.WORD,
                    min_token_len=3, max_token_len=30, lowercase=True))
        except Exception:
            pass  # ya existia, o esta version no lo admite

    for campo in ("documento", "clave", "huella", "categoria"):
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
            # LOS INDICES SE REVISAN AUNQUE LA COLECCION YA ESTE BIEN
            # -------------------------------------------------------
            # Esto salia por aqui y no miraba los indices de payload. El dia
            # que se anadio el indice de texto -- el que permite buscar sin
            # gastar cuota -- no se creo en ninguna coleccion existente, que
            # son justamente todas. Crear un indice que ya esta no cuesta
            # nada; no crearlo cuesta la busqueda entera.
            _asegurar_indices(cliente, nombre)
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
    _asegurar_indices(cliente, nombre)


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


def clave_de_documento(doc: dict) -> str:
    """
    Devuelve lo que identifica a un documento DE FORMA UNICA en el indice.

    POR QUE EL NOMBRE NO BASTA
    --------------------------
    El indice identificaba cada documento por su nombre de archivo, y en el
    Drive del proyecto hay 14 nombres repetidos en carpetas distintas:
    "6. Plantas y mariposas Jardin Botanico Medellin.pdf" esta en dos sitios,
    "PLAN DE ACCION POLINIZADORES_C Gutierrez.pdf" tambien, y asi hasta
    catorce.

    Con el nombre como identidad pasaban tres cosas, las tres en silencio:

      - Los identificadores de sus fichas chocaban, asi que el segundo
        documento escribia ENCIMA de las fichas del primero.
      - Antes de guardar, cada documento borra las fichas del que se llame
        igual: el segundo borraba las del primero a proposito.
      - El registro de huellas tambien iba por nombre, asi que el sistema
        creia que los dos estaban indexados cuando solo quedaba uno.

    Resultado: catorce documentos que estan en Drive, que se bajaron, que se
    leyeron y que costaron su cuota de vectores, y que el agente no podia
    encontrar. Nadie se enteraba, porque el recuento salia bien.

    La carpeta no es un adorno: es parte de donde vive el documento. Dos
    archivos con el mismo nombre en carpetas distintas son dos documentos.

    Args:
        doc: Documento de la fuente, con `nombre` y `categoria`.

    Returns:
        Cadena del tipo "1_NORMATIVIDAD/Decreto 1076 de 2015.pdf".
    """
    categoria = (doc.get("categoria") or "").strip() or "(raiz)"
    return f"{categoria}/{doc.get('nombre') or ''}"


def _quitar_claves_repetidas(documentos: list) -> tuple:
    """
    Deja un solo documento por clave, y dice cuales venian repetidos.

    DRIVE ADMITE DOS ARCHIVOS CON EL MISMO NOMBRE EN LA MISMA CARPETA
    ----------------------------------------------------------------
    No es un caso de laboratorio: en "5. CATALAGOS MARIPOSAS" hay dos
    `Libro_Mariposas_Cornare.pdf`, con identificadores distintos en Drive y
    EL MISMO md5. Son la misma subida hecha dos veces.

    Para el indice son el mismo documento, y debe serlo: vectorizar dos
    veces el mismo texto gasta el doble de cuota y hace que cada busqueda
    devuelva el mismo parrafo dos veces, como si dos fuentes distintas lo
    confirmaran.

    Lo que NO puede pasar en silencio es el otro caso: mismo sitio, mismo
    nombre y contenido DISTINTO. Ahi no hay forma de distinguirlos por su
    clave, cada pasada veria una huella distinta y se reindexarian el uno
    sobre el otro para siempre. Eso se dice.

    Args:
        documentos: Lo que devolvio la fuente.

    Returns:
        Tupla `(documentos_sin_repetir, repetidos)`, donde `repetidos` es
        una lista de diccionarios con `clave`, `cuantos` y `mismo_contenido`.
    """
    por_clave = {}
    orden = []
    for doc in documentos:
        clave = clave_de_documento(doc)
        if clave not in por_clave:
            por_clave[clave] = []
            orden.append(clave)
        por_clave[clave].append(doc)

    limpios, repetidos = [], []
    for clave in orden:
        grupo = por_clave[clave]
        limpios.append(grupo[0])
        if len(grupo) > 1:
            huellas = {d.get("huella") for d in grupo}
            repetidos.append({
                "clave": clave,
                "cuantos": len(grupo),
                "mismo_contenido": len(huellas) == 1,
            })
    return limpios, repetidos


def _id_de_ficha(documento: str, indice: int) -> str:
    """
    Calcula un identificador estable para una ficha.

    Tiene que ser estable para que reindexar el mismo documento pise sus
    propios puntos en vez de duplicarlos.

    Args:
        documento: CLAVE del documento de origen (ver `clave_de_documento`),
            no su nombre a secas: dos archivos con el mismo nombre en
            carpetas distintas daban el mismo identificador y se pisaban las
            fichas el uno al otro.
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

    Se indexa por CLAVE, no por nombre: ver `clave_de_documento`. Un punto
    sin clave es de antes de ese cambio, y se ignora a proposito. Asi su
    documento vuelve a entrar con la identidad buena, y el punto viejo se va
    por el camino de los sobrantes en vez de quedarse como un fantasma al
    que nadie puede llegar.

    Returns:
        Diccionario `{clave_documento: huella}`.
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
            with_payload=["clave", "documento", "huella"], with_vectors=False)
        for p in puntos:
            carga = p.payload or {}
            if carga.get("documento") == "__firma__":
                continue
            clave = carga.get("clave")
            if clave:
                encontradas[clave] = carga.get("huella", "")
        if siguiente is None:
            break
    return encontradas


#: Cada cuantos segundos mira el vigilante la carpeta de descargas.
SEGUNDOS_ENTRE_VIGILANCIAS = 15

#: Cuanta caida se considera una desaparicion y no un borrado normal. Un
#: estudio no borra archivos mientras trabaja, asi que cualquier bajada
#: brusca es alguien de fuera.
CAIDA_SOSPECHOSA = 0.5


def _vigilar_la_carpeta():
    """
    Vigila la carpeta de descargas mientras dura el estudio.

    POR QUE HACE FALTA MIRAR, Y NO SOLO AGUANTAR
    --------------------------------------------
    Tres pasadas seguidas acabaron diciendo que trescientos y pico
    documentos "no llegaron a bajarse" cuando se habian bajado los 3,2 GB:
    algo vacio la carpeta a mitad, entre la descarga y la lectura. El
    estudio ya sabe recuperarse -- vuelve a pedir el documento que no
    encuentra --, pero recuperarse no es lo mismo que saber que paso.

    Esto no impide nada: solo deja constancia de CUANDO se vacio y de
    cuantos archivos habia justo antes. Con esa hora se puede cruzar con lo
    que estuviera corriendo en ese momento, que es la unica forma de cerrar
    el caso en vez de seguir sospechando.

    Returns:
        Tupla `(hilo, parar, registro)`. El hilo ya esta en marcha; `parar`
        es el evento que lo detiene; `registro` se va rellenando y trae
        `maximo` y `desapariciones`.
    """
    import threading

    registro = {"maximo": 0, "desapariciones": []}
    parar = threading.Event()

    try:
        carpeta = fuentes.obtener().carpeta_temporal()
    except Exception:
        # Sin carpeta que vigilar no se monta nada. Vigilar es util, no
        # imprescindible: un fallo aqui no puede costar el estudio.
        return None, parar, registro

    def _cuantos_hay():
        try:
            return sum(len(a) for _, _, a in os.walk(carpeta))
        except OSError:
            return 0

    def _mirar():
        anterior = _cuantos_hay()
        while not parar.wait(SEGUNDOS_ENTRE_VIGILANCIAS):
            ahora = _cuantos_hay()
            registro["maximo"] = max(registro["maximo"], ahora, anterior)
            if anterior and ahora < anterior * CAIDA_SOSPECHOSA:
                registro["desapariciones"].append({
                    "cuando": _ahora_iso(),
                    "habia": anterior,
                    "quedan": ahora,
                })
            anterior = ahora

    hilo = threading.Thread(target=_mirar, daemon=True)
    hilo.start()
    return hilo, parar, registro


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


def _clave_de_catalogo(descartado: dict) -> str:
    """
    Clave unica de la ficha de una capa que no se leyo entera.

    Va aparte de `clave_de_documento` porque en el indice estas fichas
    llevan `categoria = CATEGORIA_CAPA`, que es una etiqueta, no la carpeta
    donde vive el archivo. Para identificarlo hace falta su carpeta de
    verdad.

    Args:
        descartado: Entrada de `fuente.descartados`.

    Returns:
        Cadena del tipo "5. CATALAGOS MARIPOSAS/capa_enorme.csv".
    """
    return clave_de_documento(descartado)


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
                 if ya_estan.get(_clave_de_catalogo(d))
                 != _huella_de_catalogo(d)]
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
            id=_id_de_ficha(_clave_de_catalogo(ficha), numero),
            vector={NOMBRE_VECTOR: vector},
            payload={
                "clave": _clave_de_catalogo(ficha),
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

    # 1. Obtenemos inventario SIN descargar archivos de Google Drive
    documentos = fuente.documentos(descargar=False)
    documentos, _repetidos = _quitar_claves_repetidas(documentos)
    ya_estan = {} if forzar else huellas_indexadas(cliente)
    # Desde aqui ya puede haber archivos bajados. Lo que siga va envuelto para
    # que el borrado ocurra tambien si algo falla.

    pendientes = [d for d in documentos
                  if ya_estan.get(clave_de_documento(d)) != d["huella"]]

    # Priorizar carpetas de Google Drive segun arquitectura del agente
    def _prioridad_cat(doc):
        cat = str(doc.get("categoria", "")).lower()
        if "agente" in cat or "documentos de agente" in cat: return 1
        if "catalago" in cat or "catalogo" in cat or "monitoreo" in cat or "mariposa" in cat: return 2
        if "hospedera" in cat or "viviero" in cat or "vivero" in cat or "planta" in cat: return 3
        if "ciclo" in cat or "anatomia" in cat or "morfologia" in cat: return 4
        if "ecologica" in cat or "bioindicador" in cat: return 5
        if "normatividad" in cat or "metodologia" in cat or "eia" in cat: return 6
        if "zoocria" in cat or "mariposario" in cat: return 7
        if "plan de negocio" in cat or "exportacion" in cat or "artesania" in cat or "trazabilidad" in cat: return 8
        if "sanitario" in cat: return 9
        return 6

    pendientes.sort(key=_prioridad_cat)
    nuevos = sum(1 for d in pendientes
                 if clave_de_documento(d) not in ya_estan)
    resumen = {
        "nuevos": nuevos,
        "actualizados": len(pendientes) - nuevos,
        "sin_cambios": len(documentos) - len(pendientes),
        "borrados": 0,
        "fichas": 0,
        "fallidos": [],
        "segundos": 0.0,
    }

    # Lo que venia repetido se declara. Una copia identica subida dos veces
    # se indexa una sola vez y punto; dos archivos distintos con el mismo
    # nombre en la misma carpeta son un problema que hay que ver, porque el
    # indice no puede distinguirlos y uno de los dos nunca se leera.
    if _repetidos:
        resumen["repetidos"] = _repetidos
        _chocan = [r for r in _repetidos if not r["mismo_contenido"]]
        if _chocan:
            resumen.setdefault("fallidos", []).extend(
                {"documento": r["clave"],
                 "motivo": (f"hay {r['cuantos']} archivos con este mismo "
                            f"nombre en la misma carpeta y con CONTENIDO "
                            f"DISTINTO. El indice no puede distinguirlos: "
                            f"solo se lee uno. Renombra uno de los dos en "
                            f"Drive.")}
                for r in _chocan)

    # --- Documentos que ya no estan en la fuente ------------------------------
    # Las capas grandes tambien estan EN la fuente, aunque no se lean enteras:
    # cuentan como vigentes. Sin esto, sus fichas de catalogo se borraban al
    # principio de cada pasada y se volvian a crear al final, y el resumen
    # decia "4 borrados" en todas las sincronizaciones aunque no hubiera
    # cambiado nada. Ademas gastaba cuatro embeddings cada vez.
    vigentes = {clave_de_documento(d) for d in documentos}
    vigentes |= {_clave_de_catalogo(d)
                 for d in getattr(fuente, "descartados", [])}
    sobrantes = [c for c in ya_estan if c not in vigentes]
    if sobrantes:
        cliente.delete(
            collection_name=nombre,
            points_selector=models.FilterSelector(filter=models.Filter(
                must=[models.FieldCondition(
                    key="clave",
                    match=models.MatchAny(any=sobrantes))])))
        resumen["borrados"] = len(sobrantes)

    # --- Documentos nuevos o modificados --------------------------------------
    for n, doc in enumerate(pendientes, start=1):
        resumen["_fraccion"] = n / max(len(pendientes), 1)
        puesto = f"[{n}/{len(pendientes)}]"
        if progreso:
            progreso(f"{puesto} {doc['nombre'][:46]}", resumen["_fraccion"])
        try:
            _indexar_documento(cliente, nombre, doc, resumen, progreso,
                               puesto, fuente)
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
    # Se cuelga el cartel ANTES de bajar nada: a partir de aqui, la carpeta
    # de descargas es de este estudio y nadie mas puede vaciarla. Se retira
    # justo antes de limpiar, para que la limpieza propia -- la unica que
    # debe ocurrir -- no se bloquee a si misma.
    fuentes.empezar_estudio()
    olvidar_el_mal_dia()   # cada pasada estrena cuota: ayer no cuenta
    _vigilante, _parar, _vigilancia = _vigilar_la_carpeta()
    try:
        resumen = _indexar_todo(forzar=forzar, progreso=progreso)
    finally:
        _parar.set()
        if _vigilante is not None:
            _vigilante.join(timeout=5)
        fuentes.terminar_estudio()
        limpieza = limpiar_descargas()
        # Despues de sincronizar SI ha podido cambiar lo que hay en la fuente,
        # asi que el estado guardado deja de valer.
        try:
            fuentes.olvidar_estado()
        except Exception:
            pass
    resumen["limpieza"] = limpieza
    if _vigilancia.get("desapariciones"):
        resumen["desapariciones"] = _vigilancia["desapariciones"]
    resumen["maximo_en_disco"] = _vigilancia.get("maximo", 0)
    return resumen


def _fichas_de_presencia(nombre_doc: str, fichas: list) -> list:
    """
    Construye unas pocas fichas que describen un documento demasiado grande.

    POR QUE NO SE DEJA EN NADA
    --------------------------
    Antes, un documento que se pasaba del presupuesto se descartaba y no
    dejaba RASTRO en el indice. El efecto practico es el peor posible: el
    agente consulta el indice, no encuentra nada, y responde "no tengo
    informacion" sobre un archivo que estuvo en el disco. Eso se lee como
    "ese documento no existe", que es falso.

    Un documento de texto no se puede agregar como una tabla -- no hay
    columnas que agrupar --, asi que lo que se indexa es su PRESENCIA: cuanto
    mide, cuantas fichas costaria entero, como empieza y que terminos
    aparecen mas. Son tres o cuatro fichas en vez de las miles que costaria
    completo, y bastan para que el agente sepa que existe, de que trata y que
    hace falta ampliar el presupuesto para leerlo entero.

    Los terminos se cuentan EN LOCAL: contar palabras no gasta cuota.

    Args:
        nombre_doc: Nombre del documento.
        fichas: Las fichas que habria dado entero, como `(pagina, texto)`.

    Returns:
        Lista corta de tuplas `(pagina, texto)` lista para indexar.
    """
    from collections import Counter

    paginas = {p for p, _ in fichas}
    cabecera = [
        f"[Presencia de {nombre_doc}]",
        f"Documento de {len(paginas)} paginas que daria "
        f"{len(fichas)} fichas, por encima del presupuesto de "
        f"{corpus.PRESUPUESTO_FICHAS}. Se leyo COMPLETO pero solo se indexa "
        f"esta descripcion: para buscar dentro de el hace falta ampliar el "
        f"presupuesto de fichas.",
    ]

    # Terminos mas frecuentes, sin las palabras de funcion que no distinguen
    # nada. No es un resumen semantico: es una pista de contenido que sale
    # gratis.
    vacias = {
        "para", "como", "esta", "este", "esto", "esas", "esos", "pero", "mas",
        "sus", "los", "las", "del", "una", "uno", "unos", "unas", "que", "con",
        "por", "sin", "sobre", "entre", "desde", "hasta", "donde", "cuando",
        "ser", "son", "fue", "han", "hay", "the", "and", "for", "with",
    }
    cuenta = Counter()
    for _pagina, texto in fichas:
        for palabra in texto.lower().split():
            limpia = "".join(c for c in palabra if c.isalpha())
            if len(limpia) > 3 and limpia not in vacias:
                cuenta[limpia] += 1
    if cuenta:
        cabecera.append("Terminos mas frecuentes: " + ", ".join(
            f"{palabra} ({veces})" for palabra, veces in cuenta.most_common(40)))

    salida = [(1, chr(10).join(cabecera))]
    # Como empieza, que suele traer el titulo, el autor y el objeto del
    # documento. Es lo que mas ayuda a reconocerlo en una busqueda.
    for pagina, texto in fichas[:2]:
        salida.append((pagina, texto))
    return salida


def _conviene_resumir(ruta: str) -> bool:
    """
    Dice si una tabla hay que recorrerla en streaming en vez de abrirla.

    POR QUE EXISTE
    --------------
    `corpus.leer_documento` carga el archivo ENTERO en memoria: para Excel
    pasa por pandas, que construye un DataFrame con todas las filas. Un libro
    de 400 MB se convierte en varios gigas de RAM y tumba el contenedor
    ANTES de llegar al guardia del presupuesto, que es justo quien deberia
    haberlo parado. Por eso la decision tiene que tomarse por el TAMANO, sin
    abrir nada.

    DE DONDE SALE EL UMBRAL
    -----------------------
    Del presupuesto mismo: `PRESUPUESTO_FICHAS` fichas de `TAMANO_FICHA`
    caracteres son 1,72 MB de texto. Por debajo de eso ninguna tabla puede
    pasarse del presupuesto, asi que se indexa entera y punto.

    Se midio que el umbral vale igual para Excel que para CSV: un .xlsx da
    unas 1.500 fichas por MB (2.000 filas -> 1.336; 20.000 -> 1.508;
    100.000 -> 1.535) y un CSV tabular daba 1.485. La compresion del ZIP y el
    relleno del XML casi se anulan entre si, asi que no hace falta un umbral
    aparte por formato.

    Args:
        ruta: Archivo ya descargado.

    Returns:
        True si hay que desviarlo al resumen agregado.
    """
    from core import catalogo as _cat

    if os.path.splitext(ruta)[1].lower() not in _cat.FORMATOS_RESUMIBLES:
        return False
    try:
        if os.path.getsize(ruta) <= (corpus.PRESUPUESTO_FICHAS
                                     * corpus.TAMANO_FICHA):
            return False
    except OSError:
        return False
    # La extension no basta. Un informe de prosa de 3 MB tambien es un .txt,
    # y agruparlo por columnas no significa nada: hay que mirar si de verdad
    # tiene filas con la misma forma.
    return _cat.parece_tabla(ruta)


def _indexar_documento(cliente, nombre: str, doc: dict, resumen: dict,
                       progreso=None, puesto: str = "", fuente=None) -> None:
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

    
    # Descargar bajo demanda UNICAMENTE este documento si aun no esta en disco
    if fuente is not None and hasattr(fuente, "asegurar_descargado"):
        ruta_doc = doc.get("ruta", "")
        if not ruta_doc or not os.path.exists(ruta_doc) or os.path.getsize(ruta_doc) == 0:
            fuente.asegurar_descargado(doc)

    fichas = []

    # TABLA GRANDE: SE RECORRE COMPLETA Y SE INDEXA SU RESUMEN
    # --------------------------------------------------------
    # Antes de este desvio, un Excel grande se bajaba entero, se intentaba
    # abrir con pandas y -- si sobrevivia a la memoria -- se descartaba abajo
    # por presupuesto SIN dejar nada: ni catalogo ni resumen. Se gastaba la
    # descarga y el agente seguia diciendo "no tengo informacion" de un
    # archivo que habia tenido en el disco.
    #
    # Ahora se recorre en streaming de punta a punta, se agrupa y se indexa
    # el agregado. La lectura es COMPLETA -- el recuento de filas y las sumas
    # son exactos sobre todo el archivo -- y no gasta ni un embedding en
    # recorrerlo: lo unico que se vectoriza son las pocas decenas de lineas
    # del resumen.
    if _conviene_resumir(doc["ruta"]):
        from core import catalogo as _cat

        datos = _cat.resumir_tabla(doc["ruta"])
        if not datos.get("lineas"):
            # No se pudo recorrer. Se dice por que y NO se cae al lector
            # normal: abrirlo con pandas es justo lo que hay que evitar.
            resumen.setdefault("no_resumidas", []).append({
                "documento": doc["nombre"],
                "error": datos.get("error") or "no se pudo recorrer",
            })
            if progreso:
                progreso(f"{puesto} {doc['nombre'][:42]} — no se pudo "
                         f"recorrer", resumen.get("_fraccion", 0.0))
            return
        for trozo in corpus.trocear(chr(10).join(datos["lineas"])):
            fichas.append((1, trozo))
        resumen.setdefault("resumidas", []).append({
            "documento": doc["nombre"],
            "filas": datos["filas"],
            "fichas": len(fichas),
            "segundos": datos["segundos"],
        })
        if progreso:
            progreso(f"{puesto} {doc['nombre'][:42]} — "
                     f"{datos['filas']} filas recorridas, "
                     f"{len(fichas)} fichas de resumen",
                     resumen.get("_fraccion", 0.0))
    else:
        # NO ESTAR Y NO DECIR NADA SON DOS COSAS DISTINTAS
        # ------------------------------------------------
        # Se contaban juntas bajo "no dio texto", y no se parecen en nada:
        # un PDF escaneado hay que pasarlo por OCR, y un archivo que no
        # llego al disco hay que volver a bajarlo. Se midio una pasada
        # entera en la que 355 de 358 documentos "sin texto" sencillamente
        # no existian en la carpeta, y el informe decia que no daban texto.
        # Dos horas buscando un problema de lectura que era de descarga.
        if not os.path.exists(doc["ruta"]):
            # SE VUELVE A PEDIR ANTES DE DARLO POR PERDIDO
            # ---------------------------------------------
            # Entre que se baja un documento y que le llega el turno de
            # leerse pueden pasar cuarenta minutos, y en esos cuarenta
            # minutos la carpeta puede desaparecer: tres veces en dos dias.
            # Antes eso se anotaba y se seguia, y la pasada terminaba
            # diciendo "387 de 425 no llegaron a bajarse" con 3,2 GB ya
            # gastados en bajarlos.
            #
            # Perseguir a quien borra es una cosa; no depender de ello es
            # otra, y es la que hace que el estudio llegue al final. Bajarlo
            # otra vez cuesta una peticion; perder la pasada cuesta horas.
            if fuente is not None and hasattr(fuente, "asegurar_descargado"):
                try:
                    fuente.asegurar_descargado(doc)
                    if os.path.exists(doc["ruta"]):
                        resumen["rebajados"] = resumen.get("rebajados", 0) + 1
                        if progreso:
                            progreso(f"{puesto} {doc['nombre'][:42]} — habia "
                                     f"desaparecido del equipo; se baja otra "
                                     f"vez", resumen.get("_fraccion", 0.0))
                except Exception as _exc:
                    doc = dict(doc)
                    doc["no_se_pudo_bajar"] = (
                        f"{type(_exc).__name__}: {str(_exc)[:140]}")

        # Si despues de volver a pedirlo sigue sin estar, ahora si se da por
        # perdido, con su motivo.
        if not os.path.exists(doc["ruta"]):
            # Si la fuente sabe POR QUE no bajo, se guarda su motivo. No es
            # lo mismo "Drive devolvio 403" que "alguien borro la carpeta a
            # mitad": lo primero se arregla con permisos y lo segundo
            # relanzando. Sin el motivo, las dos se leian igual.
            _por_que = doc.get("no_se_pudo_bajar") or ""
            resumen.setdefault("sin_descargar", []).append({
                "documento": doc["nombre"],
                "ruta": doc["ruta"],
                "motivo": _por_que or "no estaba en el equipo al ir a leerlo",
            })
            if progreso:
                progreso(f"{puesto} {doc['nombre'][:42]} — no se pudo bajar"
                         + (f": {_por_que[:60]}" if _por_que else ""),
                         resumen.get("_fraccion", 0.0))
            return

        for pagina, texto in corpus.leer_documento(doc["ruta"]):
            for trozo in corpus.trocear(texto):
                if merece_vector(trozo):
                    fichas.append((pagina, trozo))

    if not fichas:
        # NO SE SALE EN SILENCIO
        # ----------------------
        # Que un archivo no diera texto se trataba como un no-evento: se
        # hacia `return` y nadie se enteraba. Pero las dos causas que lo
        # provocan de verdad no son "estaba vacio":
        #
        #   - una imagen que no se pudo transcribir, porque la cuota del
        #     modelo se agoto o porque falta la clave
        #   - un PDF escaneado, que es una imagen sin capa de texto
        #
        # En los dos casos el archivo SI tiene contenido y hay que volver a
        # intentarlo otro dia. Se deja constancia en el informe para que se
        # vea, en vez de que el archivo se esfume.
        resumen.setdefault("sin_contenido", []).append({
            "documento": doc["nombre"],
            "extension": os.path.splitext(doc["ruta"])[1].lower(),
            "mb": round(os.path.getsize(doc["ruta"]) / (1024 * 1024), 2)
                  if os.path.exists(doc["ruta"]) else 0,
        })
        if progreso:
            progreso(f"{puesto} {doc['nombre'][:42]} — no dio texto, "
                     f"queda pendiente", resumen.get("_fraccion", 0.0))
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
        # NO se abandona. Se cambian sus miles de fichas por tres que digan
        # que existe, de que trata y que esta sin leer del todo. Antes se
        # hacia `return` y el documento desaparecia del indice, asi que el
        # agente respondia "no tengo informacion" sobre algo que si tenia.
        resumen.setdefault("fuera_de_presupuesto", []).append({
            "documento": doc["nombre"],
            "fichas": len(fichas),
            "presupuesto": corpus.PRESUPUESTO_FICHAS,
            "indexado_como": "presencia",
        })
        if progreso:
            progreso(f"{puesto} {doc['nombre'][:42]} — {len(fichas)} fichas, "
                     f"fuera de presupuesto: se indexa su presencia",
                     resumen.get("_fraccion", 0.0))
        fichas = _fichas_de_presencia(doc["nombre"], fichas)

    # El documento mas grande del corpus pesa 2,5 MB y da miles de fichas: sin
    # avisar por dentro, la indexacion parecia colgada ocho minutos seguidos
    # sobre el mismo nombre. Se informa ficha a ficha.
    def _dentro(hechas, total):
        if progreso:
            progreso(f"{puesto} {doc['nombre'][:42]} — {hechas}/{total} fichas",
                     resumen.get("_fraccion", 0.0))

    # SE PIDE PODER QUEDARSE CON LO QUE HAYA
    # ---------------------------------------
    # Casi todas las fichas de un documento ya leido otro dia estan en la
    # cache local y no cuestan cuota. Si el documento cambio un poco, las
    # pocas fichas nuevas si la cuestan -- y con la cuota agotada el
    # documento ENTERO se perdia, incluidas las que eran gratis.
    #
    # Ahora se indexa lo que se consiguio y se declara lo que falta. Una
    # ficha ausente no engana a nadie; una ficha con vector de ceros si, y
    # eso se sigue sin hacer.
    vectores = embeber([t for _, t in fichas], "RETRIEVAL_DOCUMENT",
                       progreso=_dentro, permitir_huecos=True)

    _sin_vector = sum(1 for v in vectores if v is None)
    if _sin_vector:
        enteras = [(f, v) for f, v in zip(fichas, vectores) if v is not None]
        fichas = [f for f, _ in enteras]
        vectores = [v for _, v in enteras]
        resumen.setdefault("incompletos", []).append({
            "documento": doc["nombre"],
            "indexadas": len(fichas),
            "sin_vector": _sin_vector,
        })
        if progreso:
            progreso(f"{puesto} {doc['nombre'][:42]} — entra con "
                     f"{len(fichas)} fichas; {_sin_vector} se quedaron sin "
                     f"vector (cuota)", resumen.get("_fraccion", 0.0))

    _clave = clave_de_documento(doc)

    # Se borran primero los puntos viejos del documento: si la version nueva
    # tiene menos fichas, las sobrantes quedarian huerfanas apuntando a un
    # texto que ya no existe.
    #
    # Y JUSTO AQUI, NO ANTES, SE RETIRA LO DEL FORMATO VIEJO
    # ------------------------------------------------------
    # Los puntos de antes de que un documento se identificara por su sitio no
    # llevan `clave`, asi que ni `huellas_indexadas` los ve ni el reparto de
    # sobrantes los alcanza: se quedarian para siempre.
    #
    # La tentacion es barrerlos todos al empezar la pasada. Seria un error:
    # si la pasada se queda sin cuota a la tercera ficha -- que es lo que
    # esta pasando estos dias --, el indice se habria quedado VACIO a cambio
    # de nada. Asi que cada documento se lleva los suyos en el momento en que
    # va a escribir los nuevos, que es el unico instante en que borrarlos no
    # cuesta informacion.
    cliente.delete(
        collection_name=nombre,
        points_selector=models.FilterSelector(filter=models.Filter(should=[
            models.Filter(must=[models.FieldCondition(
                key="clave",
                match=models.MatchValue(value=_clave))]),
            models.Filter(must=[
                models.FieldCondition(
                    key="documento",
                    match=models.MatchValue(value=doc["nombre"])),
                models.IsEmptyCondition(
                    is_empty=models.PayloadField(key="clave")),
            ]),
        ])))

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
                id=_id_de_ficha(_clave, i),
                vector={NOMBRE_VECTOR: vec},
                payload={
                    # `documento` es lo que se LEE en una cita, y por eso
                    # sigue siendo el nombre a secas. `clave` es lo que
                    # IDENTIFICA, y lleva la carpeta pegada para que dos
                    # archivos con el mismo nombre no sean el mismo punto.
                    "clave": _clave,
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


#: Ultimo recuento de documentos, con el numero de fichas que habia
#: cuando se hizo. Ver `contar_documentos_indexados`.
_DOCUMENTOS_CONTADOS = {"fichas": None, "documentos": 0}


def contar_documentos_indexados(cliente=None, nombre: str = None) -> int:
    """
    Cuenta cuantos DOCUMENTOS distintos hay en el indice.

    EN QUE SE DIFERENCIA DE CONTAR FICHAS, Y POR QUE IMPORTA
    -------------------------------------------------------
    Hasta ahora el agente decia "los 425 documentos del corpus" usando el
    numero que ve la FUENTE, no el que ha leido. Con el indice a medio
    construir eso es una mentira con forma de dato: contesto "no se
    encontraron menciones al genero Pyrrhopyge dentro de los 425 documentos"
    sobre una guia de Hesperiidae de 229 paginas que esta en el Drive, que
    si menciona ese genero, y que simplemente todavia no le habia tocado el
    turno.

    "No lo he leido" y "no existe" son cosas distintas. Para poder decir la
    primera hay que saber cuantos se han leido de verdad.

    Args:
        cliente: Cliente de Qdrant ya abierto.
        nombre: Coleccion a mirar. Por defecto, la configurada.

    Returns:
        Cuantos documentos distintos tienen al menos una ficha en el indice.
    """
    cliente = cliente or _cliente_qdrant()
    nombre = nombre or settings.coleccion_qdrant()
    if not cliente.collection_exists(nombre):
        return 0
    # CONTAR DOCUMENTOS CUESTA RECORRER LA COLECCION ENTERA
    # -----------------------------------------------------
    # Qdrant sabe contar puntos de una, pero no documentos distintos: hay
    # que recorrerlos y agrupar. Con 700 fichas son 20 ms y no se nota; con
    # las 15.000 de un indice completo serian varias decimas, y esto lo
    # llaman varias herramientas en cada turno del chat.
    #
    # El recuento solo cambia cuando cambia el indice, y eso se nota en el
    # numero de fichas, que SI es barato de preguntar. Asi que se guarda el
    # resultado y se reutiliza mientras las fichas sigan siendo las mismas.
    try:
        # `cuantas` y `cuantos` a una letra de distancia es una trampa para
        # quien lea esto dentro de un ano. Se nombran enteros.
        fichas_ahora = contar_fichas(cliente, nombre)
        if _DOCUMENTOS_CONTADOS.get("fichas") == fichas_ahora:
            return _DOCUMENTOS_CONTADOS["documentos"]
        documentos_ahora = len(huellas_indexadas(cliente))
        _DOCUMENTOS_CONTADOS["fichas"] = fichas_ahora
        _DOCUMENTOS_CONTADOS["documentos"] = documentos_ahora
        return documentos_ahora
    except Exception:
        # Contar es util, no imprescindible. Si Qdrant no deja recorrer la
        # coleccion, se devuelve 0 y quien llama lo tratara como "no se sabe"
        # en vez de quedarse sin responder.
        return 0


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
            if isinstance(categoria, (list, tuple, set)):
                filtro = models.Filter(
                    must=[models.FieldCondition(
                        key="categoria", match=models.MatchAny(any=list(categoria)))],
                    must_not=condiciones)
            else:
                filtro = models.Filter(
                    must=[models.FieldCondition(
                        key="categoria", match=models.MatchValue(value=str(categoria)))],
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


#: Palabras que no distinguen nada y solo ensucian una busqueda por texto.
VACIAS = {
    "que", "cual", "cuales", "como", "donde", "cuando", "quien", "para",
    "por", "con", "sin", "del", "las", "los", "una", "uno", "unos", "unas",
    "esta", "este", "esto", "esos", "esas", "sus", "the", "and", "for",
    "sobre", "entre", "desde", "hasta", "tiene", "tienen", "hay", "son",
    "dice", "dicen", "documento", "documentos", "archivo", "bibliografia",
    "corpus", "aparece", "literalmente", "genero", "especie",
}


def buscar_por_texto(consulta: str, limite: int = 8,
                     categoria: str = None) -> list:
    """
    Busca en el indice por PALABRAS, sin gastar ni una llamada a la API.

    POR QUE HACE FALTA, Y POR QUE NO BASTABA LO QUE HABIA
    ------------------------------------------------------
    La busqueda por significado necesita convertir la pregunta en un vector,
    y eso es una llamada a la API de embeddings. Con la cuota diaria agotada
    esa llamada falla, y entonces el agente NO PUEDE CONSULTAR SU PROPIO
    INDICE: puede tener 2.600 fichas dentro y contestar "no se encontro
    nada".

    El respaldo que habia era BM25 sobre la carpeta local, y con Drive esa
    carpeta esta vacia por diseno -- los documentos se borran en cuanto se
    leen --, asi que no respaldaba nada.

    Esto busca sobre el texto que ya vive en el indice. No entiende de
    sinonimos ni de significado, asi que no sustituye a la busqueda
    semantica: la sustituye el dia que la semantica no se puede pagar, que
    es exactamente cuando hace falta un respaldo.

    Args:
        consulta: Pregunta del usuario.
        limite: Cuantas fichas devolver.
        categoria: Restringe a una coleccion tematica.

    Returns:
        Fichas con la misma forma que `buscar_semantico`, ordenadas por
        cuantas palabras de la pregunta aparecen en cada una.
    """
    import re

    from qdrant_client import models

    palabras = [p.lower() for p in re.findall(r"[\wáéíóúüñÁÉÍÓÚÜÑ]{3,}", consulta or "")]
    palabras = [p for p in dict.fromkeys(palabras) if p not in VACIAS]
    if not palabras:
        return []

    try:
        cliente = _cliente_qdrant()
        nombre = settings.coleccion_qdrant()
        if not cliente.collection_exists(nombre):
            return []

        fuera = [models.FieldCondition(
            key="documento", match=models.MatchValue(value="__firma__"))]

        # Se pregunta palabra a palabra y se cuenta en cuantas coincide cada
        # ficha. Qdrant filtra, no puntua, asi que el orden lo pone esto:
        # cuantas palabras distintas de la pregunta aparecen dentro.
        hallazgos = {}
        for palabra in palabras[:6]:
            debe = [models.FieldCondition(
                key="texto", match=models.MatchText(text=palabra))]
            if categoria:
                if isinstance(categoria, (list, tuple, set)):
                    debe.append(models.FieldCondition(
                        key="categoria",
                        match=models.MatchAny(any=list(categoria))))
                else:
                    debe.append(models.FieldCondition(
                        key="categoria",
                        match=models.MatchValue(value=str(categoria))))
            try:
                puntos, _ = cliente.scroll(
                    collection_name=nombre,
                    scroll_filter=models.Filter(must=debe, must_not=fuera),
                    limit=limite * 6, with_payload=True, with_vectors=False)
            except Exception:
                # Sin indice de texto sobre `texto`, Qdrant se niega. No es
                # motivo para tumbar la respuesta: se devuelve lo reunido.
                break
            for p in puntos:
                carga = p.payload or {}
                anterior = hallazgos.get(p.id)
                if anterior is None:
                    hallazgos[p.id] = {"carga": carga, "aciertos": 1}
                else:
                    anterior["aciertos"] += 1

        if not hallazgos:
            return []

        ordenadas = sorted(hallazgos.values(),
                           key=lambda h: (-h["aciertos"],
                                          len(h["carga"].get("texto", ""))))
        return [{
            "documento": h["carga"].get("documento", ""),
            "categoria": h["carga"].get("categoria", ""),
            "pagina": h["carga"].get("pagina"),
            "paginada": h["carga"].get("pagina") is not None,
            "texto": h["carga"].get("texto", ""),
            "fecha_indexado": h["carga"].get("fecha_indexado", ""),
            # El puntaje NO es comparable con el del coseno, y se dice: aqui
            # es "cuantas palabras de la pregunta salen en esta ficha".
            "puntaje": float(h["aciertos"]),
            "por_palabras": True,
        } for h in ordenadas[:limite]]
    except Exception as exc:
        _anotar_fallo_de_busqueda(exc)
        return []



# ==============================================================================
# ENRUTAMIENTO INTELIGENTE DE CARPETAS DE GOOGLE DRIVE POR TAREA
# ==============================================================================

MAPA_CARPETAS_TAREA = {
    ("qgis", "capa", "shapefile", "geopackage", "gpkg", "mapa", "coordenada", "geodatabase", "pomca", "area de estudio", "espacial", "cartografia"): [
        "DOCUMENTOS DE AGENTE", "2. Metodología EIA", "GENERAL"
    ],
    ("mariposa", "lepidoptera", "especie", "genero", "catalogo", "catalago", "monitoreo", "ala", "morfologia", "nombre cientifico", "identificar", "foto", "observacion", "fauna"): [
        "5. CATALAGOS MARIPOSAS", "13. MONITOREO DE MARIPOSAS", "7. ANATOMIA Y MORFOLOGIA"
    ],
    ("planta", "hospedera", "nectarifera", "larval", "hoja", "flor", "passiflora", "aristolochia", "vivero", "viviero", "vegetacion", "flora", "alimento"): [
        "3_PLANTAS HOSPEDERAS", "4. VIVIERO"
    ],
    ("ciclo", "huevo", "larva", "oruga", "pupa", "crisalida", "imago", "metamorfosis", "estadio", "desarrollo"): [
        "6. CICLO DE VIDA", "7. ANATOMIA Y MORFOLOGIA"
    ],
    ("ecologia", "bioindicador", "ecosistema", "bosque", "quebrada", "ribera", "altitud", "clima", "conservacion"): [
        "3.1 FUNCION ECOLOGICA-BIOINDICADORES", "13. MONITOREO DE MARIPOSAS"
    ],
    ("zoocria", "cria", "cautiverio", "reproduccion", "mariposario", "copula", "apareamiento", "jaula", "fecundidad"): [
        "8_ ZOOCRIA DE MARIPOSAS", "9_ MARIPOSARIO", "10_ SANITARIOS"
    ],
    ("norma", "ley", "decreto", "resolucion", "cites", "permiso", "amenaza", "libro rojo", "0126", "uicn", "juridico"): [
        "1_NORMATIVIDAD", "2. Metodología EIA"
    ],
    ("conesa", "impacto", "matriz", "severidad", "importancia", "mitigacion"): [
        "2. Metodología EIA", "1_NORMATIVIDAD"
    ],
    ("negocio", "bioeconomia", "exportacion", "artesania", "mercado", "costo", "trazabilidad", "cadena"): [
        "11. PLAN DE NEGOCIO", "12_ EXPORTACION", "14. ARTESANIAS", "TRAZABILIDAD DE LA CADENA"
    ],
}


def detectar_carpetas_relevantes(texto: str) -> list:
    """
    Identifica de forma inteligente las carpetas de Google Drive mas pertinentes
    segun las palabras clave y la intencion de la consulta o tarea.
    """
    if not texto:
        return []
    texto_l = texto.lower()
    carpetas = []
    for claves, lista_carpetas in MAPA_CARPETAS_TAREA.items():
        if any(c in texto_l for c in claves):
            for carp in lista_carpetas:
                if carp not in carpetas:
                    carpetas.append(carp)
    return carpetas


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
    # Si no se indico categoria explicita, detectamos las carpetas prioritarias de Drive
    if not categoria:
        carpetas_prioritarias = detectar_carpetas_relevantes(consulta)
        if carpetas_prioritarias:
            fichas_pri = buscar_semantico(consulta, limite=limite, categoria=carpetas_prioritarias)
            if len(fichas_pri) >= min(3, limite):
                return fichas_pri[:limite]

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
        # SIN SEMANTICA Y SIN CARPETA LOCAL NO QUEDABA NADA
        # --------------------------------------------------
        # `corpus.buscar` lee la carpeta del corpus, y con Drive esa carpeta
        # esta vacia por diseno. Asi que el dia que la cuota se agota el
        # respaldo no respaldaba: el agente contestaba "no se encontro nada"
        # con el indice lleno delante.
        #
        # Se busca por palabras DENTRO del indice, que no cuesta cuota.
        if not lexicas:
            return buscar_por_texto(consulta, limite=limite,
                                    categoria=categoria)
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
