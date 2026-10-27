"""
Interfaz web del Copiloto Ambiental (Streamlit).
===============================================================================

Se lanza con `iniciar_interfaz_web.bat`, o manualmente con::

    python -m streamlit run app.py

ORGANIZACION DE LA PANTALLA
---------------------------
    Barra lateral
        - Estado del servidor API.
        - Conexion con la IA: la persona escribe su API Key y el sistema la
          valida contra el servicio. Verde = conectado, rojo = no sirve.
        - Integracion con QGIS: descarga del script PyQGIS.

    Pestana 1  Chat con el agente.
    Pestana 2  Calculo masivo de matrices de Conesa desde Excel/CSV.
    Pestana 3  Diagnostico de linea base por coordenadas.
    Pestana 4  Configuracion del area de estudio (carga del dataset y filtros).

FLUJO DE TRABAJO ESPERADO
-------------------------
    1. La persona pega su API Key en la barra lateral y la valida.
    2. En "Area de Estudio": indica su archivo, pulsa un boton, elige
       departamento y grupo de especies, y genera. Tres pasos.
    3. Ya puede consultar coordenadas, chatear con el agente y cargar el
       resultado en QGIS.

PRINCIPIO DE INTERFAZ
---------------------
Lo tecnico no se le pregunta a quien usa la aplicacion. Que columna del archivo
mirar, cuantas filas muestrear o con que nombre de la cartografia emparejar el
departamento son cosas que el sistema resuelve solo; viven en "Opciones
avanzadas", plegadas, para cuando la deteccion automatica falle con un archivo
que no siga el estandar de GBIF.

Una version intermedia de esta pantalla obligaba a elegir a mano la columna del
departamento y la del taxon. Ademas de resultar incomprensible, tenia un
defecto de fondo: al fijar UNA sola columna taxonomica, quien elegia `class` se
quedaba sin poder filtrar mariposas (Lepidoptera es un ORDEN, no una clase).
Ahora se exploran varios rangos a la vez y el menu los mezcla.

QUE SE DESQUEMO EN ESTE ARCHIVO
-------------------------------
    ANTES                                        AHORA
    ------------------------------------------   -----------------------------
    API_URL = "http://127.0.0.1:8000"            settings.api_url()
    departamentos = [33 nombres escritos a mano] descubiertos del archivo
    grupos = [7 grupos escritos a mano]          descubiertos del archivo
    ruta por defecto ".../0005926-...csv"        settings.ruta_dataset_...()
    lat=5.5560, lon=-74.0040 (Maripi, Boyaca)    centroide del area cargada
    st.image("https://cdn-icons-png.flaticon...") cabecera local, sin internet
    temporales escritos en el directorio actual  carpeta temp_uploads

SOBRE EL SCRIPT DE QGIS
-----------------------
El bloque "Integracion con QGIS" de la barra lateral estuvo marcado como
intocable, y se retiro el 22-10-2026 por peticion expresa de quien lo habia
pedido asi.

El script NO desaparecio: ahora lo entrega el propio agente cuando se le pide
un mapa, y la descarga sale dentro de la conversacion. Asi, ademas, el agente
avisa de si hay datos que dibujar antes de que nadie lo ejecute.

El archivo `cargar_capas_qgis.py` sigue siendo zona intocable: no se modifica
sin avisar. Lo que cambio es quien lo ofrece, no su contenido.
"""

import os
import subprocess

import threading
import time

import streamlit as st
import pandas as pd
import requests

from config import settings
from core import catalogo
from core.textos import emparejar


# ==============================================================================
# UTILIDADES DE LA INTERFAZ
# ==============================================================================

def abrir_selector_archivos():
    """
    Abre el cuadro de dialogo nativo de Windows para elegir un archivo.

    POR QUE NO SE USA `st.file_uploader` AQUI:
    el dataset crudo de biodiversidad puede pesar decenas de gigabytes. Subirlo
    por HTTP al servidor de Streamlit seria inviable. Con este dialogo, la
    persona solo entrega la RUTA del archivo y el backend lo lee directamente
    del disco.

    Returns:
        La ruta absoluta elegida, o None si se cancelo o hubo un error.
    """
    guion_powershell = (
        "[System.Reflection.Assembly]::LoadWithPartialName('System.Windows.Forms') | Out-Null; "
        "$dlg = New-Object System.Windows.Forms.OpenFileDialog; "
        "$dlg.Filter = 'Archivos de datos (*.csv;*.tsv;*.txt)|*.csv;*.tsv;*.txt|Todos los archivos (*.*)|*.*'; "
        "$dlg.Title = 'Selecciona el archivo de biodiversidad'; "
        "$dlg.ShowHelp = $true; "
        "$res = $dlg.ShowDialog(); "
        "if ($res -eq 'OK') { Write-Output $dlg.FileName }"
    )

    try:
        # Se construye la ruta a PowerShell desde la variable de entorno del
        # sistema en lugar de asumir "C:\\Windows". Si aun asi no existe, se
        # confia en el PATH.
        system32 = os.path.join(
            os.environ.get("SystemRoot", os.environ.get("WINDIR", "")),
            "System32",
        )
        ejecutable = os.path.join(
            system32, "WindowsPowerShell", "v1.0", "powershell.exe"
        )
        if not os.path.exists(ejecutable):
            ejecutable = "powershell"

        salida = subprocess.check_output(
            [ejecutable, "-NoProfile", "-Command", guion_powershell],
            creationflags=0x08000000,  # CREATE_NO_WINDOW: sin consola negra
        )
        return salida.decode("utf-8", errors="ignore").strip() or None

    except Exception as exc:
        st.error(f"No se pudo abrir el selector de archivos: {exc}")
        return None


@st.cache_data(show_spinner=False)
def descubrir_departamentos():
    """
    Lee del mapa oficial que divisiones administrativas existen.

    Nada de listas escritas a mano: el desplegable sale de la misma capa que
    luego se usa para recortar el area de estudio, asi que los nombres
    coinciden letra por letra. Si el proyecto cambia de pais, cambia el archivo
    y el desplegable cambia con el.

    El resultado se guarda en cache: es un archivo de 1 MB y no tiene sentido
    releerlo en cada repintado de Streamlit.

    Returns:
        Lista ordenada de nombres de departamento, o lista vacia si no se pudo
        leer la capa.
    """
    try:
        origen = settings.origen_municipios_nacional()
        if not origen or not os.path.exists(origen):
            return []
        campo = catalogo.detectar_campo_departamento(origen)
        if not campo:
            return []
        return sorted(v for v in catalogo.valores_unicos_capa(origen, campo) if v)
    except Exception:
        # Un fallo aqui no debe tumbar la interfaz: quien llama lo detecta por
        # la lista vacia y avisa al usuario con un mensaje que se entienda.
        return []


def centro_del_area_de_estudio():
    """
    Calcula el centro geografico del area de estudio generada.

    Sirve para precargar el formulario de coordenadas con un punto que este
    DENTRO de la zona del usuario. La version anterior traia fijas las
    coordenadas de Maripi (Boyaca), heredadas de un proyecto anterior, asi que
    el diagnostico por defecto caia siempre fuera del area cargada.

    POR QUE NO USA GEOPANDAS:
    esta funcion se ejecuta en CADA recarga de la pagina. Abrir el GeoJSON con
    geopandas dejaba el archivo tomado por el proceso de Streamlit, y en
    Windows eso impedia sobrescribirlo despues al regenerar el area. Leyendo
    las coordenadas como JSON plano el archivo se cierra de inmediato y, de
    paso, no hace falta que PROJ y GDAL esten configurados.

    El punto devuelto es el centro del rectangulo que envuelve al area. Es una
    aproximacion, y basta: solo sirve para rellenar el formulario con algo
    razonable, no para ningun calculo.

    Returns:
        Tupla `(latitud, longitud)`. Si no hay area generada, devuelve
        `(0.0, 0.0)`, un valor neutro que deja claro que hay que escribir uno.
    """
    ruta = settings.ruta_municipios_salida()
    if not os.path.exists(ruta):
        return 0.0, 0.0

    try:
        import json

        with open(ruta, "r", encoding="utf-8", errors="replace") as f:
            capa = json.load(f)

        # Recorrido de todas las coordenadas, sea cual sea el tipo de geometria
        # (Polygon, MultiPolygon o colecciones anidadas), para sacar el
        # rectangulo envolvente.
        lons, lats = [], []

        def _recorrer(nodo):
            """
            Acumula las coordenadas de una estructura anidada de GeoJSON.

            Args:
                nodo: Lista de coordenadas o de listas de coordenadas.

            Returns:
                None. Escribe en `lons` y `lats` del ambito exterior.
            """
            if (isinstance(nodo, (list, tuple)) and len(nodo) >= 2
                    and all(isinstance(v, (int, float)) for v in nodo[:2])):
                lons.append(float(nodo[0]))
                lats.append(float(nodo[1]))
            elif isinstance(nodo, (list, tuple)):
                for hijo in nodo:
                    _recorrer(hijo)

        for entidad in capa.get("features", []):
            geometria = (entidad or {}).get("geometry") or {}
            _recorrer(geometria.get("coordinates"))

        if not lons or not lats:
            return 0.0, 0.0

        return (round((min(lats) + max(lats)) / 2, 5),
                round((min(lons) + max(lons)) / 2, 5))

    except Exception:
        return 0.0, 0.0


def barra_de_progreso():
    """
    Crea los widgets de progreso y devuelve el callback que los alimenta.

    Returns:
        Tupla `(callback, caja_texto, barra)`. El callback tiene la firma
        `f(mensaje: str, fraccion: float)` que esperan los modulos de `core/`.
    """
    caja = st.empty()
    barra = st.progress(0.0)

    def callback(mensaje, fraccion):
        """
        Actualiza el texto y la barra de progreso en pantalla.

        Se le pasa a las funciones de `core/`, que no saben nada de Streamlit:
        ellas solo llaman a este callback y la interfaz se encarga de pintarlo.
        Asi el mismo motor de filtrado sirve para la web y para la linea de
        comandos, donde el callback dibuja una barra de texto en la terminal.

        Args:
            mensaje: Texto descriptivo del paso actual.
            fraccion: Avance entre 0.0 y 1.0. Se recorta a ese rango porque un
                valor fuera de el hace que Streamlit lance una excepcion y
                tumbe el proceso completo por un simple error de redondeo.
        """
        caja.write(f"🔄 {mensaje}")
        barra.progress(min(max(float(fraccion), 0.0), 1.0))

    return callback, caja, barra


# ==============================================================================
# CONFIGURACION DE LA PAGINA
# ==============================================================================

# ==============================================================================
# AJUSTES ELEGIDOS DESDE LA INTERFAZ
# ==============================================================================
# Se vuelcan sobre el entorno ANTES de que nadie lea configuracion. Lo que el
# usuario eligio en la interfaz (por ejemplo, estudiar de Drive en vez de la
# carpeta local) manda sobre lo que diga el .env, que es configuracion de
# despliegue y mas antigua.
from core import ajustes as _ajustes_guardados

_ajustes_guardados.aplicar()


st.set_page_config(
    page_title="Copiloto Ambiental - Conesa & QGIS",
    page_icon="🌱",
    layout="wide",
    initial_sidebar_state="expanded",
)

# La hoja de estilo vive en core/estilos.py: eran sesenta lineas de CSS a
# mitad de este archivo, imposibles de leer o de probar por separado.
from core import estilos

st.markdown(estilos.hoja(), unsafe_allow_html=True)


# ==============================================================================
# ESTADO DE LA SESION
# ==============================================================================
# Streamlit reejecuta el script entero en cada interaccion. Todo lo que deba
# sobrevivir a esa reejecucion vive en `st.session_state`.



#: Iconos de la conversacion. Distinguir de un vistazo quien habla importa mas
#: aqui que en otros chats, porque las respuestas son largas y van citadas.
AVATARES = {"user": "👤", "assistant": "🌿"}


def _agente(clave: str):
    """
    Devuelve el agente de ESTA sesion, creandolo la primera vez.

    POR QUE NO UNO NUEVO EN CADA PREGUNTA
    -------------------------------------
    El agente guarda la conversacion para poder resolver un "y esa especie?".
    Construyendo uno nuevo en cada turno, esa memoria nacia vacia siempre y el
    agente contestaba pidiendo que se le repitiera de que se hablaba, aunque
    acabara de decirlo el.

    Vive en `st.session_state`, asi que cada pestania del navegador tiene su
    propia conversacion y no se mezclan.

    Args:
        clave: API Key a usar. Si cambia, se construye un agente nuevo: una
            clave distinta puede ser otro proyecto y otra cuota.

    Returns:
        La instancia de `ConesaAgent` de esta sesion.
    """
    from agent.chatbot import ConesaAgent

    guardado = st.session_state.get("agente")
    if guardado is None or st.session_state.get("agente_clave") != clave:
        guardado = ConesaAgent(api_key=clave)
        st.session_state["agente"] = guardado
        st.session_state["agente_clave"] = clave
    return guardado


