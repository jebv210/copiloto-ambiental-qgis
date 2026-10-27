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
from core import corpus
from core import informes


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

    # El area la fija ahora la pestana de Area de Estudio a partir de un
    # poligono concreto, no del filtrado de un departamento. Se pregunta
    # primero por ahi: `resumen_area_generada` describe la capa buscando
    # campos del DANE (`DPTO_CNMBR`) que un poligono de un KMZ no trae, y
    # salia "municipios del departamento de sin identificar", que no dice
    # nada util a quien pregunta que area esta cargada.
    try:
        from core import area_estudio as _ae

        vigente = _ae.actual()
    except Exception:
        vigente = {"hay": False}

    if vigente.get("hay"):
        lineas.append(
            f"Area de estudio: {vigente['nombre']}, "
            f"{vigente.get('km2', '?')} km2 "
            f"({vigente.get('vertices', '?')} vertices). "
            f"Salio de {vigente.get('origen') or 'un archivo cargado'}."
        )
        # La ruta que se dice es la DEL EQUIPO. Quien lee esto va a abrir QGIS
        # en su maquina, y "/app/data/..." ahi no existe.
        from config import settings as _ajustes

        _equipo = _ajustes.ruta_proyecto_en_el_equipo()
        _relativa = vigente["archivo"].replace(_ajustes.BASE_DIR, "").lstrip("/\\")
        lineas.append(
            f"La capa esta en {os.path.join(_equipo, _relativa)} y es la que "
            f"lee QGIS."
        )
    elif info["municipios_existe"]:
        departamentos = ", ".join(info["departamentos"]) or "sin identificar"
        lineas.append(
            f"Cartografia: {info['municipios_total']} municipios del "
            f"departamento de {departamentos}."
        )
    else:
        lineas.append(
            "Area de estudio: no hay ninguna fijada. Se fija en la pestana "
            "'Area de Estudio' subiendo el archivo que la delimita."
        )

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


# ==============================================================================
# CORPUS DOCUMENTAL
# ==============================================================================
# Estas seis herramientas le dan al agente acceso a la bibliografia: catalogos
# de mariposas, listas de chequeo, plantas hospederas, ciclos de vida y riesgo
# climatico. Todas devuelven texto YA CITADO.
#
# El reparto de responsabilidades es deliberado: la evidencia y su procedencia
# las pone `core/corpus.py`, la estructura del informe la pone
# `core/informes.py`, y el modelo solo conversa sobre ese material. El modelo no
# redacta afirmaciones de su memoria, porque entonces no habria forma de
# distinguir lo que el corpus documenta de lo que el modelo supone.


def _panorama() -> dict:
    """
    Describe de donde sale lo que el agente sabe, ahora mismo.

    POR QUE NO VALE `corpus.resumen()`
    ----------------------------------
    Esa funcion describe la CARPETA LOCAL. Cuando la fuente es Drive, los
    documentos se borran del equipo en cuanto se leen, asi que esa carpeta
    esta vacia por diseno y describirla es describir un sitio donde ya no hay
    nada.

    Se midio el dano. Preguntado "que aprendiste de nuevo", el agente
    contestaba: "el corpus sigue contando con 1 documento, version
    1docs-753b56cc, sin texto extraible" -- el zip suelto que quedaba en la
    carpeta -- mientras el indice tenia 268 fichas de cuatro documentos de
    Drive. Peor: si ese zip se borra, `disponible` pasa a False y las
    herramientas de busqueda se niegan a buscar, con el indice entero
    delante.

    Lo que manda es: que ve la FUENTE, y que hay en el INDICE.

    Returns:
        Diccionario con `fuente`, `documentos`, `capas`, `fichas_indice`,
        `se_puede_buscar`, `motivo` y `local`.
    """
    from core import fuentes, vectorial

    try:
        fuente = fuentes.estado()
    except Exception as exc:
        fuente = {"fuente": "?", "listo": False, "motivo": str(exc)[:160],
                  "documentos": 0, "descartados": [], "por_categoria": []}

    try:
        se_puede, motivo = vectorial.disponible()
        fichas = vectorial.contar_fichas()
    except Exception as exc:
        se_puede, motivo, fichas = False, str(exc)[:160], 0

    return {
        "fuente": fuente.get("fuente", "?"),
        "listo": bool(fuente.get("listo")),
        "motivo_fuente": fuente.get("motivo", ""),
        "documentos": fuente.get("documentos", 0),
        "por_categoria": fuente.get("por_categoria", []),
        "capas": list(fuente.get("descartados") or []),
        "fichas_indice": fichas,
        "se_puede_buscar": bool(se_puede),
        "motivo": motivo,
        "local": fuente.get("en_el_equipo", True),
    }

