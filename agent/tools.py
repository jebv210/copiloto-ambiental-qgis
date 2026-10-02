"""
Herramientas que el Agente de IA puede ejecutar.
===============================================================================

Cada funcion publica de este modulo se le entrega al modelo de lenguaje como
una "tool". El modelo lee la firma y el docstring para decidir cuando llamarla,
asi que los docstrings de este archivo NO son solo documentacion para humanos:
son parte del contrato con el modelo. Si se cambian, cambia el comportamiento
del agente.

REGLAS DE DISENO DE ESTAS HERRAMIENTAS
--------------------------------------
1. Devuelven SIEMPRE texto plano, nunca lanzan excepciones. Un error se
   describe en la respuesta para que el modelo pueda explicarselo al usuario.
2. Los argumentos son tipos simples (float, str, int). Nada de diccionarios
   anidados: el esquema de function-calling los maneja mal.
3. Nunca devuelven un dato inventado. Si algo no se pudo consultar, lo dicen
   con esas palabras.

QUE SE ELIMINO
--------------
La version anterior instanciaba el conector de la Geodatabase al importar el
modulo, lo que disparaba un recorrido de disco solo por hacer
`import agent.tools`. Ahora la conexion es perezosa (`_conector_gdb`) y se
reutiliza.
"""

import os

from config import settings
from core.evaluator import ConesaEvaluator
from core.validator import ConesaValidator
from database.excel_connector import ExcelConnector
from database.gdb_connector import GDBConnector
from core import catalogo


# ==============================================================================
# CONEXIONES PEREZOSAS
# ==============================================================================

_gdb = {"instancia": None}


def _conector_gdb() -> GDBConnector:
    """
    Devuelve el conector de la Geodatabase, creandolo la primera vez.

    Returns:
        Instancia unica de `GDBConnector`. Siempre devuelve un objeto valido:
        si no hay Geodatabase, el conector lo indica con `disponible = False`.
    """
    if _gdb["instancia"] is None:
        _gdb["instancia"] = GDBConnector()
    return _gdb["instancia"]


_excel = ExcelConnector()


# ==============================================================================
# HERRAMIENTA 1: LINEA BASE GEOGRAFICA
# ==============================================================================

def consultar_linea_base(latitud: float, longitud: float) -> str:
    """
    Consulta la linea base ambiental de una coordenada GPS.

    Intersecta el punto con la capa municipal del area de estudio y con las
    capas de la Geodatabase (vereda, cuenca, areas protegidas RUNAP y paramos)
    y devuelve el diagnostico.

    Args:
        latitud: Latitud en grados decimales (WGS84). Ejemplo: 6.1872
        longitud: Longitud en grados decimales (WGS84). Ejemplo: -74.9922

    Returns:
        Reporte de texto con el municipio, la vereda, la cuenca y el resultado
        de las verificaciones de areas protegidas y paramos. Cuando una
        verificacion no se pudo realizar, lo dice de forma explicita en lugar
        de dar por hecho que no hay riesgo.
    """
    try:
        diagnostico = _conector_gdb().consultar_linea_base_coordenada(
            float(latitud), float(longitud)
        )
    except Exception as exc:
        return f"Error al consultar la linea base geografica: {exc}"

    def _o_desconocido(valor):
        """
        Traduce un dato ausente a un texto honesto.

        Es la regla central de este modulo: cuando un dato no se pudo obtener,
        se dice; nunca se sustituye por un valor plausible. La version anterior
        rellenaba la vereda y la cuenca con cadenas fabricadas a partir del
        nombre del municipio, y el modelo las repetia como si fueran reales.

        Args:
            valor: Dato leido del diagnostico. Puede ser None o cadena vacia.

        Returns:
            El propio valor si tiene contenido; si no, la frase
            "no determinado con los datos disponibles".
        """
        return valor if valor else "no determinado con los datos disponibles"

    lineas = [
        "--- Diagnostico de linea base geografica ---",
        f"Coordenadas: Lat {diagnostico['coordenadas_consulta']['latitud']}, "
        f"Lon {diagnostico['coordenadas_consulta']['longitud']}",
        f"Departamento: {_o_desconocido(diagnostico.get('departamento'))}",
        f"Municipio: {_o_desconocido(diagnostico.get('municipio'))}",
        f"Vereda: {_o_desconocido(diagnostico.get('vereda'))}",
        f"Cuenca hidrografica: {_o_desconocido(diagnostico.get('cuenca'))}",
    ]

    protegidas = diagnostico.get("areas_protegidas")
    if protegidas is None:
        lineas.append(
            "Areas protegidas (RUNAP): NO EVALUADO. No habia capa disponible "
            "para verificarlo. Esto NO significa que no haya areas protegidas."
        )
    elif protegidas:
        lineas.append(
            "Areas protegidas (RUNAP): ALERTA, el punto intersecta con "
            + ", ".join(protegidas)
        )
    else:
        lineas.append(
            "Areas protegidas (RUNAP): verificado, sin colisiones."
        )

    paramo = diagnostico.get("paramo")
    if paramo is None:
        lineas.append(
            "Ecosistemas de paramo: NO EVALUADO. No habia capa disponible "
            "para verificarlo."
        )
    elif paramo:
        lineas.append(
            "Ecosistemas de paramo: ALERTA, el punto cae dentro de un "
            "complejo de paramo delimitado."
        )
    else:
        lineas.append("Ecosistemas de paramo: verificado, fuera de paramo.")

    for advertencia in diagnostico.get("advertencias", []):
        lineas.append(f"Nota tecnica: {advertencia}")

    return "\n".join(lineas)


