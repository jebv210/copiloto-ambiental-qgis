"""
Plantillas de informe sobre el corpus documental.
===============================================================================

POR QUE EXISTE ESTE MODULO
--------------------------
Los dos cuadernos de conocimiento que dieron origen a este trabajo ya producian
dos tipos de documento con estructura fija: un "Reporte Tecnico Taxonomico" y un
"Informe Cientifico", ambos de seis secciones. Este modulo reproduce esas
estructuras sobre el corpus propio del proyecto (`core/corpus.py`).

LA PLANTILLA MANDA SOBRE EL MODELO
----------------------------------
El agente de IA NO decide la estructura del informe ni redacta sus afirmaciones
de memoria. Este modulo recupera la evidencia del corpus, la coloca en la
seccion que le corresponde y la entrega ya citada. El modelo de lenguaje solo
conversa sobre un material que ya viene con su procedencia.

Esto es deliberado y es el nucleo del diseno: si el modelo pudiera rellenar las
secciones por su cuenta, no habria forma de distinguir lo que el corpus
documenta de lo que el modelo supone.

LA AUSENCIA DE DATO ES UN RESULTADO
-----------------------------------
Cuando una seccion no encuentra evidencia, lo dice con esas palabras y nombra
que se busco. No se rellena con lo que parece razonable ni se omite la seccion.
Es la misma regla que ya aplica `database/gdb_connector.py` al distinguir
"sin hallazgos" de "no evaluado", y la que seguia el reporte sobre Polites otho
del cuaderno original cuando declaraba no haber hallado el numero de pagina.

TODO INFORME LLEVA LA VERSION DEL CORPUS
----------------------------------------
Dos informes con el mismo titulo hechos sobre corpus distintos no son
comparables. La version se estampa en la cabecera para que cualquier resultado
se pueda reproducir o, al menos, fechar.
"""

import datetime
import re

from core import corpus


#: Las familias de lepidopteros terminan en -IDAE y las subfamilias en -INAE.
#: Es una convencion del Codigo Internacional de Nomenclatura Zoologica, no una
#: heuristica inventada aqui, y permite extraer la clasificacion del texto sin
#: mantener ninguna lista de familias escrita en el codigo.
PATRON_FAMILIA = re.compile(r"\b([A-Z][a-z]+idae)\b")
PATRON_SUBFAMILIA = re.compile(r"\b([A-Z][a-z]+inae)\b")

#: Numero de fichas que se recupera por seccion. Mas de esto satura el informe
#: y deja de leerse; menos deja secciones sin respaldo suficiente.
FICHAS_POR_SECCION = 5


# ==============================================================================
# PIEZAS COMUNES
# ==============================================================================

def cita(ficha: dict) -> str:
    """
    Formatea la procedencia de una ficha tal como se mostrara en el informe.

    La pagina se incluye segun el FORMATO del documento, no segun el numero.
    Decidirlo por el numero (omitirla cuando vale 1) dejaba sin citar la pagina
    1 de los PDF, que es una pagina tan real como las demas. Solo el PDF
    conserva paginacion fiable; los .docx no exponen saltos de pagina, asi que
    ahi se omite en lugar de inventar un numero.

    Args:
        ficha: Ficha devuelta por `core.corpus`.

    Returns:
        Cadena del tipo `Lista de chequeo.pdf, p. 1 [Catalogos Nacionales]`.
    """
    pagina = f", p. {ficha['pagina']}" if ficha.get("paginada") else ""
    return f"{ficha['documento']}{pagina} [{ficha['categoria']}]"


def recortar(texto: str, maximo: int = 420) -> str:
    """
    Acorta un fragmento sin partir una palabra por la mitad.

    Args:
        texto: Texto de la ficha.
        maximo: Longitud maxima deseada.

    Returns:
        El texto completo si cabe; si no, el recorte hasta el ultimo espacio
        seguido de puntos suspensivos.
    """
    texto = " ".join(texto.split())
    if len(texto) <= maximo:
        return texto
    corte = texto.rfind(" ", 0, maximo)
    return texto[:corte if corte > 0 else maximo] + "..."


