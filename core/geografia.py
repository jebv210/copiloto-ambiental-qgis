# -*- coding: utf-8 -*-
"""
Inventario de lo que el corpus tiene de aprovechable en QGIS.
===============================================================================

QUE RESPONDE ESTE MODULO
------------------------
Una sola pregunta, la que no se podia contestar antes:

    "De lo que hay cargado en el corpus, que me sirve para hacer un mapa?"

Y la contesta con tres cosas separadas, porque no son lo mismo:

    1. COORDENADAS. Lo unico que permite situar algo en un mapa por si solo.
    2. TOPONIMOS (cuencas, veredas, quebradas). No situan nada, pero se pueden
       ENGANCHAR por nombre a una capa que si tenga geometria.
    3. SUPERFICIES Y SISTEMA DE REFERENCIA. Hacen falta para que las cifras y
       las reproyecciones salgan bien.

NADA ESTA QUEMADO
-----------------
Este modulo no sabe que el proyecto trata de San Carlos, ni de Antioquia, ni de
mariposas. No hay una sola lista de municipios, departamentos ni cuencas
escrita a mano. Busca FORMAS de dato geografico y deja que los nombres propios
salgan del texto que haya cargado. Si manana el corpus cambia de tema, este
modulo informa del tema nuevo sin tocar una linea.

POR QUE VALIDA EN VEZ DE CONTAR COINCIDENCIAS
---------------------------------------------
Se midio sobre el corpus real y la version ingenua mentia de forma peligrosa:

  - Buscando "dos numeros decimales seguidos" salian 70 fichas con
    coordenadas. Ninguna lo era. Eran indices de libro ("146, 181 Eueides
    isabella em 20, 21, 116..."), tablas de mortalidad con coma decimal
    ("0,107 0,127 0,127") y desviaciones tipicas ("0,102 +- 0,001").
  - Buscando "cinco digitos" para codigos DANE salian 710 fichas. Eran ISBN
    ("978-958-56739-5-3"), rangos de pagina ("22657-22663") y DOI.

Decir "tengo 70 fichas con coordenadas" cuando no hay ninguna es peor que
decir "no tengo": el mapa sale con puntos y parece correcto. Asi que aqui una
coordenada solo cuenta si viene MARCADA: o lleva su hemisferio (N/S/E/W/O), o
tiene una palabra de coordenada al lado. Lo que no esta marcado no se cuenta.

===============================================================================
"""

import re
import unicodedata

from core import corpus


# ==============================================================================
# 1. PATRONES
# ==============================================================================
# Son FORMAS, no lugares. Ni un nombre propio en todo el bloque.

#: Grados, minutos y segundos con su hemisferio: `5 19'24.3"N`. Se fia solo
#: porque la letra del hemisferio hace de firma: un indice de libro no la lleva.
DMS = re.compile(
    u"(\\d{1,3})\\s*[\u00b0\u00ba]\\s*(\\d{1,2})\\s*['\u2032\u00b4]"
    u"\\s*([\\d.,]+)?\\s*[\"\u2033\u201d]?\\s*([NSEWO])",
    re.IGNORECASE)

#: Par de decimales. Por si solo NO vale: pide una palabra de coordenada cerca.
PAR_DECIMAL = re.compile(
    r"(-?\d{1,2}[.,]\d{4,})\s*[,;/]\s*(-?\d{1,3}[.,]\d{4,})")

#: Las palabras que convierten un par de numeros en una coordenada creible.
#: Se comparan sin acentos ni mayusculas.
MARCAS_COORDENADA = (
    "coordenada", "coordenadas", "latitud", "longitud", "lat", "lon", "lng",
    "gps", "georreferencia", "georreferenciacion", "utm", "decimalgrados",
    "decimallatitude", "decimallongitude",
)

#: Cuanto texto se mira alrededor de un par de decimales buscando la marca.
VENTANA_MARCA = 90

#: Sistema de referencia. Sin esto, una reproyeccion es una apuesta.
CRS = re.compile(r"EPSG\s*:?\s*(\d{4,5})|(MAGNA[- ]?SIRGAS)|(WGS\s*84)", re.I)

