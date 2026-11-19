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


# IMPORTAR ESTE ARCHIVO NO PUEDE BORRAR NADA
# ===========================================
# Aqui vivian, al nivel del modulo, tres cosas que se hacen al arrancar: la
# limpieza de lo que quedara descargado, el reintento de los recuerdos sin
# vector y el calentado del inventario de Drive.
#
# La limpieza hace `rmtree` de la carpeta de descargas. Y un modulo no se
# ejecuta solo cuando uvicorn lo sirve: se ejecuta tambien cuando CUALQUIERA
# lo importa. La bateria de despliegue carga `api/server.py` con
# `exec_module` para poder probar sus funciones puras -- algo perfectamente
# razonable --, y al hacerlo borraba los 3,2 GB que un estudio en marcha
# acababa de bajar.
#
# Paso cuatro veces en dos dias. La ultima quedo cronometrada por el
# vigilante de la pasada:
#
#     desapariciones: [{'cuando': '2026-11-10T20:23:29Z',
#                       'habia': 425, 'quedan': 0}]
#
# y un segundo despues, a las 20:23:30, aparecen en el registro las primeras
# peticiones de la bateria. No era un fantasma: era un `import`.
#
# El sitio de lo que pasa al arrancar es el arranque de la APLICACION, no el
# del archivo. Con esto, importar `api.server` es inofensivo por
# construccion, y no por que el cartel del estudio llegue a tiempo.
#: Lo que se encontro al arrancar. Se rellenan en `al_arrancar()`.
_RESTOS_AL_ARRANCAR: dict = {"estado": "sin arrancar todavia"}
_MEMORIA_AL_ARRANCAR: dict = {"estado": "sin arrancar todavia"}


def _calentar_la_fuente():
    """
    Lista Drive una vez, para que el primero que pregunte no espere.

    Listar la carpeta cuesta once segundos y el resultado se guarda un
    cuarto de hora. El problema es QUIEN paga la primera vez: sin esto,
    quien hiciera la primera pregunta despues de arrancar. Se midio 14,5 s
    la primera consulta al corpus y 0,23 s la siguiente, la misma pregunta.
    """
    try:
        from core import fuentes as _fu_calentar

        _fu_calentar.estado()
    except Exception:
        pass  # si no se puede, se pagara a la primera pregunta, como antes


@app.on_event("startup")
def al_arrancar() -> None:
    """
    Lo que hay que hacer cuando la aplicacion empieza a servir.

    Se ejecuta UNA vez, y solo cuando uvicorn levanta la aplicacion de
    verdad. Importar el modulo no dispara nada de esto, que es justo el
    punto: ver la nota de arriba.
    """
    global _RESTOS_AL_ARRANCAR, _MEMORIA_AL_ARRANCAR

    # Al arrancar no puede haber ninguna sincronizacion en curso en este
    # proceso, asi que cualquier documento que quede en la carpeta temporal
    # es resto de una ejecucion anterior que se corto a la mitad (o de un
    # contenedor que se reinicio mientras leia). El compromiso es que nada se
    # conserva en el equipo, y ese compromiso no puede depender de que la vez
    # anterior terminara bien.
    try:
        from core import vectorial as _vectorial_arranque

        _RESTOS_AL_ARRANCAR = _vectorial_arranque.limpiar_descargas()
    except Exception as _exc:  # pragma: no cover
        _RESTOS_AL_ARRANCAR = {"error": str(_exc)[:120]}

    # Recuerdos que se quedaron sin vector por un corte de red. Al arrancar
    # hay conexion recien hecha, que es el mejor momento para colocarlos.
    try:
        from core import memoria as _memoria_arranque

        _MEMORIA_AL_ARRANCAR = _memoria_arranque.reintentar_pendientes()
    except Exception as _exc:  # pragma: no cover
        _MEMORIA_AL_ARRANCAR = {"error": str(_exc)[:120]}

    # En un hilo aparte: ni bloquea el arranque ni se lo encuentra nadie.
    try:
        import threading as _hilos_arranque

        _hilos_arranque.Thread(target=_calentar_la_fuente,
                               daemon=True).start()
    except Exception:
        pass


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
    # hora, ni si lo lanzo el cron de las 02:00 o alguien desde la interfaz.
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


