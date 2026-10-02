"""
Utilidades de normalizacion de texto.
===============================================================================

POR QUE EXISTE ESTE MODULO
--------------------------
Los nombres geograficos y taxonomicos llegan al sistema desde tres fuentes
distintas que NO escriben igual:

  * El CSV de GBIF          -> "Antioquia", "Boyaca", "BOGOTA D.C."
  * El GeoJSON del DANE     -> "ANTIOQUIA", "BOYACA", "BOGOTA, D.C."
  * Lo que escribe el usuario -> "antioquia", "Antioquía", " ANTIOQUIA "

Antes, cada modulo resolvia esto con cadenas de `.replace("A","A")` copiadas y
pegadas. Eso era fragil (solo cubria 5 vocales acentuadas) y estaba duplicado en
`core/dynamic_filter.py` y `descargar_antioquia.py`.

Aqui se centraliza: una sola funcion de normalizacion, usada por todos.

REGLA DE ORO: normalizar SOLO para comparar. Nunca se guarda ni se le muestra
al usuario el texto normalizado; siempre se conserva el valor original tal como
viene de la fuente de datos.
"""

import unicodedata
import re


def quitar_tildes(texto: str) -> str:
    """
    Elimina cualquier marca diacritica (tildes, dieresis, cedillas) de un texto.

    Usa descomposicion Unicode NFKD: separa la letra base de su acento y luego
    descarta los caracteres de la categoria "Mn" (Mark, nonspacing).
    Esto cubre TODO el alfabeto latino, no solo las 5 vocales.

    Excepcion deliberada: la 'N' con virgulilla se preserva, porque en los
    nombres oficiales de departamentos colombianos es una letra distinta
    ("NARINO" existe como departamento y no debe confundirse con "NARINO").

    Args:
        texto: Cadena de entrada. Si no es str, se convierte con str().

    Returns:
        La misma cadena sin marcas diacriticas.

    Ejemplos:
        >>> quitar_tildes("Antioquía")
        'Antioquia'
        >>> quitar_tildes("BOGOTÁ, D.C.")
        'BOGOTA, D.C.'
        >>> quitar_tildes("Nariño")
        'Nariño'
    """
    if texto is None:
        return ""
    texto = str(texto)

    # Se protege la enye (mayuscula y minuscula) con un marcador temporal que
    # no puede aparecer en datos reales, para que la descomposicion NFKD no la
    # convierta en "n" + virgulilla y luego pierda la virgulilla.
    marcador_n_mayus = "\x00NN\x00"
    marcador_n_minus = "\x00nn\x00"
    texto = texto.replace("Ñ", marcador_n_mayus).replace("ñ", marcador_n_minus)

    descompuesto = unicodedata.normalize("NFKD", texto)
    sin_marcas = "".join(c for c in descompuesto if not unicodedata.combining(c))

    return sin_marcas.replace(marcador_n_mayus, "Ñ").replace(marcador_n_minus, "ñ")


def normalizar(texto: str) -> str:
    """
    Lleva un texto a su forma canonica para comparaciones.

    Pasos: quitar tildes -> mayusculas -> quitar puntuacion -> colapsar espacios.

    La puntuacion se elimina porque el DANE escribe "BOGOTA, D.C." y GBIF escribe
    "Bogota D.C."; sin quitar comas y puntos jamas coincidirian.

    Args:
        texto: Cadena de entrada.

    Returns:
        Cadena normalizada, apta unicamente para comparar.

    Ejemplos:
        >>> normalizar("  Bogotá, D.C. ")
        'BOGOTA D C'
        >>> normalizar("Valle del Cauca") == normalizar("VALLE DEL CAUCA")
        True
    """
    base = quitar_tildes(texto).upper()
    base = re.sub(r"[^\w\sÑ]", " ", base, flags=re.UNICODE)
    return re.sub(r"\s+", " ", base).strip()


#: Palabras que no aportan identidad a un nombre geografico. Se ignoran al
#: comparar por palabras: "SAN ANDRES Y PROVIDENCIA" y "ARCHIPIELAGO DE SAN
#: ANDRES, PROVIDENCIA Y SANTA CATALINA" son el mismo departamento.
PALABRAS_VACIAS = {"DE", "DEL", "LA", "LAS", "EL", "LOS", "Y", "E"}


def clave_comparacion(texto: str) -> str:
    """
    Forma canonica MAS laxa, solo para decidir si dos nombres son el mismo.

    Ademas de lo que hace `normalizar`, pliega la enye a "n".

    POR QUE:
    `normalizar` conserva la enye a proposito, porque en espanol es una letra
    distinta. Pero las bases de datos internacionales no siempre la escriben:
    GBIF exporta el departamento de Narino como "Narino", sin enye. Sin este
    pliegue, "NARINO" nunca coincidiria con "NARIÑO" y ese departamento
    desaparecia en silencio de los menus.

    Args:
        texto: Texto a convertir.

    Returns:
        Cadena canonica, apta unicamente para comparar.
    """
    return normalizar(texto).replace("Ñ", "N")