def estado_del_corpus() -> str:
    """
    Informa de que bibliografia tiene cargada el sistema.

    Usala cuando el usuario pregunte que documentos conoces, de que fuentes
    dispones, o cuando una consulta al corpus no devuelva nada y convenga
    explicar por que.

    Returns:
        Resumen con la carpeta, la version del corpus, el numero de documentos
        y fichas, y el reparto por categoria tematica.
    """
    vista = _panorama()

    if not vista["listo"] and not vista["fichas_indice"]:
        return (
            "No hay nada estudiado todavia.\n\n"
            f"Fuente configurada: {vista['fuente']}. "
            f"{vista['motivo_fuente']}\n\n"
            "Cuando la fuente tenga documentos y se lance un estudio, "
            "apareceran aqui."
        )

    lineas = [
        "LO QUE TENGO ESTUDIADO",
        f"  Fuente      : {vista['fuente']}",
        f"  Documentos  : {vista['documentos']} leidos enteros",
        f"  Fichas      : {vista['fichas_indice']} en el indice, que es lo "
        f"que puedo consultar",
    ]
    if not vista["local"]:
        lineas.append("  Los documentos se leen de la fuente y se borran del "
                      "equipo; lo que queda aqui es el indice.")
    if vista["por_categoria"]:
        lineas.append("")
        lineas.append("  Por categoria:")
        for nombre, cuantos in vista["por_categoria"]:
            lineas.append(f"    {cuantos:4d}  {nombre}")

    # Lo que existe pero NO se leyo entero. Omitirlo hacia que el agente
    # negara archivos que estan ahi delante: contestaba "no tengo informacion
    # sobre e_eccmc_100K_2024" de un archivo de 434 MB que esta en la fuente.
    if vista["capas"]:
        lineas.append("")
        lineas.append(f"  Capas de datos ({len(vista['capas'])}), demasiado "
                      f"grandes para leerlas enteras. De cada una conozco su "
                      f"cabecera, no su contenido:")
        for capa in vista["capas"]:
            lineas.append(f"    {capa.get('mb', '?')} MB  {capa.get('nombre')}")

    if not vista["se_puede_buscar"]:
        lineas.append("")
        lineas.append(f"  AVISO: ahora mismo no puedo buscar. {vista['motivo']}")
    return "\n".join(lineas)


#: Marca que la interfaz busca en la respuesta para ofrecer la descarga del
#: script. Va en una linea aparte y se quita antes de mostrar el texto, asi
#: que quien lee la conversacion no la ve.
MARCA_SCRIPT_QGIS = "[[DESCARGAR_SCRIPT_QGIS]]"


