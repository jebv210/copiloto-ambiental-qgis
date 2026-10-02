"""
Configuracion central del proyecto.
===============================================================================

FILOSOFIA
---------
Ninguna ruta absoluta ni ningun dato de negocio esta escrito ("quemado") en el
codigo fuente. Todo valor configurable se resuelve SIEMPRE en este orden:

    1. Variable de entorno / archivo `.env`   -> lo que el usuario decida.
    2. Descubrimiento automatico en disco      -> lo que el sistema encuentre.
    3. None + mensaje explicito                -> el modulo avisa, NO adivina.

El paso 3 es el importante: cuando algo no se encuentra, este modulo devuelve
None en lugar de inventar una ruta. Quien lo llame debe mostrarle al usuario un
mensaje claro. Preferimos un "no lo encuentro, dime donde esta" antes que un
error de archivo inexistente veinte lineas mas abajo.

QUE SE CAMBIO Y POR QUE
-----------------------
La version anterior armaba la ruta de la Geodatabase concatenando literales:

    os.path.join(PRACTICAS_DIR, "1. Productos entregados", "DATOS",
                 "GDB_POMCA_INTERSECCION", ...)

Eso obligaba a que el proyecto viviera exactamente un nivel por debajo de una
carpeta con ese nombre exacto. En cualquier otro computador fallaba. Ahora la
GDB se busca automaticamente en disco (ver `descubrir_gdb`) o se indica por
variable de entorno.

CONTRATO CON QGIS  (IMPORTANTE, NO CAMBIAR A LA LIGERA)
-------------------------------------------------------
El script `cargar_capas_qgis.py` (que se descarga desde la interfaz web y se
ejecuta DENTRO de QGIS Desktop) busca dos archivos por nombre fijo en la carpeta
del proyecto:

    - municipios_filtrados.geojson
    - biodiversidad_filtrada.csv

Por eso `ARCHIVO_MUNICIPIOS_SALIDA` y `ARCHIVO_BIODIVERSIDAD_SALIDA` son
constantes y NO configurables: son el contrato entre el backend y QGIS.
Si algun dia se renombran, hay que renombrarlos tambien alla.
"""

import os
import glob

# ==============================================================================
# 1. DIRECTORIOS BASE  (derivados de la ubicacion de este archivo, nunca fijos)
# ==============================================================================

#: Carpeta donde vive este archivo (`proyecto/config/`).
CONFIG_DIR = os.path.dirname(os.path.abspath(__file__))

#: Raiz del codigo fuente (`proyecto/`). Todo lo del proyecto cuelga de aqui.
BASE_DIR = os.path.dirname(CONFIG_DIR)

#: Carpeta contenedora (`PRACTICAS II/`). Es donde suelen vivir los insumos
#: pesados que NO se versionan: el CSV de GBIF y la Geodatabase.
PRACTICAS_DIR = os.path.dirname(BASE_DIR)


def ruta_proyecto(*partes) -> str:
    """
    Construye una ruta absoluta relativa a la raiz del proyecto.

    Args:
        *partes: Segmentos de ruta, como los recibiria `os.path.join`.

    Returns:
        Ruta absoluta dentro de `proyecto/`.

    Ejemplo:
        >>> ruta_proyecto("biodiversidad_filtrada.csv")  # doctest: +SKIP
        'C:\\\\...\\\\proyecto\\\\biodiversidad_filtrada.csv'
    """
    return os.path.join(BASE_DIR, *partes)


# ==============================================================================
# 2. CARGA DEL ARCHIVO .env
# ==============================================================================

def _cargar_env() -> None:
    """
    Carga las variables del archivo `.env` al entorno del proceso.

    Intenta primero con `python-dotenv`. Si esa libreria no esta instalada
    (por ejemplo, cuando el codigo corre dentro del interprete de QGIS, que no
    siempre la trae), cae a un parser minimo propio para no romper el arranque.

    Nunca sobrescribe una variable que ya exista en el entorno: lo que el
    usuario exporte manualmente tiene prioridad sobre el archivo.
    """
    ruta_env = ruta_proyecto(".env")
    if not os.path.exists(ruta_env):
        return

    try:
        from dotenv import load_dotenv
        load_dotenv(ruta_env)
        return
    except ImportError:
        pass

    # Parser de respaldo: KEY=VALOR, ignora comentarios y lineas vacias.
    try:
        with open(ruta_env, "r", encoding="utf-8") as f:
            for linea in f:
                linea = linea.strip()
                if not linea or linea.startswith("#") or "=" not in linea:
                    continue
                clave, _, valor = linea.partition("=")
                clave = clave.strip()
                valor = valor.strip().strip('"').strip("'")
                if clave and clave not in os.environ:
                    os.environ[clave] = valor
    except Exception:
        # Un .env ilegible no debe tumbar la aplicacion; simplemente no aporta.
        pass


