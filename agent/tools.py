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
from core import inaturalist


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

def revisar_datos_de_campo(ruta_archivo: str) -> str:
    """
    Revisa un paquete de datos de campo y dice si sirve o no, y por que.

    Usala cuando pasen un archivo de una salida de campo y pregunten si
    esta bien, si se puede usar, si tiene errores, o cuantos registros
    trae. Tambien antes de incorporar esos datos a cualquier analisis: un
    punto mal ubicado arrastra el error hasta el informe final.

    QUE ADMITE
    ----------
    Un .zip exportado de QField (aunque pese cientos de megas: solo se
    abre lo que hay que revisar, no las fotografias), un shapefile .shp
    con sus archivos hermanos, un GeoPackage .gpkg, o una planilla .xlsx
    o .csv con columnas de latitud y longitud.

    QUE COMPRUEBA, Y QUE NO
    -----------------------
    Comprueba cosas que se pueden decidir sin opinar: coordenadas
    imposibles, puntos fuera del area de estudio, latitud y longitud
    cambiadas de sitio, registros a menos de diez metros unos de otros,
    fichas sin identificar y capas vacias. Todas son deterministas: el
    mismo archivo da siempre el mismo veredicto.

    NO comprueba si la identificacion de una especie es correcta ni si el
    dato tiene sentido ecologico. Eso no lo puede saber un programa.

    COMO INTERPRETAR LA RESPUESTA
    -----------------------------
    Hay dos niveles y no significan lo mismo. Un PROBLEMA impide usar los
    datos. Un AVISO no: senala algo que conviene mirar, y muchas veces
    tiene explicacion -- treinta puntos juntos pueden ser un muestreo
    intensivo en una parcela pequena, no un error. No presentes un aviso
    como si fuera un fallo.

    Args:
        ruta_archivo: Ruta del archivo o del paquete a revisar.

    Returns:
        El veredicto, el recuento y la lista de problemas y avisos.
    """
    import os

    from core import campo

    if not ruta_archivo or not os.path.exists(ruta_archivo):
        return (f"No encuentro el archivo '{ruta_archivo}'. Necesito la "
                f"ruta de un .zip, .shp, .gpkg, .xlsx o .csv que exista "
                f"en el equipo.")

    try:
        lectura = campo.leer(ruta_archivo)
        veredicto = campo.revisar(lectura)
    except Exception as exc:
        return (f"No pude revisar el archivo: {type(exc).__name__}: "
                f"{str(exc)[:160]}")

    resumen = veredicto["resumen"]
    nombre = os.path.basename(ruta_archivo)
    peso = os.path.getsize(ruta_archivo) / (1024 * 1024)

    lineas = [
        f"REVISION DE {nombre} ({peso:.1f} MB, formato "
        f"{resumen.get('formato', '?')})",
        "",
    ]

    # LO QUE SALIO DE AQUI NO SE JUZGA
    # --------------------------------
    # `sirve` tiene tres valores, no dos. Cuando lo que se revisa es el
    # paquete de QGIS que entrega este mismo sistema, no hay veredicto que
    # dar: comprobarlo contra el area de estudio es el sistema midiendose
    # consigo mismo. Con un `if veredicto["sirve"]` a secas, ese caso caia
    # en el `else` y el agente anunciaba que los datos NO se pueden usar,
    # sobre un archivo que el mismo acababa de generar.
    if veredicto.get("propio"):
        return (f"{nombre} ({peso:.1f} MB) no es una salida de campo: es el "
                f"paquete de QGIS que genera este mismo sistema, con "
                f"{resumen.get('capas', 0)} capas y "
                f"{resumen.get('puntos', 0)} geometrias puntuales.\n\n"
                f"No hay nada que validar en el: todo lo que lleva salio de "
                f"aqui. Esta revision es para lo que VUELVE del campo, asi "
                f"que lo que hay que subir es el .zip que exporta QField, o "
                f"un .gpkg, .xlsx o .csv con las coordenadas recogidas.\n\n"
                f"El paquete en si esta bien: se abre con doble clic en su "
                f"proyecto.qgs.")

    if veredicto["sirve"]:
        lineas.append("VEREDICTO: los datos SIRVEN. No hay nada que impida "
                      "usarlos.")
    else:
        lineas.append("VEREDICTO: los datos NO se pueden usar todavia.")

    lineas += [
        "",
        f"  Capas leidas        : {resumen.get('capas', 0)}",
        f"  Puntos con geometria: {resumen.get('puntos', 0)}",
        f"  Coordenadas validas : {resumen.get('validos', 0)}",
    ]
    if resumen.get("hay_area"):
        lineas.append(f"  Dentro del area     : {resumen.get('dentro', 0)}")
        # Se nombran aparte porque no significan lo mismo. Un recorrido que
        # nace fuera del area y entra despues es un cauce normal; una ficha
        # tomada fuera es un dato que hay que mirar. Sumados en una sola
        # cifra, el agente los contaba todos como problemas.
        lineas.append(f"  Recorridos que entran: "
                      f"{resumen.get('recorridos_fuera', 0)}")
        lineas.append(f"  Fichas fuera del area: {resumen.get('fuera', 0)}")
    else:
        lineas.append("  No hay area de estudio fijada, asi que no se pudo "
                      "comprobar si los puntos caen dentro.")

    if veredicto["problemas"]:
        lineas += ["", "PROBLEMAS (impiden usar los datos):"]
        lineas += [f"  - {p}" for p in veredicto["problemas"]]

    if veredicto["avisos"]:
        lineas += ["", "AVISOS (no impiden usarlos, pero conviene mirarlos):"]
        lineas += [f"  - {a}" for a in veredicto["avisos"]]

    if not veredicto["problemas"] and not veredicto["avisos"]:
        lineas += ["", "Sin problemas ni avisos: el paquete esta limpio."]

    # LO REVISADO SE CONVIERTE EN CAPA, NO SOLO EN VEREDICTO
    # ------------------------------------------------------
    # Antes esto terminaba aqui: se revisaba el paquete y el mapa seguia
    # mostrando solo el area de estudio. Se subia un .zip con 57 registros
    # de campo y la cartografia salia identica, como si no se hubiera hecho
    # nada. Escribir la capa es lo que cierra el circulo.
    if veredicto["sirve"]:
        capa = campo.exportar_capa(veredicto, ruta_archivo)
        if capa.get("ruta"):
            lineas += [
                "",
                f"Se escribio la capa '{capa['nombre']}' con sus "
                f"{capa['puntos']} puntos. Aparece en QGIS al ejecutar el "
                f"script, y tambien dentro del paquete de QGIS si lo pides.",
            ]
        elif capa.get("error"):
            lineas += ["", f"No se pudo escribir la capa: {capa['error']}"]

    return "\n".join(lineas)


