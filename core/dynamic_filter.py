"""
Motor de filtrado dinamico del area de estudio.
===============================================================================

QUE HACE
--------
Toma el dataset crudo de biodiversidad que cargo el usuario (una descarga de
GBIF, tipicamente de decenas de GB) y produce los dos archivos que consume
QGIS Desktop:

    municipios_filtrados.geojson  -> limites del departamento elegido
    biodiversidad_filtrada.csv    -> registros del departamento y taxon elegidos

QUE SE DESQUEMO RESPECTO A LA VERSION ANTERIOR
----------------------------------------------
    ANTES                                     AHORA
    ---------------------------------------   ----------------------------------
    RUTA_CSV_GIGANTE = ".../0005926-...csv"   la ruta la indica el usuario
    sep="\\t" fijo                             delimitador autodetectado
    mapeo = {"Aves": ("class","Aves"), ...}   columna y valor los elige el
                                              usuario a partir del propio archivo
    contador_leido / 161369380.0              total estimado del archivo real
    URL del GeoJSON escrita dos veces         una sola, en config.settings
    filtro == "ANTIOQUIA"                     comparacion flexible y bidireccional

CONTRATO CON QGIS
-----------------
Los nombres de los dos archivos de salida son fijos a proposito: el script
`cargar_capas_qgis.py` los busca por nombre. Estan definidos en
`config.settings` y NO deben cambiarse aqui.
"""

import os
import time

from config import settings
from core.textos import emparejar, resolver_toponimo
from core import catalogo


# ==============================================================================
# 0. PUBLICACION SEGURA DE LOS ARCHIVOS DE SALIDA
# ==============================================================================

#: Sufijos de los archivos de trabajo que este modulo crea y luego retira.
SUFIJOS_TEMPORALES = (".parcial", ".nuevo")


def limpiar_temporales(*destinos) -> list:
    """
    Borra los archivos de trabajo que hayan quedado de corridas anteriores.

    Una corrida interrumpida (cierras el navegador, se corta la luz) puede
    dejar un `.parcial` a medias. Se limpian ANTES de generar, para que la
    carpeta del proyecto no acumule restos y para no confundirlos nunca con
    un resultado bueno.

    Args:
        *destinos: Rutas de los archivos definitivos cuyos temporales limpiar.

    Returns:
        Lista de los archivos que se borraron.
    """
    borrados = []
    for destino in destinos:
        for sufijo in SUFIJOS_TEMPORALES:
            resto = destino + sufijo
            if os.path.exists(resto):
                try:
                    os.remove(resto)
                    borrados.append(resto)
                except OSError:
                    pass
    return borrados


def publicar_archivo(temporal: str, destino: str) -> str:
    """
    Sustituye un archivo de salida por su version nueva. Siempre.

    EL PROBLEMA:
    en Windows, un archivo abierto por otro programa no se puede REEMPLAZAR.
    Y el caso es de lo mas normal aqui: cargas las capas en QGIS Desktop,
    vuelves a la interfaz web y generas el area de estudio de otro
    departamento. QGIS mantiene abierto `municipios_filtrados.geojson` y
    `os.replace` falla con `[WinError 32]`.

    LA SOLUCION, en dos intentos:

      1. `os.replace`. Es ATOMICO: o el destino queda con el contenido nuevo
         completo, o se queda como estaba. Es lo preferible, y funciona
         siempre que nadie tenga el archivo abierto.

      2. Si el destino esta bloqueado, se sobrescribe EN EL SITIO: se abre el
         propio destino y se le vuelca encima el contenido nuevo. Windows si
         permite esto aunque otro programa lo tenga abierto, porque no se esta
         reemplazando el archivo, solo cambiando sus bytes.

    Con el segundo intento, el archivo se actualiza aunque QGIS este abierto y
    NO queda ningun resto suelto en la carpeta. QGIS seguira mostrando en
    pantalla los datos viejos hasta que se recargue la capa (clic derecho sobre
    ella > Volver a cargar), pero el archivo en disco ya es el nuevo.

    El segundo intento no es atomico: si fallara a mitad, el destino quedaria
    incompleto. Por eso el temporal NO se borra hasta que la copia termina
    bien, y si algo falla se indica donde quedo el resultado intacto.

    Args:
        temporal: Archivo recien generado, con el contenido bueno.
        destino: Archivo definitivo al que debe sustituir.

    Returns:
        La ruta del archivo publicado.

    Raises:
        RuntimeError: Solo si fallan los dos intentos. El mensaje indica donde
            quedo el resultado, que nunca se pierde.
    """
    # --- Intento 1: reemplazo atomico ----------------------------------------
    try:
        os.replace(temporal, destino)
        return destino
    except PermissionError:
        pass

    # --- Intento 2: sobrescritura en el sitio --------------------------------
    try:
        import shutil
        with open(temporal, "rb") as origen, open(destino, "wb") as salida:
            shutil.copyfileobj(origen, salida)
    except OSError as exc:
        raise RuntimeError(
            f"No se pudo actualizar '{os.path.basename(destino)}': {exc}\n\n"
            f"Tus datos NO se han perdido: el resultado esta completo en "
            f"'{os.path.basename(temporal)}'. Cierra el programa que tenga "
            f"abierto el archivo y vuelve a generar el area de estudio."
        )

    # La copia termino bien: ya se puede retirar el temporal.
    try:
        os.remove(temporal)
    except OSError:
        pass

    return destino


