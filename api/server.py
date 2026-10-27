"""
Servidor HTTP del sistema (FastAPI).
===============================================================================

Expone como servicios web las mismas capacidades que usa la interfaz Streamlit:
calculo de Conesa, procesamiento por lote, inventario de capas y consulta de
linea base por coordenadas.

Se arranca con `iniciar_servidor.bat`, o manualmente con::

    python -m uvicorn api.server:app --reload

La documentacion interactiva queda en `/docs`.

QUE SE DESQUEMO
---------------
  * Los rangos de los parametros de Conesa ya no se escriben aqui: se leen de
    `core.validator.ConesaValidator`, que es la unica fuente de verdad. Antes
    estaban duplicados y podian desincronizarse del motor de calculo.
  * La carpeta temporal se resuelve en `config.settings`, no con una ruta
    relativa que dependia del directorio desde el que se lanzara el proceso.
  * El nombre del archivo subido se sanea antes de usarlo como ruta. Antes se
    concatenaba tal cual, de modo que un nombre como `../../algo.csv` escribia
    fuera de la carpeta prevista.
"""

import os
import re
import shutil

from fastapi import FastAPI, HTTPException, UploadFile, File
from pydantic import BaseModel, Field

from config import settings
from core.evaluator import ConesaEvaluator
from core.validator import ConesaValidator
from database.gdb_connector import GDBConnector
from database.excel_connector import ExcelConnector


app = FastAPI(
    title="API del Agente de Impacto Ambiental (metodologia Conesa)",
    description=(
        "Servicios de evaluacion de impacto ambiental y consulta geografica "
        "de linea base."
    ),
    version="2.0.0",
)

# ==============================================================================
# PERMISO DE ORIGEN CRUZADO (CORS)
# ==============================================================================
# POR QUE HACE FALTA
# ------------------
# Casi todo lo que llama a esta API lo hace desde el servidor: Streamlit, n8n y
# las pruebas. Esas llamadas no pasan por el control de origen del navegador.
#
# Hay una excepcion: el JavaScript que pega imagenes con Ctrl+V. Ese corre en
# el navegador de quien usa la pagina, que esta servida en el puerto 8501, y
# tiene que escribir en esta API, que esta en el 8000. Para el navegador son
# dos sitios distintos, asi que bloquea la peticion antes de enviarla.
#
# Se midio: sin esto, pegar una imagen fallaba con "Failed to fetch" y el error
# solo aparecia en la consola del navegador. En los registros del servidor no
# se veia absolutamente nada, porque la peticion nunca llegaba a salir.
#
# QUE SE PERMITE, Y QUE NO
# ------------------------
# Solo los origenes de la propia interfaz, no "cualquiera". Esta API puede
# lanzar reindexados y gastar cuota del modelo: abrirla a todo origen dejaria
# que cualquier pagina que uno visite hiciera esas llamadas con el navegador de
# quien la visita. Si la interfaz se publica en otra direccion, se anade con
# ORIGENES_PERMITIDOS, separando por comas.
from fastapi.middleware.cors import CORSMiddleware

_PUERTO_INTERFAZ = os.environ.get("STREAMLIT_PORT", "8501")
ORIGENES_PERMITIDOS = [
    o.strip() for o in os.environ.get(
        "ORIGENES_PERMITIDOS",
        f"http://localhost:{_PUERTO_INTERFAZ},"
        f"http://127.0.0.1:{_PUERTO_INTERFAZ}"
    ).split(",") if o.strip()
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=ORIGENES_PERMITIDOS,
    allow_credentials=False,
    allow_methods=["GET", "POST", "DELETE", "OPTIONS"],
    allow_headers=["Content-Type"],
)

# ==============================================================================
# AJUSTES ELEGIDOS DESDE LA INTERFAZ
# ==============================================================================
# Se vuelcan sobre el entorno ANTES de que nadie lea configuracion. Lo que el
# usuario eligio en la interfaz (por ejemplo, estudiar de Drive en vez de la
# carpeta local) manda sobre lo que diga el .env, que es configuracion de
# despliegue y mas antigua.
from core import ajustes as _ajustes_guardados

_ajustes_guardados.aplicar()


# Al arrancar no puede haber ninguna sincronizacion en curso en este proceso,
# asi que cualquier documento que quede en la carpeta temporal es resto de una
# ejecucion anterior que se corto a la mitad (o de un contenedor que se
# reinicio mientras leia). El compromiso es que nada se conserva en el equipo,
# y ese compromiso no puede depender de que la vez anterior terminara bien.
try:
    from core import vectorial as _vectorial_arranque

    _RESTOS_AL_ARRANCAR = _vectorial_arranque.limpiar_descargas()
except Exception as _exc:  # pragma: no cover - arranque, nunca debe tumbarlo
    _RESTOS_AL_ARRANCAR = {"error": str(_exc)[:120]}


# Recuerdos que se quedaron sin vector por un corte de red. Al arrancar hay
# conexion recien hecha, que es el mejor momento para colocarlos.
try:
    from core import memoria as _memoria_arranque

    _MEMORIA_AL_ARRANCAR = _memoria_arranque.reintentar_pendientes()
except Exception as _exc:  # pragma: no cover
    _MEMORIA_AL_ARRANCAR = {"error": str(_exc)[:120]}


#: Conector espacial. Ya no lanza excepcion si falta la Geodatabase: se
#: construye igual y expone `disponible = False`.
gdb = GDBConnector()

#: Conector de matrices en Excel/CSV.
excel_conn = ExcelConnector()


# ==============================================================================
# MODELOS DE ENTRADA
# ==============================================================================