def _coordenadas_del_area(ruta: str) -> list:
    """
    Describe donde cae una capa: su recuadro y su centro.

    POR QUE EL RECUADRO Y EL CENTRO, Y NO LOS VERTICES
    --------------------------------------------------
    Porque el area de San Carlos tiene 3.405 vertices y volcarlos seria
    ilegible e inutil. Lo que situa un area en una conversacion son dos
    cosas: entre que latitudes y longitudes cae, y donde esta su centro.

    Se dan cinco decimales. Un grado son unos 111 km, asi que el quinto
    decimal vale alrededor de un metro: mas precision seria fingirla.

    Args:
        ruta: Ruta del .geojson de la capa.

    Returns:
        Lista de lineas de texto. Vacia si no se pudo leer el archivo.
    """
    import json

    try:
        with open(ruta, encoding="utf-8") as fh:
            datos = json.load(fh)
    except (OSError, ValueError):
        return []

    equis, yes = [], []

    def recorrer(coordenadas):
        """Baja por la anidacion del GeoJSON hasta los pares de numeros."""
        if (isinstance(coordenadas, (list, tuple)) and len(coordenadas) >= 2
                and all(isinstance(v, (int, float))
                        for v in coordenadas[:2])):
            equis.append(float(coordenadas[0]))
            yes.append(float(coordenadas[1]))
            return
        if isinstance(coordenadas, (list, tuple)):
            for trozo in coordenadas:
                recorrer(trozo)

    for entidad in datos.get("features") or []:
        recorrer((entidad.get("geometry") or {}).get("coordinates"))
    if not equis:
        return []

    xmin, xmax = min(equis), max(equis)
    ymin, ymax = min(yes), max(yes)
    return [
        f"Coordenadas (EPSG:4326, grados decimales): va de {ymin:.5f} a "
        f"{ymax:.5f} de latitud y de {xmin:.5f} a {xmax:.5f} de longitud.",
        f"Su centro cae en {(ymin + ymax) / 2:.5f}, {(xmin + xmax) / 2:.5f}.",
    ]


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
        # LA BARRA LA MANDA EL EQUIPO DE DESTINO, NO EL CONTENEDOR
        # --------------------------------------------------------
        # Esta ruta se arma dentro de un contenedor Linux pero apunta
        # al equipo de quien pregunta, que es Windows. Usando `os.sep`
        # salia "C:\\proyecto/data/capa.geojson", con las dos barras
        # mezcladas: se lee como un error aunque funcione. El separador
        # se deduce de la propia ruta del equipo.
        _barra = chr(92) if chr(92) in _equipo else "/"
        _en_equipo = (_equipo.rstrip("/\\") + _barra
                      + _relativa.replace("/", _barra).replace(chr(92), _barra))
        lineas.append(f"La capa esta en {_en_equipo} y es la que lee QGIS.")

        # LAS COORDENADAS, QUE SON LO QUE SE PREGUNTA
        # -------------------------------------------
        # Antes esto decia el nombre, los km2 y el numero de vertices, y
        # nada mas. A la pregunta mas obvia que se le puede hacer a un
        # area de estudio -- donde esta -- el agente no tenia con que
        # responder.
        lineas.extend(_coordenadas_del_area(vigente["archivo"]))
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
        leidos = vectorial.contar_documentos_indexados()
    except Exception as exc:
        se_puede, motivo, fichas, leidos = False, str(exc)[:160], 0, 0

    return {
        "fuente": fuente.get("fuente", "?"),
        "listo": bool(fuente.get("listo")),
        "motivo_fuente": fuente.get("motivo", ""),
        # Lo que VE la fuente y lo que se ha LEIDO son dos numeros distintos,
        # y confundirlos es lo que hacia que el agente negara documentos que
        # estaban en el Drive esperando turno.
        "documentos": fuente.get("documentos", 0),
        "documentos_indexados": leidos,
        "por_categoria": fuente.get("por_categoria", []),
        "capas": list(fuente.get("descartados") or []),
        "fichas_indice": fichas,
        "se_puede_buscar": bool(se_puede),
        "motivo": motivo,
        "local": fuente.get("en_el_equipo", True),
    }

#: Cuantas lineas como mucho devuelve el inventario del Drive.
#:
#: Con 425 archivos la lista entera son unas 480 lineas, que caben de
#: sobra. El tope existe para una carpeta de miles: antes de llenar la
#: conversacion con una lista que nadie va a leer, se corta y se dice como
#: filtrar.
MAXIMO_LINEAS_INVENTARIO = 700


