# -*- coding: utf-8 -*-
"""
Sistema de diseno de la interfaz.
===============================================================================

QUE ES ESTO
-----------
La hoja de estilo completa de la aplicacion, en un solo sitio. No es una
coleccion de parches: define primero unas pocas variables (color, espaciado,
tipografia) y despues todo lo demas sale de ellas. Cambiar el verde de la marca
es cambiar una linea.

POR QUE ESTA FUERA DE `app.py`
------------------------------
Eran sesenta lineas de CSS incrustadas a mitad de un archivo de mil ochocientas.
Sacarlas permite leerlas, probarlas y cambiarlas sin pasear por la logica.

LOS PROBLEMAS QUE RESUELVE
--------------------------
1. EL ESTILO PELEABA CON EL TEMA.
   La primera version fijaba colores claros a mano: fondo `#f9fbf9`, tarjetas
   blancas, texto `#111111`. En modo oscuro -- que es como se usa -- eso daba
   tarjetas blancas sobre negro y texto ilegible. Aqui NINGUN color va fijo:
   todo sale de variables que cambian con el tema.

2. TODOS LOS BOTONES ERAN IGUAL DE IMPORTANTES.
   `.stButton>button` pintaba de verde fuerte y agrandaba CUALQUIER boton. Un
   "Quitar" secundario quedaba tan llamativo como "Estudiar ahora", y una fila
   de dos botones se convertia en dos bloques verdes de ancho completo. Sin
   jerarquia, todo grita y nada guia.

3. NO HABIA RITMO.
   Margenes y espaciados puestos a ojo, distintos en cada pestana. Aqui hay una
   escala (`--e1` a `--e5`) y todo se alinea a ella.

LO QUE NO SE HACE AQUI
----------------------
El TAMANO de las miniaturas de imagenes pegadas. Se intento con CSS y no
funciona: `st.markdown` con una etiqueta suelta no envuelve los elementos de
Streamlit que vienen detras, porque cada uno se dibuja en su propio contenedor.
La miniatura se calcula en el servidor (`_miniatura` en app.py).

SELECTORES
----------
Se usan atributos estables de Streamlit (`data-testid`, `data-baseweb`) y las
clases `st-key-<key>` que Streamlit anade a los elementos con `key`. Estan
verificados sobre Streamlit 1.61.1. Si una version futura los renombra, la
regla deja de aplicar y se ve el aspecto por defecto: se degrada, no se rompe.

===============================================================================
"""

#: Verde del proyecto. Es el unico color de marca; el resto se deriva de el o
#: del tema que tenga puesto quien mira.
VERDE = "#2e7d32"
VERDE_OSCURO = "#1b5e20"
VERDE_CLARO = "#4caf50"