_cargar_env()


def env(nombre: str, defecto=None):
    """
    Lee una variable de entorno tratando la cadena vacia como ausencia.

    `os.getenv("X")` devuelve "" si la variable existe pero esta vacia, y ese ""
    se cuela como si fuera un valor valido. Aqui se normaliza a `defecto`.

    Args:
        nombre: Nombre de la variable.
        defecto: Valor a devolver si no existe o esta vacia.

    Returns:
        El valor de la variable o `defecto`.
    """
    valor = os.environ.get(nombre)
    if valor is None or str(valor).strip() == "":
        return defecto
    return str(valor).strip()


# ==============================================================================
# 3. CONTRATO DE ARCHIVOS DE SALIDA  (leidos por cargar_capas_qgis.py)
# ==============================================================================

#: Capa municipal recortada al area de estudio. La lee QGIS. NO renombrar solo.
ARCHIVO_MUNICIPIOS_SALIDA = "municipios_filtrados.geojson"

#: Registros de biodiversidad filtrados. Los lee QGIS. NO renombrar solo.
ARCHIVO_BIODIVERSIDAD_SALIDA = "biodiversidad_filtrada.csv"

#: Capa de departamentos de Colombia que viene incluida en el repositorio.
#: Sirve de fondo en QGIS y como fuente de nombres oficiales de departamento.
ARCHIVO_DEPARTAMENTOS = "colombia_departamentos.geojson"

#: Copia local del GeoJSON municipal nacional. Se genera la primera vez que se
#: descarga, para que las corridas siguientes funcionen sin internet.
ARCHIVO_MUNICIPIOS_NACIONAL_CACHE = "cache_municipios_nacional.geojson"

#: Catalogo de valores descubiertos en el dataset (departamentos, taxones...).
#: Evita re-escanear un archivo de decenas de GB en cada arranque.
ARCHIVO_CACHE_CATALOGO = "cache_catalogo.json"


def ruta_municipios_salida() -> str:
    """
    Ubicacion de la capa municipal filtrada que consume QGIS.

    Returns:
        Ruta absoluta de `municipios_filtrados.geojson` dentro del proyecto.
        El archivo puede no existir todavia: solo aparece cuando el usuario
        genera el area de estudio.
    """
    return ruta_proyecto(ARCHIVO_MUNICIPIOS_SALIDA)


def ruta_biodiversidad_salida() -> str:
    """
    Ubicacion del CSV de biodiversidad filtrado que consume QGIS.

    Returns:
        Ruta absoluta de `biodiversidad_filtrada.csv` dentro del proyecto.
        Igual que la capa municipal, solo existe tras generar el area.
    """
    return ruta_proyecto(ARCHIVO_BIODIVERSIDAD_SALIDA)


def ruta_departamentos() -> str:
    """
    Ubicacion de la capa nacional de departamentos incluida en el repositorio.

    Returns:
        Ruta absoluta de `colombia_departamentos.geojson`. Este si viene con el
        proyecto, asi que normalmente existe siempre.
    """
    return ruta_proyecto(ARCHIVO_DEPARTAMENTOS)


# ==============================================================================
# 4. FUENTE CARTOGRAFICA MUNICIPAL
# ==============================================================================

#: URL por defecto de los limites municipales oficiales (DANE 2018, EPSG:4326).
#: Es un valor por defecto, NO una constante: se puede reemplazar por completo
#: con la variable de entorno `URL_MUNICIPIOS_NACIONAL`, o apuntar a un archivo
#: local con `RUTA_MUNICIPIOS_NACIONAL` si no hay internet.
URL_MUNICIPIOS_NACIONAL_DEFECTO = (
    "https://raw.githubusercontent.com/caticoa3/colombia_mapa/master/"
    "co_2018_MGN_MPIO_POLITICO.geojson"
)