def leer_documento_drive(nombre_o_patron: str, paginas: int = 6) -> str:
    """
    Lee directamente el contenido real de un documento o archivo especifico de Google Drive.

    USALA SIEMPRE que el usuario pregunte '¿sabes que dice este documento?', '¿que dice el archivo X?',
    o mencione un documento concreto del Drive (ejemplo: 'Lista de Chequeo de Mariposas de Colombia',
    'Decreto 2820 de 2010', 'Decreto 1076 de 2015', 'Manual de Plagas', etc.).

    Args:
        nombre_o_patron: Nombre completo o palabras clave del titulo del archivo a leer.
        paginas: Numero de paginas iniciales o relevantes a extraer (por defecto 6).

    Returns:
        Texto completo de las paginas, indice, presentacion y contenido del documento con su cita.
    """
    import os
    import re
    from core import fuentes, corpus

    if not nombre_o_patron or not nombre_o_patron.strip():
        return "Indique el nombre o titulo del documento que desea leer."

    f = fuentes.obtener()
    docs = f.documentos(descargar=False)
    terminos = [p.lower() for p in re.findall(r"\w+", nombre_o_patron) if len(p) >= 3]

    candidatos = []
    for d in docs:
        nom_l = d.get("nombre", "").lower()
        coincidencias = sum(1 for t in terminos if t in nom_l)
        if coincidencias > 0:
            candidatos.append((coincidencias, d))

    if not candidatos:
        return f"No se encontro ningun archivo en Drive que coincida con '{nombre_o_patron}'."

    candidatos.sort(key=lambda x: x[0], reverse=True)
    doc_elegido = candidatos[0][1]

    ruta = f.asegurar_descargado(doc_elegido) if hasattr(f, "asegurar_descargado") else doc_elegido.get("ruta")
    if not ruta or not os.path.exists(ruta):
        return f"El archivo '{doc_elegido['nombre']}' se localizo en Drive pero no se pudo descargar en este momento."

    paginas_extraidas = list(corpus.leer_documento(ruta))
    if not paginas_extraidas:
        return f"El archivo '{doc_elegido['nombre']}' no contiene texto extraible (puede ser una imagen o escaneo)."

    total_pags = len(paginas_extraidas)
    lineas = [
        f"DOCUMENTO ENCONTRADO EN DRIVE: {doc_elegido['nombre']} ({total_pags} paginas)",
        f"Carpeta / Categoria: {doc_elegido.get('categoria', 'General')}",
        f"Fuente: {doc_elegido['nombre']}, p. 1-{min(paginas, total_pags)} [{doc_elegido.get('categoria', 'General')}]",
        "",
        "CONTENIDO PRINCIPAL Y PRESENTACION DEL DOCUMENTO:",
        "",
    ]

    for p_num, txt in paginas_extraidas[:paginas]:
        limpio = txt.strip()
        if limpio:
            lineas.append(f"--- [Pagina {p_num}] ---")
            lineas.append(limpio)
            lineas.append("")

    return "\n".join(lineas)