def _campo(sigla: str):
    """
    Crea la definicion Pydantic de un parametro de Conesa.

    Los limites y la descripcion se toman de `ConesaValidator`, de modo que la
    documentacion de la API y la validacion del motor de calculo no puedan
    contradecirse.

    Args:
        sigla: Sigla del parametro, por ejemplo "EX".

    Returns:
        Un objeto `Field` de Pydantic con los limites correctos.
    """
    minimo, maximo = ConesaValidator.limites(sigla)
    nombre = ConesaValidator.DESCRIPCIONES.get(sigla, sigla)
    admitidos = ", ".join(str(v) for v in ConesaValidator.RANGOS[sigla])
    return Field(
        ..., ge=minimo, le=maximo,
        description=f"{nombre}. Valores de la metodologia: {admitidos}.",
    )


class ConesaInput(BaseModel):
    """Parametros de una valoracion individual de Conesa."""

    signo: str = Field(
        ..., description="'+' impacto beneficioso, '-' impacto perjudicial."
    )
    i: int = _campo("i")
    EX: int = _campo("EX")
    MO: int = _campo("MO")
    PE: int = _campo("PE")
    RV: int = _campo("RV")
    SI: int = _campo("SI")
    AC: int = _campo("AC")
    EF: int = _campo("EF")
    PR: int = _campo("PR")
    MC: int = _campo("MC")


class CoordenadasInput(BaseModel):
    """Un punto GPS en el sistema WGS84 (EPSG:4326)."""

    latitud: float = Field(..., description="Latitud en grados decimales.")
    longitud: float = Field(..., description="Longitud en grados decimales.")


# ==============================================================================
# UTILIDADES
# ==============================================================================

def _nombre_seguro(nombre: str) -> str:
    """
    Convierte el nombre de un archivo subido en un nombre de archivo seguro.

    Elimina cualquier componente de ruta y los caracteres que permitirian
    escapar de la carpeta temporal (`..`, barras, dos puntos).

    Args:
        nombre: Nombre tal como lo envio el cliente.

    Returns:
        Un nombre de archivo sin componentes de ruta. Si queda vacio, devuelve
        "archivo_subido".
    """
    base = os.path.basename(str(nombre or "")).replace("\\", "/").split("/")[-1]
    base = re.sub(r"[^A-Za-z0-9._-]", "_", base).lstrip(".")
    return base or "archivo_subido"


# ==============================================================================
# ENDPOINTS
# ==============================================================================

@app.get("/", summary="Estado del servidor")
def read_root():
    """
    Chequeo de salud. Lo consulta la interfaz Streamlit para pintar en verde o
    en rojo el indicador "API de Agente".

    Returns:
        Estado del servidor y disponibilidad de la Geodatabase.
    """
    return {
        "status": "online",
        "message": "Servidor del Agente de Impacto Ambiental activo",
        "gdb_disponible": gdb.disponible,
        "docs_url": "/docs",
    }


@app.get("/base-path", summary="Ruta base absoluta del proyecto")
def get_base_path():
    """
    Devuelve la carpeta raiz del proyecto.

    La usa el script de QGIS para localizar los archivos geograficos sin
    depender de donde se haya guardado el script descargado.

    Returns:
        Diccionario con la clave `base_path`.
    """
    # La ruta que se devuelve es la del EQUIPO, no la del contenedor. Quien
    # pregunta es el script de QGIS, que corre fuera: contestarle `/app` le
    # hacia buscar las capas en una carpeta que en su maquina no existe.
    return {"base_path": settings.ruta_proyecto_en_el_equipo(),
            "base_path_contenedor": settings.BASE_DIR}


@app.get("/config", summary="Configuracion resuelta en este momento")
def get_config():
    """
    Devuelve el diagnostico completo de la configuracion.

    Endpoint de apoyo para depurar "por que no encuentra mis datos" sin tener
    que leer codigo ni abrir una consola.

    Returns:
        Diccionario `{concepto: valor o null}`.
    """
    return settings.diagnostico()


@app.get("/parametros", summary="Rangos validos de la metodologia Conesa")
def get_parametros():
    """
    Publica los rangos y umbrales que usa el motor de calculo.

    Permite que cualquier cliente (o el propio agente) consulte la metodologia
    en lugar de llevar una copia propia.

    Returns:
        Diccionario con `parametros`, `signos` y `umbrales`.
    """
    return {
        "parametros": {
            sigla: {
                "nombre": ConesaValidator.DESCRIPCIONES.get(sigla, sigla),
                "valores": valores,
            }
            for sigla, valores in ConesaValidator.RANGOS.items()
        },
        "signos": ConesaValidator.SIGNOS,
        "umbrales": [
            {"categoria": etiqueta, "limite_superior": limite}
            for etiqueta, limite in ConesaEvaluator.UMBRALES
        ],
    }


@app.post("/calculate", summary="Calcular un impacto individual")
def calcular_impacto(datos: ConesaInput):
    """
    Calcula la importancia y la severidad de una valoracion.

    Args:
        datos: Los 11 parametros de la metodologia.

    Returns:
        La entrada recibida y el resultado del calculo.

    Raises:
        HTTPException 400: Si algun parametro es invalido.
    """
    try:
        resultado = ConesaEvaluator.calcular_importancia(datos.model_dump())
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    return {"estado": "exitoso", "entrada": datos, "resultado": resultado}