def entregar_script_qgis() -> str:
    """
    Entrega el script que dibuja las capas en QGIS, y dice si hay con que.

    Usala cuando el usuario pida un mapa, un grafico en QGIS, el script, las
    capas, o pregunte como llevar los datos a QGIS. Devuelve el script y, muy
    importante, el estado REAL de los archivos que necesita: no sirve de nada
    entregar un script si todavia no hay datos que dibujar, y decirlo por
    adelantado evita que el usuario lo ejecute y vea un mapa vacio sin saber
    por que.

    No generes tu el codigo del script ni lo copies en la respuesta: es un
    archivo del proyecto, se mantiene aparte y la interfaz lo ofrece en un
    boton de descarga.

    Returns:
        Instrucciones de uso y el estado de los archivos que el script espera.
    """
    import os

    raiz = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    ruta = os.path.join(raiz, "cargar_capas_qgis.py")
    if not os.path.exists(ruta):
        return ("No encuentro el script `cargar_capas_qgis.py` en el "
                "proyecto. Sin el no puedo entregar nada.")

    lineas = [
        "He preparado el script de QGIS. Debajo tienes el boton para "
        "descargarlo.",
        "",
        "COMO SE USA",
        "  1. Abre QGIS y ve a Complementos > Consola de Python "
        "(Ctrl + Alt + P).",
        "  2. Pulsa el boton de la carpeta y elige el archivo descargado.",
        "  3. Dale al boton verde de Play.",
        "",
        "No hay que configurar nada: el script encuentra solo la carpeta del "
        "proyecto y los archivos generados.",
        "",
        "QUE VA A DIBUJAR, SEGUN LO QUE HAY AHORA MISMO",
    ]

    # El estado se consulta de verdad, no se da por supuesto: es la diferencia
    # entre avisar de que falta un archivo y que el usuario vea un mapa vacio.
    try:
        lineas.append(describir_area_de_estudio())
    except Exception as exc:
        lineas.append(f"  (no se pudo comprobar el estado: {exc})")

    lineas.append("")
    lineas.append(
        "Si falta la capa de municipios o el archivo de biodiversidad, "
        "generalos antes en la pestana 'Area de Estudio': el script los lee "
        "de ahi."
    )
    lineas.append("")
    lineas.append(
        "INSTRUCCION PARA TI, NO PARA EL USUARIO: copia la linea siguiente "
        "tal cual, al final de tu respuesta y en una linea aparte. Es una "
        "marca que la interfaz sustituye por un boton de descarga. NO la "
        "menciones, NO la expliques y NO la pongas entre comillas ni entre "
        "acentos graves: si la nombras, al usuario le aparece un hueco vacio "
        "en mitad del texto."
    )
    lineas.append(MARCA_SCRIPT_QGIS)
    return "\n".join(lineas)


def datos_para_qgis() -> str:
    """
    Dice que hay en la bibliografia cargada que sirva para hacer un mapa.

    Usala cuando el usuario pregunte si hay datos para QGIS, si hay
    coordenadas, cuencas, veredas o quebradas con las que dibujar algo, o que
    cartografia hace falta conseguir. Tambien antes de proponer un script de
    QGIS: evita ofrecer un mapa que no se puede hacer porque no hay con que.

    Separa tres cosas que no son lo mismo, y conviene no mezclar al responder:

      - COORDENADAS: lo unico que situa algo en un mapa por si solo.
      - TOPONIMOS (cuencas, drenajes, veredas): no situan nada, pero se pueden
        enganchar por nombre a una capa que ya tenga geometria.
      - SISTEMA DE REFERENCIA Y SUPERFICIES: hacen falta para que las cifras y
        las reproyecciones salgan bien.

    Cuidado al interpretar el resultado: que existan coordenadas NO significa
    que sean del area de estudio. Suelen venir de los antecedentes
    bibliograficos, que citan campanas de campo de otros lugares. El informe lo
    comprueba cuando hay capa del area de estudio generada, y lo advierte
    cuando no.

    Returns:
        Informe con lo que hay de cada clase, el documento de donde sale cada
        dato, y un veredicto explicito sobre si se puede dibujar un mapa.
    """
    from core import geografia
    return geografia.informe()