#: Toponimos: la palabra que clasifica, y detras su nombre propio. El nombre
#: NO esta en el codigo: se recorta del texto.
TOPONIMOS = {
    # OJO: este patron NO lleva re.IGNORECASE, a proposito. Con IGNORECASE el
    # [A-Z] del nombre propio pasaba a aceptar minusculas y el recorte se
    # tragaba la frase entera: salian "cuenca Cauca en el municipio" y
    # "cuenca Amoya representan el". Las variantes de la palabra clave van
    # escritas en la propia alternancia.
    "cuenca": re.compile(
        u"\\b((?:[Mm]icro|[Ss]ub)?[Cc]uenca)\\s+(?:[Hh]idrografica\\s+)?"
        u"(?:del\\s+|de\\s+la\\s+|de\\s+|rio\\s+|r\u00edo\\s+)*"
        u"([A-Z\u00c1\u00c9\u00cd\u00d3\u00da\u00d1][\\w\u00e1\u00e9\u00ed\u00f3\u00fa\u00f1]+"
        u"(?:\\s+[A-Z\u00c1\u00c9\u00cd\u00d3\u00da\u00d1][\\w\u00e1\u00e9\u00ed\u00f3\u00fa\u00f1]+){0,3})",
        re.UNICODE),
    "vereda": re.compile(
        u"\\b(vereda|corregimiento)\\s+(?:de\\s+|el\\s+|la\\s+)*"
        u"([A-Z\u00c1\u00c9\u00cd\u00d3\u00da\u00d1][\\w\u00e1\u00e9\u00ed\u00f3\u00fa\u00f1]+"
        u"(?:\\s+[A-Z\u00c1\u00c9\u00cd\u00d3\u00da\u00d1][\\w\u00e1\u00e9\u00ed\u00f3\u00fa\u00f1]+){0,2})",
        re.UNICODE),
    "drenaje": re.compile(
        u"\\b(quebrada|ca\u00f1ada|ca\u00f1o|r\u00edo|rio)\\s+(?:el\\s+|la\\s+|los\\s+|las\\s+)*"
        u"([A-Z\u00c1\u00c9\u00cd\u00d3\u00da\u00d1][\\w\u00e1\u00e9\u00ed\u00f3\u00fa\u00f1]+"
        u"(?:\\s+[A-Z\u00c1\u00c9\u00cd\u00d3\u00da\u00d1][\\w\u00e1\u00e9\u00ed\u00f3\u00fa\u00f1]+){0,2})",
        re.UNICODE),
}

#: Superficies declaradas, con su unidad.
SUPERFICIE = re.compile(
    u"([\\d][\\d.,]*)\\s*(hect\u00e1reas?|hectareas?|ha|km\\s*2|km\u00b2|m2|m\u00b2)\\b",
    re.IGNORECASE)

#: Capas de SIG nombradas en el texto. La pista para saber que cartografia
#: existe aunque el proyecto todavia no la tenga.
#: El nombre suelto que va detras de "capa" tiene que PARECER un identificador:
#: llevar al menos un digito o un guion bajo. Sin esa exigencia, la frase "la
#: capa externa del ala" producia una capa de SIG llamada "externa".
CAPA_SIG = re.compile(
    r"\b([A-Za-z0-9_]+\.shp)\b"
    r"|\bcapa\s+([A-Za-z][A-Za-z0-9_]*[0-9_][A-Za-z0-9_]*)\b"
    r"|\b(geodatabase)\b", re.IGNORECASE)

#: Palabras que delatan que un numero de cinco cifras NO es un codigo DANE.
#: Se comprobo: sin este filtro salian 710 fichas con "codigos DANE" que eran
#: ISBN, rangos de pagina de revistas y DOI.
RUIDO_NUMERICO = ("isbn", "issn", "doi", "pp.", "pags", "paginas", "vol",
                  "http", "www", "academy of sciences", "springer")


def _sin_acentos(texto: str) -> str:
    """
    Quita acentos y pasa a minusculas, para comparar sin sorpresas.

    Args:
        texto: Texto de entrada.

    Returns:
        El mismo texto en minusculas y sin marcas diacriticas.
    """
    descompuesto = unicodedata.normalize("NFD", texto or "")
    return "".join(c for c in descompuesto
                   if unicodedata.category(c) != "Mn").lower()