# ==============================================================================
# 1. CAPA CARTOGRAFICA MUNICIPAL
# ==============================================================================

def obtener_municipios_nacional(progreso=None):
    """
    Consigue la capa municipal nacional completa y la deja cacheada en disco.

    La primera vez descarga el GeoJSON oficial (la fuente se configura en
    `config.settings.origen_municipios_nacional`). A partir de ahi guarda una
    copia local, de modo que las corridas siguientes funcionan sin internet.

    Args:
        progreso: Callback opcional `f(mensaje, fraccion)`.

    Returns:
        GeoDataFrame con todos los municipios del pais.

    Raises:
        RuntimeError: Si no se pudo leer ni descargar la capa.
    """
    import geopandas as gpd

    origen = settings.origen_municipios_nacional()
    es_remoto = str(origen).lower().startswith(("http://", "https://"))

    if progreso:
        progreso(
            "Descargando limites municipales oficiales..." if es_remoto
            else "Leyendo limites municipales desde la copia local...",
            0.05,
        )

    try:
        gdf = gpd.read_file(origen)
    except Exception as exc:
        raise RuntimeError(
            f"No se pudo leer la capa municipal desde '{origen}'. "
            f"Si no hay internet, indique un archivo local en la variable "
            f"RUTA_MUNICIPIOS_NACIONAL del archivo .env. Detalle: {exc}"
        ) from exc

    # Guardar la cache solo si vino de la red y aun no existe.
    if es_remoto:
        destino_cache = settings.ruta_proyecto(
            settings.ARCHIVO_MUNICIPIOS_NACIONAL_CACHE
        )
        if not os.path.exists(destino_cache):
            try:
                gdf.to_file(destino_cache, driver="GeoJSON")
            except Exception:
                # La cache es una optimizacion, no un requisito.
                pass

    return gdf


def listar_departamentos_cartografia(progreso=None):
    """
    Devuelve los nombres de departamento tal como los escribe la cartografia.

    Estos son los nombres OFICIALES contra los que hay que filtrar la capa
    municipal. Se leen del archivo, nunca de una lista escrita en el codigo.

    Args:
        progreso: Callback opcional `f(mensaje, fraccion)`.

    Returns:
        Tupla `(nombres, campo)`:
            nombres: lista ordenada de nombres de departamento.
            campo  : nombre del atributo que los contiene (ej. "DPTO_CNMBR").
        Devuelve `([], None)` si la capa no se pudo leer.
    """
    # Se prefiere la capa de departamentos incluida en el repositorio: es local,
    # instantanea y trae exactamente los mismos nombres oficiales.
    ruta_deptos = settings.ruta_departamentos()
    if os.path.exists(ruta_deptos):
        campo = catalogo.detectar_campo_departamento(ruta_deptos)
        if campo:
            nombres = catalogo.valores_unicos_capa(ruta_deptos, campo)
            if nombres:
                return nombres, campo

    # Respaldo: leer los nombres desde la capa municipal nacional.
    try:
        gdf = obtener_municipios_nacional(progreso)
    except RuntimeError:
        return [], None

    campo = catalogo.sugerir_columna(
        list(gdf.columns),
        ["DPTO_CNMBR", "NOMBRE_DPT", "departamento", "dpto"],
    )
    if not campo:
        return [], None

    nombres = sorted({
        str(v).strip() for v in gdf[campo].dropna() if str(v).strip()
    })
    return nombres, campo