def hoja() -> str:
    """
    Devuelve la hoja de estilo completa, lista para `st.markdown`.

    Returns:
        Bloque `<style>` con el sistema de diseno de la interfaz.
    """
    return """
<style>
/* ===========================================================================
   1. LAS VARIABLES DE LAS QUE SALE TODO LO DEMAS
   =========================================================================== */
:root {
    /* Marca */
    --verde:        #2e7d32;
    --verde-oscuro: #1b5e20;
    --verde-claro:  #4caf50;

    /* Superficies.
       -----------------------------------------------------------------------
       EN CLARO SON GRISES, NO VERDES. La primera version las hacia con verde
       translucido para "mantener la identidad", y sobre fondo blanco eso daba
       manchas verdosas por toda la pantalla: la pestana seleccionada parecia
       un aviso, los paneles parecian resaltados y la bibliografia parecia un
       mensaje de exito. El verde es el ACENTO -- bordes, foco, boton principal
       --, no el relleno.
       En oscuro si admite un verde muy tenue, porque ahi aporta separacion en
       vez de ruido (ver el bloque de abajo). */
    --panel:        rgba(0, 0, 0, 0.035);
    --panel-fuerte: rgba(0, 0, 0, 0.062);
    --borde:        rgba(0, 0, 0, 0.11);
    --sutil:        rgba(0, 0, 0, 0.07);
    --sombra:       0 1px 2px rgba(0, 0, 0, 0.06);

    /* Acento: lo unico verde en claro. */
    --acento-suave: rgba(46, 125, 50, 0.09);
    --acento-borde: rgba(46, 125, 50, 0.38);

    /* Estados */
    --exito:   #2e7d32;
    --aviso:   #b26a00;
    --error:   #c62828;

    /* Escala de espaciado. Todo se alinea a estos cinco valores: con margenes
       puestos a ojo, cada pestana acababa con su propio ritmo. */
    --e1: 0.25rem;
    --e2: 0.5rem;
    --e3: 0.85rem;
    --e4: 1.35rem;
    --e5: 2rem;

    /* Radios y tipografia */
    --radio:       9px;
    --radio-chico: 6px;
    --mono: ui-monospace, "SF Mono", "Cascadia Mono", "Roboto Mono",
            Consolas, monospace;
}

/* En oscuro hacen falta bordes MAS visibles y rellenos MENOS visibles: un
   relleno que se ve suave sobre blanco se convierte en mancha sobre negro. */
@media (prefers-color-scheme: dark) {
    :root {
        --panel:        rgba(76, 175, 80, 0.065);
        --panel-fuerte: rgba(76, 175, 80, 0.12);
        --borde:        rgba(76, 175, 80, 0.26);
        --sutil:        rgba(255, 255, 255, 0.10);
        --sombra:       0 1px 3px rgba(0, 0, 0, 0.4);
        --acento-suave: rgba(76, 175, 80, 0.14);
        --acento-borde: rgba(76, 175, 80, 0.5);
        --exito: #66bb6a;
        --aviso: #ffb74d;
        --error: #ef5350;
    }
}

/* Streamlit pone `data-theme` cuando alguien elige el tema a mano en el menu,
   en vez de seguir al sistema. Sin esto, elegir "Light" con el sistema en
   oscuro dejaba los colores de oscuro sobre fondo blanco. */
[data-theme="light"] {
    --panel:        rgba(0, 0, 0, 0.035);
    --panel-fuerte: rgba(0, 0, 0, 0.062);
    --borde:        rgba(0, 0, 0, 0.11);
    --sutil:        rgba(0, 0, 0, 0.07);
    --acento-suave: rgba(46, 125, 50, 0.09);
    --acento-borde: rgba(46, 125, 50, 0.38);
    --exito: #2e7d32;
    --aviso: #b26a00;
    --error: #c62828;
}

/* ===========================================================================
   2. LIENZO: APROVECHAR EL ANCHO SIN DESPARRAMAR
   =========================================================================== */
.block-container {
    padding-top: var(--e4);
    padding-bottom: var(--e5);
    max-width: 1500px;
}

/* ===========================================================================
   3. TIPOGRAFIA
   =========================================================================== */
h1 {
    font-weight: 700;
    letter-spacing: -0.025em;
    font-size: 1.85rem;
    margin-bottom: var(--e1);
    /* Sin `color` fijo: lo pone el tema. Antes era verde oscuro siempre y
       sobre fondo negro apenas se leia. */
}
h2 { font-weight: 650; letter-spacing: -0.015em; font-size: 1.35rem; }
h3 { font-weight: 650; font-size: 1.08rem; margin-top: var(--e4); }
h4 {
    font-size: 0.8rem;
    font-weight: 650;
    text-transform: uppercase;
    letter-spacing: 0.06em;
    opacity: 0.6;
    margin: var(--e4) 0 var(--e2) 0;
}
code, kbd, pre, .stCode { font-family: var(--mono); }
.stCode { border-radius: var(--radio-chico); font-size: 0.8rem; }

/* El pie bajo un titulo: explica sin competir. */
.subtitulo {
    opacity: 0.62;
    font-size: 0.88rem;
    margin-bottom: var(--e4);
}

/* ===========================================================================
   4. BOTONES: SOLO EL PRINCIPAL GRITA
   =========================================================================== */
.stButton > button, .stDownloadButton > button, .stFormSubmitButton > button {
    border-radius: var(--radio-chico);
    font-weight: 550;
    font-size: 0.875rem;
    padding: 0.4rem 0.85rem;
    min-height: 2.3rem;
    border: 1px solid var(--sutil);
    background: transparent;
    transition: border-color .15s, background-color .15s, transform .06s;
}
.stButton > button:hover, .stDownloadButton > button:hover,
.stFormSubmitButton > button:hover {
    border-color: var(--verde-claro);
    background: var(--panel);
}
.stButton > button:active { transform: translateY(1px); }
.stButton > button:focus-visible,
.stFormSubmitButton > button:focus-visible {
    outline: 2px solid var(--verde-claro);
    outline-offset: 2px;
}

/* El principal de cada zona, y solo ese, va relleno. */
.stButton > button[kind="primary"],
.stButton > button[data-testid="stBaseButton-primary"],
.stFormSubmitButton > button[kind="primaryFormSubmit"] {
    background: var(--verde);
    border-color: var(--verde);
    color: #ffffff;
}
.stButton > button[kind="primary"]:hover,
.stButton > button[data-testid="stBaseButton-primary"]:hover,
.stFormSubmitButton > button[kind="primaryFormSubmit"]:hover {
    background: var(--verde-oscuro);
    border-color: var(--verde-oscuro);
}

/* Un boton que destruye no puede parecerse a uno que guarda. */
.st-key-confirmar_borrado button,
.st-key-desconectar_drive button {
    border-color: rgba(198, 40, 40, 0.5) !important;
    color: var(--error) !important;
}
.st-key-confirmar_borrado button:hover,
.st-key-desconectar_drive button:hover {
    background: rgba(198, 40, 40, 0.1) !important;
}

/* ===========================================================================
   5. PESTANAS
   =========================================================================== */
.stTabs [data-baseweb="tab-list"] {
    gap: var(--e1);
    border-bottom: 1px solid var(--sutil);
    overflow-x: auto;          /* en portatiles estrechos se desplazan */
    scrollbar-width: none;
}
.stTabs [data-baseweb="tab-list"]::-webkit-scrollbar { display: none; }
.stTabs [data-baseweb="tab"] {
    border-radius: var(--radio-chico) var(--radio-chico) 0 0;
    padding: 0.5rem 0.95rem;
    font-weight: 550;
    font-size: 0.88rem;
    white-space: nowrap;
    transition: background-color .15s;
}
.stTabs [data-baseweb="tab"]:hover { background: var(--panel); }
.stTabs [aria-selected="true"] {
    background: var(--panel);
    border-bottom: 2px solid var(--verde) !important;
    font-weight: 650;
}
/* Streamlit dibuja ademas su propia barra deslizante bajo la pestana activa.
   Se tine del verde de la marca para que no aparezca el rojo por defecto, que
   en claro se veia como un error. */
.stTabs [data-baseweb="tab-highlight"],
.stTabs [data-baseweb="tab-border"] { background-color: var(--verde) !important; }
.stTabs [data-baseweb="tab-panel"] { padding-top: var(--e4); }

/* ===========================================================================
   6. BARRA LATERAL: COMPACTA Y AGRUPADA
   =========================================================================== */
section[data-testid="stSidebar"] { border-right: 1px solid var(--sutil); }
section[data-testid="stSidebar"] > div { padding-top: var(--e3); }

/* Los grupos se separan con aire, no con rayas: una linea cada tres elementos
   convierte la columna en una reja. */
section[data-testid="stSidebar"] h3 {
    font-size: 0.72rem;
    text-transform: uppercase;
    letter-spacing: 0.1em;
    opacity: 0.5;
    font-weight: 600;
    margin: var(--e4) 0 var(--e2) 0;
}
section[data-testid="stSidebar"] hr { margin: var(--e3) 0; opacity: 0.25; }
section[data-testid="stSidebar"] .stButton > button { font-size: 0.83rem; }

/* Indicador de estado: un punto y una linea, en vez de un bloque de color que
   ocupa un tercio de la barra para decir "conectado". */
.indicador {
    display: flex;
    align-items: center;
    gap: var(--e2);
    padding: var(--e2) var(--e3);
    border-radius: var(--radio-chico);
    background: var(--sutil);
    font-size: 0.82rem;
    margin-bottom: var(--e2);
}
.indicador .punto {
    width: 7px; height: 7px;
    border-radius: 50%;
    flex: 0 0 auto;
}
.indicador .punto.ok   { background: var(--exito); }
.indicador .punto.mal  { background: var(--error); }
.indicador .punto.tibio{ background: var(--aviso); }
.indicador .detalle { opacity: 0.55; font-size: 0.76rem; margin-left: auto; }

/* ===========================================================================
   7. CHAT
   =========================================================================== */
[data-testid="stChatMessage"] {
    border-radius: var(--radio);
    padding: 0.7rem 0.95rem;
    margin-bottom: var(--e2);
    background: var(--sutil);
}
/* El mensaje del agente se distingue con un filo verde, no con otro fondo:
   dos fondos compiten, un filo guia. */
[data-testid="stChatMessage"]:has([data-testid="stChatMessageAvatarAssistant"]) {
    border-left: 3px solid var(--acento-borde);
    background: var(--panel);
}
/* Ancho de lectura comodo: una linea de 160 caracteres cansa. */
[data-testid="stChatMessage"] [data-testid="stMarkdownContainer"] {
    max-width: 78ch;
    line-height: 1.6;
}
[data-testid="stChatMessage"] table {
    font-size: 0.85rem;
    border-collapse: collapse;
}
[data-testid="stChatMessage"] th, [data-testid="stChatMessage"] td {
    border: 1px solid var(--sutil);
    padding: 0.3rem 0.55rem;
}

/* Caja de escribir: que se vea que es el sitio donde se escribe. */
[data-testid="stChatInput"] {
    border-radius: var(--radio);
    border: 1px solid var(--sutil);
    transition: border-color .15s;
}
[data-testid="stChatInput"]:focus-within { border-color: var(--verde-claro); }

/* ===========================================================================
   8. TARJETAS, METRICAS Y GALERIA
   =========================================================================== */
div[data-testid="stVerticalBlockBorderWrapper"] {
    border-radius: var(--radio);
    border-color: var(--sutil) !important;
}
.card {
    padding: var(--e3) var(--e4);
    border-radius: var(--radio);
    background: var(--panel);
    border: 1px solid var(--borde);
    border-left: 3px solid var(--acento-borde);
    box-shadow: var(--sombra);
    margin-bottom: var(--e3);
    /* Sin `color` fijo: antes forzaba texto negro y en oscuro quedaba negro
       sobre negro. */
}
div[data-testid="stMetric"] {
    background: var(--panel);
    border: 1px solid var(--sutil);
    border-radius: var(--radio);
    padding: var(--e3);
}
div[data-testid="stMetricLabel"] {
    font-size: 0.74rem !important;
    text-transform: uppercase;
    letter-spacing: 0.05em;
    opacity: 0.6;
}

/* El tamano de la miniatura se decide en el servidor (ver el encabezado de
   este archivo). Aqui solo se le da aspecto de vista previa. */
[data-testid="stImage"] img {
    border-radius: var(--radio-chico);
    border: 1px solid var(--sutil);
    background: var(--panel);
}

/* El enlace que lleva a la pantalla de permiso de Google. Es un `<a>` y no un
   boton de Streamlit porque tiene que navegar en ESTA pestana: `st.link_button`
   abre siempre una nueva, y entonces Google volvia a esa otra dejando dos
   copias de la aplicacion abiertas. Se le da el aspecto del boton principal
   para que no se note la diferencia. */
a.boton-google {
    display: block;
    text-align: center;
    background: var(--verde);
    color: #ffffff !important;
    text-decoration: none !important;
    font-weight: 550;
    font-size: 0.875rem;
    padding: 0.55rem 0.85rem;
    border-radius: var(--radio-chico);
    margin-top: var(--e2);
    transition: background-color .15s;
}
a.boton-google:hover { background: var(--verde-oscuro); }
a.boton-google:focus-visible {
    outline: 2px solid var(--verde-claro);
    outline-offset: 2px;
}

/* ===========================================================================
   9. LISTA DE DOCUMENTOS
   =========================================================================== */
.lista-docs {
    display: flex;
    flex-direction: column;
    gap: var(--e1);
    margin: var(--e2) 0 var(--e3) 0;
}
.lista-docs .fila {
    display: flex;
    align-items: center;
    gap: var(--e2);
    padding: 0.32rem var(--e3);
    border-radius: var(--radio-chico);
    background: var(--sutil);
    font-size: 0.85rem;
}
.lista-docs .fila:hover { background: var(--panel-fuerte); }
.lista-docs .etiqueta { flex: 1 1 auto; }
.lista-docs .cuenta {
    font-variant-numeric: tabular-nums;
    font-weight: 650;
    opacity: 0.85;
}
.lista-docs .tipo {
    font-family: var(--mono);
    font-size: 0.74rem;
    padding: 0.08rem 0.4rem;
    border-radius: 4px;
    background: var(--acento-suave);
    opacity: 0.9;
    min-width: 3.2rem;
    text-align: center;
}

/* ===========================================================================
   10. FORMULARIOS
   =========================================================================== */
.stTextInput input, .stTextArea textarea, .stSelectbox div[data-baseweb="select"] {
    border-radius: var(--radio-chico);
    font-size: 0.88rem;
}
.stTextArea textarea { font-family: var(--mono); font-size: 0.8rem; }
.stTextInput input:focus, .stTextArea textarea:focus {
    border-color: var(--verde-claro);
}
div[data-testid="stForm"] {
    border: 1px solid var(--sutil);
    border-radius: var(--radio);
    padding: var(--e3);
}

/* ===========================================================================
   11. MENSAJES DE ESTADO
   =========================================================================== */
div[data-testid="stAlertContainer"] {
    border-radius: var(--radio-chico);
    font-size: 0.87rem;
    padding: var(--e2) var(--e3);
}

/* ===========================================================================
   12. PANTALLAS ESTRECHAS
   =========================================================================== */
@media (max-width: 1100px) {
    .block-container { padding-left: var(--e3); padding-right: var(--e3); }
    h1 { font-size: 1.5rem; }
    [data-testid="stChatMessage"] [data-testid="stMarkdownContainer"] {
        max-width: 100%;
    }
}

/* ===========================================================================
   13. LIMPIEZA DEL MENU DE STREAMLIT
   ---------------------------------------------------------------------------
   El grueso lo quita `toolbarMode = "viewer"` en .streamlit/config.toml. Pero
   "Print" y "Record screen" no se pueden desactivar por configuracion: son
   opciones fijas del menu y solo se ocultan con CSS. El selector de tema
   (System / Light / Dark) se conserva a proposito.
   =========================================================================== */
[data-testid="stMainMenuItem-print"],
[data-testid="stMainMenuItem-recordScreencast"] { display: none !important; }

/* El boton oculto que usa el pegado de imagenes para forzar un repintado.
   Un boton con `display:none` sigue respondiendo a `.click()`. */
.st-key-boton_refrescar_pegado { display: none !important; }
</style>
"""