def _seccion(numero: int, titulo: str, fichas: list, que_se_busco: str) -> list:
    """
    Construye una seccion del informe a partir de la evidencia encontrada.

    Args:
        numero: Numero de la seccion.
        titulo: Titulo de la seccion.
        fichas: Fichas que la respaldan. Puede venir vacia.
        que_se_busco: Descripcion de la busqueda realizada, que se muestra al
            usuario cuando no hay evidencia. Sin esto, un "no se encontro" no
            se puede interpretar ni rebatir.

    Returns:
        Lista de lineas de texto.
    """
    lineas = [f"{numero}. {titulo.upper()}", ""]
    if not fichas:
        lineas.append(f"   Sin evidencia en el corpus. Se busco: {que_se_busco}.")
        lineas.append("   Esto significa que los documentos cargados no lo "
                      "tratan, no que el dato no exista.")
        lineas.append("")
        return lineas

    for ficha in fichas:
        lineas.append(f"   - {recortar(ficha['texto'])}")
        lineas.append(f"     Fuente: {cita(ficha)}")
        lineas.append("")
    return lineas


def _cabecera(titulo: str, sujeto: str, estado: dict) -> list:
    """
    Cabecera comun a todos los informes, con la trazabilidad del corpus.

    Args:
        titulo: Tipo de informe.
        sujeto: Especie, tema o asunto del informe.
        estado: Resumen del corpus (`core.corpus.resumen`).

    Returns:
        Lista de lineas de texto.
    """
    hoy = datetime.date.today().isoformat()
    return [
        "=" * 74,
        f"{titulo.upper()}: {sujeto}",
        "=" * 74,
        f"Generado        : {hoy}",
        f"Version corpus  : {estado['version']}",
        f"Base documental : {estado['documentos']} documentos, {estado['fichas']} fichas",
        "=" * 74,
        "",
    ]


def _sin_corpus(estado: dict) -> str:
    """
    Mensaje unico para cuando no hay documentos que consultar.

    Args:
        estado: Resumen del corpus.

    Returns:
        Texto explicativo que indica donde depositar los documentos.
    """
    return (
        "No hay corpus documental cargado, asi que no se puede emitir el "
        "informe.\n\n"
        f"Carpeta esperada: {estado['carpeta']}\n\n"
        "Deposite ahi los documentos, una subcarpeta por categoria tematica "
        "(por ejemplo 'Catalogos Nacionales', 'Plantas Hospederas', 'Ciclos de "
        "Vida'). Se admiten PDF, DOCX, TXT, MD y CSV. El nombre de cada "
        "subcarpeta se convierte en la categoria; no hay que declarar nada en "
        "el codigo."
    )


# ==============================================================================
# CLASIFICACION TAXONOMICA
# ==============================================================================

def clasificar(fichas: list) -> dict:
    """
    Extrae familia y subfamilia de las fichas que mencionan el taxon.

    Aprovecha que la nomenclatura zoologica es regular: las familias terminan en
    -idae y las subfamilias en -inae. Solo se proponen las que aparecen
    literalmente en el texto; nunca se deduce una familia por parecido con otro
    genero.

    Se cuenta en cuantas FICHAS DISTINTAS aparece cada nombre, no cuantas veces
    aparece en total. La diferencia no es menor: un documento que repite la
    misma frase cinco veces no aporta cinco respaldos, aporta uno. Contar
    apariciones brutas exagera la evidencia, que es justo lo que este modulo
    existe para evitar.

    Args:
        fichas: Fichas que mencionan el nombre cientifico buscado.

    Returns:
        Diccionario con `familias` y `subfamilias`, cada una una lista de
        tuplas `(nombre, numero_de_fichas)` ordenada de mas a menos respaldada.
    """
    familias, subfamilias = {}, {}
    for ficha in fichas:
        for hallada in set(PATRON_FAMILIA.findall(ficha["texto"])):
            familias[hallada] = familias.get(hallada, 0) + 1
        for hallada in set(PATRON_SUBFAMILIA.findall(ficha["texto"])):
            subfamilias[hallada] = subfamilias.get(hallada, 0) + 1
    return {
        "familias": sorted(familias.items(), key=lambda x: -x[1]),
        "subfamilias": sorted(subfamilias.items(), key=lambda x: -x[1]),
    }