# ==============================================================================
# 2. COORDENADAS: SOLO LAS QUE VIENEN MARCADAS
# ==============================================================================

def coordenadas_en(texto: str) -> list:
    """
    Saca de un texto las coordenadas que se pueden dar por buenas.

    Acepta dos formas:

      - Grados, minutos y segundos CON hemisferio. La letra N/S/E/W/O hace de
        firma, asi que no hace falta mas contexto.
      - Par de decimales, pero SOLO si hay una palabra de coordenada a menos de
        `VENTANA_MARCA` caracteres. Sin esa marca se descarta: es lo que
        separaba las coordenadas de verdad de los indices de libro y las tablas
        de mortalidad con coma decimal.

    Args:
        texto: Texto de una ficha.

    Returns:
        Lista de diccionarios con `forma` ("gms" o "decimal"), `texto` (lo que
        aparecia tal cual) y `marca` (la palabra que la respaldo, si la hubo).
    """
    salida = []
    if not texto:
        return salida

    for m in DMS.finditer(texto):
        salida.append({"forma": "gms", "texto": m.group(0).strip(),
                       "marca": "hemisferio " + m.group(4).upper()})

    plano = _sin_acentos(texto)
    for m in PAR_DECIMAL.finditer(texto):
        ini = max(0, m.start() - VENTANA_MARCA)
        alrededor = plano[ini:m.end() + VENTANA_MARCA]
        marca = next((p for p in MARCAS_COORDENADA
                      if re.search(r"\b" + re.escape(p) + r"\b", alrededor)),
                     None)
        if marca:
            salida.append({"forma": "decimal", "texto": m.group(0).strip(),
                           "marca": marca})
    return salida


def a_decimal(texto: str):
    """
    Pasa una coordenada en grados-minutos-segundos a grados decimales.

    Args:
        texto: La coordenada tal como aparecia, por ejemplo `6 21'46" N`.

    Returns:
        Tupla `(valor, eje)` donde `eje` es "lat" o "lon", o `(None, None)` si
        no se pudo interpretar. El signo ya viene aplicado: sur y oeste
        negativos.
    """
    m = DMS.search(texto or "")
    if not m:
        return None, None
    grados = float(m.group(1))
    minutos = float(m.group(2) or 0)
    segundos = float((m.group(3) or "0").replace(",", "."))
    hemisferio = m.group(4).upper()

    valor = grados + minutos / 60.0 + segundos / 3600.0
    if hemisferio in ("S", "W", "O"):
        valor = -valor
    eje = "lat" if hemisferio in ("N", "S") else "lon"
    return valor, eje


def emparejar(coordenadas: list) -> list:
    """
    Junta coordenadas sueltas en puntos utilizables.

    En los documentos la latitud y la longitud vienen seguidas pero en dos
    trozos distintos (`6 21'46" N` y luego `75 36'18" W`). Un mapa necesita las
    dos juntas, asi que se emparejan por orden de aparicion dentro del mismo
    documento.

    Args:
        coordenadas: Lo que devuelve `inventario()` en su clave `coordenadas`.

    Returns:
        Lista de diccionarios con `lat`, `lon`, `fuente` y `categoria`. Las
        coordenadas que se quedan sin pareja no se devuelven: media coordenada
        no situa nada.
    """
    puntos = []
    pendiente = {}
    for coord in coordenadas:
        valor, eje = a_decimal(coord["texto"])
        if valor is None:
            continue
        fuente = coord.get("fuente", "?")
        otro = "lon" if eje == "lat" else "lat"
        guardada = pendiente.get(fuente, {})

        if otro in guardada:
            punto = {eje: valor, otro: guardada.pop(otro),
                     "fuente": fuente,
                     "categoria": coord.get("categoria", "?")}
            puntos.append(punto)
        else:
            guardada[eje] = valor
        pendiente[fuente] = guardada
    return puntos