def indicador(texto: str, estado: str = "ok", detalle: str = "") -> str:
    """
    Compone un indicador de estado compacto.

    Sustituye a los bloques de color que ocupaban un tercio de la barra
    lateral para decir "conectado". Un punto de color y una linea de texto
    dicen lo mismo en una decima parte del espacio.

    Args:
        texto: Lo que se afirma, por ejemplo "API en linea".
        estado: "ok", "mal" o "tibio".
        detalle: Texto secundario, alineado a la derecha.

    Returns:
        HTML listo para `st.markdown(..., unsafe_allow_html=True)`.
    """
    import html

    clase = estado if estado in ("ok", "mal", "tibio") else "tibio"
    extra = (f'<span class="detalle">{html.escape(detalle)}</span>'
             if detalle else "")
    return (f'<div class="indicador"><span class="punto {clase}"></span>'
            f'<span>{html.escape(texto)}</span>{extra}</div>')


def lista_documentos(filas: list) -> str:
    """
    Compone una lista compacta de documentos o categorias.

    Args:
        filas: Lista de tuplas `(etiqueta, cuenta)` o
            `(etiqueta, cuenta, tipo)`.

    Returns:
        HTML listo para `st.markdown(..., unsafe_allow_html=True)`.
    """
    import html

    partes = ['<div class="lista-docs">']
    for fila in filas:
        etiqueta, cuenta = fila[0], fila[1]
        tipo = fila[2] if len(fila) > 2 else ""
        marca = (f'<span class="tipo">{html.escape(str(tipo))}</span>'
                 if tipo else "")
        partes.append(
            f'<div class="fila">{marca}'
            f'<span class="etiqueta">{html.escape(str(etiqueta))}</span>'
            f'<span class="cuenta">{html.escape(str(cuenta))}</span></div>')
    partes.append("</div>")
    return "".join(partes)