def inventario_de_la_fuente(buscar: str = "", carpeta: str = "") -> str:
    """
    Lista TODO lo que hay en el Drive conectado, carpeta por carpeta.

    USALA SIEMPRE que pregunten que documentos tienes, que hay en el Drive,
    que archivos ves, que carpetas hay, si existe tal documento, o cuando
    alguien dude de que hayas mirado algo. Tambien antes de decir que un
    archivo no existe.

    EN QUE SE DIFERENCIA DE `estado_del_corpus`
    -------------------------------------------
    `estado_del_corpus` da el RESUMEN de lo indexado: cuantos documentos,
    cuantas fichas, repartidos por categoria. Esta herramienta da la LISTA
    REAL de la carpeta de Drive, archivo por archivo y con su ruta, este o
    no indexado todavia.

    La diferencia importa y es la razon de que exista. Un documento puede
    estar en el Drive y no estar todavia en el indice -- porque el estudio
    va por la mitad, porque es muy grande, porque su formato no se sabe
    leer. Contestando solo con lo indexado, el agente dice "no tengo ese
    documento" de un archivo que esta ahi, y quien pregunta se queda sin
    saber si falta subirlo o solo falta estudiarlo. Son dos problemas
    distintos con dos soluciones distintas.

    Cada archivo sale marcado:

        [disponible] esta en el Drive y su formato se sabe leer
        [sin lector] esta, pero su formato no se sabe abrir

    LO QUE ESTA MARCA NO DICE, Y ES DELIBERADO
    ------------------------------------------
    No dice si el documento ya entro al indice. Decirlo exigiria preguntar
    al indice archivo por archivo, y esta herramienta existe para contestar
    deprisa "que hay en el Drive". Antes la marca era [estudiado] frente a
    [pendiente], y era una promesa que el recuento no podia cumplir: se
    calculaba por la extension del archivo, no por lo que hubiera en el
    indice. Un documento se marcaba como estudiado por llamarse .pdf.

    "Disponible" si es cierto: ese archivo se puede abrir y analizar ahora
    mismo con `leer_documento_drive`, haya pasado o no por el estudio.

    Args:
        buscar: Si se da, solo salen los archivos y carpetas cuyo nombre o
            ruta lo contengan. Sirve para "tienes algo de normatividad?" o
            "esta la resolucion 1402?". No distingue mayusculas ni tildes.
        carpeta: Si se da, solo se lista esa carpeta y lo que cuelga de
            ella. Se escribe como aparece en la lista, por ejemplo
            "1_NORMATIVIDAD".

    Returns:
        El arbol de carpetas con sus archivos, el estado de cada uno y el
        recuento. Si el Drive no esta conectado, dice por que.
    """
    import unicodedata

    from core import fuentes, vectorial

    def plano(texto):
        """
        Deja un texto comparable: sin tildes y en minusculas.

        Args:
            texto: Lo que se va a comparar.

        Returns:
            El texto normalizado.
        """
        crudo = unicodedata.normalize("NFKD", str(texto or ""))
        return "".join(c for c in crudo
                       if not unicodedata.combining(c)).lower()

    try:
        fuente = fuentes.obtener()
    except Exception as exc:
        return f"No hay ninguna fuente configurada: {exc}"

    listo, motivo = fuente.disponible()
    if not listo:
        return (f"No puedo mirar la fuente ahora mismo: {motivo}"
                + chr(10) * 2
                + "Hasta que no este conectada no puedo decir que hay "
                  "dentro, y no voy a adivinarlo.")

    arbol = fuente.inventario()
    if arbol.get("error"):
        return (f"No pude recorrer la carpeta: {arbol['error']}"
                + chr(10) * 2
                + "Lo que sigue puede estar incompleto.")

    archivos = arbol.get("archivos") or []
    if not archivos:
        return "La carpeta conectada no tiene ningun archivo dentro."

    # Que esta ya en el indice. Se compara por nombre de archivo, que es
    # con lo que se indexa y con lo que se cita.
    try:
        indexados = set(vectorial.huellas_indexadas())
    except Exception:
        indexados = set()

    # Y que se aparto, con su motivo, para no llamarlo "pendiente" cuando
    # en realidad no va a entrar nunca.
    apartados = {}
    for descartado in (getattr(fuente, "descartados", None) or []):
        apartados[descartado.get("nombre", "")] = descartado.get("motivo", "")

    from core import corpus as _corpus

    filtro = plano(buscar)
    raiz = plano(carpeta).strip("/")
    elegidos = []
    for archivo in archivos:
        ruta = plano(archivo["ruta"])
        if raiz and raiz not in ruta:
            continue
        if filtro and filtro not in ruta:
            continue
        elegidos.append(archivo)

    if not elegidos:
        que = " y ".join(x for x in (f"'{buscar}'" if buscar else "",
                                     f"la carpeta '{carpeta}'"
                                     if carpeta else "") if x)
        return (f"En el Drive hay {len(archivos)} archivos en "
                f"{len(arbol.get('carpetas') or [])} carpetas, pero ninguno "
                f"coincide con {que}." + chr(10) * 2
                + "Pidelo sin filtro para ver la lista entera.")

    por_carpeta = {}
    for archivo in elegidos:
        por_carpeta.setdefault(archivo["carpeta"], []).append(archivo)

    cuenta = {"disponible": 0, "sin lector": 0}
    lineas = [f"TODO LO QUE HAY EN EL DRIVE CONECTADO",
              f"{len(archivos)} archivos en "
              f"{len(arbol.get('carpetas') or [])} carpetas."]
    if buscar or carpeta:
        lineas.append(f"Mostrando {len(elegidos)} que coinciden con el "
                      f"filtro.")
    lineas.append("")

    cortado = False
    for nombre_carpeta in sorted(por_carpeta):
        dentro = por_carpeta[nombre_carpeta]
        lineas.append(f"{nombre_carpeta or '/'}  ({len(dentro)})")
        for archivo in dentro:
            if archivo["nombre"] in apartados:
                marca = "sin lector"
            elif archivo["extension"] not in _corpus.EXTENSIONES_LEIBLES:
                marca = "sin lector"
            else:
                marca = "disponible"
            cuenta[marca] += 1
            if len(lineas) < MAXIMO_LINEAS_INVENTARIO:
                lineas.append(f"   [{marca:<10}] {archivo['nombre']}  "
                              f"({archivo['mb']} MB)")
            else:
                cortado = True
        lineas.append("")

    lineas.append(f"De los {len(elegidos)} mostrados: {cuenta.get('disponible', 0)} disponibles en Drive para lectura y analisis, {cuenta.get('sin lector', 0)} sin lector.")
    lineas.append("Todos los documentos marcados como [disponible] pueden ser leidos y analizados de inmediato con leer_documento_drive o consultar_corpus.")
    if cortado:
        lineas.append(f"La lista se corto en {MAXIMO_LINEAS_INVENTARIO} lineas. Usa el filtro `buscar` o `carpeta` para ver el resto.")
    return chr(10).join(lineas)