@app.post("/calculate-batch", summary="Procesar una matriz completa")
def calcular_lote_archivo(file: UploadFile = File(...)):
    """
    Recibe un archivo Excel o CSV con varias valoraciones y las calcula todas.

    El archivo se guarda en la carpeta temporal con un nombre saneado, se
    procesa y se elimina, tanto si el proceso termina bien como si falla.

    Args:
        file: Archivo `.xlsx` o `.csv` subido por el cliente.

    Returns:
        Resumen del procesamiento.

    Raises:
        HTTPException 500: Si el archivo no se pudo procesar.
    """
    destino = os.path.join(settings.carpeta_temporal(),
                           _nombre_seguro(file.filename))
    try:
        with open(destino, "wb") as buffer:
            shutil.copyfileobj(file.file, buffer)

        resumen = excel_conn.procesar_archivo_evaluaciones(destino)
        return {"estado": "procesamiento_completado", "resumen": resumen}

    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))

    finally:
        # Se limpia siempre, incluso si hubo excepcion.
        if os.path.exists(destino):
            try:
                os.remove(destino)
            except OSError:
                pass


@app.get("/layers", summary="Listar capas de la Geodatabase")
def listar_capas_gdb():
    """
    Devuelve el inventario de capas vectoriales de la Geodatabase.

    Returns:
        Diccionario con la ruta de la Geodatabase y la lista de capas.

    Raises:
        HTTPException 503: Si no hay Geodatabase configurada.
    """
    if not gdb.disponible:
        raise HTTPException(
            status_code=503,
            detail=(
                "No hay Geodatabase configurada. Indique su ruta en la "
                "variable GDB_PATH del archivo .env."
            ),
        )
    return {"geodatabase": gdb.gdb_path, "capas": gdb.listar_capas()}


@app.post("/baseline", summary="Consultar linea base por coordenadas")
def consultar_linea_base(coordenadas: CoordenadasInput):
    """
    Diagnostico de linea base para un punto GPS.

    IMPORTANTE sobre la interpretacion del resultado:
      * `areas_protegidas: []`   -> se verifico y no hay colision.
      * `areas_protegidas: null` -> NO se pudo verificar. No es lo mismo.
      * `paramo: false`          -> verificado, fuera de paramo.
      * `paramo: null`           -> no evaluado.

    El campo `advertencias` explica que quedo sin evaluar y por que.

    Args:
        coordenadas: Latitud y longitud en WGS84.

    Returns:
        El diagnostico completo.

    Raises:
        HTTPException 500: Si la consulta espacial falla por completo.
    """
    try:
        diagnostico = gdb.consultar_linea_base_coordenada(
            coordenadas.latitud, coordenadas.longitud
        )
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))

    return {"estado": "exitoso", "diagnostico": diagnostico}


@app.get("/area-estudio", summary="Estado del area de estudio generada")
def get_area_estudio():
    """
    Informa que area de estudio hay generada en disco en este momento.

    Returns:
        Departamento cargado, numero de municipios y numero de registros de
        biodiversidad, leidos de los archivos reales.
    """
    from core.dynamic_filter import resumen_area_generada
    return resumen_area_generada()


# ==============================================================================
# CORPUS: SINCRONIZACION PARA N8N
# ==============================================================================
# Estos tres endpoints son los que consume el flujo de n8n. El reparto de
# responsabilidades es a proposito: n8n decide CUANDO (disparador horario,
# avisos, reintentos) y este backend hace QUE (leer, trocear, embeber, guardar).
# El geoprocesamiento pesado y el calculo de Conesa nunca salen de aqui.
#
# POR QUE LA SINCRONIZACION NO RESPONDE AL INSTANTE
# -------------------------------------------------
# Reconstruir el indice entero son 17.032 fichas y cientos de llamadas a la API
# de embeddings: minutos, no segundos. Una peticion HTTP que espere a eso se
# muere por tiempo de espera en n8n. Asi que `/corpus/sincronizar` arranca el
# trabajo y contesta de inmediato; n8n consulta despues `/corpus/estado` para
# saber como fue.

import threading
import time

#: Estado del ultimo trabajo de sincronizacion. Vive en memoria a proposito:
#: si el servidor se reinicia, el trabajo se habia perdido de todos modos.
_SINCRONIZACION = {
    "ejecutando": False,
    "mensaje": "Sin sincronizaciones en esta sesion.",
    "avance": 0.0,
    "resultado": None,
    "error": None,
    # Sin estos tres, un "Estudiando..." en pantalla no se distingue de un
    # trabajo colgado: no se sabe si empezo hace diez segundos o hace una
    # hora, ni si lo lanzo el cron de las 10:30 o alguien desde la interfaz.
    "arranco": None,
    "origen": "",
    "documento": "",
}
_CERROJO = threading.Lock()


class SincronizarInput(BaseModel):
    """Opciones de una sincronizacion del corpus."""

    forzar: bool = Field(
        False,
        description=(
            "True reindexa todos los documentos aunque no hayan cambiado. "
            "Usalo solo si cambiaron las reglas de troceado o el modelo de "
            "embeddings; en el dia a dia sale carisimo en cuota."
        ),
    )
    origen: str = Field(
        "",
        max_length=60,
        description=(
            "Quien pide la sincronizacion: 'cron' cuando la lanza n8n a su "
            "hora, 'interfaz' cuando la pide una persona. Se dice en claro "
            "en vez de adivinarlo por la cabecera del navegador, que cambia "
            "con cualquier version de cualquier cliente."
        ),
    )