# ==============================================================================
# HERRAMIENTA 2: CALCULO DE CONESA
# ==============================================================================

def calcular_impacto_conesa(signo: str, i: int, EX: int, MO: int, PE: int,
                            RV: int, SI: int, AC: int, EF: int, PR: int,
                            MC: int) -> str:
    """
    Calcula la Importancia y la severidad de un impacto segun Conesa.

    Aplica I = signo * (3*i + 2*EX + MO + PE + RV + SI + AC + EF + PR + MC)
    y clasifica el resultado en Bajo, Moderado, Severo o Critico.

    Args:
        signo: '+' si el impacto es beneficioso, '-' si es perjudicial.
        i: Intensidad, grado de destruccion (1, 2, 4, 8 o 12).
        EX: Extension, area de influencia (1, 2, 4, 8 o 12).
        MO: Momento, tiempo hasta que aparece el impacto (1, 2 o 4).
        PE: Persistencia, duracion del impacto (1, 2 o 4).
        RV: Reversibilidad por medios naturales (1, 2 o 4).
        SI: Sinergia con otros impactos (1, 2 o 4).
        AC: Acumulacion en el tiempo (1 o 4).
        EF: Efecto, relacion causa-efecto (1 indirecto, 4 directo).
        PR: Periodicidad de la accion (1, 2 o 4).
        MC: Recuperabilidad por intervencion humana (1, 2, 4 u 8).

    Returns:
        Texto con el valor de importancia, la categoria de severidad y la
        recomendacion de manejo asociada. Si algun parametro esta fuera de
        rango, devuelve el motivo exacto del rechazo.
    """
    parametros = {
        "signo": signo, "i": i, "EX": EX, "MO": MO, "PE": PE, "RV": RV,
        "SI": SI, "AC": AC, "EF": EF, "PR": PR, "MC": MC,
    }
    try:
        resultado = ConesaEvaluator.calcular_importancia(parametros)
    except ValueError as exc:
        return (
            f"No se pudo calcular: {exc}\n\n"
            f"Valores admitidos:\n{ConesaValidator.descripcion_rangos()}"
        )

    return (
        "--- Evaluacion Conesa ---\n"
        f"Importancia (I): {resultado['importancia']}\n"
        f"Severidad: IMPACTO {resultado['categoria'].upper()}\n"
        f"Recomendacion: {resultado['recomendacion']}"
    )