def palabras_significativas(texto: str) -> set:
    """
    Descompone un nombre en las palabras que lo identifican.

    Args:
        texto: Texto a descomponer.

    Returns:
        Conjunto de palabras, sin articulos ni preposiciones y sin las de una
        sola letra.
    """
    return {
        p for p in clave_comparacion(texto).split()
        if p not in PALABRAS_VACIAS and len(p) > 1
    }


def coincide(a: str, b: str) -> bool:
    """
    Compara dos textos ignorando tildes, enyes, mayusculas y puntuacion.

    Args:
        a: Primer texto.
        b: Segundo texto.

    Returns:
        True si son equivalentes tras normalizar.
    """
    return clave_comparacion(a) == clave_comparacion(b)


def contiene(texto: str, patron: str) -> bool:
    """
    Indica si `patron` aparece dentro de `texto`, ignorando tildes y mayusculas.

    Se usa para el filtrado del CSV de biodiversidad, donde la columna
    `stateProvince` a veces trae valores compuestos como
    "Antioquia, Municipio de San Carlos" y una igualdad exacta fallaria.

    Args:
        texto: Texto donde buscar.
        patron: Texto a buscar.

    Returns:
        True si `patron` esta contenido en `texto` tras normalizar ambos.
    """
    patron_norm = normalizar(patron)
    if not patron_norm:
        return False
    return patron_norm in normalizar(texto)


def parecido(a: str, b: str) -> bool:
    """
    Coincidencia flexible bidireccional: True si alguno contiene al otro.

    Resuelve el caso real del archipielago: el CSV de GBIF dice
    "San Andres y Providencia" y el GeoJSON del DANE dice
    "ARCHIPIELAGO DE SAN ANDRES, PROVIDENCIA Y SANTA CATALINA".
    Ninguna igualdad exacta funciona; una contencion en cualquier sentido si.

    Args:
        a: Primer texto.
        b: Segundo texto.

    Returns:
        True si son iguales, o si uno esta contenido en el otro.
    """
    na, nb = clave_comparacion(a), clave_comparacion(b)
    if not na or not nb:
        return False
    return na == nb or na in nb or nb in na


def emparejar(valor: str, candidatos, estricto_primero: bool = True):
    """
    Busca en una lista el candidato que mejor corresponde a `valor`.

    Estrategia en TRES pasadas, de mas exacta a mas laxa. Se para en la
    primera que encuentre algo, para que un nombre exacto nunca pierda contra
    una coincidencia aproximada:

      1. Igualdad exacta (`coincide`).
         "Cauca" -> "CAUCA", y no "VALLE DEL CAUCA".

      2. Contencion bidireccional (`parecido`), quedandose con el candidato
         mas corto, que es el mas especifico.
         "Bogota D.C." -> "BOGOTA, D.C."

      3. Coincidencia por palabras: todas las palabras significativas del
         nombre estan en el candidato. Gana el que menos palabras sobrantes
         tenga.
         "San Andres y Providencia" ->
             "ARCHIPIELAGO DE SAN ANDRES, PROVIDENCIA Y SANTA CATALINA"

    La tercera pasada existe porque los nombres largos del DANE no se parecen
    literalmente a como los escriben las bases de datos internacionales, y sin
    ella ese departamento desaparecia de los menus sin decir nada.

    Se usa para traducir el nombre de departamento que aparece en el CSV de
    biodiversidad al nombre EXACTO que usa la capa cartografica, sin ninguna
    tabla de equivalencias escrita a mano en el codigo.

    Args:
        valor: Texto a emparejar.
        candidatos: Iterable de textos donde buscar.
        estricto_primero: Si True (por defecto) intenta la igualdad exacta antes
            que las aproximaciones. Ponerlo en False fuerza la busqueda laxa.

    Returns:
        El elemento de `candidatos` que corresponde, o None si ninguno encaja.

    Ejemplo:
        >>> emparejar("Bogota D.C.", ["ANTIOQUIA", "BOGOTA, D.C."])
        'BOGOTA, D.C.'
    """
    lista = [c for c in candidatos if c is not None and str(c).strip() != ""]
    if not lista or valor is None:
        return None

    # --- Pasada 1: igualdad exacta -------------------------------------------
    if estricto_primero:
        for c in lista:
            if coincide(valor, c):
                return c

    # --- Pasada 2: uno contiene al otro --------------------------------------
    parciales = [c for c in lista if parecido(valor, c)]
    if parciales:
        return min(parciales, key=lambda c: len(clave_comparacion(c)))

    # --- Pasada 3: todas las palabras del nombre estan en el candidato -------
    palabras_valor = palabras_significativas(valor)
    if not palabras_valor:
        return None

    por_palabras = []
    for c in lista:
        palabras_c = palabras_significativas(c)
        if palabras_valor and palabras_valor <= palabras_c:
            # Cuantas palabras de mas tiene el candidato: cuantas menos, mas
            # ajustada es la correspondencia.
            por_palabras.append((len(palabras_c - palabras_valor), c))

    if por_palabras:
        return min(por_palabras)[1]

    return None