def origen_municipios_nacional() -> str:
    """
    Devuelve de donde leer la capa municipal nacional completa.

    Orden de prioridad:
      1. `RUTA_MUNICIPIOS_NACIONAL` (archivo local indicado por el usuario).
      2. Cache local descargada en una corrida anterior.
      3. `URL_MUNICIPIOS_NACIONAL` (o la URL por defecto) -> requiere internet.

    Returns:
        Ruta de archivo o URL, lista para pasarle a `geopandas.read_file`.
    """
    ruta_manual = env("RUTA_MUNICIPIOS_NACIONAL")
    if ruta_manual and os.path.exists(ruta_manual):
        return ruta_manual

    cache = ruta_proyecto(ARCHIVO_MUNICIPIOS_NACIONAL_CACHE)
    if os.path.exists(cache):
        return cache

    return env("URL_MUNICIPIOS_NACIONAL", URL_MUNICIPIOS_NACIONAL_DEFECTO)


# ==============================================================================
# 5. DATASET DE BIODIVERSIDAD  (el archivo pesado que carga el usuario)
# ==============================================================================

#: Archivos tabulares que forman parte del proyecto y NUNCA son el dataset.
#: Sin esta lista, al no haber ningun dataset de verdad el sistema proponia
#: `requirements.txt` como "archivo de biodiversidad", que es absurdo.
ARCHIVOS_PROPIOS = {
    "requirements.txt", "matriz_impactos.csv",
    "plantilla_valoracion_conesa.csv",
}

#: Tamano minimo para que un archivo pueda ser el dataset crudo. Una descarga
#: de ocurrencias de GBIF pesa como minimo decenas de MB; nada por debajo de
#: esto lo es.
TAMANO_MINIMO_DATASET = 1_000_000  # 1 MB


def _parece_dataset_biodiversidad(ruta: str) -> bool:
    """
    Comprueba si un archivo tabular tiene pinta de ser un dataset de ocurrencias.

    En lugar de fiarse del tamano, se lee la CABECERA (una sola linea, asi que
    es instantaneo aunque el archivo pese decenas de GB) y se exige que
    contenga alguna columna reconocible de division politica o de taxonomia.

    Args:
        ruta: Archivo a evaluar.

    Returns:
        True si la cabecera trae columnas propias de un archivo de biodiversidad.
    """
    # Importacion diferida: `core.catalogo` importa este modulo, asi que
    # hacerlo arriba crearia una dependencia circular.
    try:
        from core import catalogo
        columnas, _ = catalogo.leer_encabezado(ruta)
    except Exception:
        return False

    if len(columnas) < 3:
        return False

    tiene_geografia = catalogo.sugerir_columna(
        columnas, catalogo.COLUMNAS_GEOGRAFICAS_PROBABLES
    )
    tiene_taxonomia = catalogo.sugerir_columna(
        columnas, catalogo.COLUMNAS_TAXONOMICAS_PROBABLES
    )
    return bool(tiene_geografia or tiene_taxonomia)


def ruta_dataset_biodiversidad():
    """
    Ubica el dataset crudo de biodiversidad (descarga de GBIF u equivalente).

    Antes el nombre del archivo estaba quemado como
    `"0005926-260806074905277.csv"`, que es el identificador de UNA descarga
    puntual de GBIF: en cuanto alguien pide otra descarga, ese nombre deja de
    existir.

    Orden de resolucion:
      1. `DATASET_BIODIVERSIDAD`, si el usuario la definio.
      2. Busqueda en la carpeta del proyecto y en la que la contiene, filtrando
         por tamano minimo, descartando los archivos del propio proyecto y
         comprobando que la CABECERA tenga columnas de biodiversidad. De los
         que pasan el filtro se devuelve el mas pesado.
      3. None, para que la interfaz pida la ruta en vez de proponer cualquier
         cosa.

    El paso de comprobar la cabecera se anadio porque, al borrarse el dataset
    real del disco, la version anterior ofrecia `requirements.txt` como si
    fuera el archivo de biodiversidad.

    Returns:
        Ruta absoluta al dataset, o None si no se pudo determinar.
    """
    indicada = env("DATASET_BIODIVERSIDAD")
    if indicada and os.path.exists(indicada):
        return indicada

    candidatos = []
    for carpeta in (PRACTICAS_DIR, BASE_DIR):
        for extension in ("*.csv", "*.tsv", "*.txt"):
            for ruta in glob.glob(os.path.join(carpeta, extension)):
                nombre = os.path.basename(ruta)

                # Ni los productos que genera el sistema ni sus propios
                # archivos de configuracion son candidatos.
                if nombre == ARCHIVO_BIODIVERSIDAD_SALIDA:
                    continue
                if nombre in ARCHIVOS_PROPIOS:
                    continue

                try:
                    tamano = os.path.getsize(ruta)
                except OSError:
                    continue

                if tamano < TAMANO_MINIMO_DATASET:
                    continue

                candidatos.append((tamano, ruta))

    # Del mas pesado al mas ligero: el primero cuya cabecera encaje, gana.
    for _tamano, ruta in sorted(candidatos, reverse=True):
        if _parece_dataset_biodiversidad(ruta):
            return ruta

    return None