# ==============================================================================
# HERRAMIENTA 3: PROCESAMIENTO POR LOTE
# ==============================================================================

def procesar_lote_excel_csv(ruta_archivo: str) -> str:
    """
    Calcula la matriz de Conesa completa de un archivo Excel o CSV.

    El archivo debe tener una fila por valoracion y las columnas
    signo, i, EX, MO, PE, RV, SI, AC, EF, PR, MC.

    Args:
        ruta_archivo: Ruta al archivo .xlsx o .csv en el disco del usuario.

    Returns:
        Resumen del procesamiento: cuantos registros se calcularon, cuantos
        fallaron, donde quedo el archivo de resultados y el conteo por
        severidad.
    """
    try:
        resumen = _excel.procesar_archivo_evaluaciones(ruta_archivo)
    except Exception as exc:
        return f"Error al procesar el archivo en lote: {exc}"

    return (
        "--- Procesamiento por lote ---\n"
        f"Registros analizados: {resumen['registros_procesados']}\n"
        f"Calculos exitosos: {resumen['registros_exitosos']}\n"
        f"Filas con error: {resumen['registros_con_error']}\n"
        f"Archivo de resultados: {resumen['archivo_salida']}\n"
        f"Conteo por severidad: {resumen['conteo_severidad']}"
    )


# ==============================================================================
# HERRAMIENTA 4: BIODIVERSIDAD DEL AREA DE ESTUDIO
# ==============================================================================

def consultar_biodiversidad_local(buscar_termino: str = "") -> str:
    """
    Busca y resume las especies registradas en el area de estudio generada.

    Lee el archivo de biodiversidad filtrado del proyecto y devuelve un
    analisis: cuantos registros coinciden, que clases y familias predominan y
    cuales son las especies mas observadas.

    La busqueda se hace sobre TODAS las columnas de texto del archivo, asi que
    admite tanto nombres cientificos ("Lepidoptera", "Nymphalidae") como
    localidades o nombres de departamento.

    Args:
        buscar_termino: Texto a buscar. Dejar vacio para analizar todos los
            registros del area de estudio.

    Returns:
        Reporte analitico de la biodiversidad encontrada, o un mensaje
        indicando que aun no se ha generado el area de estudio.
    """
    ruta = settings.ruta_biodiversidad_salida()
    if not os.path.exists(ruta):
        return (
            "Todavia no hay area de estudio generada: no existe el archivo de "
            "biodiversidad filtrada. El usuario debe cargar su dataset y "
            "generar el area en la pestana 'Area de Estudio'. No supongas "
            "ningun departamento ni ninguna especie."
        )

    try:
        import pandas as pd
        columnas, delimitador = catalogo.leer_encabezado(ruta)
        df = pd.read_csv(ruta, sep=delimitador, dtype=str,
                         on_bad_lines="skip", encoding="utf-8",
                         encoding_errors="replace")
    except Exception as exc:
        return f"Error al leer la base de biodiversidad: {exc}"

    if df.empty:
        return "El archivo de biodiversidad del area de estudio esta vacio."

    total = len(df)
    termino = (buscar_termino or "").strip()

    if termino:
        # Se busca en todas las columnas de texto en lugar de en una lista fija
        # de columnas. Antes se consultaban siete columnas escritas a mano y,
        # si el archivo no tenia alguna, la herramienta reventaba con KeyError.
        mascara = None
        for columna in df.columns:
            coincide = df[columna].astype(str).str.contains(
                termino, case=False, na=False, regex=False
            )
            mascara = coincide if mascara is None else (mascara | coincide)
        filtrado = df[mascara] if mascara is not None else df.iloc[0:0]
    else:
        filtrado = df

    if filtrado.empty:
        return (
            f"No hay registros que coincidan con '{termino}' entre los "
            f"{total} registros del area de estudio."
        )

    lineas = [
        "--- Analisis de biodiversidad del area de estudio ---",
        f"Filtro aplicado: {termino or 'ninguno (todos los registros)'}",
        f"Coincidencias: {len(filtrado)} de {total} registros.",
    ]

    def _ranking(nombres_probables, titulo, tope):
        """
        Anade al reporte el ranking de valores de una columna.

        Si la columna no existe en el archivo, no hace nada y no falla. Eso
        permite que la misma herramienta sirva para un archivo de GBIF completo
        y para uno recortado con menos columnas, sin listas de columnas
        obligatorias escritas en el codigo.

        Args:
            nombres_probables: Nombres posibles de la columna, en orden de
                preferencia. Se resuelve con `catalogo.sugerir_columna`.
            titulo: Encabezado que se muestra antes del ranking.
            tope: Cuantos valores incluir como maximo.

        Returns:
            None. Modifica la lista `lineas` del ambito exterior.
        """
        columna = catalogo.sugerir_columna(list(df.columns), nombres_probables)
        if not columna:
            return
        conteo = filtrado[columna].dropna().value_counts().head(tope)
        if conteo.empty:
            return
        lineas.append(f"\n{titulo} (columna '{columna}'):")
        for valor, cantidad in conteo.items():
            lineas.append(f"  - {valor}: {cantidad} registros")

    _ranking(["stateProvince", "departamento", "state"], "Departamentos presentes", 5)
    _ranking(["class", "clase"], "Clases de organismos", 8)
    _ranking(["order", "orden"], "Ordenes taxonomicos", 8)
    _ranking(["family", "familia"], "Familias predominantes", 5)
    _ranking(["scientificName", "species", "especie"], "Especies mas reportadas", 10)

    return "\n".join(lineas)