def _archivo_ultima_sincronizacion() -> str:
    """
    Ruta del archivo donde queda constancia de la ultima sincronizacion.

    Returns:
        Ruta dentro de `data/`, que sobrevive al contenedor.
    """
    import os as _os

    base = _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))
    carpeta = _os.path.join(base, "data")
    _os.makedirs(carpeta, exist_ok=True)
    return _os.path.join(carpeta, "ultima_sincronizacion.json")


def _sin_sustitutos(valor):
    """
    Deja un valor en condiciones de escribirse como UTF-8.

    POR QUE UN NOMBRE DE ARCHIVO PUEDE TUMBAR EL INFORME
    ----------------------------------------------------
    Cuando Python lee un nombre de archivo cuyos bytes no son UTF-8 valido
    -- y en un montaje de Windows pasa -- mete "sustitutos" sueltos en la
    cadena. Esa cadena se puede manejar, pero NO se puede escribir:
    `json.dump` levanta "UnicodeEncodeError: surrogates not allowed".

    El dano no es el error, es donde cae: el informe del estudio se escribe
    al terminar, y si revienta ahi la pasada entera se queda sin constancia.
    Al volver, la pantalla dice "Sin estudios en esta sesion" sobre un
    estudio que si hubo y que puede haber durado horas.

    Args:
        valor: Cualquier cosa que vaya a json.dump.

    Returns:
        Lo mismo, con los sustitutos cambiados por un interrogante. Se
        prefiere un nombre con un interrogante a no tener informe.
    """
    if isinstance(valor, str):
        return valor.encode("utf-8", "replace").decode("utf-8")
    if isinstance(valor, dict):
        return {_sin_sustitutos(k): _sin_sustitutos(v)
                for k, v in valor.items()}
    if isinstance(valor, (list, tuple)):
        return [_sin_sustitutos(v) for v in valor]
    return valor


def _dejar_constancia(estado: dict) -> None:
    """
    Guarda en disco como acabo la sincronizacion.

    POR QUE NO BASTA LA MEMORIA
    ---------------------------
    `_SINCRONIZACION` vive en el proceso. Al reiniciar la API vuelve a
    "Sin sincronizaciones en esta sesion", y con el se pierde si la del
    cron de las 02:00 funciono, cuanto indexo y que fallo.
    
    Se vio en marcha y es exactamente el caso malo: una sincronizacion se
    corto a mitad -- el equipo se durmio --, el contenedor arranco de nuevo
    y no quedaba ni rastro. Hubo que deducir que estaba incompleta contando
    documentos distintos dentro de Qdrant. Eso no se le puede pedir a nadie
    a las ocho de la manana.

    Un fallo al escribir no interrumpe nada: perder la constancia es malo,
    pero tirar la sincronizacion por no poder anotarla seria peor.

    Args:
        estado: El bloque `_SINCRONIZACION` tal como quedo.
    """
    import json as _json

    try:
        cuerpo = {
            "cuando": time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime()),
            "origen": estado.get("origen", ""),
            "mensaje": estado.get("mensaje", ""),
            "error": estado.get("error"),
            "resultado": estado.get("resultado"),
            # SIN ESTO, UN ESTUDIO MUERTO SE LEE COMO UNO TERMINADO
            # -----------------------------------------------------
            # Se anota si quedo cerrado. La constancia se escribia solo al
            # final, asi que un estudio que moria a mitad -- la API
            # reiniciada, el equipo dormido -- no dejaba ninguna linea: al
            # volver, el sistema decia "sin sincronizaciones en esta sesion",
            # igual que si el cron no se hubiera disparado nunca.
            #
            # Y n8n hacia lo mismo: su bucle sale en cuanto la API deja de
            # decir "ejecutando", y una API recien arrancada dice exactamente
            # eso. Tres estudios muertos quedaron marcados como `success`.
            "terminado": not estado.get("ejecutando", False),
        }
        with open(_archivo_ultima_sincronizacion(), "w",
                  encoding="utf-8") as fh:
            _json.dump(_sin_sustitutos(cuerpo), fh, ensure_ascii=False,
                       indent=2)
    except Exception:
        # Se traga CUALQUIER fallo, no solo los de disco. Antes solo miraba
        # OSError, y un UnicodeEncodeError por un nombre de archivo raro
        # subia hasta el hilo del estudio y lo dejaba sin informe.
        pass