def _api(ruta: str, metodo: str = "GET", cuerpo=None, espera: int = 15):
    """
    Llama a la API del proyecto y devuelve lo que conteste.

    Args:
        ruta: Camino a partir de la raiz, empezando por barra.
        metodo: Verbo HTTP.
        cuerpo: Diccionario a enviar como JSON, o None.
        espera: Segundos antes de rendirse.

    Returns:
        Tupla `(codigo, datos)`. El codigo es 0 si no se pudo ni conectar, y
        entonces `datos` lleva la clave `error` con el motivo.
    """
    import json
    import urllib.error
    import urllib.request

    url = settings.api_url().rstrip("/") + ruta
    datos = json.dumps(cuerpo).encode("utf-8") if cuerpo is not None else None
    peticion = urllib.request.Request(
        url, data=datos, method=metodo,
        headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(peticion, timeout=espera) as r:
            crudo = r.read().decode("utf-8")
            return r.status, (json.loads(crudo) if crudo else {})
    except urllib.error.HTTPError as exc:
        try:
            return exc.code, json.loads(exc.read().decode("utf-8"))
        except Exception:
            return exc.code, {}
    except Exception as exc:
        return 0, {"error": str(exc)}


def _imagenes_pegadas(con_datos: bool = False) -> list:
    """
    Recoge las imagenes que esperan en el buzon de esta sesion.

    Args:
        con_datos: Si se piden tambien los bytes. Para pintar el aviso de
            "2 imagenes pegadas" no hacen falta, y traerlos en cada pasada
            seria mover megas sin motivo.

    Returns:
        Lista de diccionarios con `nombre`, `tipo`, `bytes` y, si se pidieron,
        `datos` en base64. Lista vacia si la API no responde: una imagen
        pegada nunca debe impedir escribir.
    """
    sesion = st.session_state.get("session_id", "default")
    sufijo = "?con_datos=true" if con_datos else ""
    codigo, datos = _api(f"/chat/imagenes/{sesion}{sufijo}", espera=10)
    if codigo != 200:
        return []
    return datos.get("imagenes", [])


def _vaciar_imagenes_pegadas() -> None:
    """Descarta las imagenes en espera de esta sesion."""
    sesion = st.session_state.get("session_id", "default")
    _api(f"/chat/imagenes/{sesion}", metodo="DELETE", espera=10)


def _tamano_legible(cuantos_bytes: int) -> str:
    """
    Escribe un tamano de archivo de forma que se entienda.

    Dividir siempre entre 1024 hacia enteros hace que todo lo que pese menos
    de un kilobyte salga como "0 KB", que parece un fallo: se vio con una
    captura pequena recien pegada.

    Args:
        cuantos_bytes: Tamano en bytes.

    Returns:
        Algo como `840 B`, `27 KB` o `1,4 MB`.
    """
    cuantos_bytes = int(cuantos_bytes or 0)
    if cuantos_bytes < 1024:
        return f"{cuantos_bytes} B"
    if cuantos_bytes < 1024 * 1024:
        return f"{cuantos_bytes / 1024:.0f} KB"
    return f"{cuantos_bytes / (1024 * 1024):.1f} MB".replace(".", ",")


#: Alto maximo de la vista previa de una imagen pegada, en pixeles.
ALTO_MINIATURA = 260

#: Como se llama cada formato en la lista de documentos. Decir "Hoja de
#: calculo" en vez de ".xlsx" ahorra traducir mentalmente, y la extension
#: sigue viendose al lado.
#:
#: Es un diccionario de PRESENTACION: lo que el sistema sabe leer se decide en
#: `core/corpus.LECTORES`, no aqui. Un formato que falte en esta lista se
#: muestra como "Archivo" y se lee igual de bien.
_NOMBRES_FORMATO = {
    ".md": "Markdown", ".txt": "Texto", ".pdf": "PDF",
    ".docx": "Word", ".xlsx": "Excel", ".xls": "Excel",
    ".csv": "CSV", ".json": "JSON", ".xml": "XML",
    ".html": "HTML", ".htm": "HTML", ".log": "Registro",
    ".jpg": "Imagen", ".jpeg": "Imagen", ".png": "Imagen",
    ".webp": "Imagen", ".heic": "Imagen", ".heif": "Imagen",
    ".gif": "Imagen",
}


@st.cache_data(show_spinner=False, max_entries=24)
def _miniatura(datos: bytes) -> bytes:
    """
    Reduce una imagen a una vista previa pequena.

    POR QUE SE HACE EN EL SERVIDOR Y NO CON CSS
    -------------------------------------------
    El primer intento fue envolver la galeria en un `<div>` con `max-height`.
    No funciono: `st.markdown` con una etiqueta suelta NO envuelve los
    elementos de Streamlit que vienen detras, porque cada uno se dibuja en su
    propio contenedor. La miniatura seguia midiendo 531 pixeles de alto.

    Encogerla aqui es ademas lo correcto por otra razon: mandar 46 KB al
    navegador para dibujar 150 pixeles es desperdiciar el viaje. La imagen
    completa se queda en el servidor y es la que ve el modelo; al navegador
    solo va la vista previa.

    Args:
        datos: La imagen original, en bytes.

    Returns:
        La miniatura en PNG. Si algo falla, los bytes originales: una vista
        previa grande es peor que ninguna, pero mucho mejor que un error.
    """
    try:
        import io as _io

        from PIL import Image

        imagen = Image.open(_io.BytesIO(datos))
        imagen.thumbnail((ALTO_MINIATURA * 3, ALTO_MINIATURA))
        if imagen.mode not in ("RGB", "RGBA"):
            imagen = imagen.convert("RGB")
        destino = _io.BytesIO()
        # WEBP en vez de PNG. Se midio con una captura de 1920x1080: la
        # miniatura en PNG pesaba 12,2 KB y en WEBP 0,7 KB, con la misma
        # pinta. Son 17 veces menos bytes viajando al navegador cada vez que
        # se vuelve a dibujar la pagina.
        imagen.save(destino, format="WEBP", quality=80, method=4)
        return destino.getvalue()
    except Exception:
        return datos


def _mostrar_resultado_drive(resultado: dict) -> None:
    """
    Cuenta como fue el intento de conectar con Drive.

    Las dos formas de conectar -- por campos o pegando el JSON -- terminan
    aqui, para que digan exactamente lo mismo ante el mismo resultado. Con el
    mensaje duplicado en dos sitios, arreglar uno y olvidar el otro deja al
    usuario recibiendo consejos distintos para el mismo problema.

    Args:
        resultado: Lo que devuelve `conexion_drive.conectar`.
    """
    if resultado.get("conectado"):
        st.session_state.aviso_drive = (
            f"Conectado. {resultado['archivos']} archivos y "
            f"{resultado['carpetas']} carpetas visibles. Pulsa "
            f"«Estudiar ahora» para que el agente los lea.")
        st.rerun()
        return

    st.error(resultado.get("motivo", "No se pudo conectar."))
    # Si se llego a saber el correo de la cuenta, se ofrece: compartir la
    # carpeta con el es el paso que falta en la mayoria de los fallos.
    if resultado.get("cuenta"):
        st.caption("Comparte la carpeta de Drive con este correo, con permiso "
                   "de **Lector**:")
        st.code(resultado["cuenta"], language=None)


def _quitar_imagen_pegada(indice: int) -> None:
    """
    Descarta una sola de las imagenes en espera.

    Args:
        indice: Posicion de la imagen, empezando en cero.
    """
    sesion = st.session_state.get("session_id", "default")
    _api(f"/chat/imagenes/{sesion}/{indice}", metodo="DELETE", espera=10)


def _adjuntos_para_el_modelo() -> list:
    """
    Convierte las imagenes del buzon en lo que el agente sabe mirar.

    Returns:
        Lista de tuplas `(bytes, tipo)`. Vacia si no hay nada pegado o si algo
        viniera corrupto: se descarta en silencio y la pregunta sigue su curso
        como texto.
    """
    import base64

    salida = []
    for imagen in _imagenes_pegadas(con_datos=True):
        try:
            salida.append((base64.b64decode(imagen["datos"]),
                           imagen.get("tipo", "image/png")))
        except Exception:
            continue
    return salida


def _pintar_mensaje(texto: str, orden: int = 0) -> None:
    """
    Escribe un mensaje del chat y, si toca, ofrece el script de QGIS.

    El agente no puede dibujar botones: solo devuelve texto. Cuando entrega el
    script deja una marca al final, esta funcion la quita antes de mostrar
    nada y pone en su sitio un boton de descarga de verdad. Asi quien lee la
    conversacion nunca ve la marca.

    Args:
        texto: Contenido del mensaje.
        orden: Posicion del mensaje en la conversacion. Solo sirve para que
            cada boton tenga una clave distinta: dos botones con la misma
            clave hacen que Streamlit se queje y corte el repintado.
    """
    from agent.tools import MARCA_SCRIPT_QGIS

    if MARCA_SCRIPT_QGIS not in (texto or ""):
        st.write(texto)
        return

    limpio = texto.replace(MARCA_SCRIPT_QGIS, "")
    # El modelo a veces nombra la marca en mitad de la frase y la deja entre
    # acentos graves ("...mediante el boton (`[[...]]`)"). Al quitarla queda un
    # par de acentos vacios, que se ven como un hueco raro. Se barren tambien
    # los parentesis que hubieran quedado sin contenido.
    import re as _re
    limpio = _re.sub(r"\(\s*``\s*\)", "", limpio)
    limpio = _re.sub(r"``", "", limpio)
    limpio = _re.sub(r"[ \t]{2,}", " ", limpio)
    st.write(limpio.rstrip())

    ruta = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        "cargar_capas_qgis.py")
    if not os.path.exists(ruta):
        st.warning("No encuentro `cargar_capas_qgis.py` en el proyecto.")
        return
    with open(ruta, "r", encoding="utf-8") as f:
        st.download_button(
            label="📥 Descargar el script para QGIS",
            data=f.read(),
            file_name="cargar_capas_qgis.py",
            mime="text/x-python",
            key=f"descargar_script_qgis_{orden}",
        )