def dentro_del_area(puntos: list):
    """
    Dice cuantos puntos caen dentro del area de estudio generada.

    No sabe cual es el area de estudio: la lee de la capa que haya generado la
    interfaz web, cuya ruta sale de la configuracion. Si esa capa no existe
    todavia, no se inventa nada y lo dice.

    Args:
        puntos: Lo que devuelve `emparejar()`.

    Returns:
        Tupla `(dentro, total, motivo)`. Si no se pudo comprobar, `dentro` es
        None y `motivo` explica por que.
    """
    import os

    try:
        from config import settings
        ruta = settings.ruta_municipios_salida()
    except Exception as exc:
        return None, len(puntos), f"no se pudo leer la configuracion ({exc})"

    if not ruta or not os.path.exists(ruta):
        return None, len(puntos), ("todavia no hay capa del area de estudio "
                                   "generada, asi que no hay con que comparar")
    try:
        import json
        from shapely.geometry import shape, Point
    except ImportError:
        return None, len(puntos), "falta shapely para comprobar la pertenencia"

    try:
        with open(ruta, encoding="utf-8") as f:
            capa = json.load(f)
        formas = [shape(r["geometry"]) for r in capa.get("features", [])]
    except Exception as exc:
        return None, len(puntos), f"no se pudo leer la capa ({exc})"

    if not formas:
        return None, len(puntos), "la capa del area de estudio esta vacia"

    dentro = sum(1 for p in puntos
                 if any(g.contains(Point(p["lon"], p["lat"])) for g in formas))
    return dentro, len(puntos), ""


def _es_ruido(texto: str, posicion: int) -> bool:
    """
    Dice si un numero esta rodeado de senales de no ser un codigo territorial.

    Args:
        texto: Texto completo de la ficha.
        posicion: Donde empieza el numero.

    Returns:
        Verdadero si alrededor hay un ISBN, un DOI, un rango de paginas o
        similar.
    """
    ini = max(0, posicion - 70)
    alrededor = _sin_acentos(texto[ini:posicion + 40])
    return any(r in alrededor for r in RUIDO_NUMERICO)


# ==============================================================================
# 3. EL INVENTARIO
# ==============================================================================

def inventario(fichas: list = None) -> dict:
    """
    Recorre el corpus y resume que hay de aprovechable para un mapa.

    Args:
        fichas: Fichas a revisar. Si no se dan, se carga el corpus completo.

    Returns:
        Diccionario con estas claves:

        - `fichas_revisadas`: cuantas se miraron.
        - `coordenadas`: lista de coordenadas validadas, con su procedencia.
        - `cuencas`, `veredas`, `drenajes`: nombres propios encontrados, cada
          uno con las categorias del corpus donde aparece.
        - `superficies`: superficies declaradas con su unidad.
        - `sistemas_referencia`: los CRS nombrados.
        - `capas_sig`: capas de SIG mencionadas.
        - `por_categoria`: que aporta cada categoria del corpus.
    """
    if fichas is None:
        fichas = corpus.cargar()["fichas"]

    coordenadas = []
    hallados = {clave: {} for clave in TOPONIMOS}
    superficies = []
    sistemas = {}
    capas = {}
    por_categoria = {}

    for ficha in fichas:
        texto = ficha.get("texto", "") or ""
        fuente = ficha.get("fuente") or ficha.get("documento") or "?"
        categoria = ficha.get("categoria", "?")
        resumen_cat = por_categoria.setdefault(
            categoria, {"fichas": 0, "coordenadas": 0, "toponimos": 0,
                        "superficies": 0, "capas": 0})
        resumen_cat["fichas"] += 1

        for coord in coordenadas_en(texto):
            coord.update({"fuente": fuente, "categoria": categoria})
            coordenadas.append(coord)
            resumen_cat["coordenadas"] += 1

        for clave, patron in TOPONIMOS.items():
            for m in patron.finditer(texto):
                nombre = " ".join(m.group(2).split())
                if len(nombre) < 3:
                    continue
                registro = hallados[clave].setdefault(
                    nombre, {"tipo": m.group(1).lower(), "categorias": set(),
                             "fuentes": set()})
                registro["categorias"].add(categoria)
                registro["fuentes"].add(fuente)
                resumen_cat["toponimos"] += 1

        for m in SUPERFICIE.finditer(texto):
            if _es_ruido(texto, m.start()):
                continue
            superficies.append({"valor": m.group(1), "unidad": m.group(2),
                                "fuente": fuente, "categoria": categoria})
            resumen_cat["superficies"] += 1

        for m in CRS.finditer(texto):
            etiqueta = next(g for g in m.groups() if g)
            sistemas.setdefault(etiqueta.upper(), set()).add(fuente)

        for m in CAPA_SIG.finditer(texto):
            etiqueta = next(g for g in m.groups() if g)
            capas.setdefault(etiqueta, set()).add(fuente)
            resumen_cat["capas"] += 1

    def ordenar(mapa):
        """
        Pasa los conjuntos a listas ordenadas, para poder serializar.

        Args:
            mapa: Diccionario {nombre: {tipo, categorias, fuentes}} con
                conjuntos dentro.

        Returns:
            Lista de diccionarios ordenada por nombre, ya sin conjuntos.
        """
        return [
            {"nombre": nombre, "tipo": datos["tipo"],
             "categorias": sorted(datos["categorias"]),
             "fuentes": sorted(datos["fuentes"])[:3]}
            for nombre, datos in sorted(mapa.items())
        ]

    return {
        "fichas_revisadas": len(fichas),
        "coordenadas": coordenadas,
        "cuencas": ordenar(hallados["cuenca"]),
        "veredas": ordenar(hallados["vereda"]),
        "drenajes": ordenar(hallados["drenaje"]),
        "superficies": superficies,
        "sistemas_referencia": {k: sorted(v)[:3] for k, v in sistemas.items()},
        "capas_sig": {k: sorted(v)[:3] for k, v in capas.items()},
        "por_categoria": por_categoria,
    }


