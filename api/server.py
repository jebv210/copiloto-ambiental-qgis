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
    return {"base_path": settings.BASE_DIR}


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