def _responder_con_cronometro(pregunta: str, imagenes=None) -> str:
    """
    Pregunta al agente mostrando el tiempo que lleva pensando.

    POR QUE YA NO PASA POR n8n
    --------------------------
    Antes esta funcion llamaba al webhook `copiloto-chat` y solo caia al agente
    directo si n8n no respondia. Ese flujo se retiro porque no hacia nada que
    la API no hiciera ya: validaba la pregunta, la reenviaba a `/agente/chat` y
    anadia tres datos de adorno. Toda la memoria de la conversacion estaba, y
    sigue estando, en el agente.

    Ademas el rodeo IMPEDIA pegar imagenes: el webhook solo transportaba texto,
    asi que una imagen pegada se perdia por el camino.

    Args:
        pregunta: Lo que escribio la persona.
        imagenes: Lista opcional de tuplas `(bytes, tipo)` para que el modelo
            las mire junto con la pregunta.

    Returns:
        La respuesta del agente en Markdown.
    """
    clave = st.session_state.api_key
    sesion = st.session_state.get("session_id", "default")
    salida = {}

    def _trabajo():
        """
        Hace la consulta fuera del hilo que pinta, para poder cronometrar.

        Deja el resultado en `salida`, que el hilo principal lee cuando este
        termina.

        Raises:
            Nada. Cualquier fallo se convierte en texto dentro de `salida`:
            una excepcion aqui moriria en un hilo de fondo sin que nadie la
            viera, y la conversacion se quedaria colgada sin explicacion.
        """
        import json
        import urllib.request

        n8n = os.environ.get(
            "N8N_URL",
            "http://n8n:5678" if os.path.exists("/.dockerenv")
            else "http://localhost:5678")
        cuerpo = json.dumps({
            "pregunta": pregunta,
            "session_id": sesion,
            "api_key": clave or None,
        }).encode("utf-8")
        try:
            peticion = urllib.request.Request(
                f"{n8n}/webhook/copiloto-chat", data=cuerpo,
                headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(peticion, timeout=200) as r:
                datos = json.loads(r.read().decode("utf-8"))
            item = datos[0] if isinstance(datos, list) and datos else datos
            texto = item.get("respuesta") or ""
            if not texto:
                raise ValueError("n8n no devolvio respuesta")

            # Acuse de lo que hizo el orquestador. Sin esto no hay forma de
            # saber desde la interfaz si el intercambio llego a Qdrant.
            if item.get("memoria_guardada") and not item.get("memoria_en_espera"):
                texto += (f"\n\n---\n*Guardado en `"
                          f"{item.get('memoria_coleccion', 'Qdrant')}` el "
                          f"{item.get('memoria_fecha', '')} · "
                          f"n8n #{item.get('ejecucion_id', '?')}*")
            elif item.get("memoria_en_espera"):
                # No se perdio: espera a que vuelva la red. Decir "no se
                # guardo" cuando el texto esta a salvo en disco asusta sin
                # motivo y, encima, era falso.
                texto += ("\n\n---\n*Guardado. Se añadirá a la memoria en "
                          "cuanto la red lo permita.*")
            elif item.get("memoria_motivo"):
                texto += (f"\n\n---\n*No se guardó en la memoria: "
                          f"{item['memoria_motivo']}*")
            salida["texto"] = texto
            return
        except Exception as exc:
            salida["aviso_n8n"] = str(exc)[:160]

        # Reserva: si n8n no esta, la conversacion no se interrumpe. Se avisa,
        # eso si: callarlo haria creer que el flujo esta funcionando.
        try:
            salida["texto"] = _agente(clave).responder(pregunta, imagenes)
        except Exception as exc:
            salida["texto"] = f"Error al consultar el agente: {exc}"

    hilo = threading.Thread(target=_trabajo, daemon=True)
    hilo.start()

    marcador = st.empty()
    inicio = time.time()
    while hilo.is_alive():
        segundos = int(time.time() - inicio)
        aviso = ""
        if segundos > 90:
            aviso = "  ·  consultando varias fuentes, puede tardar unos minutos"
        marcador.markdown(f"*Pensando… **{segundos} s**{aviso}*")
        time.sleep(1)
    hilo.join(timeout=5)
    marcador.empty()

    texto = salida.get("texto", "El agente no devolvió respuesta.")
    if salida.get("aviso_n8n"):
        # Se responde igual, pero diciendo que el orquestador no intervino: si
        # n8n esta caido, el intercambio NO se guardo en Qdrant, y eso hay que
        # verlo en vez de suponerlo.
        texto += (f"\n\n---\n*El orquestador de n8n no respondió "
                  f"(`{salida['aviso_n8n']}`), así que esta respuesta vino "
                  f"del agente directo y **no quedó guardada en Qdrant**.*")
    st.session_state.messages.append({"role": "assistant", "content": texto})
    st.session_state.pregunta_pendiente = None
    return texto


_ESTADO_INICIAL = {
    # Chat
    "session_id": "sesion_" + str(int(time.time()))[-6:],
    "messages": [{
        "role": "assistant",
        "content": (
            "¡Hola! Soy tu copiloto ambiental, conectado a Gemini en la nube.\n\n"
            # No se nombran temas concretos a proposito. El saludo anterior
            # prometia "plantas hospederas, cria, catalogos de especies", que
            # era la bibliografia de OTRO trabajo: el agente estudia lo que
            # haya en la fuente conectada, y anunciar temas que no estan es la
            # forma mas rapida de que la primera pregunta se quede sin
            # respuesta.
            "Puedes preguntarme por la **documentación que tengo estudiada**, "
            "calcular un **impacto ambiental con la metodología Conesa**, "
            "consultar una **coordenada** o preguntarme **qué datos sirven "
            "para QGIS**.\n\n"
            "También puedes **pegar una imagen con Ctrl+V** y preguntarme por "
            "ella: la leo y te digo qué dice."
        ),
    }],
    # Pregunta que el usuario ya envio y el agente todavia no ha
    # respondido. Separar las dos cosas es lo que permite que su
    # mensaje aparezca al instante y no tras dos minutos de espera.
    "pregunta_pendiente": None,
    # Conexion con la IA.
    # Si el proveedor configurado es el modelo LOCAL, se arranca con la clave
    # vacia a proposito: una clave de Gemini heredada del `.env` desviaria la
    # conversacion a la nube sin que el usuario lo hubiera pedido, que es
    # justo lo contrario del objetivo del proyecto.
    "api_key": settings.api_key_gemini() or "",
    "api_key_valida": None,      # None = aun no se ha validado
    "api_key_mensaje": "",
    # Dataset de biodiversidad
    "ruta_dataset": settings.ruta_dataset_biodiversidad() or "",
    "ficha_dataset": None,       # resultado de catalogo.describir_dataset
    "catalogo": None,            # resultado de catalogo.explorar_valores
    "columna_geografica": None,
    "columna_taxonomica": None,
}

for clave, valor in _ESTADO_INICIAL.items():
    if clave not in st.session_state:
        st.session_state[clave] = valor


# ==============================================================================
# TITULO
# ==============================================================================

st.title("🌱 Copiloto Ambiental Inteligente (Conesa + QGIS)")
st.caption("Práctica Profesional II - Ingeniería de Software (UMB)")


# ==============================================================================
# BARRA LATERAL
# ==============================================================================

with st.sidebar:
    # --- Historial de conversaciones ----------------------------------------
    st.subheader("Mis Conversaciones")
    from core import memoria

    if st.button("Nueva charla", use_container_width=True, type="primary"):
        st.session_state.session_id = "sesion_" + str(int(time.time()))[-6:]
        st.session_state.messages = list(_ESTADO_INICIAL["messages"])
        st.session_state.pregunta_pendiente = None
        st.session_state.pop("editando_sesion", None)
        st.session_state.pop("borrando_sesion", None)
        st.rerun()

    lista_sesiones = memoria.listar_sesiones(limite=20)
    if lista_sesiones:
        opciones = {s["id"]: s for s in lista_sesiones}
        if st.session_state.session_id not in opciones:
            opciones[st.session_state.session_id] = {
                "id": st.session_state.session_id,
                "titulo": "Charla actual (en curso)",
                "creada_en": "",
            }

        claves = list(opciones.keys())
        idx = (claves.index(st.session_state.session_id)
               if st.session_state.session_id in claves else 0)

        def _etiqueta(clave):
            """
            Compone la etiqueta de una conversacion para el desplegable.

            Args:
                clave: Identificador de la conversacion.

            Returns:
                El titulo con su fecha entre parentesis, o solo el titulo si
                la conversacion aun no tiene fecha.
            """
            s = opciones.get(clave, {})
            fecha = (s.get("creada_en") or "")[:10]
            titulo = s.get("titulo") or clave
            return f"{titulo} ({fecha})" if fecha else titulo

        seleccionada = st.selectbox(
            "Cargar conversación:", options=claves, format_func=_etiqueta,
            index=idx, key="selector_sesiones_sb")

        if seleccionada != st.session_state.session_id:
            st.session_state.session_id = seleccionada
            cargados = memoria.cargar_historial(seleccionada)
            st.session_state.messages = (
                cargados if cargados else list(_ESTADO_INICIAL["messages"]))
            st.session_state.pop("editando_sesion", None)
            st.session_state.pop("borrando_sesion", None)
            st.rerun()

        # --- Renombrar y eliminar ------------------------------------------
        # Van en dos pasos a proposito. Un boton de borrar que borra al primer
        # clic, en una lista donde las charlas se llaman "hola" y "recuerdas
        # la primera pregunta", es una perdida de datos esperando a ocurrir:
        # no hay papelera, y lo borrado se va de SQLite Y de Qdrant.
        col_ed, col_bo = st.columns(2)
        with col_ed:
            if st.button("Renombrar", use_container_width=True,
                         disabled=not seleccionada):
                st.session_state.editando_sesion = seleccionada
                st.session_state.pop("borrando_sesion", None)
                st.rerun()
        with col_bo:
            if st.button("Eliminar", use_container_width=True,
                         disabled=not seleccionada):
                st.session_state.borrando_sesion = seleccionada
                st.session_state.pop("editando_sesion", None)
                st.rerun()

        if st.session_state.get("editando_sesion") == seleccionada:
            with st.form("formulario_renombrar", clear_on_submit=False):
                nuevo = st.text_input(
                    "Nuevo título",
                    value=opciones[seleccionada].get("titulo", ""),
                    max_chars=80)
                c1, c2 = st.columns(2)
                guardar = c1.form_submit_button("Guardar",
                                                use_container_width=True,
                                                type="primary")
                cancelar = c2.form_submit_button("Cancelar",
                                                 use_container_width=True)
            if guardar:
                if memoria.renombrar_sesion(seleccionada, nuevo):
                    st.session_state.pop("editando_sesion", None)
                    st.rerun()
                else:
                    st.warning("El título no puede quedar vacío.")
            elif cancelar:
                st.session_state.pop("editando_sesion", None)
                st.rerun()

        if st.session_state.get("borrando_sesion") == seleccionada:
            st.warning(
                f"¿Eliminar **{opciones[seleccionada].get('titulo')}**? "
                f"Se borra el historial y también lo que el agente recuerda "
                f"de esta charla. No hay vuelta atrás."
            )
            c1, c2 = st.columns(2)
            if c1.button("Sí, eliminar", use_container_width=True,
                         key="confirmar_borrado"):
                res = memoria.borrar_sesion(seleccionada)
                st.session_state.pop("borrando_sesion", None)
                if res["recuerdos"] < 0:
                    st.session_state.aviso_borrado = (
                        "Se borró el historial, pero Qdrant no respondió: "
                        "los recuerdos de esa charla pueden seguir ahí.")
                else:
                    st.session_state.aviso_borrado = (
                        f"Eliminada: {res['mensajes']} mensajes y "
                        f"{res['recuerdos']} recuerdos.")
                # Se sale de la charla borrada: quedarse en ella mostraria el
                # historial en pantalla de algo que ya no existe.
                st.session_state.session_id = "sesion_" + str(int(time.time()))[-6:]
                st.session_state.messages = list(_ESTADO_INICIAL["messages"])
                st.rerun()
            if c2.button("No, cancelar", use_container_width=True):
                st.session_state.pop("borrando_sesion", None)
                st.rerun()

        if st.session_state.get("aviso_borrado"):
            st.success(st.session_state.pop("aviso_borrado"))


    st.write("---")
    st.subheader("Estado")

    # --- Estado del sistema, en dos lineas -----------------------------------
    # Antes eran dos bloques de color a pantalla completa que gritaban
    # "CONECTADO" ocupando un tercio de la barra lateral. Un punto de color y
    # una linea dicen lo mismo, y dejan sitio para lo que de verdad se usa.
    from core import estilos as _est

    URL_API = settings.api_url()
    try:
        respuesta = requests.get(f"{URL_API}/", timeout=2)
        if respuesta.status_code == 200:
            _api_estado, _api_texto = "ok", "API en línea"
        else:
            _api_estado = "tibio"
            _api_texto = f"API responde {respuesta.status_code}"
    except requests.exceptions.RequestException:
        _api_estado, _api_texto = "mal", "API apagada"
    st.markdown(
        _est.indicador(_api_texto, _api_estado,
                       URL_API.replace("http://", "")),
        unsafe_allow_html=True)

    from agent.chatbot import estado as estado_proveedor
    info = estado_proveedor()
    st.markdown(
        _est.indicador(
            "IA activa" if info["listo"] else "IA no disponible",
            "ok" if info["listo"] else "mal",
            info["modelo"] if info["listo"] else ""),
        unsafe_allow_html=True)
    if not info["listo"]:
        st.caption(info["motivo"])

    # El estado del modelo ya se muestra arriba, junto al de la API: son las
    # dos cosas que o funcionan o no, y verlas juntas dice de un vistazo si el
    # sistema esta listo. Aqui queda solo lo que se TOCA.
    #
    # Nota sobre el proveedor: desde 17/10/2026 el de por defecto es Gemini,
    # por rendimiento. El modelo local se midio en 4,16 tokens por segundo y
    # no sirve para un despliegue, pero sigue completo y a un cambio de
    # variable: es el que hay que usar en campo, donde no hay cobertura.
    with st.expander("Clave de Gemini"):
        st.caption(
            "La clave se lee de GEMINI_API_KEY en el archivo .env. Aquí puedes "
            "escribir otra para esta sesión sin tocar el archivo."
        )

        clave_escrita = st.text_input(
            "API Key de Gemini",
            value=st.session_state.api_key,
            type="password",
            help="Puedes obtener una clave gratuita en https://aistudio.google.com/",
        )

        # Si la persona cambia la clave, se invalida el resultado anterior para que
        # no quede un "verde" de una clave que ya no es la que esta escrita.
        if clave_escrita != st.session_state.api_key:
            st.session_state.api_key = clave_escrita
            st.session_state.api_key_valida = None
            st.session_state.api_key_mensaje = ""

        if st.button("🔌 Validar y conectar", type="primary",
                     use_container_width=True):
            with st.spinner("Verificando la clave con el servicio..."):
                from agent.chatbot import validar_api_key
                valida, mensaje = validar_api_key(st.session_state.api_key)
                st.session_state.api_key_valida = valida
                st.session_state.api_key_mensaje = mensaje

        if st.session_state.api_key_valida is True:
            st.success(f"IA: CONECTADA. {st.session_state.api_key_mensaje}")
        elif st.session_state.api_key_valida is False:
            st.error(f"IA: SIN CONEXIÓN. {st.session_state.api_key_mensaje}")
        else:
            st.info(
                "IA: sin verificar. Sin una clave válida el agente responde en "
                "modo simulador (motor de reglas local)."
            )

        # --- Revisión de seguridad ---------------------------------------
        # Que la clave FUNCIONE y que esté bien GUARDADA son dos cosas
        # distintas: una clave puede responder perfectamente y estar a la vez
        # escrita en un archivo que se sube al repositorio. Lo de arriba
        # comprueba lo primero; esto comprueba lo segundo.
        from core import seguridad as _seg

        _rev = _seg.revisar_api_key(st.session_state.api_key or None)
        st.caption(f"Clave en uso: `{_rev['huella']}` — nunca se muestra "
                   f"entera, ni aquí ni en los registros.")
        for _aviso in _rev["avisos"]:
            (st.error if _rev["grave"] else st.warning)(_aviso)
        if _rev["hay_clave"] and not _rev["avisos"]:
            st.caption("✅ Sin problemas de resguardo: no aparece en archivos "
                       "versionados y el `.env` está ignorado por git.")

    st.write("---")

    # --- Memoria del agente ---------------------------------------------
    # El panel que mostraba los turnos guardados se retiro de la barra
    # lateral por peticion expresa: ocupaba sitio y repetia algo que ya se
    # ve en la propia conversacion.
    #
    # La memoria NO se ha tocado: el agente sigue recordando igual. Lo que
    # desaparece es el escaparate, no el mecanismo. Para vaciarla esta el
    # boton 'Limpiar' de la cabecera del chat.

    # --- Bibliografia del agente ---------------------------------------------
    # Sin este panel no habia forma de saber desde la interfaz que documentos
    # conoce el agente, ni de notar que la carpeta estaba vacia.
    st.subheader("Fuente")

    # --- De donde estudia el agente, siempre a la vista --------------------
    # Estaba enterrado en una pestana. Saber si el agente lee de Drive o de la
    # carpeta local condiciona todo lo demas -- que documentos ve, que pasa
    # esta noche a las 02:00 -- asi que tiene que verse sin ir a buscarlo.
    from core import conexion_drive as _cdrive

    _est_drive = _cdrive.estado()
    if _est_drive["activo"]:
        st.markdown(
            _est.indicador("Drive conectado", "ok",
                           _est_drive["carpeta_id"][:12] + "…"),
            unsafe_allow_html=True)
        if _est_drive.get("cuenta"):
            st.caption(f"Como `{_est_drive['cuenta'][:34]}`")
        if st.button("Desconectar Drive", use_container_width=True,
                     key="desconectar_drive_lateral"):
            _cdrive.desconectar()
            st.session_state.aviso_drive = (
                "Drive desconectado. El agente vuelve a estudiar de la "
                "carpeta local. Lo ya descargado NO se ha borrado.")
            st.rerun()
    else:
        st.markdown(
            _est.indicador("Carpeta local", "tibio", "Drive sin conectar"),
            unsafe_allow_html=True)
        st.caption("Conéctalo en la pestaña **Drive y Estudio**.")

    st.subheader("Lo estudiado")
    from core import corpus as _corpus

    # Se pregunta a la API en vez de leer la carpeta local, porque con Drive
    # la carpeta local NO es donde vive lo estudiado: los documentos se borran
    # en cuanto se leen. Leyendola, la barra decia "1 documentos" (el zip que
    # quedaba) mientras en Drive habia cuatro. La API ya resuelve de donde
    # salen los numeros segun cual sea la fuente activa.
    _cod_bib, _est_bib = _api("/corpus/estado", espera=20)
    if _cod_bib == 200:
        _bib = _est_bib.get("corpus") or {}
        # Las fichas que importan son las del INDICE: es lo que el agente
        # puede consultar. Las de la carpeta local son un paso intermedio que
        # con Drive ni siquiera queda en disco.
        _bib = dict(_bib)
        _bib["fichas"] = (_est_bib.get("vectorial") or {}).get("fichas", 0)
    else:
        _bib = _corpus.resumen()

    if _bib.get("disponible"):
        # Un indicador, no un `st.success`. Un bloque verde de ancho completo
        # para decir cuantos documentos hay compite con los avisos de verdad:
        # si todo esta en verde, el verde deja de significar nada.
        st.markdown(
            _est.indicador(
                f"{_bib['documentos']} documentos", "ok",
                f"{_bib['fichas']:,} fichas".replace(",", ".")),
            unsafe_allow_html=True)
        # Los archivos que la fuente VE pero no lee enteros. Sin esta linea la
        # barra decia "4 documentos" sobre una carpeta de ocho, y la
        # diferencia solo se descubria abriendo Drive y contando a mano.
        _grandes_barra = _bib.get("demasiado_grandes") or []
        if _grandes_barra:
            st.markdown(
                _est.indicador(f"{len(_grandes_barra)} capas de datos",
                               "tibio", "no se leen enteras"),
                unsafe_allow_html=True)
            with st.expander("¿Cuáles y por qué?"):
                st.markdown(
                    _est.lista_documentos(
                        [(_g["nombre"], f"{_g['mb']} MB")
                         for _g in _grandes_barra]),
                    unsafe_allow_html=True)
                st.caption(
                    f"Darían más de las "
                    f"{_bib.get('presupuesto_fichas', 2000):,} fichas de "
                    f"presupuesto por documento. De cada una se lee **solo la "
                    f"cabecera**, y eso sí queda en el índice: pregúntale al "
                    f"agente por el nombre del archivo y te dice qué columnas "
                    f"trae y si sirve para QGIS.".replace(",", ".")
                )

        # La version es la huella del conjunto de documentos del EQUIPO. Con
        # Drive no se conserva nada en el equipo, asi que esa huella describe
        # una carpeta vacia: decirla ahi seria dar un dato falso con aire de
        # dato exacto. Se dice de donde salen los numeros, que es lo util.
        if (_bib.get("describe") or "local") == "local":
            st.caption(f"Versión del corpus: `{_bib.get('version', '?')}`")
        else:
            st.caption(f"Contado directamente en **{_bib['describe']}**; "
                       f"en el equipo no queda ningún documento.")
        with st.expander("Reparto por categoría"):
            st.markdown(
                _est.lista_documentos(
                    [(_n, _c) for _n, _c in _bib.get("categorias") or []]),
                unsafe_allow_html=True)
            # Que significa "categoria" no es obvio, y verla vacia parece un
            # fallo de lectura cuando en realidad es una descripcion correcta
            # de como esta ordenada la carpeta.
            st.caption(
                "La categoría es **el nombre de la subcarpeta** donde está "
                "cada archivo. «Raíz de la carpeta» significa que están "
                "sueltos arriba, sin subcarpetas: si quieres agruparlos, "
                "créalas en Drive y aparecerán aquí."
            )
    else:
        if _est_drive["activo"]:
            st.caption(
                "El agente lee de Drive. Pulsa **Estudiar ahora** en la "
                "pestaña Drive y Estudio para que incorpore lo que haya."
            )
        else:
            st.caption(
                "Conecta Google Drive en la pestaña **Drive y Estudio** para "
                "que el agente tenga de dónde estudiar."
            )

    # --- Integracion con QGIS --------------------------------------------
    # El bloque que ofrecia aqui el boton "Descargar Script PyQGIS" se retiro
    # por peticion expresa. El script NO ha desaparecido: ahora lo entrega el
    # propio agente cuando se le pide un mapa, y la descarga aparece dentro de
    # la conversacion. Ver `agent/tools.entregar_script_qgis`.
    #
    # El archivo `cargar_capas_qgis.py` sigue intacto: solo cambio quien lo
    # ofrece.


# ==============================================================================
# AVISO DE CARGA, VISIBLE DESDE CUALQUIER PESTANA
# ==============================================================================
# La sincronizacion de las 10:30 la lanza n8n, no una persona. Si nadie esta
# mirando la pestana de Drive en ese momento, el trabajo ocurre sin que se vea
# nada: se midio, y la pregunta que quedaba era "esta cargando o esta colgado".
#
# Por eso el aviso va ARRIBA, antes de las pestanas, y se refresca solo. Es un
# `st.fragment`, asi que lo que se vuelve a dibujar cada tres segundos es esta
# banda y nada mas: la conversacion del chat no se recarga ni pierde el foco
# del cuadro de texto, que es lo que pasaba al hacer `st.rerun()` de la pagina
# entera.


def _tiempo_legible(segundos) -> str:
    """
    Pasa unos segundos a algo que se lee de un vistazo.

    Args:
        segundos: Cuantos segundos lleva el trabajo, o None.

    Returns:
        Texto como "47 s" o "3 min 12 s". Cadena vacia si no se sabe.
    """
    if not segundos:
        return ""
    segundos = int(segundos)
    if segundos < 60:
        return f"{segundos} s"
    return f"{segundos // 60} min {segundos % 60:02d} s"


@st.fragment(run_every="3s")
def _banda_de_carga() -> None:
    """
    Dice si el sistema esta cargando informacion de Drive al indice.

    Se consulta el estado cada tres segundos. Cuando no hay nada en marcha no
    pinta nada: un aviso permanente que casi siempre dice "inactivo" deja de
    leerse a los dos dias.
    """
    # Se pregunta al endpoint LIGERO. Con `/corpus/estado` cada vuelta de esta
    # banda costaba cerca de un segundo (lista Drive y recorre el indice), y
    # da la vuelta cada tres: la interfaz se sentia pastosa al escribir o al
    # pegar una imagen. Aqui solo se lee un diccionario en memoria: 3 ms.
    codigo, trabajo = _api("/corpus/sincronizacion", espera=10)
    if codigo != 200:
        return
    trabajo = trabajo or {}
    if not trabajo.get("ejecutando"):
        # Si el ultimo intento fallo, se dice aqui aunque ya no este en marcha:
        # un fallo del cron de las 10:30 ocurre cuando nadie mira, y callarlo
        # equivale a no haberlo intentado.
        if trabajo.get("error"):
            st.error(
                f"⚠️ La última carga de información **falló**: "
                f"{trabajo['error'][:200]}"
            )
        return

    avance = min(float(trabajo.get("avance") or 0.0), 1.0)
    documento = trabajo.get("documento") or ""
    origen = trabajo.get("origen") or "sin identificar"
    lleva = _tiempo_legible(trabajo.get("segundos"))

    with st.container(border=True):
        cabeza, reloj = st.columns([5, 2])
        cabeza.markdown(
            "**⏳ Cargando información de Drive al índice**"
            if avance < 1.0 else "**⏳ Terminando de cargar**"
        )
        reloj.markdown(
            f"<div style='text-align:right;opacity:.7;font-size:.85rem'>"
            f"{lleva}</div>", unsafe_allow_html=True)
        st.progress(avance, text=trabajo.get("mensaje") or "Estudiando…")
        detalle = f"Lo pidió: {origen}"
        if documento:
            detalle += f" · leyendo ahora: {documento}"
        st.caption(detalle + " · esta banda se actualiza sola")


_banda_de_carga()



# ==============================================================================
# PESTANAS
# ==============================================================================

tab_chat, tab_lote, tab_gps, tab_estudio, tab_drive = st.tabs([
    "💬 Chat con el Agente",
    "📊 Cálculo Masivo (Excel/CSV)",
    "📍 Diagnóstico de Coordenadas",
    "⚙️ Área de Estudio",
    "☁️ Drive y Estudio",
])


# ------------------------------------------------------------------------------
# PESTANA 1: CHAT
# ------------------------------------------------------------------------------
with tab_chat:
    # --------------------------------------------------------------------------
    # POR QUE ESTE ORDEN DE CONTENEDORES
    # --------------------------------------------------------------------------
    # `st.chat_input` solo se ancla al pie de la pagina cuando se usa en el
    # cuerpo principal. Dentro de una pestana se dibuja DONDE SE LLAMA, asi que
    # la version anterior lo pintaba a mitad de la conversacion: el historial
    # quedaba arriba, la caja de escribir en medio, y los mensajes nuevos
    # debajo de la caja.
    #
    # La solucion es reservar los dos huecos por adelantado. Streamlit fija la
    # posicion de un contenedor cuando se CREA, no cuando se rellena, de modo
    # que se puede dibujar primero la caja (para que no desaparezca mientras el
    # modelo piensa) y que aun asi aparezca al final.
    caja_cabecera = st.container()
    caja_historial = st.container()
    caja_entrada = st.container()

    with caja_cabecera:
        encabezado, boton = st.columns([5, 1])
        with encabezado:
            st.subheader("Chat del Copiloto Ambiental")
        with boton:
            if st.button("Limpiar", help="Vacía la conversación",
                         use_container_width=True):
                st.session_state.messages = list(_ESTADO_INICIAL["messages"])
                st.session_state.pregunta_pendiente = None
                st.rerun()

        from agent.chatbot import estado as estado_proveedor
        _info = estado_proveedor()

        if _info["listo"]:
            # `api_key_valida` solo se rellena si alguien pulsa "Validar" en la
            # barra lateral. La clave que viene del `.env` nunca pasa por ahi,
            # asi que mirar esa bandera anunciaria "modo simulador" con el
            # agente perfectamente conectado.
            st.caption(
                f"Modelo `{_info['modelo']}` · requiere conexión. "
                "El texto de cada consulta sale de este equipo."
            )
        elif st.session_state.api_key_valida is not True:
            st.info(
                "El agente está en **modo simulador**: reconoce la intención por "
                "palabras clave y ejecuta las mismas herramientas, pero no razona. "
                "Para activar el modo IA, configura `GEMINI_API_KEY` en el `.env` "
                "o valida una clave en la barra lateral."
            )

    # La caja de escribir se rellena ANTES de generar nada, para que siga
    # visible durante la espera, pero aparece al final por el orden de los
    # contenedores.
    with caja_entrada:
        # --- Pegar imagenes con Ctrl+V -------------------------------------
        # El oyente del portapapeles vive en un componente HTML y deja las
        # imagenes en la API; aqui solo se recogen. Ver core/pegado.py.
        import streamlit.components.v1 as componentes
        from core import pegado

        componentes.html(
            pegado.guion(settings.api_url_navegador(),
                         st.session_state.get("session_id", "default")),
            height=pegado.ALTO,
        )

        # Boton escondido. No hace nada por si mismo: existe para que el
        # JavaScript del portapapeles pueda provocar una pasada nueva en
        # cuanto guarda una imagen. Se oculta por la clase `st-key-...` que
        # Streamlit anade a todo elemento que lleve `key`, que es mas estable
        # que apuntar a su estructura interna. Un boton oculto sigue
        # respondiendo a `.click()` desde JavaScript.
        st.markdown(
            "<style>.st-key-boton_refrescar_pegado{display:none;}</style>",
            unsafe_allow_html=True)
        st.button(pegado.ETIQUETA_BOTON, key="boton_refrescar_pegado")

        # --- Imagenes pegadas, con vista previa --------------------------
        # Antes solo salia el nombre del archivo, que en un pegado del
        # portapapeles es siempre "image.png" y no dice nada: pegar dos
        # capturas distintas daba dos lineas identicas. Con la miniatura se
        # ve QUE se pego, y se puede quitar una sin tirar las demas.
        pendientes_img = _imagenes_pegadas(con_datos=True)
        if pendientes_img:
            peso = sum(i.get("bytes", 0) for i in pendientes_img)
            cabecera, boton = st.columns([5, 1])
            with cabecera:
                st.caption(
                    f"**{len(pendientes_img)} imagen(es) lista(s) para "
                    f"enviar** · {_tamano_legible(peso)} en total · se irán "
                    f"con tu próximo mensaje")
            with boton:
                if st.button("Quitar todas", key="quitar_pegadas",
                             use_container_width=True):
                    _vaciar_imagenes_pegadas()
                    st.rerun()

            import base64 as _b64

            # Cuantas caben a lo ancho sin quedar diminutas. Se reparte el
            # ancho entre las imagenes QUE HAY, no entre un numero fijo: con
            # una sola imagen, repartir en cuatro columnas la dejaba en un
            # octavo de la pantalla (unos 125 pixeles) y no se distinguia que
            # se habia pegado, que es justo para lo que sirve la vista previa.
            _CABEN = 3
            _a_la_vista = pendientes_img[:_CABEN]
            _huecos = len(_a_la_vista)
            # Con una o dos imagenes se deja un hueco a la derecha para que no
            # se estiren hasta ocupar media pantalla.
            _reparto = [1] * _huecos + ([3 - _huecos] if _huecos < 3 else [])
            columnas = st.columns(_reparto)
            for _i, _img in enumerate(_a_la_vista):
                with columnas[_i]:
                    try:
                        # La miniatura se calcula aqui, no se deja al CSS:
                        # ver `_miniatura`.
                        st.image(_miniatura(_b64.b64decode(_img["datos"])),
                                 use_container_width=True)
                    except Exception:
                        # Una miniatura que no se puede pintar no debe
                        # impedir enviar la imagen: el modelo la mirara igual.
                        st.caption("(no se pudo previsualizar)")
                    _pie1, _pie2 = st.columns([3, 1])
                    _pie1.caption(_tamano_legible(_img.get("bytes", 0)))
                    if _pie2.button("✕", key=f"quitar_img_{_i}",
                                    help="Quitar esta imagen"):
                        _quitar_imagen_pegada(_i)
                        st.rerun()
            if len(pendientes_img) > _CABEN:
                st.caption(f"… y {len(pendientes_img) - _CABEN} más.")


        pregunta = st.chat_input(
            "Escribe tu pregunta (o pega una imagen con Ctrl+V)"
        )

    with caja_historial:
        # La conversacion entera se pinta dentro de un UNICO hueco.
        #
        # Streamlit no borra la pantalla entre pasadas: va reemplazando
        # elemento por elemento, y los que sobran de la pasada anterior se
        # quedan donde estaban. Si la conversacion se acorta (al pulsar
        # "Limpiar", por ejemplo), los mensajes viejos seguian visibles DEBAJO
        # de los nuevos, con el aspecto de que el orden estaba al reves.
        #
        # Un `st.empty()` se reemplaza por completo cada vez que se rellena,
        # asi que no puede quedar nada de la pasada anterior.
        hueco = st.empty()
        with hueco.container():
            for _n_msg, mensaje in enumerate(st.session_state.messages):
                with st.chat_message(mensaje["role"],
                                     avatar=AVATARES.get(mensaje["role"])):
                    _pintar_mensaje(mensaje["content"], _n_msg)
                    _n_img = mensaje.get("imagenes") or 0
                    if _n_img:
                        st.caption(
                            f"📎 Se enviaron {_n_img} imagen(es) con este "
                            f"mensaje.")

            # Si quedo una pregunta sin responder de la pasada anterior, se atiende
            # aqui. Separar "registrar la pregunta" de "responderla" en dos pasadas
            # es lo que permite que el mensaje del usuario aparezca al instante en
            # vez de despues de dos minutos de espera.
            pendiente = st.session_state.get("pregunta_pendiente")
            if pendiente:
                with st.chat_message("assistant", avatar=AVATARES["assistant"]):
                    # Las imagenes se recogen AQUI, no al registrar la
                    # pregunta: asi vale tanto pegar antes de escribir como
                    # pegar y enviar en el mismo gesto.
                    _adjuntas = _adjuntos_para_el_modelo()
                    _responder_con_cronometro(pendiente, _adjuntas or None)
                    if _adjuntas:
                        # El buzon se vacia solo despues de que el modelo las
                        # haya mirado. Si la respuesta falla, las imagenes
                        # siguen ahi y se puede reintentar sin volver a pegar.
                        _vaciar_imagenes_pegadas()
                # Redibujado limpio en cuanto llega la respuesta.
                #
                # Mientras el modelo piensa, el guion lleva un minuto detenido en
                # ese punto y el navegador conserva lo que quedo de la pasada
                # anterior: el intercambio nuevo aparecia descolocado, por encima de
                # uno viejo. La respuesta ya se guardo en el historial, asi que una
                # pasada mas la pinta en su sitio y borra cualquier resto.
                st.rerun()

    if pregunta:
        # Se anota cuantas imagenes acompanan al mensaje, para DECIRLO en la
        # conversacion.
        #
        # POR QUE: una imagen pegada se adjuntaba al siguiente mensaje sin
        # dejar rastro. Alguien pegaba una captura, escribia "hola", y el
        # agente contestaba sobre la captura: desde fuera parecia que se
        # habia enviado algo que no se quiso enviar. El aviso de arriba lo
        # decia de antemano, pero una vez enviado no quedaba constancia.
        _cuantas = len(_imagenes_pegadas())
        st.session_state.messages.append({
            "role": "user", "content": pregunta, "imagenes": _cuantas})
        st.session_state.pregunta_pendiente = pregunta
        st.rerun()


# ------------------------------------------------------------------------------
# PESTANA 2: CALCULO MASIVO
# ------------------------------------------------------------------------------
with tab_lote:
    from core import estilos as _est_lote

    st.markdown("## Cálculo masivo")
    st.markdown(
        '<div class="subtitulo">Sube tu matriz de valoraciones en Excel o CSV '
        'y el sistema calcula la importancia y la severidad de cada fila, '
        'con la metodología Conesa.</div>',
        unsafe_allow_html=True)

    # La plantilla de ejemplo se genera a partir de los rangos reales de la
    # metodologia, no de una tabla escrita a mano en este archivo.
    from core.validator import ConesaValidator

    plantilla = pd.DataFrame([
        {"id_valoracion": 1, "nombre_accion": "Ejemplo de acción",
         "nombre_factor": "Ejemplo de factor", "signo": "-",
         **{sigla: valores[0] for sigla, valores in ConesaValidator.RANGOS.items()}},
        {"id_valoracion": 2, "nombre_accion": "Ejemplo de acción",
         "nombre_factor": "Ejemplo de factor", "signo": "+",
         **{sigla: valores[-1] for sigla, valores in ConesaValidator.RANGOS.items()}},
    ])

    with st.container(border=True):
        st.markdown("#### 1 · La plantilla")
        st.caption(
            "Si es la primera vez, descarga la plantilla: trae las columnas "
            "con los nombres exactos que el sistema espera y dos filas de "
            "ejemplo con los valores extremos admitidos.")
        columna_izq, columna_der = st.columns([1, 2])
        with columna_izq:
            st.download_button(
                label="Descargar plantilla (.csv)",
                data=plantilla.to_csv(index=False).encode("utf-8"),
                file_name="plantilla_valoracion_conesa.csv",
                mime="text/csv",
                use_container_width=True,
            )
        with columna_der:
            with st.expander("Valores admitidos en cada parámetro"):
                st.code(ConesaValidator.descripcion_rangos())

    with st.container(border=True):
        st.markdown("#### 2 · Tu matriz")
        archivo_subido = st.file_uploader(
            "Elige tu archivo Excel (.xlsx) o CSV", type=["xlsx", "csv"],
            label_visibility="collapsed",
        )

        if archivo_subido is None:
            # Estado vacio: decir QUE hacer, no solo que no hay nada.
            st.caption(
                "Arrastra aquí tu archivo, o pulsa para buscarlo. Nada se "
                "envía fuera de este equipo: el archivo se procesa en el "
                "servidor y se borra al terminar.")
        else:
            st.markdown(
                _est_lote.indicador(archivo_subido.name, "ok",
                                    _tamano_legible(archivo_subido.size)),
                unsafe_allow_html=True)

    if archivo_subido is not None:
        if st.button("Calcular matriz de Conesa", type="primary",
                     use_container_width=True):
            with st.spinner("Procesando filas y validando parámetros..."):
                # Los temporales van a la carpeta configurada, no al directorio
                # de trabajo actual (que dependia de desde donde se lanzara).
                carpeta = settings.carpeta_temporal()
                extension = os.path.splitext(archivo_subido.name)[1]
                ruta_entrada = os.path.join(carpeta, f"entrada{extension}")
                ruta_salida = os.path.join(carpeta, f"resultado{extension}")

                try:
                    with open(ruta_entrada, "wb") as destino:
                        destino.write(archivo_subido.getbuffer())

                    from database.excel_connector import ExcelConnector
                    resumen = ExcelConnector().procesar_archivo_evaluaciones(
                        ruta_entrada, ruta_salida
                    )

                    st.markdown("#### 3 · Resultado")
                    c1, c2, c3 = st.columns(3)
                    c1.metric("Registros procesados", resumen["registros_procesados"])
                    c2.metric("Cálculos exitosos", resumen["registros_exitosos"])
                    c3.metric("Filas con error", resumen["registros_con_error"])

                    if resumen["errores"]:
                        with st.expander(
                            f"Ver el detalle de las {len(resumen['errores'])} "
                            f"filas rechazadas"
                        ):
                            st.dataframe(pd.DataFrame(resumen["errores"]))

                    resultado = (
                        pd.read_csv(ruta_salida)
                        if ruta_salida.lower().endswith(".csv")
                        else pd.read_excel(ruta_salida)
                    )
                    st.caption("Matriz calculada")
                    st.dataframe(resultado, use_container_width=True)

                    with open(ruta_salida, "rb") as fuente:
                        st.download_button(
                            label="Descargar resultados",
                            use_container_width=True,
                            data=fuente.read(),
                            file_name=f"resultado_{archivo_subido.name}",
                            mime=(
                                "application/vnd.openxmlformats-officedocument"
                                ".spreadsheetml.sheet"
                                if ruta_salida.lower().endswith(".xlsx")
                                else "text/csv"
                            ),
                        )

                except Exception as exc:
                    st.error(f"Error al procesar el archivo: {exc}")

                finally:
                    # Limpieza garantizada, haya funcionado o no.
                    for temporal in (ruta_entrada, ruta_salida):
                        if os.path.exists(temporal):
                            try:
                                os.remove(temporal)
                            except OSError:
                                pass


# ------------------------------------------------------------------------------
# PESTANA 3: DIAGNOSTICO DE COORDENADAS
# ------------------------------------------------------------------------------
with tab_gps:
    from core import estilos as _est_gps

    st.markdown("## Diagnóstico de coordenadas")
    st.markdown(
        '<div class="subtitulo">Introduce un punto para cruzarlo con la capa '
        'municipal de tu área de estudio y con la Geodatabase.</div>',
        unsafe_allow_html=True)

    lat_defecto, lon_defecto = centro_del_area_de_estudio()
    _hay_area = (lat_defecto, lon_defecto) != (0.0, 0.0)

    with st.container(border=True):
        if _hay_area:
            st.markdown(
                _est_gps.indicador(
                    "Punto precargado", "ok",
                    f"centro del área de estudio ({lat_defecto}, "
                    f"{lon_defecto})"),
                unsafe_allow_html=True)
        else:
            # Estado vacio que dice QUE hacer, no solo que falta algo.
            st.markdown(
                _est_gps.indicador("Sin área de estudio", "tibio",
                                   "escribe las coordenadas a mano"),
                unsafe_allow_html=True)
            st.caption(
                "Genera el área en la pestaña **Área de Estudio** y este "
                "punto se precargará con su centro.")

        col_lat, col_lon = st.columns(2)
        latitud = col_lat.number_input("Latitud (WGS84)", value=lat_defecto,
                                       format="%.5f")
        longitud = col_lon.number_input("Longitud (WGS84)", value=lon_defecto,
                                        format="%.5f")

    if st.button("Consultar capas", type="primary",
                 use_container_width=True):
        with st.spinner("Proyectando el punto e intersectando polígonos..."):
            try:
                from database.gdb_connector import GDBConnector
                diagnostico = GDBConnector().consultar_linea_base_coordenada(
                    latitud, longitud
                )

                st.markdown("### Diagnóstico geográfico")

                def _mostrar(valor):
                    """
                    Formatea un dato del diagnóstico para la tarjeta HTML.

                    Args:
                        valor: Dato leído del diagnóstico; puede ser None.

                    Returns:
                        El valor tal cual, o `<i>no determinado</i>` en
                        cursiva. Nunca inventa un valor de relleno: un dato
                        ausente debe verse distinto de un dato real.
                    """
                    return valor if valor else "<i>no determinado</i>"

                st.markdown(f"""
                <div class="card">
                    <h4>📍 Ubicación</h4>
                    <p><b>Departamento:</b> {_mostrar(diagnostico.get('departamento'))}</p>
                    <p><b>Municipio:</b> {_mostrar(diagnostico.get('municipio'))}</p>
                    <p><b>Vereda:</b> {_mostrar(diagnostico.get('vereda'))}</p>
                    <p><b>Cuenca hidrográfica:</b> {_mostrar(diagnostico.get('cuenca'))}</p>
                </div>
                """, unsafe_allow_html=True)

                # --- Areas protegidas -----------------------------------------
                # Tres estados posibles, y la diferencia importa: rojo = hay
                # colisión, verde = se verificó y no hay, amarillo = NO se pudo
                # verificar. La versión anterior pintaba verde también en el
                # tercer caso, lo que equivalía a dar vía libre sin mirar.
                protegidas = diagnostico.get("areas_protegidas")
                if protegidas is None:
                    st.warning(
                        "⚠️ **Áreas protegidas (RUNAP): NO EVALUADO.** No hubo "
                        "capa disponible para verificarlo. Esto **no** "
                        "significa que el punto esté libre de restricciones."
                    )
                elif protegidas:
                    st.error(
                        "⛔ **COLISIÓN CON ÁREA PROTEGIDA (RUNAP):** "
                        + ", ".join(protegidas)
                    )
                else:
                    st.success(
                        "✅ **Áreas protegidas:** verificado, el punto está "
                        "fuera de áreas del RUNAP."
                    )

                # --- Paramos ---------------------------------------------------
                paramo = diagnostico.get("paramo")
                if paramo is None:
                    st.warning(
                        "⚠️ **Ecosistemas de páramo: NO EVALUADO.** No hubo "
                        "capa disponible para verificarlo."
                    )
                elif paramo:
                    st.error(
                        "⛔ **COLISIÓN CON ECOSISTEMA DE PÁRAMO.** "
                        "Restricciones ambientales muy severas."
                    )
                else:
                    st.success(
                        "✅ **Páramos:** verificado, el punto está fuera de "
                        "complejos de páramo delimitados."
                    )

                if diagnostico.get("advertencias"):
                    with st.expander("Notas técnicas del diagnóstico"):
                        for nota in diagnostico["advertencias"]:
                            st.write(f"- {nota}")

                if diagnostico.get("fuentes"):
                    with st.expander("¿De dónde salió cada dato?"):
                        st.json(diagnostico["fuentes"])

            except Exception as exc:
                st.error(f"Error al consultar la información espacial: {exc}")


# ------------------------------------------------------------------------------

# ------------------------------------------------------------------------------
# PESTANA 4: AREA DE ESTUDIO
# ------------------------------------------------------------------------------
# FLUJO, deliberadamente corto:
#
#     1. Indicas tu archivo.
#     2. Un boton lo analiza y descubre que contiene.
#     3. Eliges departamento y grupo de especies.
#     4. Generas.
#
# Todo lo tecnico (que columna del archivo mirar, cuantas filas muestrear, con
# que departamento de la cartografia emparejar) se resuelve solo y vive en
# "Opciones avanzadas", plegado. Solo hace falta abrirlo si la deteccion
with tab_estudio:
    from core import area_estudio as _ae
    from core import campo as _campo
    from core import estilos as _est_area

    st.markdown("## Área de estudio")
    st.markdown(
        '<div class="subtitulo">Define dónde trabaja el proyecto y revisa '
        'contra ese límite la información que llega de campo.</div>',
        unsafe_allow_html=True)

    # --- POR QUE ESTA PESTANA YA NO FILTRA BIODIVERSIDAD ---------------------
    # Antes recortaba una descarga de GBIF por departamento y grupo taxonomico.
    # Eso venia del trabajo anterior, sobre mariposas en todo el pais. Este
    # proyecto es UNA cuenca concreta: el filtro por departamento no se usaba,
    # y los menus ofrecian 33 departamentos y una lista de taxones que no
    # tenian nada que ver con lo que hay que entregar.
    #
    # Lo que si hace falta, y no existia, es lo de abajo: fijar el limite del
    # area y poder revisar contra el lo que llega de campo.

    # ==========================================================================
    # 1 - EL LIMITE DEL AREA
    # ==========================================================================
    _vigente = _ae.actual()

    with st.container(border=True):
        st.markdown("#### 1 · El límite del área")

        if _vigente["hay"]:
            st.markdown(
                _est_area.indicador(
                    f"Área fijada: {_vigente['nombre']}", "ok",
                    f"{_vigente['km2']} km²" if _vigente.get("km2") else ""),
                unsafe_allow_html=True)
            _c1, _c2, _c3 = st.columns(3)
            _c1.metric("Superficie", f"{_vigente.get('km2', 0):,.0f} km²"
                       .replace(",", "."))
            _c2.metric("Vértices", f"{_vigente.get('vertices', 0):,}"
                       .replace(",", "."))
            _c3.metric("Origen", _vigente.get("origen", "—")[:18] or "—")
            # La ruta se muestra relativa al proyecto: la de dentro del
            # contenedor (/app/...) no le sirve a quien abre QGIS en su equipo.
            _visible = _vigente["archivo"].replace("/app/", "")
            st.caption(
                f"Es el polígono contra el que se comprueba cada coordenada. "
                f"Está en `{_visible}` dentro del proyecto.")
        else:
            st.warning(
                "**No hay área de estudio fijada.** Sin ella no se puede "
                "comprobar si un punto de campo cae dentro: el sistema lo "
                "dirá, pero no podrá dar un veredicto."
            )

        with st.expander("Cambiar el área de estudio"):
            st.caption(
                "Sube el archivo que delimita el área: un **.kmz** o **.kml** "
                "de Google Earth, o un **.geojson**. Se leen todos sus "
                "polígonos y tú eliges cuál es el área."
            )
            _subido_area = st.file_uploader(
                "Archivo con el límite", type=["kmz", "kml", "geojson"],
                key="subir_area", label_visibility="collapsed")

            if _subido_area is not None:
                import tempfile as _tmp

                _carpeta = _tmp.mkdtemp(prefix="area_")
                _ruta_area = os.path.join(_carpeta, _subido_area.name)
                with open(_ruta_area, "wb") as _fh:
                    _fh.write(_subido_area.getbuffer())
                try:
                    _opciones = _ae.leer_poligonos(_ruta_area)
                except Exception as _exc:
                    st.error(f"No se pudo leer: {_exc}")
                    _opciones = []

                if _opciones:
                    st.caption(
                        f"{len(_opciones)} polígono(s) en el archivo. Cada uno "
                        f"significa una cosa distinta, así que la elección es "
                        f"tuya: cambia qué puntos de campo se aceptan.")
                    _etiquetas = [
                        f"{o['nombre']} — {o['km2']:,.2f} km²".replace(",", ".")
                        for o in _opciones]
                    _elegido = st.radio(
                        "¿Cuál es el área de estudio?", _etiquetas,
                        key="elegir_poligono")
                    if st.button("Fijar como área de estudio", type="primary"):
                        _res = _ae.fijar(_opciones[_etiquetas.index(_elegido)],
                                         origen=_subido_area.name)
                        # Del mismo archivo salen las veredas, los sitios y la
                        # red hídrica. Sin ellas el mapa queda con el contorno
                        # y nada dentro, que es exactamente como salía antes.
                        try:
                            _extra = _ae.exportar_referencias(_ruta_area)
                        except Exception:
                            _extra = {"puntos": 0, "lineas": 0}
                        st.success(
                            f"Área fijada: **{_res['nombre']}**, "
                            f"{_res['km2']} km².")
                        if _extra["puntos"] or _extra["lineas"]:
                            st.info(
                                f"Del mismo archivo se extrajeron "
                                f"**{_extra['puntos']} puntos** (veredas, "
                                f"sitios) y **{_extra['lineas']} líneas** "
                                f"(quebradas, rutas). QGIS las carga solo.")
                        st.rerun()

    # ==========================================================================
    # 2 - REVISAR INFORMACION DE CAMPO
    # ==========================================================================
    with st.container(border=True):
        st.markdown("#### 2 · Revisar información de campo")
        st.caption(
            "Sube lo que llegó de campo. Se revisa contra el área de estudio "
            "**antes** de guardarlo: si algo no cuadra, se dice qué y por qué."
        )

        _subido_campo = st.file_uploader(
            "Archivo de campo",
            type=["zip", "gpkg", "xlsx", "xls", "csv"],
            key="subir_campo", label_visibility="collapsed")

        if _subido_campo is None:
            st.caption(
                "Se admiten: **.zip** de QField (notas y recorridos), "
                "**.gpkg**, **.xlsx** y **.csv** con columnas de latitud y "
                "longitud. Nada sale de este equipo."
            )
        else:
            import tempfile as _tmp

            _carpeta_c = _tmp.mkdtemp(prefix="campo_ui_")
            _ruta_c = os.path.join(_carpeta_c, _subido_campo.name)
            with open(_ruta_c, "wb") as _fh:
                _fh.write(_subido_campo.getbuffer())

            st.markdown(
                _est_area.indicador(_subido_campo.name, "ok",
                                    _tamano_legible(_subido_campo.size)),
                unsafe_allow_html=True)

            with st.spinner("Leyendo y revisando…"):
                _lectura = _campo.leer(_ruta_c)
                _revision = _campo.revisar(_lectura)

            _r = _revision["resumen"]

            # --- Lo que trae dentro ------------------------------------------
            if _lectura["capas"]:
                with st.expander(f"Qué trae el archivo "
                                 f"({len(_lectura['capas'])} capa/s)"):
                    for _capa in _lectura["capas"]:
                        _epsg = (f"EPSG:{_capa['epsg']}"
                                 if _capa.get("epsg") else "sin sistema")
                        st.caption(
                            f"**{_capa['nombre']}** · {_capa['tipo']} · "
                            f"{_epsg} · {_capa['registros']} registros")
                        if _capa.get("columnas"):
                            st.caption("  campos: "
                                       + ", ".join(_capa["columnas"][:14]))

            # --- El veredicto -------------------------------------------------
            _m1, _m2, _m3 = st.columns(3)
            _m1.metric("Puntos leídos", _r.get("puntos", 0))
            _m2.metric("Dentro del área", _r.get("dentro", 0))
            _m3.metric("Fuera del área", _r.get("fuera", 0))

            if _revision["sirve"]:
                st.success(
                    "**La información es válida.** Todos los puntos caen "
                    "dentro del área de estudio y las coordenadas son "
                    "coherentes.")
            else:
                st.error("**La información NO se puede dar por buena.**")
                for _problema in _revision["problemas"]:
                    st.markdown(f"- {_problema}")

            for _aviso in _revision["avisos"]:
                st.warning(_aviso)

            # --- Punto por punto ----------------------------------------------
            if _revision["puntos"]:
                with st.expander("Punto por punto"):
                    _filas = []
                    for _p in _revision["puntos"][:60]:
                        _donde = ("dentro" if _p.get("dentro")
                                  else f"fuera · {_p.get('km_fuera', '?')} km")
                        _etiqueta = (_p.get("titulo") or _p.get("nota")
                                     or _p.get("capa") or "(sin título)")
                        _filas.append((
                            f"{_etiqueta[:44]}  ·  "
                            f"{_p['lat']:.5f}, {_p['lon']:.5f}", _donde))
                    st.markdown(_est_area.lista_documentos(_filas),
                                unsafe_allow_html=True)
                    if len(_revision["puntos"]) > 60:
                        st.caption(
                            f"… y {len(_revision['puntos']) - 60} más.")

            # --- Guardar o no --------------------------------------------------
            if _revision["sirve"]:
                if st.button("Guardar como validado", type="primary",
                             key="guardar_campo"):
                    _destino = os.path.join("data", "campo_validado")
                    os.makedirs(_destino, exist_ok=True)
                    _sello = time.strftime("%Y%m%dT%H%M%S")
                    _final = os.path.join(
                        _destino, f"{_sello}_{_subido_campo.name}")
                    with open(_final, "wb") as _fh:
                        _fh.write(_subido_campo.getbuffer())
                    st.success(f"Guardado en `{_final}`.")
            else:
                st.caption(
                    "No se ofrece guardar algo que no pasó la revisión. Si el "
                    "problema está en el archivo de origen, corrígelo allí y "
                    "vuelve a subirlo."
                )

            # --- Que opina el agente -------------------------------------------
            with st.expander("Pedirle al agente que lo interprete"):
                st.caption(
                    "Las comprobaciones de arriba las hace el código y dan "
                    "siempre el mismo resultado. El agente no decide si el "
                    "dato es válido: explica qué significa el veredicto."
                )
                if st.button("Interpretar", key="interpretar_campo"):
                    _detalle = [
                        f"Área de estudio: "
                        f"{_vigente.get('nombre', 'sin fijar')}.",
                        f"Archivo: {_subido_campo.name} ({_r.get('formato')}).",
                        f"Puntos: {_r.get('puntos')}, dentro "
                        f"{_r.get('dentro')}, fuera {_r.get('fuera')}.",
                    ]
                    _detalle += [f"Problema: {x}"
                                 for x in _revision["problemas"]]
                    _detalle += [f"Aviso: {x}" for x in _revision["avisos"]]
                    _pregunta = (
                        "Esta es la revisión automática de una salida de "
                        "campo. NO vuelvas a juzgar si es válida: eso ya está "
                        "decidido. Explica qué implica para el estudio de "
                        "impacto y qué conviene hacer.\n\n"
                        + "\n".join(_detalle))
                    _cod, _resp = _api("/agente/chat", "POST", {
                        "pregunta": _pregunta,
                        "sesion": st.session_state.session_id,
                        "guardar_memoria": False,
                    }, espera=120)
                    if _cod == 200:
                        st.markdown(_resp.get("respuesta", ""))
                    else:
                        st.error("No se pudo consultar al agente.")

    # ==========================================================================
    # 3 - LO QUE SE HA VALIDADO
    # ==========================================================================
    with st.container(border=True):
        st.markdown("#### 3 · Lo que ya se validó")
        _carpeta_val = os.path.join("data", "campo_validado")
        _guardados = (sorted(os.listdir(_carpeta_val), reverse=True)
                      if os.path.isdir(_carpeta_val) else [])
        if not _guardados:
            st.caption(
                "Todavía no se ha guardado ninguna salida de campo. Las que "
                "pasen la revisión aparecerán aquí, con la fecha en que "
                "entraron.")
        else:
            st.markdown(
                _est_area.lista_documentos([
                    (_n[16:], _n[:8]) for _n in _guardados[:20]]),
                unsafe_allow_html=True)
            st.caption(f"{len(_guardados)} salida(s) validada(s).")


# ------------------------------------------------------------------------------
# PESTANA 5: DRIVE Y ESTUDIO BAJO DEMANDA
# ------------------------------------------------------------------------------
# Dos cosas que antes solo se podian hacer a ciegas:
#
#   1. Saber si la conexion con la fuente documental funciona ANTES de lanzar
#      nada. Hasta ahora, si faltaba una credencial uno se enteraba cuando la
#      sincronizacion de las 02:00 fallaba de madrugada.
#   2. Mandar al agente a estudiar cuando uno quiera, sin esperar al reloj.
# ------------------------------------------------------------------------------

with tab_drive:
    # --------------------------------------------------------------------------
    # PESTANA: FUENTE DOCUMENTAL
    # --------------------------------------------------------------------------
    # Tres cosas, en este orden, porque es el orden en que se necesitan:
    #
    #   1. DE DONDE estudia el agente ahora mismo, y como cambiarlo.
    #   2. QUE hay en esa fuente.
    #   3. MANDARLO a estudiar.
    #
    # La conexion con Drive se configura aqui, no editando archivos. Antes
    # hacia falta abrir el .env, escribir tres variables, dejar el JSON en su
    # sitio y recrear los contenedores con --force-recreate (no `restart`, que
    # no basta y hace creer que el cambio no funciona).
    # --------------------------------------------------------------------------
    from core import conexion_drive, estilos, seguridad as _seg

    st.markdown("## Fuente documental")
    st.markdown(
        '<div class="subtitulo">De dónde saca el agente lo que sabe. '
        'Puede ser la carpeta del proyecto, una carpeta de Google Drive, o '
        'las dos a la vez.</div>',
        unsafe_allow_html=True)

    # --- Vuelta de Google con el codigo ------------------------------------
    # Google redirige aqui con ?code=…&state=…  La espera se anoto EN DISCO
    # antes de salir, porque al volver Streamlit puede abrir una sesion nueva y
    # la memoria de sesion nace vacia. La llave de `state` es lo que permite
    # recuperar con que cliente y para que carpeta se habia salido.
    #
    # Esto va ANTES de pintar nada: si la conexion se completa, lo que se
    # dibuje despues tiene que reflejar ya el estado nuevo.
    _codigo = st.query_params.get("code")
    _llave_vuelta = st.query_params.get("state")
    if _codigo and _llave_vuelta:
        _espera = conexion_drive.recoger_pendiente(_llave_vuelta)
        # El codigo vale una sola vez: se limpia la direccion para que
        # recargar la pagina no intente canjearlo otra vez.
        st.query_params.clear()
        if not _espera:
            st.error(
                "Llegó una autorización de Google, pero ya había caducado o "
                "no era de este equipo. Vuelve a pulsar «Autorizar en "
                "Google».")
        else:
            _redir = settings.api_url_navegador().replace(":8000", ":8501")
            if not _redir.endswith("/"):
                _redir += "/"
            with st.spinner("Terminando de conectar con Google…"):
                _canje = conexion_drive.canjear_codigo(
                    _espera["client_id"], _espera["client_secret"],
                    _codigo, _redir)
                if _canje["ok"]:
                    _res = conexion_drive.conectar_con_oauth(
                        _espera["client_id"], _espera["client_secret"],
                        _canje["refresh_token"], _espera["carpeta"])
                else:
                    _res = {"conectado": False, "motivo": _canje["motivo"]}
            _mostrar_resultado_drive(_res)

    codigo_f, fuente = _api("/corpus/fuente", espera=120)
    if codigo_f != 200:
        st.error(
            "No se pudo consultar la API ({}). Comprueba que el contenedor "
            "`copiloto-api` esté levantado.".format(
                fuente.get("error", codigo_f)))
        fuente = {}

    _drive = conexion_drive.estado()
    _nombre_fuente = fuente.get("fuente", "?")
    _listo = bool(fuente.get("listo"))

    # --- Resumen de un vistazo --------------------------------------------
    m1, m2, m3 = st.columns(3)
    m1.metric("Documentos visibles", fuente.get("documentos", 0))
    m2.metric("Carpetas", len(fuente.get("por_categoria", [])))
    m3.metric("Imágenes", fuente.get("imagenes", 0))

    st.markdown(
        estilos.indicador(
            f"Fuente activa: {_nombre_fuente}",
            "ok" if _listo else "mal",
            fuente.get("motivo", "")[:70]),
        unsafe_allow_html=True)

    st.markdown("#### Conexión con Google Drive")

    # --- Ya conectado -------------------------------------------------------
    if _drive["activo"] and _listo:
        with st.container(border=True):
            st.markdown(
                estilos.indicador("Drive conectado", "ok",
                                  f"carpeta {_drive['carpeta_id'][:14]}…"),
                unsafe_allow_html=True)
            st.caption(f"Cuenta de servicio: `{_drive['cuenta']}`")
            st.caption(
                "Los documentos se leen desde Drive y **se borran del equipo "
                "en cuanto se han leído**, incluso si la carga falla a mitad. "
                "Lo que queda aquí es el índice: números, no archivos."
            )
            cda, cdb = st.columns([1, 1])
            if cda.button("Volver a probar", use_container_width=True):
                _p = conexion_drive.probar_conexion(
                    conexion_drive.ruta_credencial(), _drive["carpeta_id"])
                if _p["conecta"]:
                    st.success(
                        f"Responde: {_p['archivos']} archivos y "
                        f"{_p['carpetas']} carpetas en el primer nivel.")
                else:
                    st.error(_p["motivo"])
            if cdb.button("Desconectar", use_container_width=True,
                          key="desconectar_drive"):
                conexion_drive.desconectar()
                st.session_state.aviso_drive = (
                    "Desconectado. El agente vuelve a estudiar de la carpeta "
                    "local. Lo ya descargado de Drive **no se ha borrado**.")
                st.rerun()

    # --- Sin conectar: el formulario ---------------------------------------
    else:
        with st.container(border=True):
            if _drive["hay_credencial"]:
                st.markdown(
                    estilos.indicador(
                        "Hay credenciales guardadas, pero Drive no está activo",
                        "tibio", _drive["cuenta"][:38]),
                    unsafe_allow_html=True)
            else:
                st.markdown(
                    estilos.indicador("Drive sin configurar", "tibio",
                                      "el agente estudia de la carpeta local"),
                    unsafe_allow_html=True)

            # --- El formulario -------------------------------------------
            # TRES campos cortos: ID de cliente, secreto y carpeta. Ni
            # archivos, ni JSON, ni bloques de texto que nadie entiende.
            _REDIRECCION = settings.api_url_navegador().replace(":8000", ":8501")
            if not _REDIRECCION.endswith("/"):
                _REDIRECCION += "/"

            with st.form("drive_oauth"):
                id_cliente = st.text_input(
                    "ID de cliente",
                    placeholder="616567408789-abc….apps.googleusercontent.com",
                    help="El campo «ID de cliente» de tu cliente de OAuth en "
                         "la consola de Google.",
                )
                secreto = st.text_input(
                    "Secreto del cliente",
                    type="password",
                    placeholder="GOCSPX-…",
                    help="El campo «Secreto del cliente». Úsalo con el botón "
                         "de copiar: la consola lo muestra oculto.",
                )
                carpeta_c = st.text_input(
                    "Carpeta de Drive",
                    placeholder="https://drive.google.com/…/folders/1A2b3C…",
                    help="La dirección de la carpeta tal como aparece en el "
                         "navegador.",
                )
                autorizar = st.form_submit_button(
                    "Autorizar en Google", type="primary",
                    use_container_width=True)

            if autorizar:
                _problema = conexion_drive.revisar_cliente(id_cliente, secreto)
                if _problema:
                    st.error(_problema)
                elif not conexion_drive.extraer_identificador(carpeta_c):
                    st.error(
                        "No reconozco la carpeta. Pega la dirección completa "
                        "de la carpeta en Drive.")
                else:
                    # Se anota EN DISCO con qué se está conectando, porque al
                    # salir a Google el navegador abandona la página y la
                    # memoria de sesión se pierde. La llave viaja a Google y
                    # vuelve con el código.
                    _llave = conexion_drive.anotar_pendiente(
                        id_cliente, secreto, carpeta_c)
                    # El enlace navega en ESTA pestana (`target="_self"`).
                    #
                    # `st.link_button` abre siempre una pestana nueva, y
                    # entonces Google redirige a esa otra: quedaban dos copias
                    # de la aplicacion abiertas, una a medio conectar y otra
                    # sin enterarse. Navegando en la misma, se sale a Google y
                    # se vuelve al mismo sitio.
                    _enlace = conexion_drive.url_de_autorizacion(
                        id_cliente, _REDIRECCION, _llave)
                    st.markdown(
                        f'<a href="{_enlace}" target="_self" '
                        f'class="boton-google">Continuar en Google →</a>',
                        unsafe_allow_html=True)
                    st.caption(
                        "Se abrirá la pantalla de permiso de Google **en esta "
                        "misma pestaña**. Acepta y volverás aquí con la "
                        "conexión hecha.")

            with st.expander("¿De dónde salen el ID y el secreto?"):
                st.markdown(
                    "En `console.cloud.google.com` → **Clientes** → crea un "
                    "**ID de cliente de OAuth** de tipo *Aplicación web*. La "
                    "ficha que queda muestra los dos datos.\n\n"
                    "**Importante:** en ese mismo cliente, el apartado *URIs "
                    "de redireccionamiento autorizados* tiene que contener "
                    "exactamente esta dirección, con la barra final:"
                )
                st.code(_REDIRECCION, language=None)
                st.markdown(
                    "Si no coincide letra por letra, Google rechaza la "
                    "autorización con `redirect_uri_mismatch`.\n\n"
                    "**El paso que más se olvida:** la cuenta de Google con "
                    "la que aceptes el permiso tiene que ser una que **vea la "
                    "carpeta**. El agente entra como esa cuenta, en modo solo "
                    "lectura."
                )

            with st.expander("¿Hace falta volver a autorizar cada día?"):
                st.markdown(
                    "No. Al aceptar, Google entrega un **token de refresco** "
                    "que no caduca mientras se use. El estudio programado lo "
                    "usa para pedir un acceso nuevo cada día, sin que haya "
                    "nadie delante.\n\n"
                    "Solo hay que repetirlo si revocas el permiso desde tu "
                    "cuenta de Google o si cambias el secreto del cliente."
                )

    if st.session_state.get("aviso_drive"):
        st.success(st.session_state.pop("aviso_drive"))

    # --- Seguridad del acceso ----------------------------------------------
    with st.expander("Seguridad del acceso"):
        _drive_seg = _seg.revisar_drive()
        cs1, cs2 = st.columns(2)
        with cs1:
            st.caption("**Permiso que pide el agente**")
            if "drive.readonly" in _drive_seg["alcance"]:
                st.markdown(
                    estilos.indicador("Solo lectura", "ok", "drive.readonly"),
                    unsafe_allow_html=True)
                st.caption(
                    "Con esta autorización el agente **no puede** crear, "
                    "modificar, mover ni borrar nada en Drive. No es una "
                    "promesa del código: es un candado del lado de Google."
                )
            else:
                st.error(f"Permiso declarado: `{_drive_seg['alcance']}`")
        with cs2:
            st.caption("**Identidad del agente**")
            if _drive_seg.get("cuenta"):
                st.code(_drive_seg["cuenta"], language=None)
                st.caption(
                    "Este correo **no es secreto**: es lo que hay que "
                    "compartir con quien administre la carpeta.")
            else:
                st.caption("Sin credenciales cargadas todavía.")

        for _aviso in _drive_seg["avisos"]:
            (st.error if _drive_seg["grave"] else st.warning)(_aviso)
        if _drive_seg["estado"] == "configurado" and not _drive_seg["avisos"]:
            st.caption("Credenciales bien resguardadas: ignoradas por git y "
                       "con permiso de solo lectura.")

        st.caption(
            "**Nunca se muestra** ni la clave privada ni la API Key, ni "
            "entera ni en parte. Un diagnóstico que enseña la credencial para "
            "«ayudar a comprobarla» la deja escrita en la pantalla, en las "
            "capturas y en los registros."
        )

    # --- Explorador documental ---------------------------------------------
    st.markdown("#### Lo que ve el agente")

    _por_categoria = fuente.get("por_categoria") or []
    _por_formato = fuente.get("por_formato") or []

    if not _por_categoria:
        st.info(
            "No hay documentos todavía. Conecta Google Drive arriba: el "
            "agente lee los archivos **directamente de ahí** y no conserva "
            "copias en este equipo."
        )
    else:
        _filtro = st.text_input(
            "Buscar carpeta", placeholder="escribe para filtrar…",
            label_visibility="collapsed")
        _texto = (_filtro or "").strip().lower()

        cc1, cc2 = st.columns([3, 2])
        with cc1:
            st.caption("**Por carpeta**")
            _visibles = [(c, n) for c, n in _por_categoria
                         if not _texto or _texto in c.lower()]
            if _visibles:
                st.markdown(estilos.lista_documentos(_visibles),
                            unsafe_allow_html=True)
            else:
                st.caption(f"Ninguna carpeta contiene «{_filtro}».")
        with cc2:
            st.caption("**Por formato**")
            st.markdown(
                estilos.lista_documentos(
                    [(_NOMBRES_FORMATO.get(f, "Archivo"), n, f)
                     for f, n in _por_formato[:10]]),
                unsafe_allow_html=True)

    st.divider()

    # --- Estudiar ahora ----------------------------------------------------
    st.markdown("#### Mandar al agente a estudiar")
    # No se nombra la hora aqui. Estuvo escrita "las 02:00" mucho despues de
    # que el cron corriera a otra hora, y un texto que dice una hora falsa es
    # peor que no decir ninguna: manda a buscar un problema donde no lo hay.
    # La hora vive en un sitio solo, el nodo de n8n.
    st.caption(
        "Lo mismo que hace el estudio programado, pero cuando tú quieras. "
        "Solo procesa lo que ha cambiado: si no hay nada nuevo, termina en "
        "segundos."
    )

    # --- Aviso de lo que se va a PERDER ------------------------------------
    # La indexacion borra del indice los documentos que ya no estan en la
    # fuente. Es lo correcto -- un indice que conserva lo que se borro miente
    # --, pero al CAMBIAR de fuente ese comportamiento normal se convierte en
    # un vaciado: ninguno de los documentos viejos esta en la nueva carpeta.
    #
    # Reconstruir 15.000 fichas cuesta una quincena con el plan gratuito, asi
    # que esto tiene que verse ANTES de pulsar, no descubrirse despues.
    _codigo_pre, _estado_pre = _api("/corpus/estado", espera=30)
    if _codigo_pre == 200:
        _en_indice = (_estado_pre.get("vectorial") or {}).get("fichas", 0)
        _en_fuente = fuente.get("documentos", 0)
        if _en_indice > 0 and _en_fuente == 0:
            st.warning(
                f"**Atención.** El índice tiene {_en_indice:,} fichas, pero la "
                f"fuente activa no ve ningún documento. Si estudias ahora, "
                f"esas fichas **se borrarán**: la indexación elimina lo que ya "
                f"no está en la fuente.\n\n"
                f"Conecta Drive primero, o devuelve los documentos a "
                f"`corpus/`."
                .replace(",", "."))
        elif _en_indice > 0 and _en_fuente > 0:
            st.caption(
                f"El índice tiene {_en_indice:,} fichas y la fuente ve "
                f"{_en_fuente} documentos. Lo que no esté en la fuente se "
                f"quitará del índice."
                .replace(",", "."))

    # El boton PRIMERO y la casilla debajo, no al lado. Puestos en la misma
    # fila parecian dos acciones distintas, y marcar la casilla recargaba la
    # pagina (Streamlit lo hace con cualquier control), asi que parecia que
    # ella sola hacia algo. No hace nada por si misma: solo decide COMO va a
    # estudiar el boton.
    lanzar = st.button("Estudiar ahora", type="primary")
    forzar = st.checkbox(
        "Volver a estudiarlo todo (opción del botón, no hace nada por sí sola)",
        help="Sin marcar, el botón solo lee lo que cambió en la fuente: lo "
             "demás ya está en el índice y se deja como está. Marcada, vuelve "
             "a leer TODO desde cero, incluso lo que no ha cambiado. Gasta "
             "mucha más cuota del modelo: úsala solo si sospechas que el "
             "índice quedó mal.",
    )
    if forzar:
        st.caption(
            "⚠️ Marcada: al pulsar **Estudiar ahora** se releerá todo desde "
            "cero, no solo lo que cambió.")

    st.caption(
        "Los archivos se bajan de Drive solo para leerlos, y **se borran del "
        "equipo en cuanto se terminan de leer**. Lo que queda es el índice: "
        "números, no copias de tus documentos."
    )

    if lanzar:
        codigo_s, respuesta = _api("/corpus/sincronizar", "POST",
                                   {"forzar": bool(forzar)}, espera=30)
        if codigo_s == 409:
            st.warning("Ya hay un estudio en marcha. Abajo verás cómo va.")
        elif codigo_s != 200:
            st.error("No se pudo arrancar: {}".format(
                respuesta.get("detail") or respuesta))
        else:
            # Una pasada nueva antes de leer el estado: sin esto, el bloque de
            # "cómo va" se dibujaba con lo de ANTES de pulsar y parecía que el
            # botón no hacía nada.
            st.rerun()

    codigo_e, estado_api = _api("/corpus/estado", espera=30)
    if codigo_e == 200:
        sincro = estado_api.get("sincronizacion", {})
        if sincro.get("ejecutando"):
            st.progress(min(float(sincro.get("avance") or 0.0), 1.0),
                        text=sincro.get("mensaje", "Estudiando…"))
            st.caption("Puedes irte a otra pestaña: el trabajo sigue en el "
                       "servidor.")
            if st.button("Actualizar progreso", key="refrescar_sincro"):
                st.rerun()
        elif sincro.get("error"):
            st.error("El último estudio falló: {}".format(sincro["error"]))
        else:
            st.caption(sincro.get("mensaje") or "Sin estudios en esta sesión.")

        _bloque = estado_api.get("corpus", {})
        _vect = estado_api.get("vectorial", {})
        if _bloque.get("categorias"):
            # "Fichas extraidas" contaba las de la CARPETA LOCAL, que con
            # Drive se vacia despues de leer: marcaba 0 al lado de 268 fichas
            # en el indice, y leido junto parecia que algo se habia perdido.
            # Se cuenta lo que hay de verdad: leidos enteros, catalogados por
            # encima, y el total que el agente puede consultar.
            _capas = len(_bloque.get("demasiado_grandes") or [])
            v1, v2, v3 = st.columns(3)
            v1.metric("Documentos leídos", _bloque.get("documentos", 0),
                      help="Se leyeron enteros y están en el índice.")
            v2.metric("Capas catalogadas", _capas,
                      help="Demasiado grandes para leerlas enteras. De cada "
                           "una se leyó solo la cabecera.")
            v3.metric("Fichas en el índice", f"{_vect.get('fichas', 0):,}"
                      .replace(",", "."),
                      help="Fragmentos que el agente puede consultar.")
            st.caption(_vect.get("motivo", ""))

        # Documentos que se ven pero NO entran al indice por tamano. Decirlo
        # con nombre y peso evita la busqueda a ciegas de por que "falta" algo.
        _grandes = (estado_api.get("corpus") or {}).get("demasiado_grandes") or []
        if _grandes:
            # El límite ya no son los megas sino las FICHAS, porque es lo que
            # cuesta: cada ficha es una llamada a la API de embeddings. Decir
            # "superan los 25 MB" mandaba a mirar el tamaño, que es sólo un
            # síntoma: 1 MB de CSV da 1.485 fichas y 1 MB de informe, decenas.
            _presu = (estado_api.get("corpus") or {}).get(
                "presupuesto_fichas", 2000)
            st.warning(
                f"{len(_grandes)} archivo(s) **no entran al índice**: darían "
                f"más de las {_presu:,} fichas de presupuesto por documento. "
                f"De cada uno se lee la cabecera, así que el agente sabe que "
                f"existen y qué columnas traen — pregúntale por su nombre."
                .replace(",", "."))
            st.markdown(
                estilos.lista_documentos(
                    [(_g["nombre"],
                      (f"{_g['fichas_estimadas']:,} fichas".replace(",", ".")
                       if _g.get("fichas_estimadas") else f"{_g['mb']} MB"))
                     for _g in _grandes]),
                unsafe_allow_html=True)
            st.caption(
                "El presupuesto se cambia con `PRESUPUESTO_FICHAS` en el "
                "`.env`. Súbelo si quieres que entren documentos más largos; "
                "cada ficha de más gasta cuota del modelo.")

        _fallos = (estado_api.get("corpus") or {}).get("fallos_de_lectura") or {}
        if _fallos:
            st.warning(
                f"{len(_fallos)} documento(s) no se pudieron leer. "
                f"Un fallo así no interrumpe el estudio, pero esos documentos "
                f"NO entran al índice.")
            for _nombre, _motivo in list(_fallos.items())[:6]:
                st.caption(f"· `{_nombre}` — {_motivo}")

    # --- Lo que no se puede hacer, dicho claramente ------------------------
    with st.expander("¿Puedo subir archivos A Drive desde aquí?"):
        st.markdown(
            "**No, y es a propósito.** El agente entra a Drive con permiso de "
            "**solo lectura** (`drive.readonly`). Con esa autorización no "
            "puede crear, modificar, mover ni borrar nada.\n\n"
            "Los archivos se suben a Drive desde Drive, como siempre, y el "
            "agente los recoge en el siguiente estudio.\n\n"
            "Para subir desde aquí haría falta permiso de escritura, y eso "
            "significa que el agente podría también borrar cosas de la "
            "carpeta compartida. Si lo quieres, hablémoslo antes de cambiarlo."
        )

    with st.expander("¿Por qué no aparecen mis Documentos de Google?"):
        st.markdown(
            "Los archivos **nativos de Google** (Documentos, Hojas de cálculo, "
            "Presentaciones) no tienen extensión en su nombre dentro de Drive, "
            "así que el filtro de formatos los descarta.\n\n"
            "Dos salidas:\n\n"
            "- **La fácil:** subirlos como `.pdf`, `.docx` o `.xlsx` en vez de "
            "crearlos dentro de Drive.\n"
            "- **La de código:** añadir la exportación por `mimeType`."
        )