# ==============================================================================
# HERRAMIENTA 5: ESTADO DEL AREA DE ESTUDIO
# ==============================================================================

def describir_area_de_estudio() -> str:
    """
    Informa que area de estudio esta cargada ahora mismo en el sistema.

    Usar SIEMPRE que el usuario pregunte de que departamento son los datos,
    cuantos registros hay, o que informacion tiene disponible el sistema. Esta
    herramienta lee el estado real de los archivos, de modo que la respuesta
    nunca es una cifra memorizada.

    Returns:
        Texto con el departamento cargado, el numero de municipios, el numero
        de registros de biodiversidad y las capas de la Geodatabase conectada.
    """
    from core.dynamic_filter import resumen_area_generada

    try:
        info = resumen_area_generada()
    except Exception as exc:
        return f"No se pudo leer el estado del area de estudio: {exc}"

    lineas = ["--- Area de estudio cargada actualmente ---"]

    if info["municipios_existe"]:
        departamentos = ", ".join(info["departamentos"]) or "sin identificar"
        lineas.append(
            f"Cartografia: {info['municipios_total']} municipios del "
            f"departamento de {departamentos}."
        )
    else:
        lineas.append("Cartografia: no hay capa municipal generada.")

    if info["biodiversidad_existe"] and info["registros"] is not None:
        lineas.append(
            f"Biodiversidad: {info['registros']} registros de ocurrencias en "
            f"el archivo filtrado."
        )
    else:
        lineas.append("Biodiversidad: no hay archivo filtrado generado.")

    conector = _conector_gdb()
    if conector.disponible:
        capas = conector.nombres_capas()
        lineas.append(f"Geodatabase: conectada, {len(capas)} capas disponibles.")
    else:
        lineas.append(
            "Geodatabase: no conectada. Las consultas de areas protegidas y "
            "paramos no se pueden realizar."
        )

    return "\n".join(lineas)


#: Lista de herramientas que se le entregan al modelo. Tenerla aqui evita que
#: `chatbot.py` y `test_gemini.py` mantengan copias distintas del conjunto.
HERRAMIENTAS = [
    consultar_linea_base,
    calcular_impacto_conesa,
    procesar_lote_excel_csv,
    consultar_biodiversidad_local,
    describir_area_de_estudio,
]