def estado_del_corpus() -> str:
    """
    Informa de que bibliografia tiene cargada el sistema.

    Usala para el RESUMEN de lo indexado: cuantos documentos, cuantas
    fichas y como se reparten por categoria.

    Si lo que piden es la LISTA de archivos -- "que documentos tienes",
    "que hay en el Drive", "esta tal informe" --, usa el inventario de la
    fuente en vez de esta. Esta solo sabe de lo que ya esta indexado, y
    contestar con ella hace negar archivos que estan en el Drive esperando
    a ser leidos.

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

    _en_la_fuente = vista["documentos"]
    _leidos = vista.get("documentos_indexados", 0)
    lineas = [
        "LO QUE TENGO ESTUDIADO",
        f"  Fuente      : {vista['fuente']}",
        f"  En la fuente: {_en_la_fuente} documentos",
        f"  Leidos      : {_leidos} de esos {_en_la_fuente} estan en el "
        f"indice",
        f"  Fichas      : {vista['fichas_indice']} en el indice, que es lo "
        f"que puedo consultar",
    ]

    # EL AVISO QUE EVITA NEGAR LO QUE SI EXISTE
    # -----------------------------------------
    # Con el indice a medias, "no lo encuentro" se parece mucho a "no
    # existe" y no es lo mismo. Se dice aqui, con numeros, para que la
    # respuesta lo pueda decir tambien.
    if _en_la_fuente and _leidos < _en_la_fuente:
        _faltan = _en_la_fuente - _leidos
        lineas.append("")
        lineas.append(
            f"  OJO: el indice esta INCOMPLETO. Faltan {_faltan} documentos "
            f"por leer de los {_en_la_fuente} que hay en la fuente.")
        lineas.append(
            "  Si algo no aparece en una busqueda, NO digas que no existe "
            "en el corpus: di que todavia no se ha leido y cuantos faltan.")
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

#: Marca que la interfaz cambia por el boton de descarga del paquete.
MARCA_PAQUETE_QGIS = "[[DESCARGAR_PAQUETE_QGIS]]"


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


def entregar_paquete_qgis() -> str:
    """
    Arma un proyecto de QGIS listo para abrir y lo deja descargable.

    Usala cuando pidan "un archivo para QGIS", "el mapa", "las capas", "el
    proyecto" o algo que puedan abrir directamente. Es lo que casi siempre
    quiere quien lo pide.

    DIFERENCIA CON `entregar_script_qgis`
    -------------------------------------
    El script hay que pegarlo en la consola de Python de QGIS y ejecutarlo:
    sirve para quien ya tiene QGIS abierto y su proyecto montado. Esta
    herramienta entrega otra cosa, un .zip con el proyecto, las capas y un
    LEEME, que se descomprime y se abre con doble clic.

    Si dudas, usa esta. Pide el script solo si lo piden por su nombre o si
    quieren meter las capas en un proyecto de QGIS que ya tienen abierto.

    QUE LLEVA DENTRO
    ----------------
    Un `proyecto.qgs` con rutas relativas, un `capas.gpkg` con todas las
    capas en EPSG:4326 y un LEEME. Va en GeoPackage y no en shapefile
    porque el shapefile corta los nombres de campo a 10 caracteres.

    No describas el contenido del .zip de memoria: lo que devuelve esta
    funcion ya dice que capas lleva y cuantas entidades tiene cada una.

    Returns:
        Las instrucciones de uso y el inventario de lo que se empaqueto, o
        la explicacion de por que no se pudo armar.
    """
    import glob
    import os

    from core import paquete_qgis

    raiz = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    # Las capas del area salen de `data/`; las de campo, de `capas_extra/`,
    # que es la carpeta donde el script de QGIS recoge lo que anade el
    # usuario. El paquete tiene que llevar las dos: entregar el area sin
    # los datos de campo que se acaban de validar seria entregar la mitad.
    fuentes = (sorted(glob.glob(os.path.join(raiz, "data", "*.geojson")))
               + sorted(glob.glob(os.path.join(raiz, "capas_extra",
                                               "*.geojson"))))
    destino = os.path.join(raiz, "data", "entregas", "Area_estudio_QGIS.zip")

    try:
        hecho = paquete_qgis.construir(destino, fuentes)
    except ValueError as exc:
        # Es el caso de "todavia no hay capas". No es un error del sistema y
        # no conviene presentarlo como tal: hay que decir que falta hacer.
        return (f"No puedo armar el paquete todavia: {exc}\n\n"
                f"Las capas se generan desde la pestana Area de Estudio, "
                f"fijando el limite del area. Hasta entonces no hay "
                f"geometria que dibujar.")
    except Exception as exc:
        return (f"No pude armar el paquete de QGIS: "
                f"{type(exc).__name__}: {exc}")

    lineas = [
        "He armado el proyecto de QGIS. Debajo tienes el boton para "
        "descargarlo.",
        "",
        "COMO SE ABRE",
        "  1. Descomprime el .zip entero en una carpeta.",
        "  2. Haz doble clic en `proyecto.qgs`.",
        "",
        "  No saques el `proyecto.qgs` de la carpeta: las rutas son "
        "relativas para que el paquete funcione en cualquier ordenador, y "
        "eso quiere decir que el proyecto y el `capas.gpkg` van juntos.",
        "",
        "QUE LLEVA",
    ]
    for capa in hecho["capas"]:
        lineas.append(f"  - {capa['nombre']}: {capa['entidades']} "
                      f"entidades ({capa['tipo']})")
        if capa["columnas"]:
            lineas.append(f"      campos: {', '.join(capa['columnas'])}")
    lineas += [
        "",
        f"  Todo en EPSG:4326, el mismo sistema del GPS y de los datos de "
        f"QField. El .zip pesa {hecho['bytes'] // 1024} kB.",
        "",
        MARCA_PAQUETE_QGIS,
    ]
    return "\n".join(lineas)


def datos_para_qgis() -> str:
    """
    Dice QUE GEOMETRIA tiene el sistema para dibujar un mapa.

    Usala siempre que pregunten si hay coordenadas, si hay veredas, si hay
    cuencas, con que se puede hacer un mapa, o que falta para hacerlo.
    Tambien antes de prometer cualquier cartografia.

    LEE LAS CAPAS, NO LOS DOCUMENTOS
    --------------------------------
    Esta es la diferencia que importa y la razon de que esta herramienta
    exista. Un documento puede MENCIONAR un shapefile, una geodatabase o
    un sistema de referencia; eso no significa que el sistema tenga esa
    geometria. Son dos cosas distintas:

        "el informe cita Drenaje_Sencillo_San_Carlos.shp"
        "el sistema tiene las lineas de ese drenaje y puede dibujarlas"

    Lo primero sale de leer texto. Lo segundo, de mirar las capas. Esta
    herramienta mira las capas.

    NO AFIRMES QUE HAY COORDENADAS PORQUE UN DOCUMENTO HABLE DE ELLAS.
    Si esta funcion no lista una capa, esa geometria NO esta, por mucho
    que el corpus la nombre. Decir lo contrario manda a la persona a
    buscar en QGIS algo que no va a encontrar.

    Returns:
        Inventario de las capas que existen, con su tipo y su recuento, y
        un veredicto explicito sobre que se puede dibujar y que no.
    """
    import glob
    import json
    import os

    raiz = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    rutas = (sorted(glob.glob(os.path.join(raiz, "data", "*.geojson")))
             + sorted(glob.glob(os.path.join(raiz, "capas_extra",
                                             "*.geojson"))))

    capas = []
    for ruta in rutas:
        try:
            with open(ruta, encoding="utf-8") as fh:
                datos = json.load(fh)
        except (OSError, ValueError):
            continue
        entidades = datos.get("features") or []
        if not entidades:
            continue
        tipos = sorted({e["geometry"]["type"] for e in entidades
                        if e.get("geometry")})
        campos = []
        for entidad in entidades:
            for clave in (entidad.get("properties") or {}):
                if clave not in campos:
                    campos.append(clave)
        capas.append({
            "nombre": os.path.splitext(os.path.basename(ruta))[0],
            "donde": os.path.basename(os.path.dirname(ruta)),
            "tipos": tipos,
            "entidades": len(entidades),
            "campos": campos,
        })

    if not capas:
        return (
            "NO hay ninguna capa con geometria en el sistema, asi que no se "
            "puede dibujar nada todavia.\n\n"
            "El area de estudio se fija en la pestana 'Area de Estudio', "
            "subiendo el archivo que la delimita (un .kmz, .kml o .geojson). "
            "De ahi salen las primeras capas.\n\n"
            "Aunque algun documento del corpus hable de shapefiles o de "
            "geodatabases, eso es texto: el sistema no tiene esa geometria."
        )

    lineas = ["GEOMETRIA QUE EL SISTEMA TIENE AHORA MISMO", ""]
    poligonos = lineas_geo = puntos = 0
    for capa in capas:
        lineas.append(f"  {capa['nombre']}")
        lineas.append(f"      {capa['entidades']} entidades "
                      f"({', '.join(capa['tipos'])}) en {capa['donde']}/")
        if capa["campos"]:
            lineas.append(f"      campos: {', '.join(capa['campos'][:10])}")
        for tipo in capa["tipos"]:
            if tipo.endswith("Polygon"):
                poligonos += capa["entidades"]
            elif tipo.endswith("LineString"):
                lineas_geo += capa["entidades"]
            elif tipo.endswith("Point"):
                puntos += capa["entidades"]

    lineas += [
        "",
        f"En total: {poligonos} poligono(s), {lineas_geo} linea(s) y "
        f"{puntos} punto(s). Todo en EPSG:4326, grados decimales.",
        "",
        "SE PUEDE DIBUJAR UN MAPA con lo que hay. Pidelo y se entrega el "
        "paquete de QGIS, que se abre con doble clic.",
    ]

    # Lo que NO hay se dice por su nombre. Es la pregunta que mas se repite
    # y la que peor se responde callando: quien pregunta por veredas
    # merece un "no las hay y de donde sacarlas", no una lista de otras
    # cosas que si estan.
    # SE BUSCA LA CAPA, NO SE CUENTAN POLIGONOS
    # -----------------------------------------
    # Antes esto decia "faltan las veredas" cuando habia un solo poligono en
    # todo el sistema. Funcionaba por casualidad: el unico poligono era el
    # limite municipal. En cuanto entraron las areas protegidas y las
    # unidades geologicas hubo 581 poligonos, la condicion dejo de cumplirse
    # y el aviso desaparecio -- sin que hubiera aparecido ni una vereda.
    #
    # Dejar de avisar de algo que sigue faltando es peor que no haber
    # avisado nunca: quien lea el inventario entendera que ya esta resuelto.
    def hay_poligono_de(*palabras):
        """
        Dice si existe una capa de poligonos sobre alguno de esos temas.

        Args:
            *palabras: Terminos a buscar en el nombre de la capa.

        Returns:
            True si alguna capa de poligonos los nombra.
        """
        for capa in capas:
            if not any(t.endswith("Polygon") for t in capa["tipos"]):
                continue
            nombre = capa["nombre"].lower()
            if any(palabra in nombre for palabra in palabras):
                return True
        return False

    faltan = []
    if not hay_poligono_de("vereda"):
        faltan.append(
            "  - Poligonos de VEREDA. Las veredas solo existen como nombres "
            "en tablas y en capas de puntos, no como superficies. La capa de "
            "veredas de Colombia la publican el DANE y el IGAC.")
    if not hay_poligono_de("cuenca", "pomca", "hidrograf"):
        faltan.append(
            "  - El poligono de la CUENCA. El limite municipal SI esta, pero "
            "no son lo mismo: la cuenca son unos 702 km2 y el municipio "
            "720,77. La cuenca sale del POMCA o de la zonificacion "
            "hidrografica del IDEAM.")
    if faltan:
        lineas += ["", "LO QUE NO HAY, Y HACE FALTA CONSEGUIR:"] + faltan
        lineas += [
            "",
            "Esas capas se anaden dejando el archivo en la carpeta "
            "'capas_extra' del proyecto: no hay que tocar codigo.",
        ]

    return "\n".join(lineas)


def _buscar_directo_en_documentos(pregunta: str, limite: int = 6, carpeta: str = None) -> list:
    """
    Busca de forma directa en todos los documentos descargados de Drive cuando el
    indice vectorial aun no contiene el documento o la consulta no tuvo coincidencia.

    Permite que el agente responda INMEDIATAMENTE sobre cualquier archivo de Drive
    (decretos, normas, guias, catalogos) sin depender exclusivamente de que la
    vectorizacion en segundo plano haya terminado. Permite priorizar por carpeta.
    """
    import os
    import re
    from core import fuentes, corpus

    try:
        f = fuentes.obtener()
        docs = f.documentos(descargar=False)
    except Exception:
        return []

    # Extraer terminos de busqueda significativos
    palabras = [
        p.lower() for p in re.findall(r"\w+", pregunta)
        if len(p) >= 3 and p.lower() not in (
            "que", "dice", "del", "los", "las", "por", "con", "para", "una",
            "uno", "sobre", "como", "cual", "cuales", "esta", "este", "tiene",
            "documento", "archivo"
        )
    ]
    if not palabras:
        return []

    # Manejar filtro de carpeta (puede ser cadena o lista)
    carpetas_target = set()
    if carpeta:
        if isinstance(carpeta, (list, tuple, set)):
            carpetas_target = {str(c).lower().strip() for c in carpeta}
        else:
            carpetas_target = {str(carpeta).lower().strip()}

    # 1. Puntuacion de documentos por coincidencia en nombre o categoria
    candidatos = []
    for d in docs:
        nom_l = d.get("nombre", "").lower()
        cat_l = d.get("categoria", "").lower()
        coincidencias = 0

        # Bonificacion si pertenece a la carpeta solicitada
        if carpetas_target:
            if any(target in cat_l for target in carpetas_target):
                coincidencias += 10

        for p in palabras:
            if p in nom_l:
                coincidencias += 4  # Mayor peso al nombre de archivo
            elif p in cat_l:
                coincidencias += 1
        if coincidencias > 0:
            candidatos.append((coincidencias, d))

    # Ordenar por mayor coincidencia
    candidatos.sort(key=lambda x: x[0], reverse=True)

    fichas = []
    # Solo se inspeccionan documentos descargados localmente
    for _, d in candidatos[:8]:
        ruta = d.get("ruta")
        if not ruta or not os.path.exists(ruta):
            continue
        try:
            for pag_num, texto in corpus.leer_documento(ruta):
                txt_l = texto.lower()
                if any(p in txt_l for p in palabras):
                    for trozo in corpus.trocear(texto):
                        trozo_l = trozo.lower()
                        coincidencias_trozo = sum(1 for p in palabras if p in trozo_l)
                        if coincidencias_trozo > 0:
                            fichas.append({
                                "documento": d["nombre"],
                                "categoria": d.get("categoria", "General"),
                                "pagina": pag_num,
                                "paginada": True,
                                "texto": trozo.strip(),
                                "_c": coincidencias_trozo,
                            })
                            if len(fichas) >= limite * 2:
                                break
                if len(fichas) >= limite * 2:
                    break
        except Exception:
            continue

    if not fichas:
        return []

    fichas.sort(key=lambda x: x.get("_c", 0), reverse=True)
    return fichas[:limite]


def consultar_corpus(pregunta: str, carpeta: str = None) -> str:
    """
    Busca en la bibliografia cargada de Google Drive y devuelve los fragmentos pertinentes.

    Permite consultar directamente el corpus de conocimiento sin re-analizar archivos ni
    descargar datos innecesarios. Usala para cualquier duda sobre mariposas, taxonomía,
    plantas hospederas, plantas nectaríferas, ciclo de vida, zoocría, normativa ambiental,
    metodología EIA Conesa o documentos de soporte.

    Args:
        pregunta: Consulta en lenguaje natural o terminos clave.
        carpeta: Opcional. Filtra la busqueda a una de las carpetas especializadas de Google Drive:
                 - "DOCUMENTOS DE AGENTE": Informes de base, capas QGIS y especificaciones del agente.
                 - "5. CATALAGOS MARIPOSAS": Listas de chequeo de mariposas de Colombia y catalogos taxonomicos.
                 - "13. MONITOREO DE MARIPOSAS": Registros de monitoreo de campo, transectos y abundancia.
                 - "3_PLANTAS HOSPEDERAS": Plantas hospederas larvales (herbáceas, arbustos, árboles).
                 - "6. CICLO DE VIDA": Huevo, larva, estadios (L1-L5), pupa y adulto.
                 - "8_ ZOOCRIA DE MARIPOSAS": Zoocría, reproducción ex situ, dietas y jaulas de vuelo.
                 - "9_ MARIPOSARIO": Manejo de mariposarios, enriquecimiento y visitantes.
                 - "3.1 FUNCION ECOLOGICA-BIOINDICADORES": Ecología, bioindicación y redes tróficas.
                 - "1_NORMATIVIDAD": CITES, Resolución 0126 de 2024, Libro Rojo Invertebrados, Ley 611 de 2000.
                 - "2. Metodología EIA": Metodología Conesa, matrices de impacto y evaluación.
                 - "7. ANATOMIA Y MORFOLOGIA": Morfología alar, nervaduras, piezas bucales y genitales.
                 - "10_ SANITARIOS": Sanidad biológica, patógenos (virus, bacterias, hongos) y cuarentena.
                 - "11. PLAN DE NEGOCIO": Bioeconomía y modelos de negocio sostenibles.
                 - "12_ EXPORTACION": Comercio internacional y requisitos ICA/CITES.
                 - "14. ARTESANIAS": Uso de alas y biocomercio de especímenes no vivos.
                 - "4. VIVIERO": Propagación de flora nativa y plantas hospederas.
                 - "TRAZABILIDAD DE LA CADENA": Trazabilidad zootécnica y cadena de valor.
                 Si no se especifica, el sistema detecta de forma inteligente la carpeta mas adecuada.

    Returns:
        Los fragmentos encontrados con su fuente y categoria, o una explicacion de por que
        no se encontro nada.
    """
    if not pregunta or not pregunta.strip():
        return "Indique que desea consultar en la bibliografia."

    vista = _panorama()
    if not vista["se_puede_buscar"]:
        return estado_del_corpus()

    from core import vectorial

    # Deteccion inteligente de carpetas relevantes si no se paso explicitamente
    carpeta_filtro = carpeta
    if not carpeta_filtro:
        detectadas = vectorial.detectar_carpetas_relevantes(pregunta)
        if detectadas:
            carpeta_filtro = detectadas

    # Busqueda hibrida (Qdrant + BM25) con filtrado selectivo por carpeta
    fichas = vectorial.buscar_hibrido(pregunta, limite=6, categoria=carpeta_filtro)
    if not fichas and carpeta_filtro:
        # Fallback sin filtro si el filtro acotado no arrojo resultados
        fichas = vectorial.buscar_hibrido(pregunta, limite=6)

    if not fichas:
        # Si aun no esta en Qdrant o es un archivo recien subido, busqueda directa
        fichas = _buscar_directo_en_documentos(pregunta, limite=6, carpeta=carpeta_filtro)

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
        # NO SE CUENTAN LOS DOCUMENTOS QUE NO SE HAN LEIDO
        # -------------------------------------------------
        # Esto decia "en lo estudiado (425 documentos...)" usando el numero
        # que ve la FUENTE, no el que se ha leido. Con el indice a medio
        # construir, eso convierte un "todavia no le ha tocado el turno" en
        # un "no existe en el corpus", que es una afirmacion mucho mas
        # fuerte y, encima, falsa.
        #
        # Paso con la guia de Hesperiidae del Neotropico: 229 paginas, en el
        # Drive, con el genero Pyrrhopyge escrito dentro. La respuesta fue
        # "no se encontraron menciones explicitas al genero Pyrrhopyge
        # dentro de los 425 documentos indexados del proyecto".
        _en_fuente = vista["documentos"]
        _leidos = vista.get("documentos_indexados", 0)
        if _en_fuente and _leidos < _en_fuente:
            incompleto = (
                f"\n\nIMPORTANTE: el indice esta INCOMPLETO. De los "
                f"{_en_fuente} documentos de la fuente solo hay {_leidos} "
                f"leidos, asi que esto NO permite afirmar que el dato no "
                f"exista en el corpus: faltan {_en_fuente - _leidos} "
                f"documentos por leer. Dilo asi en la respuesta, sin "
                f"presentar la ausencia como una comprobacion hecha.")
        else:
            incompleto = (
                "\n\nNo se trata de que el dato no exista en el mundo: es "
                "que los documentos estudiados no lo tratan. Puede "
                "consultarse otra formulacion o anadir la fuente que falte.")
        return (
            f"No se encontro nada sobre esa consulta en lo estudiado "
            f"({_leidos} documentos leidos de {vista['fuente']}, "
            f"{vista['fichas_indice']} fichas)." + incompleto + aviso
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
    #
    # SE DECIDE SIN TOCAR EL DISCO, Y ESO NO ES UN DETALLE
    # -----------------------------------------------------
    # Antes esta linea era `corpus.resumen(reconstruir=True)`, que RECONSTRUYE
    # el corpus local: abre y trocea todos los documentos de la carpeta. Y la
    # carpeta del corpus y la de descargas de Drive son LA MISMA
    # (`data/copiloto_drive`), asi que mientras hay un estudio en marcha esa
    # llamada se pone a leer los 3,2 GB recien bajados.
    #
    # Medido: mas de 400 segundos sin terminar, dentro del turno del chat.
    # Preguntar "y esas como se llaman?" sobre una foto disparaba esto, y la
    # pantalla se quedaba en "Pensando... 53 s" subiendo.
    #
    # Y no hacia falta leer nada: lo que decide la respuesta es si la fuente
    # conserva los documentos en el equipo o no, y eso se sabe de antemano.
    # Con Drive -- la unica fuente que queda -- la respuesta es siempre que
    # no. Leer gigabytes para confirmarlo es pagar por una certeza que ya se
    # tenia.
    if not vista.get("local"):
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
def consultar_inaturalist(termino_o_especie: str = "") -> str:
    """
    Consulta en vivo la API de iNaturalist para buscar observaciones reales, fotos
    y taxonomia de mariposas (Lepidoptera) y fauna/flora en San Carlos, Antioquia.

    Usala siempre que el usuario pregunte por mariposas o biodiversidad en San Carlos,
    validacion de nombres cientificos/comunes, fotos de referencia o cuando se analice
    una fotografia pegada en el chat para cotejarla con observaciones reales de campo.

    Args:
        termino_o_especie: Nombre cientifico, genero o nombre comun a buscar (por ejemplo
            'Heliconius', 'Morpho', 'Danaus', 'mariposa buho'). Si se deja vacio, devuelve
            las observaciones de mariposas registradas en San Carlos.

    Returns:
        Registros de campo en San Carlos con su validacion cientifica, fotos y taxonomia.
    """
    return inaturalist.consultar_resumen(termino=termino_o_especie, solo_san_carlos=True)


HERRAMIENTAS = [
    consultar_linea_base,
    calcular_impacto_conesa,
    procesar_lote_excel_csv,
    consultar_biodiversidad_local,
    describir_area_de_estudio,
    revisar_datos_de_campo,
    estado_del_corpus,
    inventario_de_la_fuente,
    leer_documento_drive,
    datos_para_qgis,
    entregar_paquete_qgis,
    entregar_script_qgis,
    consultar_corpus,
    buscar_especie_exacta,
    reporte_taxonomico_especie,
    ficha_de_campo,
    informe_cientifico_tema,
    consultar_inaturalist,
]