def generar_capa_municipal(nombre_departamento: str, progreso=None) -> dict:
    """
    Recorta la capa municipal nacional al departamento indicado y la guarda.

    El archivo de salida es `municipios_filtrados.geojson`, que es lo que lee
    el script de QGIS. Se sobrescribe en cada corrida.

    La comparacion del nombre NO es una igualdad literal: usa `emparejar`, que
    ignora tildes, mayusculas y puntuacion y admite contenciones parciales. Eso
    permite que funcione con "Bogota D.C." frente a "BOGOTA, D.C." o con
    "San Andres" frente a "ARCHIPIELAGO DE SAN ANDRES, PROVIDENCIA Y SANTA
    CATALINA", casos que en la version anterior lanzaban un error.

    Args:
        nombre_departamento: Departamento a recortar, en cualquier grafia.
        progreso: Callback opcional `f(mensaje, fraccion)`.

    Returns:
        Diccionario con:
            archivo             : ruta del GeoJSON generado.
            departamento_oficial: nombre exacto usado de la cartografia.
            municipios          : cuantos municipios quedaron.
            campo_departamento  : atributo empleado para filtrar.

    Raises:
        ValueError: Si el departamento no existe en la cartografia. El mensaje
            incluye los nombres disponibles para que el usuario corrija.
        RuntimeError: Si la capa nacional no se pudo obtener.
    """
    gdf = obtener_municipios_nacional(progreso)

    campo = catalogo.sugerir_columna(
        list(gdf.columns),
        ["DPTO_CNMBR", "NOMBRE_DPT", "departamento", "dpto"],
    )
    if not campo:
        raise RuntimeError(
            "La capa municipal no tiene un atributo reconocible de "
            f"departamento. Atributos disponibles: {list(gdf.columns)}"
        )

    disponibles = sorted({
        str(v).strip() for v in gdf[campo].dropna() if str(v).strip()
    })
    oficial = emparejar(nombre_departamento, disponibles)

    if oficial is None:
        raise ValueError(
            f"El departamento '{nombre_departamento}' no existe en la "
            f"cartografia. Nombres disponibles: {', '.join(disponibles)}"
        )

    if progreso:
        progreso(f"Recortando municipios de {oficial}...", 0.15)

    recorte = gdf[gdf[campo].astype(str).str.strip() == oficial].copy()
    recorte = recorte.to_crs("EPSG:4326")

    destino = settings.ruta_municipios_salida()

    # Se escribe en un temporal y solo al final se reemplaza el definitivo.
    # Asi, si el destino esta bloqueado (QGIS abierto con esa capa), el error
    # es explicativo y el trabajo hecho no se pierde. Ver `publicar_archivo`.
    temporal = destino + ".parcial"
    if os.path.exists(temporal):
        os.remove(temporal)
    recorte.to_file(temporal, driver="GeoJSON")
    publicar_archivo(temporal, destino)

    return {
        "archivo": destino,
        "departamento_oficial": oficial,
        "municipios": len(recorte),
        "campo_departamento": campo,
    }


# ==============================================================================
# 2. FILTRADO DEL DATASET DE BIODIVERSIDAD
# ==============================================================================