def _trabajo_sincronizar(forzar: bool) -> None:
    """
    Ejecuta la indexacion fuera del hilo que atiende la peticion.

    Args:
        forzar: Si se reindexa todo.
    """
    from core import vectorial

    def avanzar(mensaje, fraccion):
        """
        Anota por donde va la indexacion para que la pueda leer quien pregunte.

        Args:
            mensaje: Que se esta haciendo ahora mismo.
            fraccion: Avance entre 0 y 1.
        """
        with _CERROJO:
            _SINCRONIZACION["mensaje"] = mensaje
            _SINCRONIZACION["avance"] = round(float(fraccion), 3)
            # El mensaje viene como "[3/7] nombre_del_documento.xlsx". Se
            # guarda el nombre aparte para que la interfaz pueda decir QUE se
            # esta leyendo ahora mismo sin tener que descomponer el texto.
            _SINCRONIZACION["documento"] = mensaje.split("] ", 1)[-1].strip()

    try:
        resultado = vectorial.indexar(forzar=forzar, progreso=avanzar)
        with _CERROJO:
            _SINCRONIZACION.update({
                "resultado": resultado, "error": None, "avance": 1.0,
                "mensaje": (
                    f"Listo: {resultado['nuevos']} nuevos, "
                    f"{resultado['actualizados']} actualizados, "
                    f"{resultado['sin_cambios']} sin cambios, "
                    f"{resultado['borrados']} borrados."
                ),
            })
    except Exception as exc:
        with _CERROJO:
            _SINCRONIZACION.update({
                "error": str(exc), "resultado": None,
                "mensaje": f"Fallo la sincronizacion: {exc}",
            })
    finally:
        with _CERROJO:
            _SINCRONIZACION["ejecutando"] = False


@app.post("/corpus/sincronizar", summary="Actualizar el indice documental")
def sincronizar_corpus(opciones: SincronizarInput = None):
    """
    Arranca la sincronizacion del corpus con su fuente documental.

    Es el endpoint que dispara n8n cada noche. Devuelve en el acto: el trabajo
    sigue en segundo plano y su marcha se consulta en `/corpus/estado`.

    Args:
        opciones: `forzar` para reindexar todo.

    Returns:
        Diccionario con `estado` y el mensaje de arranque.

    Raises:
        HTTPException 409: Si ya hay una sincronizacion en marcha. Es
            deliberado: dos indexaciones a la vez se pisarian los puntos y
            gastarian el doble de cuota.
    """
    forzar = bool(opciones.forzar) if opciones else False
    origen = (opciones.origen if opciones else "") or "sin identificar"

    with _CERROJO:
        if _SINCRONIZACION["ejecutando"]:
            raise HTTPException(
                status_code=409,
                detail="Ya hay una sincronizacion en marcha. Consulta /corpus/estado.",
            )
        _SINCRONIZACION.update({
            "ejecutando": True, "avance": 0.0, "resultado": None,
            "error": None, "mensaje": "Arrancando...",
            "arranco": time.time(), "documento": "",
            "origen": origen,
        })

    threading.Thread(target=_trabajo_sincronizar, args=(forzar,),
                     daemon=True).start()
    return {"estado": "arrancada", "forzar": forzar,
            "mensaje": "Sincronizacion en marcha. Consulta /corpus/estado."}


@app.get("/corpus/sincronizacion", summary="Solo como va la carga")
def estado_sincronizacion():
    """
    Dice si hay una carga en marcha, y nada mas.

    POR QUE EXISTE SEPARADO DE `/corpus/estado`
    -------------------------------------------
    La banda de la interfaz pregunta cada tres segundos si se esta cargando
    algo. Preguntarselo a `/corpus/estado` costaba cerca de un segundo por
    vez, porque ese endpoint LISTA DRIVE y recorre el indice para contar
    fichas y fechas. Se midio: 1,0 s de reloj, de los cuales 1,2 s eran la
    consulta a Drive y 0,8 s el indice.

    Aqui no se consulta nada: el estado vive en memoria, asi que responder
    cuesta microsegundos. Es la diferencia entre una interfaz que se siente
    lenta al pegar una imagen y una que no.

    Returns:
        El mismo bloque `sincronizacion` que devuelve `/corpus/estado`.
    """
    with _CERROJO:
        trabajo = dict(_SINCRONIZACION)
    if trabajo.get("arranco"):
        trabajo["segundos"] = round(time.time() - trabajo["arranco"], 1)
    else:
        trabajo["segundos"] = None
    return trabajo


@app.get("/corpus/estado", summary="Estado del corpus y del indice vectorial")
def estado_corpus():
    """
    Informa del corpus, de su fuente y del indice vectorial.

    n8n lo consulta despues de disparar la sincronizacion para saber si
    termino y con que resultado, y decidir si avisa.

    Returns:
        Diccionario con `fuente`, `corpus`, `vectorial` y `sincronizacion`.
    """
    from core import corpus as corpus_mod
    from core import fuentes, vectorial

    try:
        resumen = corpus_mod.resumen()
    except Exception as exc:
        resumen = {"disponible": False, "motivo": str(exc)}

    with _CERROJO:
        trabajo = dict(_SINCRONIZACION)

    # Cuanto lleva, calculado al leer. Guardar el "lleva X segundos" seria
    # guardar un numero que envejece: a los cinco minutos mentiria.
    if trabajo.get("arranco"):
        trabajo["segundos"] = round(time.time() - trabajo["arranco"], 1)
    else:
        trabajo["segundos"] = None

    fuente = fuentes.estado()

    # Cuando la fuente es remota, la carpeta local es un paso de paso: los
    # archivos se borran en cuanto se leen, porque nada se conserva en el
    # equipo. Si el reparto saliera de ahi diria "Sin categoria: 1" aunque en
    # Drive haya cuatro documentos repartidos en carpetas. Quien manda es la
    # fuente; de la carpeta local solo se conserva lo que ella sabe: cuantas
    # fichas quedaron indexadas y que no se pudo leer.
    if resumen.get("disponible") is not None and not fuente.get("en_el_equipo", True):
        resumen = dict(resumen)
        resumen["documentos"] = fuente.get("documentos", 0)
        resumen["categorias"] = fuente.get("por_categoria", [])
        resumen["disponible"] = bool(fuente.get("listo"))
        resumen["describe"] = fuente.get("fuente", "?")
        # Los que la fuente aparto por tamano se cuentan igual que los del
        # corpus local: la interfaz ya sabe pintar esa lista con nombre y
        # peso, y es la respuesta a "y los otros cuatro archivos?".
        resumen["demasiado_grandes"] = [
            {"nombre": d.get("nombre", "?"), "mb": d.get("mb", 0),
             # Cuando se pudo estimar, se dice lo que COSTARIA, que es el
             # motivo real de que no entre. Los megas solo son el sintoma.
             "fichas_estimadas": d.get("fichas_estimadas"),
             "motivo": d.get("motivo", "")}
            for d in (fuente.get("descartados") or [])]
    else:
        resumen = dict(resumen)
        resumen["describe"] = "local"

    return {
        "fuente": fuente,
        "corpus": resumen,
        "vectorial": vectorial.estado(),
        "sincronizacion": trabajo,
    }


