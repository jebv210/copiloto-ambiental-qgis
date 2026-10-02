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

ZONA INTOCABLE
--------------
El bloque "Integracion con QGIS" de la barra lateral (el boton
"Descargar Script PyQGIS") se mantiene exactamente como estaba, por peticion
expresa. No modificar sin avisar.
"""

import os
import subprocess

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

st.set_page_config(
    page_title="Copiloto Ambiental - Conesa & QGIS",
    page_icon="🌱",
    layout="wide",
    initial_sidebar_state="expanded",
)

st.markdown("""
<style>
    .main { background-color: #f9fbf9; }
    h1 {
        color: #1b5e20;
        font-family: 'Outfit', sans-serif;
        font-weight: 700;
    }
    .stButton>button {
        background-color: #2e7d32;
        color: white;
        border-radius: 8px;
        border: none;
        padding: 10px 24px;
        transition: 0.3s;
    }
    .stButton>button:hover {
        background-color: #1b5e20;
        color: white;
        box-shadow: 0 4px 8px rgba(0,0,0,0.1);
    }
    .card {
        padding: 20px;
        border-radius: 12px;
        background-color: white;
        color: #111111 !important;
        box-shadow: 0 4px 6px rgba(0,0,0,0.05);
        margin-bottom: 20px;
        border-left: 5px solid #2e7d32;
    }
    .card h4, .card p, .card span, .card b { color: #111111 !important; }

    /* ----------------------------------------------------------------------
       Limpieza del menu de Streamlit (los tres puntos, arriba a la derecha).

       El grueso ya lo quita `toolbarMode = "viewer"` en .streamlit/config.toml
       (boton Deploy, Rerun, Auto rerun, Clear cache). Pero "Print" y
       "Record screen" no se pueden desactivar por configuracion: son opciones
       fijas del menu y solo se ocultan con CSS.

       El selector de tema (System / Light / Dark) se conserva a proposito:
       son otros identificadores y no los tocamos.

       Los identificadores estan verificados sobre el DOM de Streamlit 1.61.1.
       Si una version futura los renombra, esto simplemente deja de aplicar y
       el menu vuelve a mostrarlos: no rompe nada.
       ---------------------------------------------------------------------- */
    [data-testid="stMainMenuItem-print"],
    [data-testid="stMainMenuItem-recordScreencast"] {
        display: none !important;
    }
</style>
""", unsafe_allow_html=True)


# ==============================================================================
# ESTADO DE LA SESION
# ==============================================================================
# Streamlit reejecuta el script entero en cada interaccion. Todo lo que deba
# sobrevivir a esa reejecucion vive en `st.session_state`.

_ESTADO_INICIAL = {
    # Chat
    "messages": [{
        "role": "assistant",
        "content": (
            "¡Hola! Soy tu copiloto ambiental. Para empezar, valida tu API Key "
            "en la barra lateral y genera tu área de estudio en la pestaña "
            "**⚙️ Área de Estudio**."
        ),
    }],
    # Conexion con la IA
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
    st.markdown("## 🌿 Copiloto Ambiental")
    st.subheader("Estado del Sistema")

    # --- Estado del servidor API ---------------------------------------------
    # La URL sale de la configuracion; antes estaba escrita en el codigo.
    URL_API = settings.api_url()
    try:
        respuesta = requests.get(f"{URL_API}/", timeout=2)
        if respuesta.status_code == 200:
            st.success(f"API de Agente: EN LÍNEA ({URL_API})")
        else:
            st.warning(f"API de Agente: respuesta inesperada "
                       f"({respuesta.status_code})")
    except requests.exceptions.RequestException:
        st.error("API de Agente: APAGADA (inicia `iniciar_servidor.bat`)")

    st.write("---")

    # --- Conexion con la IA ---------------------------------------------------
    st.subheader("🔑 Conexión con la IA")
    st.caption(
        "Pega tu API Key de Google Gemini. Se valida contra el servicio: "
        "verde si funciona, rojo si no."
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

    if st.button("🔌 Validar y conectar"):
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

    st.write("---")
    st.subheader("⚡ Integración con QGIS")
    st.write("Descarga el script de automatización para generar tus capas y puntos en QGIS Desktop:")

    # Leer y proveer descarga del script de carga de QGIS
    BASE_DIR = os.path.dirname(os.path.abspath(__file__))
    ruta_script = os.path.join(BASE_DIR, "cargar_capas_qgis.py")
    if os.path.exists(ruta_script):
        with open(ruta_script, "r", encoding="utf-8") as f:
            script_code = f.read()
        st.download_button(
            label="📥 Descargar Script PyQGIS",
            data=script_code,
            file_name="cargar_capas_qgis.py",
            mime="text/x-python"
        )
    else:
        st.warning("Archivo 'cargar_capas_qgis.py' no encontrado.")


# ==============================================================================
# PESTANAS
# ==============================================================================

tab_chat, tab_lote, tab_gps, tab_estudio = st.tabs([
    "💬 Chat con el Agente",
    "📊 Cálculo Masivo (Excel/CSV)",
    "📍 Diagnóstico de Coordenadas",
    "⚙️ Área de Estudio",
])


# ------------------------------------------------------------------------------
# PESTANA 1: CHAT
# ------------------------------------------------------------------------------
with tab_chat:
    st.subheader("Chat del Copiloto Ambiental")
    st.write(
        "Escribe en lenguaje natural sobre coordenadas, parámetros de Conesa "
        "o las especies de tu área de estudio."
    )

    if st.session_state.api_key_valida is not True:
        st.info(
            "Sin API Key validada, el agente funciona en **modo simulador**: "
            "reconoce la intención por palabras clave y ejecuta las mismas "
            "herramientas, pero no razona. Valida tu clave en la barra "
            "lateral para activar el modo IA."
        )

    for mensaje in st.session_state.messages:
        with st.chat_message(mensaje["role"]):
            st.write(mensaje["content"])

    if pregunta := st.chat_input(
        "Pregúntale al agente (ej.: ¿qué datos tienes cargados?)"
    ):
        st.session_state.messages.append({"role": "user", "content": pregunta})
        with st.chat_message("user"):
            st.write(pregunta)

        with st.chat_message("assistant"):
            with st.spinner("Razonando con las herramientas del sistema..."):
                try:
                    from agent.chatbot import ConesaAgent
                    # La clave escrita en la interfaz se le pasa al agente en
                    # cada consulta: asi el usuario puede cambiarla sin
                    # reiniciar la aplicacion.
                    agente = ConesaAgent(api_key=st.session_state.api_key)
                    respuesta = agente.responder(pregunta)
                except Exception as exc:
                    respuesta = f"Error al consultar el agente: {exc}"

                st.write(respuesta)
                st.session_state.messages.append(
                    {"role": "assistant", "content": respuesta}
                )


# ------------------------------------------------------------------------------
# PESTANA 2: CALCULO MASIVO
# ------------------------------------------------------------------------------
with tab_lote:
    st.subheader("Carga y procesamiento de matrices de impacto")
    st.write(
        "Sube tu matriz de valoraciones en Excel o CSV y el sistema calcula "
        "la importancia y la severidad de cada fila."
    )

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

    columna_izq, columna_der = st.columns([1, 2])
    with columna_izq:
        st.download_button(
            label="📥 Descargar plantilla (.csv)",
            data=plantilla.to_csv(index=False).encode("utf-8"),
            file_name="plantilla_valoracion_conesa.csv",
            mime="text/csv",
        )
    with columna_der:
        with st.expander("Ver los valores admitidos por parámetro"):
            st.code(ConesaValidator.descripcion_rangos())

    st.write("---")

    archivo_subido = st.file_uploader(
        "Elige tu archivo Excel (.xlsx) o CSV", type=["xlsx", "csv"]
    )

    if archivo_subido is not None:
        st.success(f"Archivo `{archivo_subido.name}` cargado en memoria.")

        if st.button("🚀 Calcular matriz de Conesa"):
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

                    st.markdown("### Resumen del procesamiento")
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
                    st.write("#### Resultados calculados")
                    st.dataframe(resultado)

                    with open(ruta_salida, "rb") as fuente:
                        st.download_button(
                            label="📥 Descargar resultados",
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
    st.subheader("Consulta espacial de línea base geográfica")
    st.write(
        "Introduce unas coordenadas para cruzarlas en tiempo real con la capa "
        "municipal de tu área de estudio y con la Geodatabase."
    )

    lat_defecto, lon_defecto = centro_del_area_de_estudio()
    if (lat_defecto, lon_defecto) == (0.0, 0.0):
        st.info(
            "Todavía no hay área de estudio generada, así que no hay un punto "
            "de referencia por defecto. Genera el área en la pestaña "
            "**⚙️ Área de Estudio** o escribe tus coordenadas a mano."
        )
    else:
        st.caption(
            f"Punto precargado: centro geométrico del área de estudio actual "
            f"({lat_defecto}, {lon_defecto})."
        )

    col_lat, col_lon = st.columns(2)
    latitud = col_lat.number_input("Latitud (WGS84)", value=lat_defecto,
                                   format="%.5f")
    longitud = col_lon.number_input("Longitud (WGS84)", value=lon_defecto,
                                    format="%.5f")

    if st.button("🔍 Consultar capas"):
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
    st.subheader("⚙️ Configuración del Área de Estudio")
    st.write(
        "Indica tu archivo de biodiversidad y elige el departamento y el grupo de especies "
        "para generar tu línea base y las capas correspondientes para QGIS."
    )

    # ==========================================================================
    # PASO 1: EL ARCHIVO
    # ==========================================================================
    st.markdown("#### 1️⃣ Tu archivo de biodiversidad")

    # Inicializar ruta en session_state si no existe (respaldo)
    if "ruta_dataset" not in st.session_state:
        from config import settings
        st.session_state.ruta_dataset = settings.ruta_dataset_biodiversidad() or ""

    col_ruta, col_boton = st.columns([5, 1])
    ruta_escrita = col_ruta.text_input(
        "Ruta del archivo (.csv / .tsv / .txt)",
        value=st.session_state.ruta_dataset,
        label_visibility="collapsed",
        placeholder=r"Ejemplo: C:\datos\descarga_gbif.csv",
        help="Ruta completa en tu disco. El archivo no se sube a ningún sitio: "
             "se lee directamente de donde está, por eso admite decenas de GB.",
    )
    if ruta_escrita != st.session_state.ruta_dataset:
        st.session_state.ruta_dataset = ruta_escrita

    if col_boton.button("📂 Buscar..."):
        seleccionada = abrir_selector_archivos()
        if seleccionada:
            st.session_state.ruta_dataset = seleccionada
            st.rerun()

    # ==========================================================================
    # PASO 2: FILTROS (DEPARTAMENTO Y GRUPO DE ESPECIES)
    # ==========================================================================
    st.markdown("#### 2️⃣ Elige los filtros para tu área de estudio")
    
    departamentos = [
        "ANTIOQUIA", "AMAZONAS", "ARAUCA", "ATLANTICO", "BOLIVAR", "BOYACA", 
        "CALDAS", "CAQUETA", "CASANARE", "CAUCA", "CESAR", "CHOCO", "CORDOBA", 
        "CUNDINAMARCA", "GUAINIA", "GUAVIARE", "HUILA", "LA GUAJIRA", "MAGDALENA", 
        "META", "NARIÑO", "NORTE DE SANTANDER", "PUTUMAYO", "QUINDIO", "RISARALDA", 
        "SAN ANDRES", "SANTANDER", "SUCRE", "TOLIMA", "VALLE DEL CAUCA", "VAUPES", 
        "VICHADA", "BOGOTA"
    ]
    
    grupos = [
        "Todos", 
        "Aves", 
        "Mariposas (Lepidoptera)", 
        "Plantas", 
        "Anfibios", 
        "Reptiles", 
        "Insectos"
    ]
    
    col_dpto, col_grupo = st.columns(2)
    dpto_select = col_dpto.selectbox("Selecciona el Departamento", departamentos, index=0)
    grupo_select = col_grupo.selectbox("Selecciona el Filtro de Especies (Grupo)", grupos, index=0)

    # ==========================================================================
    # PASO 3: GENERAR ÁREA
    # ==========================================================================
    st.markdown("#### 3️⃣ Generar")
    st.write(
        f"Se filtrará el archivo de biodiversidad original conservando **{dpto_select}** + **{grupo_select}**, "
        f"y se actualizarán las capas que lee QGIS."
    )

    if st.button("🚀 Generar área de estudio", type="primary"):
        if not st.session_state.ruta_dataset:
            st.error("Por favor, selecciona la ruta del archivo de biodiversidad primero.")
        else:
            progreso, caja, barra = barra_de_progreso()
            try:
                # 1. Resolver variaciones geográficas para el filtro
                dpto_normalized = dpto_select.title()
                variantes_dpto = {dpto_select, dpto_normalized}
                if dpto_select == "ANTIOQUIA":
                    variantes_dpto.update(["Antioquia", "Antioquía", "ANTIOQUÍA"])
                elif dpto_select == "BOYACA":
                    variantes_dpto.update(["Boyaca", "Boyacá", "BOYACÁ"])
                elif dpto_select == "BOGOTA":
                    variantes_dpto.update(["Bogota", "Bogotá", "BOGOTÁ"])
                valores_geo = list(variantes_dpto)

                # 2. Resolver variaciones taxonómicas para el filtro
                col_tax = None
                valores_tax = None
                if grupo_select == "Aves":
                    col_tax = "class"
                    valores_tax = ["Aves"]
                elif grupo_select == "Mariposas (Lepidoptera)":
                    col_tax = "order"
                    valores_tax = ["Lepidoptera"]
                elif grupo_select == "Plantas":
                    col_tax = "kingdom"
                    valores_tax = ["Plantae"]
                elif grupo_select == "Anfibios":
                    col_tax = "class"
                    valores_tax = ["Amphibia"]
                elif grupo_select == "Reptiles":
                    col_tax = "class"
                    valores_tax = ["Reptilia"]
                elif grupo_select == "Insectos":
                    col_tax = "class"
                    valores_tax = ["Insecta"]

                from core.dynamic_filter import ejecutar_filtrado_dinamico
                
                # Ejecutar el filtrado completo en caliente
                resultado = ejecutar_filtrado_dinamico(
                    ruta_dataset=st.session_state.ruta_dataset,
                    departamento=dpto_select,
                    columna_geografica="stateProvince",
                    valores_geograficos=valores_geo,
                    columna_taxonomica=col_tax,
                    valores_taxonomicos=valores_tax,
                    delimitador=None, # Autodetectar separador
                    generar_cartografia=True,
                    filas_max=0, # Procesar archivo completo
                    progreso=progreso
                )

                caja.empty()
                barra.empty()

                biodiversidad = resultado["biodiversidad"]
                cartografia = resultado["cartografia"]

                st.success("¡Área de estudio generada con éxito!")

                r1, r2, r3 = st.columns(3)
                r1.metric("Registros guardados", f"{biodiversidad['filas_guardadas']:,}".replace(",", "."))
                r2.metric("Filas revisadas", f"{biodiversidad['filas_leidas']:,}".replace(",", "."))
                r3.metric("Municipios en la capa", cartografia["municipios"] if cartografia else 0)

                if biodiversidad["filas_guardadas"] == 0:
                    st.warning("Ninguna fila cumplió ambos filtros a la vez.")
                else:
                    st.balloons()

                st.info(
                    "Siguiente paso: descarga el script desde la barra lateral "
                    "(**📥 Descargar Script PyQGIS**) y ejecútalo en la consola de Python de QGIS."
                )
            except Exception as exc:
                caja.empty()
                barra.empty()
                st.error(f"Error al generar el área de estudio: {exc}")

    # ==========================================================================
    # ESTADO ACTUAL DEL AREA GENERADA
    # ==========================================================================
    with st.expander("📋 ¿Qué área de estudio hay generada ahora mismo?"):
        from core.dynamic_filter import resumen_area_generada
        estado = resumen_area_generada()

        if estado["municipios_existe"]:
            st.write(
                f"**Cartografía:** {estado['municipios_total']} municipios de "
                f"{', '.join(estado['departamentos']) or 'departamento sin identificar'}."
            )
        else:
            st.write("**Cartografía:** no hay capa municipal generada.")

        if estado["biodiversidad_existe"]:
            st.write(
                f"**Biodiversidad:** {estado['registros']} registros en "
                f"`{settings.ARCHIVO_BIODIVERSIDAD_SALIDA}`."
            )
        else:
            st.write("**Biodiversidad:** no hay archivo filtrado generado.")