# ==============================================================================
# 4. EL INFORME QUE LEE EL AGENTE
# ==============================================================================
# El orden no es casual: primero CUENCA, despues municipio. Es el orden de
# importancia que fijo quien dirige el proyecto.

def informe(limite_ejemplos: int = 12) -> str:
    """
    Redacta en castellano llano que se puede llevar a QGIS y que no.

    Args:
        limite_ejemplos: Cuantos nombres propios listar de cada clase.

    Returns:
        Texto listo para mostrar, con un veredicto explicito al final.
    """
    if not corpus.esta_disponible():
        return ("No hay corpus cargado, asi que no hay nada que inventariar. "
                "Pon documentos en la carpeta del corpus y vuelve a preguntar.")

    inv = inventario()
    lineas = []
    an = lineas.append

    an("DATOS DEL CORPUS APROVECHABLES EN QGIS")
    an(f"  Fichas revisadas: {inv['fichas_revisadas']}")
    an("")

    # --- 1. Lo unico que situa algo en un mapa por si solo -----------------
    coords = inv["coordenadas"]
    an("1. COORDENADAS (lo unico que situa algo por si solo)")
    if not coords:
        an("   NINGUNA. No hay una sola coordenada verificable en el corpus.")
        an("   Sin coordenadas no se puede dibujar un punto en el mapa.")
    else:
        an(f"   {len(coords)} coordenadas verificables.")
        cats = {}
        for c in coords:
            cats[c["categoria"]] = cats.get(c["categoria"], 0) + 1
        for cat, n in sorted(cats.items(), key=lambda x: -x[1]):
            an(f"      {n:>5}  {cat}")
        puntos = emparejar(coords)
        an(f"   Se emparejan en {len(puntos)} puntos situables "
           f"(cada punto necesita latitud Y longitud).")
        an("   Donde caen, y de que documento salen:")
        for p in puntos:
            an(f"      {p['lat']:>9.4f}, {p['lon']:>9.4f}   [{p['fuente']}]")
        an("")
        dentro, total, motivo = dentro_del_area(puntos)
        if dentro is None:
            an(f"   NO se pudo comprobar si pertenecen al area de estudio: "
               f"{motivo}.")
            an("   Importante: que haya coordenadas no significa que sean DE la")
            an("   zona del proyecto. Pueden venir de los antecedentes")
            an("   bibliograficos, que citan trabajos de campo de otros sitios.")
        elif dentro == 0:
            an(f"   NINGUNO de los {total} puntos cae dentro del area de "
               f"estudio.")
            an("   Son coordenadas de la bibliografia, de otros lugares.")
        else:
            an(f"   {dentro} de {total} puntos caen DENTRO del area de estudio.")
    an("")

    # --- 2. Cuencas primero, por decision del proyecto ---------------------
    an("2. CUENCAS (se enganchan por nombre, no situan por si solas)")
    _listar(an, inv["cuencas"], limite_ejemplos, "cuencas")
    an("")

    an("3. DRENAJES: quebradas, canadas, canos y rios")
    _listar(an, inv["drenajes"], limite_ejemplos, "drenajes")
    an("")

    an("4. VEREDAS Y CORREGIMIENTOS")
    _listar(an, inv["veredas"], limite_ejemplos, "veredas o corregimientos")
    an("")

    # --- 5. Lo que hace falta para que las cifras cuadren ------------------
    an("5. SISTEMA DE REFERENCIA")
    if inv["sistemas_referencia"]:
        for etiqueta, fuentes in inv["sistemas_referencia"].items():
            an(f"   {etiqueta}  [{', '.join(fuentes)}]")
    else:
        an("   NINGUNO declarado. Ni EPSG, ni MAGNA-SIRGAS, ni WGS 84.")
        an("   Habra que decidirlo a mano antes de reproyectar nada.")
    an("")

    an("6. SUPERFICIES DECLARADAS")
    if inv["superficies"]:
        an(f"   {len(inv['superficies'])} cifras de superficie.")
        for s in inv["superficies"][:5]:
            an(f"      {s['valor']} {s['unidad']}  [{s['fuente']}]")
    else:
        an("   Ninguna.")
    an("")

    an("7. CAPAS DE SIG NOMBRADAS EN EL TEXTO")
    if inv["capas_sig"]:
        an("   El corpus NO trae estas capas: solo las menciona. Son la pista")
        an("   de que cartografia existe y habria que conseguir.")
        for etiqueta, fuentes in sorted(inv["capas_sig"].items()):
            an(f"      {etiqueta}  [{', '.join(fuentes)}]")
    else:
        an("   Ninguna.")
    an("")

    # --- Veredicto ---------------------------------------------------------
    an("=" * 68)
    an("VEREDICTO")
    if not coords:
        an("  NO se puede dibujar un mapa con lo que hay en el corpus:")
        an("  no contiene ninguna coordenada verificable.")
    else:
        puntos = emparejar(coords)
        dentro, total, _ = dentro_del_area(puntos)
        if dentro is None:
            an(f"  Hay {total} puntos con coordenadas, pero el corpus NO dice")
            an("  que sean del area de estudio del proyecto. Antes de llevarlos")
            an("  a un mapa hay que mirar de que documento salen: la mayoria")
            an("  son sitios de campo citados en los antecedentes.")
        elif dentro == 0:
            an(f"  Los {total} puntos con coordenadas quedan FUERA del area de")
            an("  estudio. Para el mapa del proyecto no sirven.")
        else:
            an(f"  SE PUEDE dibujar: {dentro} de {total} puntos caen dentro del")
            an("  area de estudio.")
    enganchables = (len(inv["cuencas"]) + len(inv["drenajes"])
                    + len(inv["veredas"]))
    if enganchables:
        an(f"  SI se pueden enganchar {enganchables} nombres propios "
           f"(cuencas, drenajes, veredas)")
        an("  a una capa que tenga geometria, usando el nombre como clave.")
    an("=" * 68)
    return "\n".join(lineas)


def _listar(an, elementos: list, limite: int, etiqueta: str) -> None:
    """
    Escribe una lista de toponimos en el informe.

    Args:
        an: Funcion con la que anadir una linea.
        elementos: Toponimos encontrados.
        limite: Cuantos mostrar como maximo.
        etiqueta: Como llamarlos en el recuento.
    """
    if not elementos:
        an(f"   Ninguna mencion de {etiqueta}.")
        return
    an(f"   {len(elementos)} {etiqueta} nombrados.")
    for e in elementos[:limite]:
        cats = ", ".join(e["categorias"][:2])
        an(f"      {e['tipo']} {e['nombre']}  ({cats})")
    if len(elementos) > limite:
        an(f"      ... y {len(elementos) - limite} mas.")