class ConsultaCorpus(BaseModel):
    """Pregunta dirigida al corpus documental."""

    pregunta: str = Field(..., description="Pregunta en lenguaje natural.")
    limite: int = Field(8, ge=1, le=30, description="Cuantas fichas devolver.")
    categoria: str = Field(
        "", description="Restringe a una coleccion tematica. Vacio = todas.")


@app.post("/corpus/consultar", summary="Buscar en el corpus documental")
def consultar_corpus_api(consulta: ConsultaCorpus):
    """
    Busca fichas pertinentes mezclando palabras y significado.

    Expone la busqueda hibrida para que n8n u otro agente puedan consultar la
    bibliografia sin pasar por la interfaz. Cada ficha vuelve con su
    procedencia, que es lo que permite citarla.

    Args:
        consulta: Pregunta, limite y categoria.

    Returns:
        Diccionario con `fichas` y cuantas se encontraron.

    Raises:
        HTTPException 500: Si la busqueda falla por completo.
    """
    from core import vectorial

    try:
        fichas = vectorial.buscar_hibrido(
            consulta.pregunta, limite=consulta.limite,
            categoria=consulta.categoria or None)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))

    return {"estado": "exitoso", "encontradas": len(fichas), "fichas": fichas}


# ==============================================================================
# CHAT DEL AGENTE (ORQUESTACION PARA N8N Y STREAMLIT)
# ==============================================================================

class AgenteChatInput(BaseModel):
    """Entrada para conversar con el agente a traves del flujo de n8n."""
    pregunta: str = Field(..., description="Pregunta en lenguaje natural para el agente.")
    session_id: str = Field("default", description="Identificador de sesion para mantener memoria conversacional.")
    api_key: str | None = Field(None, description="Clave de Gemini opcional.")
    guardar_memoria: bool = Field(
        True,
        description=(
            "Si el propio endpoint escribe el intercambio en la memoria "
            "vectorial. n8n lo pone en false porque lo hace el mismo, en un "
            "nodo aparte, para que ese paso se vea en el flujo."))
    con_imagenes: bool = Field(
        True,
        description=(
            "Si se recogen las imagenes que esa sesion haya dejado pegadas. "
            "Asi una imagen pegada llega al modelo venga la pregunta por "
            "donde venga, sin tener que viajar en el cuerpo de la peticion."))


_SESIONES_AGENTE: dict = {}


def _imagenes_de_la_sesion(session_id: str) -> list:
    """
    Recoge y consume las imagenes pegadas de una sesion.

    POR QUE SE LEEN AQUI Y NO VIAJAN EN LA PETICION
    -----------------------------------------------
    Cuando la pregunta pasa por n8n, hacer viajar una captura de pantalla en
    base64 significa moverla tres veces: interfaz -> n8n -> API. Son megas por
    cada pregunta y obliga a n8n a manejar cuerpos enormes.

    Leyendolas aqui, por sesion, la imagen se queda quieta en el servidor y
    llega al modelo igual, venga la pregunta por el camino que venga.

    Args:
        session_id: Sesion del navegador.

    Returns:
        Lista de tuplas `(bytes, tipo)` lista para el agente. Vacia si no hay
        nada pegado o si algo viniera corrupto.
    """
    import base64

    with _CERROJO_IMAGENES:
        cola = _IMAGENES_PEGADAS.pop(session_id, [])

    salida = []
    for imagen in cola:
        try:
            salida.append((base64.b64decode(imagen["datos"]),
                           imagen.get("tipo", "image/png")))
        except Exception:
            continue
    return salida