# ==============================================================================
# 6. GEODATABASE (GDB) DE QGIS
# ==============================================================================

def _es_gdb_valida(ruta: str) -> bool:
    """
    Comprueba si una carpeta `.gdb` es realmente una File Geodatabase de ESRI.

    Una FileGDB valida contiene archivos internos con extension `.gdbtable`.
    Sin esta comprobacion, una carpeta contenedora que solo agrupa otras `.gdb`
    (caso real de este proyecto) se tomaria por una base de datos y fallaria
    al intentar listar capas.

    Args:
        ruta: Ruta de la carpeta a evaluar.

    Returns:
        True si contiene al menos un archivo `.gdbtable`.
    """
    if not os.path.isdir(ruta):
        return False
    try:
        return any(n.lower().endswith(".gdbtable") for n in os.listdir(ruta))
    except OSError:
        return False


def descubrir_gdb(profundidad_max: int = 6):
    """
    Busca automaticamente Geodatabases en disco, sin rutas escritas a mano.

    Recorre `PRACTICAS_DIR` (la carpeta contenedora del proyecto) limitando la
    profundidad, porque ahi es donde se depositan los insumos pesados. El
    recorrido solo lista nombres de carpetas: no abre ni lee archivos, asi que
    es rapido incluso conviviendo con un CSV de 99 GB.

    Args:
        profundidad_max: Cuantos niveles bajar desde `PRACTICAS_DIR`.

    Returns:
        Lista de rutas absolutas a Geodatabases validas, ordenadas por nombre.
    """
    encontradas = []
    raiz = PRACTICAS_DIR
    nivel_raiz = raiz.rstrip(os.sep).count(os.sep)

    for carpeta_actual, subcarpetas, _archivos in os.walk(raiz):
        if carpeta_actual.count(os.sep) - nivel_raiz >= profundidad_max:
            subcarpetas[:] = []
            continue

        # No entrar a carpetas de entorno o cache: nunca contienen una GDB.
        subcarpetas[:] = [
            s for s in subcarpetas
            if s not in (".venv", "__pycache__", ".git", "node_modules")
        ]

        for sub in list(subcarpetas):
            if sub.lower().endswith(".gdb"):
                completa = os.path.join(carpeta_actual, sub)
                if _es_gdb_valida(completa):
                    encontradas.append(completa)

    return sorted(set(encontradas))


#: Cache en memoria del descubrimiento, para no repetir el recorrido de disco.
_gdb_cache = {"resuelta": False, "candidatas": []}


def gdb_forzada():
    """
    Devuelve la Geodatabase indicada explicitamente por el usuario, si la hay.

    Returns:
        Ruta de `GDB_PATH` si esta definida y es valida, o None.
    """
    indicada = env("GDB_PATH")
    return indicada if (indicada and _es_gdb_valida(indicada)) else None


def gdb_candidatas(forzar_redescubrimiento: bool = False):
    """
    Lista todas las Geodatabases utilizables, cacheando el recorrido de disco.

    Cuando hay varias (es lo habitual: una GDB suele contener otras anidadas),
    NO se elige aqui. La eleccion la hace `database.gdb_connector.GDBConnector`,
    que si sabe que capas necesita y puede puntuar cual sirve mejor.

    Args:
        forzar_redescubrimiento: Ignora la cache y vuelve a recorrer el disco.

    Returns:
        Lista de rutas absolutas. Si `GDB_PATH` esta definida, la lista contiene
        solo esa.
    """
    forzada = gdb_forzada()
    if forzada:
        return [forzada]

    if not _gdb_cache["resuelta"] or forzar_redescubrimiento:
        _gdb_cache.update(resuelta=True, candidatas=descubrir_gdb())

    return list(_gdb_cache["candidatas"])