def _con_lo_que_paso_la_vez_anterior(trabajo: dict) -> dict:
    """
    Adjunta al estado el registro del ultimo estudio que hubo.

    Lo usan los DOS endpoints de estado. Cuando solo lo hacia uno, la misma
    aplicacion daba dos versiones del mismo hecho segun la pestana: la banda
    del chat fechaba el fallo y la pestana de Drive lo pintaba en rojo sin
    decir de cuando era.

    Args:
        trabajo: El bloque de seguimiento en memoria.

    Returns:
        El mismo diccionario con `anterior` y con la marca de si el estudio
        anterior quedo a medias.
    """
    import json as _json

    try:
        with open(_archivo_ultima_sincronizacion(), encoding="utf-8") as fh:
            trabajo["anterior"] = _json.load(fh)
    except (OSError, ValueError):
        trabajo["anterior"] = None

    return _marcar_si_quedo_a_medias(trabajo)


def _marcar_si_quedo_a_medias(trabajo: dict) -> dict:
    """
    Declara que el estudio anterior se corto, si asi fue.

    UN ESTUDIO INTERRUMPIDO NO ES "NINGUN ESTUDIO"
    ----------------------------------------------
    Si el ultimo quedo sin cerrar y ahora no hay nada en marcha, es que
    murio por el camino: la API se reinicio, el equipo se durmio. Antes eso
    se leia como "Sin sincronizaciones en esta sesion", exactamente igual
    que si el cron no se hubiera disparado nunca -- y de ahi la pregunta
    razonable de "el cron no esta funcionando?" sobre un cron que se
    disparaba todos los dias.

    n8n cometia el mismo error por su cuenta: su bucle sale en cuanto la
    API deja de decir "ejecutando", y una API recien arrancada dice justo
    eso. Tres estudios muertos quedaron marcados como `success`.

    Args:
        trabajo: El bloque de seguimiento, ya con su clave `anterior`.

    Returns:
        El mismo diccionario, con `interrumpida` y, si procede, el motivo.
    """
    anterior = trabajo.get("anterior") or {}
    if (trabajo.get("ejecutando") or not anterior
            or anterior.get("terminado") is not False):
        trabajo["interrumpida"] = False
        return trabajo

    trabajo["interrumpida"] = True
    trabajo["mensaje"] = (
        f"El estudio que empezo el {anterior.get('cuando', '?')} "
        f"({anterior.get('origen') or 'sin identificar'}) se corto antes de "
        f"terminar: la API se reinicio o el equipo se durmio. Lo que alcanzo "
        f"a indexar NO se pierde; al volver a estudiar sigue por donde iba.")
    if trabajo.get("error") is None:
        trabajo["error"] = trabajo["mensaje"]
    return trabajo