@app.post("/agente/chat", summary="Chat con el Copiloto Ambiental (con memoria)")
def agente_chat(payload: AgenteChatInput):
    """
    Conversa con el agente manteniendo la memoria de la sesion.

    Es el servicio que llama el flujo de n8n en cada turno. Recupera o crea el
    agente de esa sesion, le pasa las imagenes que se hayan pegado y guarda el
    intercambio en SQLite.

    Args:
        payload: Pregunta, sesion, clave opcional y los dos interruptores
            `guardar_memoria` y `con_imagenes`.

    Returns:
        Diccionario con `estado`, `respuesta`, `session_id`, los turnos que
        quedan en memoria y el modo en que respondio el agente.
    """
    from agent.chatbot import ConesaAgent
    from core import memoria

    clave = payload.api_key or settings.api_key_gemini()
    session_id = payload.session_id or "default"

    # 1. Guardar mensaje del usuario en base de datos SQLite
    memoria.guardar_mensaje(session_id, "user", payload.pregunta)

    # 2. Reutilizar o instanciar agente con memoria
    agente = _SESIONES_AGENTE.get(session_id)
    if agente is None or (clave and getattr(agente, "_api_key_usada", None) != clave):
        agente = ConesaAgent(api_key=clave)
        agente._api_key_usada = clave
        agente._sesion_id = session_id
        # Cargar historial previo de SQLite si es una sesion reabierta
        historial_previo = memoria.cargar_historial(session_id)
        if hasattr(agente, "_historial") and not agente._historial and len(historial_previo) > 1:
            for m in historial_previo[:-1]:
                agente._recordar(m["role"], m["content"])
        _SESIONES_AGENTE[session_id] = agente

    # 3. Las imagenes que se hayan pegado en esta sesion viajan con la
    #    pregunta. Se consumen al leerlas: una imagen se mira una vez.
    adjuntas = (_imagenes_de_la_sesion(session_id)
                if payload.con_imagenes else [])

    # El agente guarda recuerdos por su cuenta en un hilo de fondo. Cuando
    # quien llama va a escribir en la memoria el mismo -- n8n, en su propio
    # nodo -- se le pide que no lo haga, o el mismo intercambio entraria dos
    # veces en Qdrant.
    _ahorro = getattr(agente, "_guardar_recuerdo_de_fondo", None)
    if not payload.guardar_memoria and _ahorro is not None:
        agente._guardar_recuerdo_de_fondo = lambda *a, **k: None
    try:
        respuesta = agente.responder(payload.pregunta, adjuntas or None)
    finally:
        if not payload.guardar_memoria and _ahorro is not None:
            agente._guardar_recuerdo_de_fondo = _ahorro

    # 3. Guardar respuesta del asistente en SQLite
    memoria.guardar_mensaje(session_id, "assistant", respuesta)

    turnos = len(agente._historial) // 2 if hasattr(agente, "_historial") else 0

    return {
        "estado": "exitoso",
        "respuesta": respuesta,
        "session_id": session_id,
        "turnos_en_memoria": turnos,
        "modo": agente.modo,
    }


@app.get("/agente/sesiones", summary="Listar sesiones de conversacion guardadas")
def listar_sesiones_api():
    """
    Lista las conversaciones guardadas.

    Returns:
        Diccionario con la clave `sesiones`, de la mas reciente a la mas
        antigua.
    """
    from core import memoria
    return {"sesiones": memoria.listar_sesiones()}


@app.get("/agente/sesiones/{sesion_id}", summary="Cargar mensajes de una sesion")
def cargar_sesion_api(sesion_id: str):
    """
    Recupera el historial completo de una conversacion anterior.

    Args:
        sesion_id: Conversacion a cargar.

    Returns:
        Diccionario con `sesion_id` y `mensajes` en orden cronologico.
    """
    from core import memoria
    return {"sesion_id": sesion_id, "mensajes": memoria.cargar_historial(sesion_id)}




# ==============================================================================
# IMAGENES PEGADAS EN EL CHAT (Ctrl+V)
# ==============================================================================
# POR QUE HACE FALTA UN BUZON EN EL SERVIDOR
# ------------------------------------------
# Streamlit no sabe nada del portapapeles: `st.file_uploader` obliga a abrir el
# explorador y elegir un archivo. Para poder PEGAR hay que escuchar el evento
# `paste` del navegador, y eso solo lo puede hacer un trozo de JavaScript.
#
# Ese JavaScript no tiene forma directa de entregarle los bytes a Streamlit, asi
# que los deja aqui y Streamlit los recoge en la siguiente pasada. El buzon vive
# en memoria a proposito: una imagen pegada es efimera, y si el servidor se
# reinicia lo ultimo que importa es recuperarla.

_IMAGENES_PEGADAS = {}
_CERROJO_IMAGENES = threading.Lock()

#: Tope por imagen. Una captura de pantalla ronda 1 MB; 12 deja margen de sobra
#: sin que un pegado accidental de algo enorme llene la memoria del servidor.
TOPE_IMAGEN_BYTES = 12 * 1024 * 1024

#: Cuantas imagenes se guardan a la vez por sesion.
TOPE_IMAGENES_POR_SESION = 6


class ImagenPegadaInput(BaseModel):
    """Una imagen que alguien acaba de pegar en el chat."""

    session_id: str = Field("default", description="Sesion del navegador.")
    nombre: str = Field("pegada.png", description="Nombre para mostrar.")
    tipo: str = Field("image/png", description="Tipo MIME de la imagen.")
    datos: str = Field(..., description="Contenido en base64.")