def filtrar_dataset(ruta_dataset: str,
                    columna_geografica: str = None, valores_geograficos=None,
                    columna_taxonomica: str = None, valores_taxonomicos=None,
                    delimitador: str = None, tamano_bloque: int = 200000,
                    filas_max: int = 0, progreso=None,
                    rango_progreso=(0.25, 1.0),
                    filtrar_pais: bool = True, columna_pais: str = None,
                    valores_pais=None,
                    resolver_departamento: bool = True) -> dict:
    """
    Recorre el dataset crudo por bloques y escribe solo las filas que interesan.

    La lectura es incremental (`chunksize`), asi que la memoria usada no depende
    del tamano del archivo: un CSV de 99 GB se procesa igual que uno de 10 MB.

    El filtro NO usa comparaciones de texto normalizadas fila por fila (seria
    lentisimo sobre cientos de millones de filas). En su lugar recibe la lista
    exacta de variantes de escritura que se descubrieron en el catalogo y usa
    `isin`, que esta vectorizado en C.

    Args:
        ruta_dataset: Archivo crudo a filtrar.
        columna_geografica: Columna con el departamento. None = sin filtro.
        valores_geograficos: Variantes exactas a conservar (lista de str).
        columna_taxonomica: Columna con el taxon. None = sin filtro.
        valores_taxonomicos: Variantes exactas a conservar (lista de str).
        delimitador: Separador. None = autodeteccion.
        tamano_bloque: Filas por bloque de lectura.
        filas_max: Tope de filas a leer (0 = archivo completo).
        progreso: Callback opcional `f(mensaje, fraccion)`.
        rango_progreso: Tramo (inicio, fin) de la barra que ocupa este paso.
        filtrar_pais: Si True (por defecto), descarta los registros de otros
            paises. IMPRESCINDIBLE con datos de GBIF: "Amazonas" es a la vez
            departamento de Colombia, estado de Brasil, departamento de Peru y
            estado de Venezuela, y sin este filtro se mezclan los cuatro.
        columna_pais: Columna con el codigo ISO del pais. None = se detecta
            sola en la cabecera (`countryCode` en GBIF).
        valores_pais: Codigos a conservar. None = el de
            `config.settings.pais_estudio()` ("CO" por defecto).
        resolver_departamento: Si True (por defecto), el departamento se
            compara por su nombre OFICIAL: cada valor del archivo se traduce
            con `resolver_toponimo` a un departamento de la cartografia de
            Colombia, comparando palabras completas.
            Asi "CHOCO" encuentra "Chocó" y "BOGOTA" encuentra "Bogotá, D.C.",
            sin que "Cauca" arrastre "Valle del Cauca". Si False, se exige
            coincidencia literal con `valores_geograficos`.

    Returns:
        Diccionario con:
            archivo         : ruta del CSV generado.
            filas_leidas    : filas del archivo crudo revisadas.
            filas_guardadas : filas que pasaron el filtro.
            filas_otro_pais : filas que cumplian departamento y taxon pero eran
                              de otro pais, y por eso se descartaron.
            columna_pais    : columna usada para el filtro de pais, o None si
                              el archivo no tiene ninguna reconocible.
            pais            : codigos de pais conservados.
            departamento_oficial: nombre de la cartografia al que se resolvio
                              el departamento pedido, o None si no se resolvio
                              (en ese caso se uso la comparacion literal).
            segundos        : duracion del proceso.
            completo        : True si se recorrio el archivo entero.

    Raises:
        FileNotFoundError: Si el dataset no existe.
        ValueError: Si alguna columna de filtro no existe en el archivo.
    """
    import pandas as pd

    if not os.path.exists(ruta_dataset):
        raise FileNotFoundError(
            f"No se encontro el archivo de biodiversidad en: {ruta_dataset}"
        )

    columnas_archivo, delimitador_detectado = catalogo.leer_encabezado(
        ruta_dataset, delimitador
    )
    delimitador = delimitador or delimitador_detectado

    # --- Pais del estudio ----------------------------------------------------
    # Si quien llama no indica la columna de pais, se detecta sola en la
    # cabecera (`countryCode` en GBIF) y se usa el pais configurado. Asi el
    # filtro protege a TODOS los que llaman a este motor sin tener que cambiar
    # sus llamadas. Ver `config.settings.PAIS_ESTUDIO_DEFECTO` para el porque.
    if filtrar_pais and columna_pais is None:
        columna_pais = catalogo.sugerir_columna(
            columnas_archivo, catalogo.COLUMNAS_PAIS_PROBABLES
        )
    if not filtrar_pais:
        columna_pais = None
    if columna_pais and not valores_pais:
        valores_pais = [settings.pais_estudio()]

    # Validacion temprana y explicita: mejor fallar aqui con un mensaje claro
    # que a los veinte minutos con un KeyError en mitad del bucle.
    for columna in (columna_geografica, columna_taxonomica, columna_pais):
        if columna and columna not in columnas_archivo:
            raise ValueError(
                f"La columna '{columna}' no existe en el archivo. "
                f"Columnas disponibles: {columnas_archivo[:20]}..."
            )

    valores_geograficos = [str(v) for v in (valores_geograficos or [])]
    valores_taxonomicos = [str(v) for v in (valores_taxonomicos or [])]
    # Los codigos ISO se comparan en mayusculas: "co" y "CO" son el mismo pais.
    valores_pais = [str(v).strip().upper() for v in (valores_pais or [])]

    # --- Departamento: comparacion por nombre oficial ------------------------
    # La comparacion literal fallaba con las tildes: la interfaz pide "CHOCO" y
    # GBIF escribe "Chocó", asi que 9 de los 33 departamentos devolvian cero
    # registros. En lugar de mantener a mano una lista de grafias por
    # departamento, se traducen a nombre oficial TANTO lo pedido COMO cada valor
    # del archivo, y se conservan las filas cuyo nombre oficial coincide.
    # La cartografia es la de Colombia, asi que solo se aplica si el estudio es
    # de Colombia (o si no se filtra por pais).
    departamento_objetivo = None
    oficiales = []
    estudio_en_colombia = not valores_pais or valores_pais == ["CO"]
    if (resolver_departamento and estudio_en_colombia
            and columna_geografica and valores_geograficos):
        oficiales, _campo = listar_departamentos_cartografia()
        for pedido in valores_geograficos:
            departamento_objetivo = resolver_toponimo(pedido, oficiales)
            if departamento_objetivo:
                break
    # Cache entre bloques: valor escrito en el archivo -> nombre oficial.
    resueltos = {}

    destino = settings.ruta_biodiversidad_salida()
    # Se escribe primero a un archivo temporal y solo al final se reemplaza el
    # definitivo. Asi, si el proceso se interrumpe, QGIS sigue teniendo la capa
    # anterior completa en lugar de un archivo a medias.
    temporal = destino + ".parcial"
    if os.path.exists(temporal):
        os.remove(temporal)

    # Las columnas de filtro se fuerzan a texto para que un codigo numerico no
    # se lea como entero y deje de coincidir con la variante descubierta.
    tipos = {}
    if columna_geografica:
        tipos[columna_geografica] = str
    if columna_taxonomica:
        tipos[columna_taxonomica] = str
    if columna_pais:
        tipos[columna_pais] = str

    filas_estimadas = catalogo.estimar_filas(ruta_dataset) or 1
    objetivo = filas_max if filas_max else filas_estimadas
    inicio_barra, fin_barra = rango_progreso

    filas_leidas = 0
    filas_guardadas = 0
    filas_otro_pais = 0
    inicio = time.time()
    completo = True

    lector = pd.read_csv(
        ruta_dataset,
        sep=delimitador,
        chunksize=tamano_bloque,
        dtype=tipos or None,
        low_memory=False,
        on_bad_lines="skip",
        encoding="utf-8",
        encoding_errors="replace",
    )

    for bloque in lector:
        seleccion = bloque

        if columna_geografica and valores_geograficos:
            valores_col = seleccion[columna_geografica].astype(str).str.strip()
            if departamento_objetivo:
                # Cada valor DISTINTO se resuelve una sola vez: son unos pocos
                # miles frente a cientos de millones de filas.
                for valor in valores_col.unique():
                    if valor not in resueltos:
                        vacio = valor.lower() in ("", "nan", "none")
                        resueltos[valor] = (None if vacio
                                            else resolver_toponimo(valor, oficiales))
                aceptados = [v for v, oficial in resueltos.items()
                             if oficial == departamento_objetivo]
                seleccion = seleccion[valores_col.isin(aceptados)]
            else:
                seleccion = seleccion[valores_col.isin(valores_geograficos)]

        if len(seleccion) and columna_taxonomica and valores_taxonomicos:
            seleccion = seleccion[
                seleccion[columna_taxonomica].astype(str).str.strip()
                .isin(valores_taxonomicos)
            ]

        # El pais va al final a proposito: asi se sabe exactamente cuantos
        # registros cumplian departamento y taxon pero eran de otro pais.
        if len(seleccion) and columna_pais and valores_pais:
            antes = len(seleccion)
            seleccion = seleccion[
                seleccion[columna_pais].astype(str).str.strip().str.upper()
                .isin(valores_pais)
            ]
            filas_otro_pais += antes - len(seleccion)

        if len(seleccion):
            seleccion.to_csv(
                temporal, mode="a", index=False,
                header=(filas_guardadas == 0), encoding="utf-8",
            )
            filas_guardadas += len(seleccion)

        filas_leidas += len(bloque)

        if progreso:
            avance = min(filas_leidas / max(objetivo, 1), 1.0)
            fraccion = inicio_barra + avance * (fin_barra - inicio_barra)
            progreso(
                f"Analizando datos: {filas_leidas:,} filas revisadas | "
                f"{filas_guardadas:,} coincidencias".replace(",", "."),
                min(fraccion, fin_barra),
            )

        if filas_max and filas_leidas >= filas_max:
            completo = filas_leidas >= filas_estimadas
            break

    # Cero coincidencias: se genera igualmente un archivo con solo la cabecera,
    # para que QGIS y el agente no fallen al abrirlo.
    if not os.path.exists(temporal):
        pd.DataFrame(columns=columnas_archivo).to_csv(
            temporal, index=False, encoding="utf-8"
        )

    # Publicacion atomica, con mensaje claro si el destino esta bloqueado.
    publicar_archivo(temporal, destino)

    return {
        "archivo": destino,
        "filas_leidas": filas_leidas,
        "filas_guardadas": filas_guardadas,
        "filas_otro_pais": filas_otro_pais,
        "columna_pais": columna_pais,
        "pais": valores_pais,
        "departamento_oficial": departamento_objetivo,
        "segundos": round(time.time() - inicio, 1),
        "completo": completo,
    }