def _resumen_de_la_pasada(resultado: dict) -> str:
    """
    Redacta en una linea como fue la sincronizacion.

    LO QUE IMPORTA VA PRIMERO
    -------------------------
    El mensaje empezaba por los documentos intentados, y eso se lee como un
    exito aunque no haya entrado ni una ficha al indice: "367 documentos" y
    cero fichas es una pasada fallida, no una buena.

    Lo que hay que mirar para saber si una pasada sirvio de algo es
    cuantas fichas entraron, y eso es lo que va primero.

    Args:
        resultado: Lo que devolvio la indexacion.

    Returns:
        El mensaje para la interfaz y para n8n.
    """
    fichas = resultado.get("fichas", 0)
    trozos = [f"{fichas} fichas nuevas en el indice"]
    trozos.append(f"{resultado.get('nuevos', 0)} documentos leidos")
    if resultado.get("sin_cambios"):
        trozos.append(f"{resultado['sin_cambios']} sin cambios")
    if resultado.get("borrados"):
        trozos.append(f"{resultado['borrados']} borrados")
    # Un documento que desaparecio del equipo y se volvio a pedir NO es un
    # fallo, pero tampoco es un no-evento: si pasa, hay algo que mirar.
    if resultado.get("rebajados"):
        trozos.append(f"{resultado['rebajados']} hubo que volver a bajarlos "
                      f"porque desaparecieron del equipo")

    pendientes = []
    if resultado.get("sin_descargar"):
        pendientes.append(f"{len(resultado['sin_descargar'])} no llegaron a "
                          f"bajarse")
    if resultado.get("sin_contenido"):
        pendientes.append(f"{len(resultado['sin_contenido'])} no dieron "
                          f"texto")
    if resultado.get("incompletos"):
        _huecos = sum(d.get("sin_vector", 0)
                      for d in resultado["incompletos"])
        pendientes.append(f"{len(resultado['incompletos'])} entraron "
                          f"incompletos ({_huecos} fichas sin vector)")
    if resultado.get("fallidos"):
        pendientes.append(f"{len(resultado['fallidos'])} fallaron")

    mensaje = "Listo: " + ", ".join(trozos) + "."
    if pendientes:
        mensaje += " Quedan fuera: " + ", ".join(pendientes) + "."
    return mensaje