@app.post("/chat/imagen", summary="Guardar una imagen pegada en el chat")
def guardar_imagen_pegada(entrada: ImagenPegadaInput):
    """
    Recoge una imagen pegada con Ctrl+V y la deja en espera.

    Args:
        entrada: Sesion, nombre, tipo y contenido en base64.

    Returns:
        Cuantas imagenes hay en espera para esa sesion.

    Raises:
        HTTPException: 400 si el contenido no es base64 valido o pasa del tope.
    """
    import base64

    try:
        crudo = base64.b64decode(entrada.datos, validate=True)
    except Exception:
        raise HTTPException(status_code=400, detail="El contenido no es base64.")

    if not crudo:
        raise HTTPException(status_code=400, detail="La imagen llego vacia.")
    if len(crudo) > TOPE_IMAGEN_BYTES:
        raise HTTPException(
            status_code=400,
            detail=f"La imagen pesa {len(crudo) // 1024} KB y el tope son "
                   f"{TOPE_IMAGEN_BYTES // 1024} KB.")

    with _CERROJO_IMAGENES:
        cola = _IMAGENES_PEGADAS.setdefault(entrada.session_id, [])
        if len(cola) >= TOPE_IMAGENES_POR_SESION:
            raise HTTPException(
                status_code=400,
                detail=f"Ya hay {len(cola)} imagenes en espera. Envia el "
                       f"mensaje o quitalas antes de pegar mas.")
        cola.append({"nombre": entrada.nombre, "tipo": entrada.tipo,
                     "bytes": len(crudo), "datos": entrada.datos})
        return {"estado": "guardada", "en_espera": len(cola)}


@app.get("/chat/imagenes/{session_id}",
         summary="Ver las imagenes pegadas que esperan")
def listar_imagenes_pegadas(session_id: str, con_datos: bool = False):
    """
    Devuelve las imagenes en espera de una sesion.

    Args:
        session_id: Sesion del navegador.
        con_datos: Si se incluye el contenido en base64. La interfaz lo pide
            solo cuando va a enviar el mensaje; para pintar el aviso de
            "2 imagenes pegadas" basta con los nombres.

    Returns:
        Diccionario con `cuantas` e `imagenes`.
    """
    with _CERROJO_IMAGENES:
        cola = list(_IMAGENES_PEGADAS.get(session_id, []))
    if not con_datos:
        cola = [{k: v for k, v in i.items() if k != "datos"} for i in cola]
    return {"session_id": session_id, "cuantas": len(cola), "imagenes": cola}


@app.delete("/chat/imagenes/{session_id}/{indice}",
            summary="Quitar una sola imagen pegada")
def borrar_una_imagen_pegada(session_id: str, indice: int):
    """
    Descarta una imagen concreta de las que esperan.

    Pegar tres capturas y tener que quitarlas todas para corregir una sola es
    una friccion tonta, sobre todo cuando pegar es tan rapido que es facil
    pegar de mas.

    Args:
        session_id: Sesion del navegador.
        indice: Posicion de la imagen, empezando en cero.

    Returns:
        Diccionario con `estado` y cuantas quedan.

    Raises:
        HTTPException: 404 si esa posicion no existe.
    """
    with _CERROJO_IMAGENES:
        cola = _IMAGENES_PEGADAS.get(session_id, [])
        if not 0 <= indice < len(cola):
            raise HTTPException(
                status_code=404,
                detail=f"No hay ninguna imagen en la posicion {indice}.")
        quitada = cola.pop(indice)
        if not cola:
            _IMAGENES_PEGADAS.pop(session_id, None)
        return {"estado": "quitada", "nombre": quitada.get("nombre"),
                "en_espera": len(cola)}


@app.delete("/chat/imagenes/{session_id}",
            summary="Vaciar las imagenes pegadas que esperan")
def borrar_imagenes_pegadas(session_id: str):
    """
    Vacia el buzon de una sesion.

    Args:
        session_id: Sesion del navegador.

    Returns:
        Cuantas imagenes se descartaron.
    """
    with _CERROJO_IMAGENES:
        cuantas = len(_IMAGENES_PEGADAS.pop(session_id, []))
    return {"estado": "vaciado", "descartadas": cuantas}


# ==============================================================================
# DETALLE DE LA FUENTE DOCUMENTAL (para el panel de Drive)
# ==============================================================================

@app.get("/corpus/fuente", summary="Diagnostico de la fuente documental")
def diagnostico_fuente():
    """
    Cuenta si la fuente esta conectada y que trae, sin indexar nada.

    Es lo que mira el panel de Drive: permite comprobar la conexion y ver el
    reparto por carpeta ANTES de lanzar una sincronizacion, que puede tardar.

    Returns:
        Diccionario con `fuente`, `listo`, `motivo`, `documentos`,
        `por_categoria`, `por_formato` e `imagenes`.
    """
    from core import corpus, fuentes

    base = fuentes.estado()
    detalle = {"por_categoria": [], "por_formato": [], "imagenes": 0,
               "ejemplos": []}

    if base.get("listo"):
        try:
            # `descargar=False`: este endpoint solo CUENTA lo que hay. Con
            # Drive conectado, pedirlo entero descargaba la carpeta completa
            # cada vez que alguien abria la interfaz -- 77 segundos con la
            # pantalla en blanco.
            documentos = fuentes.obtener().documentos(descargar=False)
        except Exception as exc:
            base["listo"] = False
            base["motivo"] = f"No se pudo listar la fuente: {exc}"
            documentos = []

        porcat, porfor = {}, {}
        for doc in documentos:
            categoria = doc.get("categoria") or "Sin categoria"
            porcat[categoria] = porcat.get(categoria, 0) + 1
            extension = os.path.splitext(doc.get("nombre", ""))[1].lower()
            porfor[extension or "(sin extension)"] = \
                porfor.get(extension or "(sin extension)", 0) + 1
            if extension in corpus.TIPOS_IMAGEN:
                detalle["imagenes"] += 1

        # Lo que la fuente vio pero descarto, con su motivo. Sin esto, un
        # archivo que esta en Drive y no aparece en el indice no tiene
        # explicacion desde la interfaz.
        detalle["descartados"] = list(
            getattr(fuentes.obtener(), "descartados", []) or [])
        detalle["por_categoria"] = sorted(porcat.items())
        detalle["por_formato"] = sorted(porfor.items(), key=lambda x: -x[1])
        detalle["ejemplos"] = [
            {"nombre": d.get("nombre"), "categoria": d.get("categoria")}
            for d in documentos[:12]
        ]

    base.update(detalle)
    return base