# ==============================================================================
# 3. ORQUESTADOR
# ==============================================================================

def ejecutar_filtrado_dinamico(ruta_dataset: str,
                               departamento: str = None,
                               columna_geografica: str = None,
                               valores_geograficos=None,
                               columna_taxonomica: str = None,
                               valores_taxonomicos=None,
                               delimitador: str = None,
                               generar_cartografia: bool = True,
                               filas_max: int = 0,
                               progreso=None) -> dict:
    """
    Ejecuta el proceso completo: capa municipal + filtrado de biodiversidad.

    Es la funcion que llama la interfaz web cuando el usuario pulsa
    "Aplicar y Generar Area de Estudio".

    Reparto de la barra de progreso:
        0.00 - 0.25  ->  descarga y recorte de la capa municipal
        0.25 - 1.00  ->  recorrido del dataset de biodiversidad

    Args:
        ruta_dataset: Archivo crudo de biodiversidad indicado por el usuario.
        departamento: Departamento para recortar la cartografia. None = no se
            genera capa municipal (util si solo interesa el filtro de especies).
        columna_geografica: Columna del dataset con el departamento.
        valores_geograficos: Variantes exactas de ese departamento en el dataset.
        columna_taxonomica: Columna del dataset con el taxon.
        valores_taxonomicos: Variantes exactas de ese taxon en el dataset.
        delimitador: Separador del dataset. None = autodeteccion.
        generar_cartografia: Si False, omite el paso de la capa municipal.
        filas_max: Tope de filas a leer (0 = archivo completo).
        progreso: Callback opcional `f(mensaje, fraccion)`.

    Returns:
        Diccionario con las claves `cartografia` (o None) y `biodiversidad`,
        cada una con el detalle devuelto por su funcion correspondiente.

    Raises:
        FileNotFoundError / ValueError / RuntimeError: propagados desde los
            pasos internos, siempre con un mensaje dirigido al usuario final.
    """
    resultado = {"cartografia": None, "biodiversidad": None}

    # Se barren los restos de corridas anteriores antes de empezar, para que la
    # carpeta del proyecto no acumule archivos sueltos entre departamento y
    # departamento.
    limpiar_temporales(settings.ruta_municipios_salida(),
                       settings.ruta_biodiversidad_salida())

    if generar_cartografia and departamento:
        resultado["cartografia"] = generar_capa_municipal(departamento, progreso)

    resultado["biodiversidad"] = filtrar_dataset(
        ruta_dataset=ruta_dataset,
        columna_geografica=columna_geografica,
        valores_geograficos=valores_geograficos,
        columna_taxonomica=columna_taxonomica,
        valores_taxonomicos=valores_taxonomicos,
        delimitador=delimitador,
        filas_max=filas_max,
        progreso=progreso,
        rango_progreso=(0.25 if resultado["cartografia"] else 0.0, 1.0),
    )

    return resultado