def _bloque_clasificacion(binomio: str, fichas: list) -> list:
    """
    Redacta la seccion de clasificacion taxonomica.

    Args:
        binomio: Nombre cientifico consultado.
        fichas: Fichas que lo mencionan.

    Returns:
        Lista de lineas de texto.
    """
    partes = binomio.split()
    lineas = ["3. CLASIFICACION TAXONOMICA", ""]
    propuesta = clasificar(fichas)

    if propuesta["familias"]:
        nombre, veces = propuesta["familias"][0]
        lineas.append(f"   Familia      : {nombre}  "
                      f"(aparece en {veces} fichas junto al taxon)")
        if len(propuesta["familias"]) > 1:
            otras = ", ".join(f"{n} ({v})" for n, v in propuesta["familias"][1:4])
            lineas.append(f"                  Tambien se mencionan: {otras}. "
                          "Conviene revisarlo a mano.")
    else:
        lineas.append("   Familia      : no consta en las fichas que nombran al taxon.")

    if propuesta["subfamilias"]:
        nombre, veces = propuesta["subfamilias"][0]
        lineas.append(f"   Subfamilia   : {nombre}  (en {veces} fichas)")
    else:
        lineas.append("   Subfamilia   : no consta.")

    lineas.append(f"   Genero       : {partes[0] if partes else '(no indicado)'}")
    lineas.append(f"   Epiteto      : {partes[1] if len(partes) > 1 else '(no indicado)'}")
    lineas.append("")
    lineas.append("   Nota curatorial: no se indica la autoridad taxonomica "
                  "(autor y ano de")
    lineas.append("   descripcion) porque el corpus no la recoge para este "
                  "taxon. Se omite en vez")
    lineas.append("   de completarla desde fuera de las fuentes cargadas.")
    lineas.append("")
    return lineas


# ==============================================================================
# REPORTE TECNICO TAXONOMICO
# ==============================================================================