# ==============================================================================
# ESCRITURA EXPLICITA EN LA MEMORIA VECTORIAL
# ==============================================================================
# POR QUE EXISTE ESTE ENDPOINT SI EL AGENTE YA GUARDA SOLO
# --------------------------------------------------------
# El agente guarda sus recuerdos en un hilo de fondo, sin que se vea. Eso esta
# bien para que una pregunta no espere a que termine de escribir, pero deja el
# paso invisible: desde fuera no hay forma de saber si entro algo en Qdrant ni
# cuando.
#
# Con este endpoint, n8n puede hacer la escritura en un NODO PROPIO. Asi el
# recorrido completo -- pregunta, respuesta, guardado, contestacion -- se ve en
# el lienzo y queda en el historial de ejecuciones, que es justo lo que se
# pidio. Cuando n8n lo usa, le dice a /agente/chat que no guarde por su cuenta
# (`guardar_memoria: false`) para que el mismo intercambio no entre dos veces.

class MemoriaInput(BaseModel):
    """Un intercambio que se quiere conservar en la memoria vectorial."""

    pregunta: str = Field(..., description="Lo que pregunto la persona.")
    respuesta: str = Field("", description="Lo que contesto el agente.")
    session_id: str = Field("default", description="Sesion de procedencia.")
    tipo: str = Field("intercambio",
                      description="Clase de recuerdo, para poder filtrarlo.")


@app.post("/agente/memoria", summary="Guardar un intercambio en Qdrant")
def guardar_en_memoria(entrada: MemoriaInput):
    """
    Escribe una pregunta y su respuesta en la memoria vectorial.

    Args:
        entrada: Pregunta, respuesta, sesion y tipo.

    Returns:
        Diccionario con `guardado`, `coleccion`, `fecha` y `caracteres`. Si no
        se pudo guardar, `guardado` es False y `motivo` dice por que; no se
        lanza error, porque perder un recuerdo no debe tumbar la conversacion
        que lo origino.
    """
    import datetime

    from core import memoria

    texto = entrada.pregunta.strip()
    if entrada.respuesta.strip():
        texto = f"P: {texto}\nR: {entrada.respuesta.strip()}"

    # El mismo formato que usan las fichas del corpus: ISO 8601 en UTC, para
    # que todo el indice se pueda ordenar junto.
    fecha = datetime.datetime.now(
        datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    try:
        resultado = memoria.guardar_recuerdo_detallado(
            texto, sesion_id=entrada.session_id, tipo=entrada.tipo)
    except Exception as exc:
        return {"guardado": False, "en_indice": False, "en_espera": 0,
                "motivo": str(exc)[:200],
                "coleccion": memoria.COLECCION_MEMORIA, "fecha": fecha}

    # `guardar_recuerdo` devuelve True tanto si entro en el indice como si
    # quedo anotado para reintentarlo: en los dos casos el recuerdo esta a
    # salvo, que es lo que importa de cara a quien pregunta.
    return {
        # `guardado` significa "no se ha perdido", que es lo que importa de
        # cara a quien pregunta. `en_indice` dice si ademas ya se puede
        # buscar, o si esta esperando a que la red lo permita.
        "guardado": resultado["a_salvo"],
        "en_indice": resultado["en_indice"],
        "en_espera": resultado["en_espera"],
        "motivo": resultado["motivo"],
        "coleccion": memoria.COLECCION_MEMORIA,
        "fecha": fecha,
        "caracteres": len(texto),
    }


@app.get("/agente/memoria/{session_id}",
         summary="Ver cuantos recuerdos hay guardados")
def contar_memoria(session_id: str = "default"):
    """
    Cuenta los recuerdos guardados y dice cuando entro el ultimo.

    Sirve para comprobar de un vistazo que la escritura en Qdrant esta
    ocurriendo de verdad, que era imposible de ver cuando solo la hacia un
    hilo de fondo.

    Args:
        session_id: Sesion por la que filtrar. Usa "*" para contar todas.

    Returns:
        Diccionario con `coleccion`, `total`, `de_esta_sesion` y `ultima`.
    """
    from core import memoria, vectorial

    try:
        cliente = vectorial._cliente_qdrant()
        if not cliente.collection_exists(memoria.COLECCION_MEMORIA):
            return {"coleccion": memoria.COLECCION_MEMORIA, "total": 0,
                    "de_esta_sesion": 0, "ultima": "",
                    "motivo": "la coleccion todavia no existe"}
        total = cliente.count(memoria.COLECCION_MEMORIA, exact=True).count

        propios, ultima = 0, ""
        siguiente = None
        while True:
            lote, siguiente = cliente.scroll(
                collection_name=memoria.COLECCION_MEMORIA, limit=512,
                offset=siguiente, with_payload=["sesion_id", "fecha"],
                with_vectors=False)
            if not lote:
                break
            for p in lote:
                carga = p.payload or {}
                if session_id == "*" or carga.get("sesion_id") == session_id:
                    propios += 1
                    fecha = carga.get("fecha", "")
                    if fecha > ultima:
                        ultima = fecha
            if siguiente is None:
                break

        return {"coleccion": memoria.COLECCION_MEMORIA, "total": total,
                "de_esta_sesion": propios, "ultima": ultima}
    except Exception as exc:
        return {"coleccion": memoria.COLECCION_MEMORIA, "total": 0,
                "de_esta_sesion": 0, "ultima": "", "motivo": str(exc)[:200]}