def _lo_que_salio_mal(resultado: dict) -> str:
    """
    Decide si una pasada que no revento merece contarse como fallida.

    UNA PASADA QUE NO INDEXA NADA NO ES UNA PASADA BUENA
    ----------------------------------------------------
    No levanta ninguna excepcion -- cada documento fallo por su cuenta y se
    anoto --, asi que llegaba hasta aqui con `error: None` y se pintaba en
    verde. n8n tambien la daba por buena, porque lo unico que mira es el
    error. Dos sitios distintos informando de que todo fue bien sobre un
    indice que no crecio ni una ficha.

    Args:
        resultado: Lo que devolvio la indexacion.

    Returns:
        El motivo, o cadena vacia si la pasada fue sana.
    """
    sin_bajar = len(resultado.get("sin_descargar") or [])
    intentados = resultado.get("nuevos", 0) + resultado.get("actualizados", 0)

    # SE BAJARON Y DESAPARECIERON NO ES LO MISMO QUE NO SE BAJARON
    # -------------------------------------------------------------
    # El aviso decia "la descarga no llego a completarse" sobre pasadas que
    # SI habian bajado los 3,2 GB enteros: lo que paso es que algo vacio la
    # carpeta entre la descarga y la lectura. Mandar a alguien a mirar su
    # conexion cuando el problema es otro cuesta horas.
    #
    # El vigilante de la pasada apunta la hora exacta en que la carpeta se
    # quedo sin archivos. Si hay constancia de eso, se dice eso.
    _desapariciones = resultado.get("desapariciones") or []
    if sin_bajar and _desapariciones:
        _primera = _desapariciones[0]
        _hora = str(_primera.get("cuando", "?")).replace("T", " a las ")[:19]
        _pico = resultado.get("maximo_en_disco") or _primera.get("habia", 0)
        return (
            f"{sin_bajar} documentos se habian bajado y ya no estaban cuando "
            f"les llego el turno de leerse. NO es un problema de conexion ni "
            f"de Drive: llegaron a haber {_pico} archivos en el equipo y la "
            f"carpeta se vacio el {_hora} (quedaron "
            f"{_primera.get('quedan', 0)}). Algo ajeno al estudio la borro "
            f"mientras trabajaba. Vuelve a lanzarlo y, si se repite, mira que "
            f"habia corriendo a esa hora.")

    if sin_bajar and sin_bajar >= max(1, intentados // 2):
        # EL MOTIVO QUE MAS SE REPITE, DICHO
        # ----------------------------------
        # Decia solo cuantos faltaban. Eso basta para saber que la pasada
        # esta mal y no basta para arreglarla: un 403 de Drive y una carpeta
        # borrada a mitad se leian exactamente igual. Ahora se cuenta cual
        # fue el tropiezo mas frecuente y se pone en el aviso.
        import collections as _col

        _motivos = _col.Counter(
            (d.get("motivo") or "").split(":")[0].strip()
            for d in (resultado.get("sin_descargar") or [])
            if d.get("motivo"))
        _comun = ""
        if _motivos:
            _nombre, _veces = _motivos.most_common(1)[0]
            if _nombre and _nombre != "no estaba en el equipo al ir a leerlo":
                _comun = (f" El tropiezo mas repetido fue '{_nombre}' "
                          f"({_veces} veces).")
        return (f"{sin_bajar} de {intentados} documentos no llegaron a "
                f"bajarse del Drive, asi que no se pudieron leer. No es un "
                f"problema de lectura: es que los archivos no estaban en el "
                f"equipo.{_comun} Vuelve a lanzar el estudio.")

    if intentados and not resultado.get("fichas"):
        return (f"Se procesaron {intentados} documentos y no entro NI UNA "
                f"ficha al indice. Algo impidio leerlos o vectorizarlos; "
                f"mira el detalle de los que quedaron fuera.")
    return ""


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

    # Se firma la entrada antes de empezar. Si esto es lo ultimo que queda
    # escrito, es que el trabajo no llego al final, y eso es justo lo que
    # hay que poder saber luego.
    with _CERROJO:
        _dejar_constancia(_SINCRONIZACION)

    try:
        resultado = vectorial.indexar(forzar=forzar, progreso=avanzar)
        with _CERROJO:
            _SINCRONIZACION.update({
                "resultado": resultado, "avance": 1.0,
                "mensaje": _resumen_de_la_pasada(resultado),
                "error": _lo_que_salio_mal(resultado),
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
            _dejar_constancia(_SINCRONIZACION)


@app.post("/ajustes/recargar", summary="Releer la configuracion guardada")
def recargar_ajustes():
    """
    Vuelve a aplicar lo que hay en los ajustes guardados.

    POR QUE HACE FALTA UN ENDPOINT PARA ESTO
    ----------------------------------------
    La interfaz y la API son dos procesos distintos. Cuando alguien conecta
    o desconecta Drive desde la interfaz, el cambio se escribe en el
    archivo de ajustes y se aplica al entorno DE LA INTERFAZ. La API no se
    entera: sigue con lo que tenia cuando arranco.

    Se vio asi: la persona pulsaba "Desconectar", el archivo quedaba limpio
    y la pantalla seguia diciendo "Drive conectado", porque ese dato lo
    pinta la API. Parecia que el boton no hacia nada.

    Returns:
        Las claves que quedaron aplicadas, para poder comprobarlo.
    """
    from core import ajustes as _aj
    from core import fuentes as _fu

    aplicados = _aj.aplicar()
    # El estado de la fuente se guarda unos segundos para no listar Drive
    # en cada recarga de pantalla. Tras un cambio de configuracion ese
    # recuerdo es justo lo que no se quiere.
    try:
        _fu.olvidar_estado()
    except Exception:
        pass

    # UN ERROR DE CONFIGURACION CADUCA AL CAMBIAR LA CONFIGURACION
    # ------------------------------------------------------------
    # El ultimo fallo se conserva a proposito: si el cron de las 02:00 se
    # rompe, nadie lo esta mirando y callarlo equivale a no haberlo
    # intentado. Pero cuando el fallo FUE la configuracion y la
    # configuracion acaba de cambiar, ese veredicto ya no describe nada.
    #
    # Se vio en pantalla: la banda roja decia "Drive no esta conectado
    # todavia" mientras la barra lateral, dos centimetros mas a la
    # izquierda, decia "Drive conectado". Las dos tenian razon sobre
    # momentos distintos, y juntas no se podian creer.
    #
    # Lo guardado en disco NO se borra: ahi queda el historial.
    with _CERROJO:
        if _SINCRONIZACION.get("error") and not _SINCRONIZACION["ejecutando"]:
            _SINCRONIZACION["error"] = None
            _SINCRONIZACION["mensaje"] = (
                "La configuracion cambio. El ultimo intento fallo antes de "
                "ese cambio: vuelve a estudiar para saber como queda ahora.")
    return {"aplicados": sorted(aplicados)}


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

    # LO DE ANTES DE REINICIAR TAMBIEN CUENTA
    # ---------------------------------------
    # Sin esto, al reiniciar la API el endpoint contesta "Sin
    # sincronizaciones en esta sesion" aunque el cron llevara semanas
    # funcionando. La pregunta que importa por la manana -- "funciono la de
    # anoche?" -- se quedaba sin respuesta, y habia que deducirla contando
    # documentos dentro de Qdrant.
    #
    # Va en una clave aparte y no mezclada: una cosa es lo que esta pasando
    # ahora y otra lo que paso la ultima vez. Confundirlas haria creer que
    # hay trabajo en marcha cuando no lo hay.
    return _con_lo_que_paso_la_vez_anterior(trabajo)


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

    # AQUI TAMBIEN HACE FALTA SABER COMO ACABO LA ANTERIOR
    # ----------------------------------------------------
    # `/corpus/sincronizacion` adjunta el registro del ultimo estudio y este
    # no lo hacia, asi que la pestana "Drive y Estudio" -- que lee de aqui --
    # pintaba un aviso rojo SIN fecha, sin saber quien lo habia lanzado y,
    # tras un reinicio, directamente decia "Sin sincronizaciones en esta
    # sesion" sobre un estudio que habia fallado media hora antes.
    #
    # Dos pantallas de la misma aplicacion contaban cosas distintas del
    # mismo hecho. Ahora las dos leen lo mismo.
    trabajo = _con_lo_que_paso_la_vez_anterior(trabajo)

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
        # SE DICE SI ADEMAS SE RESUMIO, Y NO SOLO QUE SE APARTO
        # ------------------------------------------------------
        # "Apartado" y "sin analizar" no son lo mismo. Una capa apartada
        # por tamano se recorre entera despues y se indexa su agregado; de
        # eso queda un resumen guardado en `data/resumenes_capas/`.
        #
        # Sin este dato la interfaz no podia distinguirlas, y acababa
        # avisando de cuatro archivos "con problema" que estaban
        # perfectamente estudiados. Un aviso que sale siempre deja de
        # leerse.
        def _tiene_resumen(nombre: str) -> bool:
            """Dice si esa capa ya tiene su resumen agregado en disco."""
            import os as _os

            base = _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))
            return _os.path.exists(_os.path.join(
                base, "data", "resumenes_capas", f"{nombre}.json"))

        resumen["demasiado_grandes"] = [
            {"nombre": d.get("nombre", "?"), "mb": d.get("mb", 0),
             # Cuando se pudo estimar, se dice lo que COSTARIA, que es el
             # motivo real de que no entre. Los megas solo son el sintoma.
             "fichas_estimadas": d.get("fichas_estimadas"),
             "motivo": d.get("motivo", ""),
             "resumido": _tiene_resumen(d.get("nombre", ""))}
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
    buzon_id: str | None = Field(
        None,
        description=(
            "De que buzon recoger las imagenes pegadas. EL BUZON NO ES LA "
            "CONVERSACION: la conversacion cambia al abrir otra o al empezar "
            "una nueva, y el buzon pertenece a la PESTANA del navegador, que "
            "es donde esta el portapapeles. Cuando colgaban del mismo "
            "identificador, pegabas una captura, el guion la guardaba bajo la "
            "charla de ese momento y para cuando enviabas el mensaje la "
            "charla era otra: la imagen seguia en el servidor, bajo un nombre "
            "que ya no miraba nadie. Si no se indica, se usa `session_id`."))


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

    # UNA PETICION MAL FORMADA NO ES UNA CAIDA DEL SERVIDOR
    # -----------------------------------------------------
    # Si el identificador no vale, `guardar_mensaje` levanta y FastAPI lo
    # convierte en un 500. Eso le dice a n8n "el servidor se rompio" cuando
    # lo que pasa es que la peticion venia mal, y de paso la pregunta de
    # quien escribio se pierde con un aviso que no explica nada.
    #
    # Se vio asi: una pestana abierta de antes seguia mandando el texto
    # "Charla actual (en curso)" como identificador. La respuesta del agente
    # llegaba bien, pero el intercambio no se guardaba y la pantalla decia
    # "El orquestador de n8n no respondio (HTTP Error 500)". El 500 era
    # nuestro, y el motivo real no aparecia por ningun lado.
    if not memoria.es_identificador_de_sesion(session_id):
        raise HTTPException(
            status_code=400,
            detail=(f"El identificador de conversacion '{session_id}' no "
                    f"vale: lleva espacios o simbolos, asi que es una "
                    f"etiqueta de pantalla y no un identificador. Recarga la "
                    f"pagina para empezar una conversacion con uno nuevo; lo "
                    f"que ya estaba guardado no se pierde."),
        )

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

    # 3. Las imagenes que se hayan pegado viajan con la pregunta. Se consumen
    #    al leerlas: una imagen se mira una vez.
    #
    #    EL BUZON NO ES LA CONVERSACION
    #    ------------------------------
    #    Colgaba del identificador de la charla, y ese identificador cambia:
    #    al abrir otra conversacion, al empezar una nueva. Pegabas una
    #    captura, se guardaba bajo la charla de ese momento, y para cuando
    #    enviabas el mensaje la charla era otra: la imagen seguia en el
    #    servidor bajo un nombre que ya no miraba nadie, y el agente
    #    contestaba que no puede con imagenes.
    buzon = payload.buzon_id or session_id
    adjuntas = (_imagenes_de_la_sesion(buzon)
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

#: Alto en pixeles de la vista previa que se guarda de cada imagen pegada.
#:
#: La vista previa existe porque el portapapeles entrega casi siempre un
#: archivo llamado "image.png": pegar tres capturas distintas daba tres
#: lineas identicas en pantalla y no habia forma de saber cual era cual.
ALTO_MINIATURA_PEGADA = 90


def _miniatura_base64(crudo: bytes) -> str:
    """
    Reduce una imagen a una vista previa diminuta, en base64.

    Se guarda WEBP y no PNG: con una captura de 1920x1080 se midio 12,2 kB
    en PNG frente a 0,7 kB en WEBP, con la misma pinta.

    Args:
        crudo: La imagen original, en bytes.

    Returns:
        La miniatura en base64, o cadena vacia si no se pudo hacer. Que
        falle no puede impedir pegar: el modelo mira la imagen completa, la
        miniatura es solo para verla en pantalla.
    """
    import base64
    import io

    from PIL import Image

    try:
        imagen = Image.open(io.BytesIO(crudo))
        imagen.thumbnail((ALTO_MINIATURA_PEGADA * 4, ALTO_MINIATURA_PEGADA))
        if imagen.mode not in ("RGB", "RGBA"):
            imagen = imagen.convert("RGB")
        chico = io.BytesIO()
        imagen.save(chico, "WEBP", quality=70)
        return base64.b64encode(chico.getvalue()).decode("utf-8")
    except Exception:
        return ""


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
                     "bytes": len(crudo), "datos": entrada.datos,
                     # La vista previa va aqui y no se calcula al pintar: si
                     # se hiciera en cada pasada de la interfaz, pegar una
                     # captura costaria redimensionarla una y otra vez.
                     "miniatura": _miniatura_base64(crudo)})
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
def diagnostico_fuente(refrescar: bool = False):
    """
    Cuenta si la fuente esta conectada y que trae, sin indexar nada.

    Es lo que mira el panel de Drive: permite comprobar la conexion y ver el
    reparto por carpeta ANTES de lanzar una sincronizacion, que puede tardar.

    Returns:
        Diccionario con `fuente`, `listo`, `motivo`, `documentos`,
        `por_categoria`, `por_formato` e `imagenes`.
    """
    from core import corpus, fuentes

    # EL BOTON DE "VOLVER A PROBAR" TIENE QUE PODER SALTARSE LA CAJA
    # --------------------------------------------------------------
    # El inventario de Drive se guarda un cuarto de hora, porque recorrerlo
    # cuesta once segundos y entre dos preguntas del chat no cambia nada.
    # Pero quien acaba de subir un archivo no quiere esperar quince minutos
    # para verlo contado, y sin esta puerta no tendria forma de forzarlo.
    if refrescar:
        fuentes.olvidar_estado()

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