def consultar_corpus(pregunta: str) -> str:
    """
    Busca en la bibliografia cargada y devuelve los fragmentos pertinentes.

    Usala para CUALQUIER pregunta cuya respuesta deba salir de la bibliografia
    cargada, sea cual sea su tema: el corpus se cambia sin tocar el codigo, asi
    que no des por hecho de que trata. Si no sabes que contiene, consulta antes
    el estado del corpus. Cada fragmento viene con el documento y la pagina:
    cita siempre esa procedencia en tu respuesta y no anadas afirmaciones que no
    aparezcan en los fragmentos.

    Args:
        pregunta: La consulta en lenguaje natural, tal como la hizo el usuario.

    Returns:
        Los fragmentos encontrados con su fuente, o una explicacion de por que
        no se encontro nada.
    """
    if not pregunta or not pregunta.strip():
        return "Indique que desea consultar en la bibliografia."

    # Lo que decide si se puede buscar es el INDICE, no la carpeta local.
    # Antes se miraba `corpus.resumen()["disponible"]`, y con Drive esa
    # carpeta queda vacia a proposito: bastaba borrar el ultimo archivo suelto
    # para que el agente se negara a buscar con 268 fichas indexadas delante.
    vista = _panorama()
    if not vista["se_puede_buscar"]:
        return estado_del_corpus()

    # Busqueda hibrida: palabras (BM25) mezcladas con significado (Qdrant).
    # Si el indice vectorial no esta construido o Qdrant no responde, la
    # funcion devuelve exactamente lo que daria `corpus.buscar`, de modo que
    # el agente sigue contestando sin base vectorial.
    #
    # OJO: esto NO afecta a `buscar_especie_exacta`, que sigue siendo literal.
    # Un vector no distingue `Polites otho` de `Polites vibex`, y ese fue el
    # fallo que obligo a crear la busqueda por binomio.
    from core import vectorial
    fichas = vectorial.buscar_hibrido(pregunta, limite=6)
    if not fichas:
        aviso = ""
        if vista["capas"]:
            # Puede estar en una de las capas que no se leyeron enteras. Decir
            # "no hay nada" sin mencionarlas seria negar algo que esta ahi.
            aviso = (
                "\n\nAdemas hay "
                f"{len(vista['capas'])} capa(s) de datos demasiado grandes "
                "para leerlas enteras, de las que solo conozco la cabecera: "
                + ", ".join(c.get("nombre", "?") for c in vista["capas"])
                + ". Si lo que busca esta dentro de una de ellas, no puedo "
                "verlo desde aqui."
            )
        return (
            f"No se encontro nada sobre esa consulta en lo estudiado "
            f"({vista['documentos']} documentos de {vista['fuente']}, "
            f"{vista['fichas_indice']} fichas).\n\n"
            "No se trata de que el dato no exista: es que los documentos "
            "estudiados no lo tratan. Puede consultarse otra formulacion o "
            "anadir la fuente que falte." + aviso
        )

    lineas = [f"Encontrado en lo estudiado ({vista['fuente']}):", ""]
    for numero, ficha in enumerate(fichas, start=1):
        lineas.append(f"[{numero}] {informes.recortar(ficha['texto'])}")
        lineas.append(f"    Fuente: {informes.cita(ficha)}")
        lineas.append("")
    return "\n".join(lineas)


def buscar_especie_exacta(nombre_cientifico: str) -> str:
    """
    Comprueba si un nombre cientifico aparece literalmente en la bibliografia.

    Usala antes de afirmar que una especie esta documentada. La busqueda es
    EXACTA a proposito: no devuelve nombres parecidos. Si no encuentra nada,
    significa que el corpus no nombra esa especie, y debes decirlo asi en lugar
    de ofrecer una especie de nombre similar.

    Args:
        nombre_cientifico: Binomio, por ejemplo "Polites otho". Tambien se
            admite solo el genero.

    Returns:
        Las menciones literales con su fuente, o la constancia de que no hay
        ninguna.
    """
    if not nombre_cientifico or not nombre_cientifico.strip():
        return "Indique el nombre cientifico que desea comprobar."

    vista = _panorama()

    # Esta busqueda es LITERAL y lee las fichas de la carpeta local. Con una
    # fuente remota esas fichas no existen: los documentos se borran al
    # leerlos. Entonces la busqueda devuelve cero por falta de texto donde
    # buscar, no por ausencia del nombre.
    #
    # Afirmar "NO aparece" en ese caso es la peor respuesta posible: es
    # exactamente la clase de certeza que esta herramienta existe para dar, y
    # darla sin haber mirado nada la vuelve peligrosa. Mejor decir que no se
    # pudo comprobar.
    hay_texto_local = corpus.resumen().get("fichas", 0) > 0
    if not hay_texto_local:
        return (
            f"No puedo comprobar literalmente '{nombre_cientifico}'.\n\n"
            f"La comprobacion literal necesita el texto de los documentos en "
            f"el equipo, y con la fuente '{vista['fuente']}' los documentos se "
            f"borran en cuanto se leen: aqui solo queda el indice, que compara "
            f"por significado y NO sirve para afirmar que un binomio aparece "
            f"tal cual.\n\n"
            f"Puedo buscarlo por significado con la consulta normal al corpus, "
            f"pero el resultado no valdria como comprobacion literal."
        )

    fichas = corpus.buscar_binomio(nombre_cientifico, limite=8)
    if not fichas:
        return (
            f"'{nombre_cientifico}' NO aparece literalmente en ninguno de los "
            f"{vista['documentos']} documentos estudiados.\n\n"
            "No se ha buscado ningun nombre aproximado: entre nombres "
            "cientificos, el parecido no es indicio de identidad. Generos como "
            "Magneuptychia y Megeuptychia se diferencian en dos letras y no "
            "tienen relacion directa."
        )

    lineas = [f"'{nombre_cientifico}' aparece en {len(fichas)} fichas:", ""]
    for numero, ficha in enumerate(fichas, start=1):
        lineas.append(f"[{numero}] {informes.recortar(ficha['texto'], 300)}")
        lineas.append(f"    Fuente: {informes.cita(ficha)}")
        lineas.append("")
    return "\n".join(lineas)