# ==============================================================================
# RESOLUCION ESTRICTA DE TOPONIMOS
# ==============================================================================

#: Palabras genericas de toponimia que, por si solas, no identifican un lugar.
#: No son datos de ningun departamento: son terminos que acompanan a los nombres
#: ("Distrito Capital de Bogota", "Archipielago de San Andres").
TERMINOS_GENERICOS = {"DISTRITO", "CAPITAL", "ARCHIPIELAGO", "DEPARTAMENTO",
                      "PROVINCIA", "ESTADO", "REGION"}

#: Abreviaturas que se expanden antes de comparar por palabras. "D.C." queda
#: como "D C" tras normalizar, y sin expandirla "Bogota, D.C." no tendria nada
#: en comun con "Distrito Capital", que es como la escribe GBIF.
ABREVIATURAS = {r"\bD C\b": "DISTRITO CAPITAL"}


def palabras_toponimo(texto: str) -> set:
    """
    Descompone un nombre de lugar en sus palabras identificativas.

    A diferencia de `palabras_significativas`, expande las abreviaturas de
    `ABREVIATURAS` antes de partir el texto.

    Args:
        texto: Nombre de lugar.

    Returns:
        Conjunto de palabras en forma canonica, sin articulos, preposiciones
        ni letras sueltas.
    """
    base = clave_comparacion(texto)
    for patron, expansion in ABREVIATURAS.items():
        base = re.sub(patron, expansion, base)
    return {p for p in base.split() if p not in PALABRAS_VACIAS and len(p) > 1}


def resolver_toponimo(valor: str, oficiales):
    """
    Traduce un nombre de lugar escrito de cualquier forma a su nombre oficial.

    POR QUE NO BASTA CON `emparejar`:
    `emparejar` acepta que un texto este CONTENIDO en otro. Contra una lista
    corta de opciones eso es comodo, pero contra los miles de regiones del
    mundo que trae GBIF es peligroso. Medido sobre 20 millones de registros,
    confundia "Coahuila" (Mexico) con HUILA, "Lima" (Peru) con TOLIMA,
    "Araucania" (Chile) con ARAUCA, "Mayo" con PUTUMAYO, "Atlantico Sur"
    (Nicaragua) con ATLANTICO y "San'a'" (Yemen) con SAN ANDRES.

    Esta funcion compara PALABRAS COMPLETAS, nunca trozos de palabra, y con
    reglas estrictas, de mas segura a menos:

      1. Igualdad exacta tras normalizar.
         "Choco" -> CHOCÓ, "Narino" -> NARIÑO.
      2. Las palabras coinciden con el nucleo del nombre oficial (quitando
         terminos genericos) o con el nombre oficial completo.
         "Bogota" -> BOGOTÁ, D.C.   "Guajira" -> LA GUAJIRA.
      3. Todas las palabras estan en el nombre oficial, siempre que sean al
         menos DOS. Una sola palabra suelta ("Valle", "San") es demasiado
         ambigua para aceptarla por aproximacion.
         "Distrito Capital" -> BOGOTÁ, D.C.
         "San Andres y Providencia" -> ARCHIPIELAGO DE SAN ANDRES...

    Si dos nombres oficiales encajan igual de bien, devuelve None: ante la
    duda, no adivina.

    Limitacion conocida: nombres de dos palabras que existen en varios paises
    (por ejemplo "Santa Catalina" o "Distrito Capital" de Venezuela) pueden
    resolverse a un departamento colombiano. Por eso el motor aplica tambien
    el filtro de pais: las dos protecciones se complementan.

    Args:
        valor: Nombre tal como aparece en los datos.
        oficiales: Nombres oficiales candidatos.

    Returns:
        El nombre oficial que corresponde, o None si no hay correspondencia
        segura.
    """
    if valor is None:
        return None
    clave = clave_comparacion(valor)
    if not clave:
        return None

    lista = [o for o in oficiales if o is not None and str(o).strip()]

    # --- Regla 1: igualdad exacta --------------------------------------------
    for oficial in lista:
        if clave_comparacion(oficial) == clave:
            return oficial

    palabras = palabras_toponimo(valor)
    if not palabras:
        return None

    candidatos = []
    for oficial in lista:
        del_oficial = palabras_toponimo(oficial)
        nucleo = del_oficial - TERMINOS_GENERICOS
        # --- Regla 2: coincide con el nucleo o con el nombre completo --------
        if palabras == nucleo or palabras == del_oficial:
            candidatos.append((0, oficial))
        # --- Regla 3: subconjunto, con al menos dos palabras -----------------
        elif len(palabras) >= 2 and palabras <= del_oficial:
            candidatos.append((len(del_oficial - palabras), oficial))

    if not candidatos:
        return None

    candidatos.sort(key=lambda c: c[0])
    if len(candidatos) > 1 and candidatos[0][0] == candidatos[1][0]:
        return None
    return candidatos[0][1]