def ruta_gdb(forzar_redescubrimiento: bool = False):
    """
    Devuelve una Geodatabase utilizable, o None si no hay ninguna.

    Orden de prioridad:
      1. Variable de entorno `GDB_PATH`.
      2. Primera GDB valida encontrada en disco.
      3. None -> quien llame debe avisarle al usuario, no inventar una ruta.

    Args:
        forzar_redescubrimiento: Ignora la cache en memoria y vuelve a buscar.

    Returns:
        Ruta absoluta a la GDB, o None.
    """
    candidatas = gdb_candidatas(forzar_redescubrimiento)
    return candidatas[0] if candidatas else None


# ==============================================================================
# 7. DICCIONARIOS DE DATOS EN EXCEL  (documentacion de las capas de la GDB)
# ==============================================================================

def ruta_diccionarios_excel():
    """
    Ubica la carpeta con los diccionarios de datos en Excel, si existe.

    Antes esta ruta estaba quemada como
    `PRACTICAS II/1. Productos entregados/DATOS/DICCIONARIO DE DATOS`.
    Ahora se busca por nombre de carpeta, sin depender de la jerarquia exacta.

    Returns:
        Ruta absoluta de la carpeta, o None si no se encuentra.
    """
    indicada = env("EXCEL_DIR")
    if indicada and os.path.isdir(indicada):
        return indicada

    for carpeta_actual, subcarpetas, _ in os.walk(PRACTICAS_DIR):
        subcarpetas[:] = [
            s for s in subcarpetas
            if s not in (".venv", "__pycache__", ".git")
        ]
        for sub in subcarpetas:
            if "DICCIONARIO" in sub.upper():
                return os.path.join(carpeta_actual, sub)
    return None


# ==============================================================================
# 7-bis. AMBITO GEOGRAFICO DEL ESTUDIO
# ==============================================================================

#: Pais al que se limita el estudio, en codigo ISO 3166-1 alfa-2.
#:
#: POR QUE HACE FALTA:
#: el dataset de GBIF es mundial, y los nombres de departamento se repiten
#: entre paises. "Amazonas" es un departamento de Colombia, pero tambien un
#: estado de Brasil, un departamento de Peru y un estado de Venezuela. Filtrando
#: solo por nombre, un area de estudio de Amazonas salio con 7.980 registros de
#: los que apenas 961 eran colombianos: los demas eran de Manaos y del Peru.
#: Pasa lo mismo con Bolivar (Venezuela, Ecuador), Sucre (Venezuela) o
#: Cordoba (Argentina, Espana).
#:
#: Configurable con la variable `PAIS_ESTUDIO` del archivo .env.
PAIS_ESTUDIO_DEFECTO = "CO"


def pais_estudio() -> str:
    """
    Codigo del pais al que se limita el filtrado de biodiversidad.

    Returns:
        Codigo ISO 3166-1 alfa-2 en mayusculas: el valor de `PAIS_ESTUDIO`, o
        "CO" (Colombia) si no se definio.
    """
    return str(env("PAIS_ESTUDIO", PAIS_ESTUDIO_DEFECTO)).strip().upper()


# ==============================================================================
# 8. AGENTE DE IA
# ==============================================================================

#: Modelo por defecto. Configurable con la variable `MODELO_LLM`.
MODELO_LLM_DEFECTO = "gemini-2.5-flash"


def modelo_llm() -> str:
    """
    Nombre del modelo de lenguaje a usar.

    Returns:
        El valor de la variable `MODELO_LLM`, o `MODELO_LLM_DEFECTO` si no se
        definio. Cambiar de modelo no requiere tocar codigo: basta editar esa
        variable en el archivo `.env`.
    """
    return env("MODELO_LLM", MODELO_LLM_DEFECTO)


def api_key_gemini():
    """
    Devuelve la API Key de Gemini disponible en el entorno, o None.

    La interfaz web permite ademas escribir la clave a mano en la barra lateral;
    esa clave escrita tiene prioridad sobre esta y se pasa explicitamente al
    agente. Aqui solo se resuelve el valor "de arranque".

    Returns:
        La clave, o None si no hay ninguna configurada.
    """
    return env("GEMINI_API_KEY")


# ==============================================================================
# 9. SERVIDOR API
# ==============================================================================