def reporte_taxonomico_especie(nombre_cientifico: str) -> str:
    """
    Emite el Reporte Tecnico Taxonomico completo de una especie.

    Usala cuando el usuario pida un informe, un reporte o una caracterizacion
    taxonomica de una especie. Devuelve el documento ya estructurado en seis
    secciones; presentalo tal cual, sin reescribirlo ni resumirlo, porque su
    valor esta en que cada afirmacion conserva su fuente.

    Args:
        nombre_cientifico: Binomio de la especie, por ejemplo "Polites otho".

    Returns:
        El informe completo en texto plano.
    """
    if not nombre_cientifico or not nombre_cientifico.strip():
        return "Indique la especie de la que desea el reporte taxonomico."
    return informes.reporte_taxonomico(nombre_cientifico.strip())


def ficha_de_campo(nombre_cientifico: str) -> str:
    """
    Emite una ficha breve de una especie para llevar al trabajo de campo.

    Usala cuando el usuario pida algo corto y practico sobre una especie: de que
    planta depende, en que fase del ciclo esta, que necesita para criarse. Es
    mas breve que el reporte taxonomico y esta pensada para imprimirse.

    Args:
        nombre_cientifico: Binomio de la especie.

    Returns:
        La ficha en texto plano.
    """
    if not nombre_cientifico or not nombre_cientifico.strip():
        return "Indique la especie de la que desea la ficha de campo."
    return informes.ficha_de_especie(nombre_cientifico.strip())


def informe_cientifico_tema(tema: str) -> str:
    """
    Emite el Informe Cientifico de seis secciones sobre un tema.

    Usala cuando el usuario pida un informe sobre un asunto amplio y no sobre
    una especie concreta: el fenomeno del Nino, la conservacion del bosque seco,
    las plantas hospederas de una region. Presentalo tal cual.

    Args:
        tema: El asunto del informe.

    Returns:
        El informe completo en texto plano.
    """
    if not tema or not tema.strip():
        return "Indique el tema del informe cientifico."
    return informes.informe_cientifico(tema.strip())


#: Lista de herramientas que se le entregan al modelo. Tenerla aqui evita que
#: `chatbot.py` y `test_gemini.py` mantengan copias distintas del conjunto.
HERRAMIENTAS = [
    consultar_linea_base,
    calcular_impacto_conesa,
    procesar_lote_excel_csv,
    consultar_biodiversidad_local,
    describir_area_de_estudio,
    estado_del_corpus,
    datos_para_qgis,
    entregar_script_qgis,
    consultar_corpus,
    buscar_especie_exacta,
    reporte_taxonomico_especie,
    ficha_de_campo,
    informe_cientifico_tema,
]