def reporte_taxonomico(binomio: str) -> str:
    """
    Emite el Reporte Tecnico Taxonomico de una especie, en seis secciones.

    Reproduce la estructura del reporte que ya producia el cuaderno de origen.
    Toda afirmacion va acompanada de su fuente, y las secciones sin evidencia lo
    declaran expresamente.

    Args:
        binomio: Nombre cientifico, por ejemplo "Polites otho". Se admite solo
            el genero, en cuyo caso el informe lo advierte.

    Returns:
        El informe completo como texto plano.
    """
    estado = corpus.resumen()
    if not estado["disponible"]:
        return _sin_corpus(estado)

    menciones = corpus.buscar_binomio(binomio, limite=FICHAS_POR_SECCION * 2)
    genero = binomio.split()[0] if binomio.split() else binomio

    lineas = _cabecera("Reporte Tecnico Taxonomico", binomio, estado)

    if not menciones:
        lineas.append("AVISO PRELIMINAR")
        lineas.append("")
        lineas.append(f"   El nombre '{binomio}' no aparece literalmente en "
                      "ningun documento del")
        lineas.append("   corpus. No se ha sustituido por ningun nombre "
                      "parecido: una coincidencia")
        lineas.append("   aproximada entre nombres cientificos produce "
                      "informes equivocados con")
        lineas.append("   apariencia de correctos.")
        lineas.append("")
        lineas.append("   Las secciones siguientes se apoyan en lo que el "
                      f"corpus dice del genero '{genero}'")
        lineas.append("   y de su entorno, y lo indican en cada caso.")
        lineas.append("")

    # Las secciones 1, 4 y 5 exigen `debe_contener=genero`: sin eso, BM25
    # devolveria fichas que hablan del asunto pero de OTRA especie, y el
    # informe atribuiria a este taxon un habitat o una lamina ajenos.
    #
    # `usadas` acumula solo las fichas que de verdad se muestran. La
    # bibliografia promete listar lo citado, asi que tiene que construirse con
    # lo mostrado y no con una busqueda aparte.
    usadas = []

    s1 = corpus.buscar(f"{genero} lista de chequeo catalogo nacional",
                       limite=FICHAS_POR_SECCION, debe_contener=genero)
    usadas += s1
    lineas += _seccion(1, "Identificacion del catalogo y fuente primaria", s1,
                       f"fichas que nombren a '{genero}' junto a terminos de catalogo")

    s2 = menciones[:FICHAS_POR_SECCION]
    usadas += s2
    lineas += _seccion(2, "Localizacion y registro en la literatura cientifica",
                       s2, f"el nombre literal '{binomio}' en todo el corpus")

    lineas += _bloque_clasificacion(binomio, menciones)

    s4 = corpus.buscar(f"{genero} lamina fotografia figura ejemplar",
                       limite=FICHAS_POR_SECCION, debe_contener=genero)
    usadas += s4
    lineas += _seccion(4, "Analisis de registro visual", s4,
                       f"laminas y pies de figura que nombren a '{genero}'")

    s5 = corpus.buscar(f"{genero} habitat bosque ecosistema altitud vegetacion",
                       limite=FICHAS_POR_SECCION, debe_contener=genero)
    usadas += s5
    lineas += _seccion(5, "Habitat y entorno ecologico", s5,
                       f"fichas que nombren a '{genero}' junto a terminos de habitat")

    lineas += _bibliografia(usadas)
    return "\n".join(lineas)


def _bibliografia(fichas: list) -> list:
    """
    Lista los documentos efectivamente citados en el informe.

    Solo entran las fuentes de las que se extrajo alguna ficha. Una
    bibliografia que incluyera documentos no usados daria una impresion de
    respaldo que el informe no tiene.

    Args:
        fichas: Todas las fichas empleadas en el informe.

    Returns:
        Lista de lineas de texto.
    """
    lineas = ["6. FUENTES EFECTIVAMENTE CITADAS", ""]
    vistos = {}
    for ficha in fichas:
        vistos.setdefault(ficha["documento"], ficha["categoria"])
    if not vistos:
        lineas.append("   Ninguna. El informe no pudo apoyarse en documento alguno.")
        lineas.append("")
        return lineas
    for numero, (documento, categoria) in enumerate(sorted(vistos.items()), start=1):
        lineas.append(f"   [{numero}] {documento}  -  {categoria}")
    lineas.append("")
    lineas.append("   Solo se listan los documentos de los que se extrajo "
                  "alguna ficha usada")
    lineas.append("   arriba. El corpus contiene mas fuentes que no trataban "
                  "este asunto.")
    lineas.append("")
    return lineas


# ==============================================================================
# FICHA DE ESPECIE PARA TRABAJO DE CAMPO
# ==============================================================================