def resumen_area_generada() -> dict:
    """
    Describe el area de estudio que hay generada en este momento en disco.

    Sirve para que la interfaz y el agente de IA hablen de datos reales en vez
    de repetir cifras escritas en el codigo (la version anterior afirmaba
    "136.997 registros" cuando el archivo tenia dos mil).

    Returns:
        Diccionario con:
            municipios_archivo / municipios_existe / municipios_total
            departamentos      : departamentos presentes en la capa municipal
            biodiversidad_archivo / biodiversidad_existe / registros
            columnas           : columnas del CSV filtrado
        Los conteos son None cuando el archivo correspondiente no existe.
    """
    info = {
        "municipios_archivo": settings.ruta_municipios_salida(),
        "municipios_existe": os.path.exists(settings.ruta_municipios_salida()),
        "municipios_total": None,
        "departamentos": [],
        "biodiversidad_archivo": settings.ruta_biodiversidad_salida(),
        "biodiversidad_existe": os.path.exists(settings.ruta_biodiversidad_salida()),
        "registros": None,
        "columnas": [],
    }

    if info["municipios_existe"]:
        # Se leen los atributos del GeoJSON como JSON plano: no hace falta
        # geopandas (ni que PROJ/GDAL esten configurados) para contar entidades
        # y saber de que departamento son. Solo se recurre a geopandas si el
        # archivo no se pudo interpretar como GeoJSON.
        propiedades = catalogo.propiedades_geojson(info["municipios_archivo"])
        if propiedades:
            info["municipios_total"] = len(propiedades)
            campo = catalogo.sugerir_columna(
                list(propiedades[0].keys()),
                ["DPTO_CNMBR", "NOMBRE_DPT", "departamento"],
            )
            if campo:
                info["departamentos"] = sorted({
                    str(p[campo]).strip() for p in propiedades
                    if p.get(campo) is not None and str(p[campo]).strip()
                })
        else:
            try:
                import geopandas as gpd
                gdf = gpd.read_file(info["municipios_archivo"])
                info["municipios_total"] = len(gdf)
                campo = catalogo.sugerir_columna(
                    list(gdf.columns),
                    ["DPTO_CNMBR", "NOMBRE_DPT", "departamento"],
                )
                if campo:
                    info["departamentos"] = sorted({
                        str(v).strip() for v in gdf[campo].dropna()
                    })
            except Exception:
                pass

    if info["biodiversidad_existe"]:
        try:
            import pandas as pd
            columnas, delim = catalogo.leer_encabezado(info["biodiversidad_archivo"])
            info["columnas"] = columnas
            # Conteo exacto y barato: el archivo filtrado es pequeno.
            total = 0
            for bloque in pd.read_csv(info["biodiversidad_archivo"], sep=delim,
                                      chunksize=100000, usecols=[columnas[0]],
                                      dtype=str, on_bad_lines="skip",
                                      encoding="utf-8", encoding_errors="replace"):
                total += len(bloque)
            info["registros"] = total
        except Exception:
            pass

    return info


if __name__ == "__main__":
    # Prueba manual: muestra que departamentos ofrece la cartografia disponible
    # y que area de estudio hay generada en este momento.
    print("Departamentos disponibles en la cartografia:")
    nombres, campo = listar_departamentos_cartografia()
    print(f"  Campo detectado: {campo}")
    print(f"  Total: {len(nombres)}")
    for n in nombres[:5]:
        print(f"   - {n}")
    print("\nArea de estudio actualmente generada:")
    for clave, valor in resumen_area_generada().items():
        print(f"  {clave}: {valor}")