def api_host() -> str:
    """
    Direccion en la que escucha el servidor FastAPI.

    Returns:
        El valor de `API_HOST`, o `"127.0.0.1"` (solo accesible desde este
        computador). Poner `"0.0.0.0"` lo expone a la red local.
    """
    return env("API_HOST", "127.0.0.1")


def api_port() -> int:
    """
    Puerto del servidor FastAPI.

    Returns:
        El valor de `API_PORT` convertido a entero, o 8000. Si la variable
        contiene algo que no es un numero se devuelve 8000 en lugar de
        propagar el error: un `.env` mal escrito no debe impedir el arranque.
    """
    try:
        return int(env("API_PORT", "8000"))
    except ValueError:
        return 8000


def api_url() -> str:
    """
    URL base del servidor FastAPI.

    La interfaz Streamlit la usa para el chequeo de salud. Antes estaba quemada
    como `"http://127.0.0.1:8000"` dentro de `app.py`.

    Returns:
        URL completa, sin barra final.
    """
    explicita = env("API_URL")
    if explicita:
        return explicita.rstrip("/")
    return f"http://{api_host()}:{api_port()}"


def carpeta_temporal() -> str:
    """
    Carpeta para archivos temporales de subida. Se crea si no existe.

    Returns:
        Ruta absoluta de `proyecto/temp_uploads` (o la que indique `TEMP_DIR`).
    """
    destino = env("TEMP_DIR", ruta_proyecto("temp_uploads"))
    os.makedirs(destino, exist_ok=True)
    return destino


# ==============================================================================
# 10. DIAGNOSTICO
# ==============================================================================

def geodatabase_en_uso():
    """
    Devuelve la Geodatabase que el sistema va a usar realmente.

    POR QUE NO BASTA CON `ruta_gdb()`:
    cuando hay varias Geodatabases anidadas, `ruta_gdb()` devuelve la primera,
    pero quien decide de verdad es `GDBConnector`, que puntua cada candidata
    segun cuantas capas utiles contiene. Si el diagnostico mostrara la primera
    y el sistema usara otra, el usuario estaria depurando sobre datos falsos.

    La importacion es diferida a proposito: `database` importa `config` al
    cargarse, asi que hacerlo aqui arriba crearia una dependencia circular.

    Returns:
        Ruta de la Geodatabase elegida, o `ruta_gdb()` como aproximacion si el
        conector no se pudo cargar (por ejemplo, sin pyogrio instalado).
    """
    try:
        from database.gdb_connector import GDBConnector
        return GDBConnector().gdb_path
    except Exception:
        return ruta_gdb()


def diagnostico() -> dict:
    """
    Resume el estado de toda la configuracion resuelta en este momento.

    Pensado para depurar "por que no encuentra mis datos" sin leer codigo.
    Se puede ejecutar directamente:  python -m config.settings

    Returns:
        Diccionario {concepto: valor o None} con todo lo resuelto.
    """
    dataset = ruta_dataset_biodiversidad()
    candidatas = gdb_candidatas()
    return {
        "BASE_DIR": BASE_DIR,
        "PRACTICAS_DIR": PRACTICAS_DIR,
        "Geodatabase en uso": geodatabase_en_uso(),
        "Geodatabases encontradas": (
            f"{len(candidatas)}: " + " | ".join(os.path.basename(c) for c in candidatas)
            if candidatas else None
        ),
        "Diccionarios Excel": ruta_diccionarios_excel(),
        "Dataset biodiversidad (crudo)": dataset,
        "Origen municipios nacional": origen_municipios_nacional(),
        "Capa departamentos": ruta_departamentos()
        if os.path.exists(ruta_departamentos()) else None,
        "Salida municipios (QGIS)": ruta_municipios_salida()
        if os.path.exists(ruta_municipios_salida()) else None,
        "Salida biodiversidad (QGIS)": ruta_biodiversidad_salida()
        if os.path.exists(ruta_biodiversidad_salida()) else None,
        "API URL": api_url(),
        "Modelo LLM": modelo_llm(),
        "API Key Gemini": "definida" if api_key_gemini() else None,
    }


if __name__ == "__main__":
    print("=" * 70)
    print("DIAGNOSTICO DE CONFIGURACION")
    print("=" * 70)
    for concepto, valor in diagnostico().items():
        estado = "OK " if valor else "-- "
        print(f"[{estado}] {concepto}: {valor if valor else 'NO DISPONIBLE'}")