def ficha_de_especie(binomio: str) -> str:
    """
    Resumen breve de una especie, pensado para imprimirse y llevarse al campo.

    A diferencia del reporte taxonomico, no documenta la bibliografia: responde
    a lo que hace falta saber delante del ejemplar, que es de que planta depende
    y en que fase del ciclo.

    Args:
        binomio: Nombre cientifico de la especie.

    Returns:
        La ficha como texto plano.
    """
    estado = corpus.resumen()
    if not estado["disponible"]:
        return _sin_corpus(estado)

    genero = binomio.split()[0] if binomio.split() else binomio
    menciones = corpus.buscar_binomio(binomio, limite=4)
    propuesta = clasificar(menciones)

    lineas = _cabecera("Ficha de especie", binomio, estado)
    familia = propuesta["familias"][0][0] if propuesta["familias"] else "no consta"
    lineas.append(f"   Familia segun el corpus: {familia}")
    lineas.append(f"   Menciones literales    : {len(menciones)}")
    lineas.append("")

    for titulo, consulta, descripcion in [
        ("PLANTA HOSPEDERA (fase larval)",
         f"{genero} planta hospedera oruga larva alimentacion",
         "plantas hospederas asociadas al genero"),
        ("PLANTA NECTARIFERA (fase adulta)",
         f"{genero} nectar nectarifera flor adulto",
         "plantas nectariferas asociadas al genero"),
        ("CICLO DE VIDA",
         f"{genero} huevo oruga crisalida pupa emergencia ciclo",
         "fases del ciclo de vida del genero"),
    ]:
        # `debe_contener` es aqui aun mas critico que en el reporte: una ficha
        # de campo que atribuya a esta especie la planta hospedera de otra
        # manda a alguien a buscar al sitio equivocado.
        fichas = corpus.buscar(consulta, limite=2, debe_contener=genero)
        lineas.append(f"   {titulo}")
        if not fichas:
            lineas.append(f"      Sin evidencia. Se busco: {descripcion}.")
        for ficha in fichas:
            lineas.append(f"      {recortar(ficha['texto'], 300)}")
            lineas.append(f"      Fuente: {cita(ficha)}")
        lineas.append("")
    return "\n".join(lineas)


# ==============================================================================
# INFORME CIENTIFICO
# ==============================================================================

#: Las seis secciones del Informe Cientifico, con las palabras que orientan la
#: recuperacion en cada una. Cambiar esta tabla cambia el informe entero, que es
#: precisamente la idea: la estructura vive en un solo sitio.
SECCIONES_CIENTIFICO = [
    ("Marco teorico y modelos de prediccion",
     "marco teorico modelo prediccion indice umbral definicion"),
    ("Diagnostico de impactos y vulnerabilidad",
     "impacto vulnerabilidad riesgo amenaza perdida afectacion"),
    ("Metodologias de resiliencia y evaluacion",
     "resiliencia metodologia evaluacion indicador integridad monitoreo"),
    ("Estrategias de conservacion y mitigacion",
     "conservacion mitigacion estrategia restauracion manejo proteccion"),
    ("Conclusiones tecnicas",
     "conclusion resultado hallazgo evidencia recomendacion"),
]


def informe_cientifico(tema: str) -> str:
    """
    Emite el Informe Cientifico sobre un tema, en seis secciones.

    Reproduce la estructura del informe que ya producia el cuaderno de origen.
    Cada seccion recupera del corpus la evidencia pertinente al tema y a esa
    seccion en concreto, y la entrega citada.

    Args:
        tema: Asunto del informe, por ejemplo "fenomeno del Nino" o "plantas
            hospederas del bosque seco tropical".

    Returns:
        El informe completo como texto plano.
    """
    estado = corpus.resumen()
    if not estado["disponible"]:
        return _sin_corpus(estado)

    lineas = _cabecera("Informe Cientifico", tema, estado)
    usadas = []

    for numero, (titulo, orientacion) in enumerate(SECCIONES_CIENTIFICO, start=1):
        fichas = corpus.buscar(f"{tema} {orientacion}", limite=FICHAS_POR_SECCION)
        usadas += fichas
        lineas += _seccion(numero, titulo, fichas,
                           f"'{tema}' junto a terminos de {titulo.lower()}")

    lineas += _bibliografia(usadas)
    return "\n".join(lineas)


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1:
        print(reporte_taxonomico(" ".join(sys.argv[1:])))
    else:
        print(informe_cientifico("mariposas de Colombia"))
