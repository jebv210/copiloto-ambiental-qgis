"""
Corpus documental del agente: ingesta, fichas e indice de busqueda.
===============================================================================

POR QUE EXISTE ESTE MODULO
--------------------------
El agente del proyecto sabia responder sobre la Geodatabase y sobre el area de
estudio, pero no sabia nada de la bibliografia: catalogos de mariposas, listas
de chequeo, plantas hospederas, ciclos de vida, riesgo climatico. Ese
conocimiento vivia en dos cuadernos de Gemini Notebook (168 documentos) a los
que el sistema no tenia acceso.

Este modulo construye la memoria documental del agente a partir de una carpeta
del disco. No descarga nada ni depende de ningun servicio externo.

LA UNIDAD MINIMA ES LA FICHA, NO EL DOCUMENTO
---------------------------------------------
Un documento de 300 paginas no sirve como respuesta. Lo que se indexa es la
FICHA: un fragmento de texto corto que viene acompanado SIEMPRE de su
procedencia (documento, categoria y pagina). Esa es la pieza que luego se cita.
Si un fragmento no puede decir de donde sale, no entra en el indice.

POR QUE LA BUSQUEDA ES HIBRIDA
------------------------------
Hay dos tipos de pregunta y necesitan motores distintos:

  * "que plantas sostienen las orugas del bosque seco tropical"
    -> busqueda por palabras, tolerante: `buscar`.

  * "Polites otho"
    -> busqueda EXACTA del binomio: `buscar_binomio`.

El segundo caso no admite aproximacion. `Magneuptychia` y `Megeuptychia` son
generos distintos, y una busqueda tolerante los confunde sin avisar. Es el mismo
error que ya se pago en este proyecto cuando el filtro geografico resolvia
"Coahuila" a "HUILA" (ver `core/textos.resolver_toponimo`). Ante la duda,
`buscar_binomio` NO devuelve nada en lugar de devolver lo parecido.

EL CORPUS SE VERSIONA
---------------------
Los informes que produjo Gemini Notebook declaran haberse hecho sobre 92, 95 y
98 fuentes, con el mismo titulo. Sin saber sobre que version del corpus se
genero un informe no hay forma de reproducirlo ni de explicar por que cambio.
Por eso `version_corpus` calcula una huella del conjunto de documentos, y esa
huella se estampa en cada informe que emite `core/informes.py`.

DEPENDENCIAS
------------
Ninguna nueva. Los .docx se leen con `zipfile` de la libreria estandar y los
.pdf con PyPDF2, que ya viene en el entorno de QGIS. Si PyPDF2 faltase, los PDF
se saltan con un aviso explicito en vez de romper la ingesta.
"""

import hashlib
import json
import math
import os
import re
import unicodedata
import xml.etree.ElementTree as ET
import zipfile

from config import settings


# ==============================================================================
# ESTRUCTURA DEL CORPUS EN DISCO
# ==============================================================================

#: Carpeta del proyecto donde se depositan los documentos. Cada subcarpeta es
#: una categoria tematica; el nombre de la subcarpeta ES la categoria. No hay
#: ninguna lista de categorias escrita en el codigo: se descubren del disco,
#: igual que los departamentos y las especies del resto del sistema.
CARPETA_CORPUS_DEFECTO = "corpus"

#: Extensiones que el modulo sabe leer. El resto se inventaria pero no se
#: trocea, y se deja constancia de ello.
#: Se calcula de LECTORES para que anadir un formato sea una sola
#: linea: antes habia que acordarse de tocar dos sitios, y un formato
#: con lector pero sin declarar aqui quedaba marcado como ilegible.
EXTENSIONES_LEIBLES = set()   # se rellena tras definir LECTORES

#: Archivos que viven en la carpeta pero NO son bibliografia: son las
#: instrucciones de uso del propio corpus. Sin esta exclusion, el LEEME se
#: indexaba a si mismo y el agente podia acabar citando las instrucciones como
#: si fueran una fuente cientifica.
ARCHIVOS_NO_FUENTE = {"LEEME.MD", "README.MD", "LEEME.TXT", "README.TXT"}

#: Subcarpetas donde las fuentes REMOTAS dejan lo que se bajan. No son
#: categorias: son el almacen intermedio, porque el troceador solo sabe leer
#: archivos locales.
#:
#: POR QUE HAY QUE SALTARSELAS AL CALCULAR LA CATEGORIA
#: ----------------------------------------------------
#: La categoria es la subcarpeta de PRIMER nivel. Con la fuente de Drive el
#: arbol queda `corpus/_drive/Cartografia/mapa.pdf`, asi que el primer nivel
#: es "_drive" y TODOS los documentos bajados caian en una unica categoria
#: llamada "_drive". Se comprobo: dos carpetas distintas de Drive
#: ("Cartografia" y "Fichas de campo") salian las dos como "_drive" y el
#: reparto por categoria dejaba de informar de nada.
#:
#: Saltandose este nivel, la categoria vuelve a ser la carpeta de Drive, que
#: es lo que el usuario ve y espera. El corpus manda sobre su propia
#: estructura; las fuentes leen de aqui el nombre del almacen.
CARPETAS_DE_DESCARGA = {"_drive"}

#: Tamano maximo de un documento para entrar al corpus, en bytes.
#:
#: POR QUE HAY UN LIMITE
#: ---------------------
#: El corpus es BIBLIOGRAFIA: textos que el agente lee y cita. Un CSV de
#: cientos de megas no es eso, es una capa de datos, y su sitio es la
#: cartografia o la geodatabase, no el indice documental.
#:
#: Sin este tope el sistema no decia que no: lo intentaba. Se midio con una
#: carpeta de Drive que traia cuatro CSV de 450 MB -- casi 1,8 GB de texto --,
#: y el resultado fue que `cargar()` se quedaba colgado, la cache del corpus
#: crecia hasta 2,5 GB y el contenedor de la API moria por falta de memoria.
#: La aplicacion se quedaba en blanco sin un solo mensaje de error.
#:
#: Un limite con aviso es mejor que un intento que cuelga: se dice que
#: documento es, cuanto pesa y por que no entra.
#:
#: POR QUE YA NO SE MIDE EN MEGAS
#: ------------------------------
#: Los megas no predicen el coste. Se midio sobre los archivos reales: 1 MB de
#: CSV tabular da unas 1.485 fichas, y 1 MB de informe en PDF unas pocas
#: decenas. Con un tope en megas, un informe de 200 MB que cabria de sobra
#: quedaba fuera, y un CSV de 30 MB -- 45.000 fichas, mes y medio de cuota
#: gratuita -- habria entrado si el tope hubiera sido un poco mas alto.
#:
#: Lo que de verdad cuesta son las FICHAS: cada una es una llamada a la API de
#: embeddings, y la cuota es el recurso escaso. Asi que el limite es ese, y el
#: de megas se queda solo como guardia de ancho de banda y memoria.
#: Ya NO decide que entra: eso lo decide el presupuesto de fichas. Esto es
#: solo un guardia de ancho de banda para que un archivo absurdo no se baje
#: entero. Estaba en 300 MB y rechazaba las capas de 434 MB por el peso,
#: contradiciendo la regla de fichas que se habia acordado: el aviso decia
#: "cabria por fichas, pero pesa 434 MB", que ademas afirmaba algo no medido.
TAMANO_MAXIMO_DOCUMENTO = 2 * 1024 * 1024 * 1024

#: Lo mas que se baja A CIEGAS, cuando no se ha podido medir cuantas fichas
#: daria el archivo.
#:
#: POR QUE HACEN FALTA DOS LIMITES
#: -------------------------------
#: El de arriba protege el ancho de banda de un archivo que SI se midio y
#: resulto caber. Este protege del caso contrario: un archivo que no se dejo
#: medir -- porque la muestra no se pudo bajar, o porque solo se esta
#: listando la carpeta sin descargar nada.
#:
#: Se midio el dano de no tenerlo: al subir el guardia de 300 MB a 2 GB para
#: que las fichas decidieran, un archivo de 450 MB que no se pudo muestrear
#: dejo de tener quien lo parara y se bajaba entero. No saber lo que algo
#: cuesta no es razon para asumir que es gratis.
TAMANO_SIN_MEDIR = 300 * 1024 * 1024

#: Cuantas fichas vale la pena indexar de UN solo documento.
#:
#: Referencias para calibrarlo: un informe de 400 paginas da del orden de
#: 1.200 fichas; los cuatro CSV de ecosistemas darian 1.265.369 entre todos,
#: que son 97 horas de proceso y 3,5 anos de cuota gratuita.
#:
#: Lo que pase de aqui no se descarta en silencio: se cataloga, se dice su
#: nombre, su peso y cuantas fichas habria hecho falta.
PRESUPUESTO_FICHAS = int(os.environ.get("PRESUPUESTO_FICHAS") or 2000)

#: Tamano de la muestra con la que se estima cuantas fichas dara un archivo
#: grande antes de bajarlo entero. Cuatro megas bastan: con ellos se midio una
#: densidad de 1.485 fichas/MB que resulto exacta al recorrer el archivo
#: completo.
MUESTRA_PARA_ESTIMAR = 4 * 1024 * 1024

#: Formatos que se pueden estimar leyendo solo el principio. Son los que van
#: por lineas: cortar el archivo por la mitad deja algo legible. Un PDF, un
#: DOCX o un XLSX truncado no se puede abrir, asi que esos se miden despues de
#: bajarlos, contando sus fichas de verdad (que no cuesta cuota, solo tiempo).
FORMATOS_ESTIMABLES = {".csv", ".tsv", ".txt", ".md", ".log", ".json", ".xml"}

#: Cache del indice. Reconstruir las fichas de 168 documentos tarda; esto evita
#: repetirlo en cada arranque. Se invalida solo cuando cambia la version.
ARCHIVO_CACHE_CORPUS = "cache_corpus.json"

#: Longitud objetivo de una ficha, en caracteres. Es un equilibrio: demasiado
#: corta pierde el contexto de la frase, demasiado larga deja de ser citable
#: porque la cita ya no senala un punto concreto del documento.
TAMANO_FICHA = 900

#: Solape entre fichas consecutivas. Evita partir una afirmacion justo por la
#: mitad y que deje de encontrarse.
SOLAPE_FICHA = 150

#: Una ficha por debajo de esto es ruido (encabezados, numeros de pagina).
TAMANO_MINIMO_FICHA = 120


def carpeta_corpus() -> str:
    """
    Ubicacion de la carpeta de documentos.

    Se puede mover por completo con la variable de entorno `RUTA_CORPUS`, igual
    que el resto de rutas del proyecto. Si no se indica, se usa `corpus/` dentro
    del proyecto.

    Returns:
        Ruta absoluta de la carpeta. Puede no existir todavia; quien llame debe
        comprobarlo con `esta_disponible()`.
    """
    indicada = settings.env("RUTA_CORPUS")
    if indicada:
        return os.path.abspath(os.path.expanduser(indicada))
    return settings.ruta_proyecto(CARPETA_CORPUS_DEFECTO)


def esta_disponible() -> bool:
    """
    Indica si hay una carpeta de corpus con al menos un documento legible.

    Returns:
        True si existe la carpeta y contiene algun archivo con extension
        conocida. False en cualquier otro caso, incluida la carpeta vacia.
    """
    return bool(inventariar())


# ==============================================================================
# INVENTARIO: QUE DOCUMENTOS HAY
# ==============================================================================

def inventariar(carpeta: str = None) -> list:
    """
    Recorre la carpeta del corpus y describe cada documento encontrado.

    La categoria de un documento es el nombre de la subcarpeta de primer nivel
    que lo contiene. Un documento suelto en la raiz queda como "Sin categoria".

    Args:
        carpeta: Ruta a inventariar. Si se omite, se usa `carpeta_corpus()`.

    Returns:
        Lista de diccionarios ordenada por categoria y nombre, cada uno con:
        `ruta`, `nombre`, `categoria`, `extension`, `bytes`, `modificado` y
        `legible` (si la extension esta entre las que se saben leer).
    """
    base = carpeta or carpeta_corpus()
    if not os.path.isdir(base):
        return []

    documentos = []
    for raiz, _, archivos in os.walk(base):
        for archivo in archivos:
            if archivo.startswith("~$") or archivo.startswith("."):
                continue  # temporales de Office y ocultos
            if archivo.upper() in ARCHIVOS_NO_FUENTE:
                continue  # instrucciones del corpus, no bibliografia
            ruta = os.path.join(raiz, archivo)
            relativa = os.path.relpath(ruta, base)
            partes = relativa.split(os.sep)
            # El almacen de las fuentes remotas no es una categoria: se salta
            # ese nivel para que la categoria sea la carpeta de verdad (la de
            # Drive), y no el nombre del almacen. Ver CARPETAS_DE_DESCARGA.
            if partes and partes[0] in CARPETAS_DE_DESCARGA:
                partes = partes[1:]
            categoria = partes[0] if len(partes) > 1 else "Sin categoria"
            extension = os.path.splitext(archivo)[1].lower()
            try:
                datos = os.stat(ruta)
            except OSError:
                continue
            documentos.append({
                "ruta": ruta,
                "relativa": relativa,
                "nombre": archivo,
                "categoria": categoria,
                "extension": extension,
                "bytes": datos.st_size,
                "modificado": int(datos.st_mtime),
                # Un documento demasiado grande se inventaria, pero NO se
                # marca legible: se vera en la lista con su tamano y el
                # motivo, en vez de tumbar el sistema al intentar leerlo.
                "legible": (extension in EXTENSIONES_LEIBLES
                            and datos.st_size <= TAMANO_MAXIMO_DOCUMENTO),
                "demasiado_grande": datos.st_size > TAMANO_MAXIMO_DOCUMENTO,
                # Solo el PDF conserva una paginacion real. Guardarlo aqui, y no
                # deducirlo luego del numero de pagina, es lo que permite citar
                # la pagina 1 de un PDF sin inventar paginas en los demas
                # formatos (ver `core/informes.cita`).
                "paginada": extension == ".pdf",
            })

    documentos.sort(key=lambda d: (d["categoria"].upper(), d["nombre"].upper()))
    return documentos


def categorias(documentos: list = None) -> list:
    """
    Lista las categorias presentes en el corpus, con su recuento.

    Args:
        documentos: Inventario ya calculado. Si se omite, se recalcula.

    Returns:
        Lista de tuplas `(categoria, numero_de_documentos)` ordenada por nombre.
    """
    inventario = documentos if documentos is not None else inventariar()
    conteo = {}
    for doc in inventario:
        conteo[doc["categoria"]] = conteo.get(doc["categoria"], 0) + 1
    return sorted(conteo.items())


def version_corpus(documentos: list = None) -> str:
    """
    Huella reproducible del conjunto de documentos.

    Se calcula sobre la RUTA RELATIVA y el CONTENIDO de cada documento. Dos
    corpus con la misma huella contienen exactamente los mismos archivos con
    exactamente los mismos bytes, asi que un informe generado con una huella se
    puede reproducir mas tarde comprobando que la huella coincide.

    Se usa la ruta relativa y no solo el nombre porque la categoria de un
    documento es la subcarpeta que lo contiene: mover un archivo de
    "Guias de Campo" a "Catalogos Nacionales" cambia las citas de los informes
    sin cambiar ni un byte del archivo. Con el nombre a secas, la version no se
    enteraba de ese cambio.

    POR QUE NO ENTRA LA FECHA DE MODIFICACION
    -----------------------------------------
    Antes entraba, y rompia justo lo que la huella promete. Al desplegar en
    Docker, el mismo corpus daba una version en Windows y otra dentro del
    contenedor, porque el montaje reporta otras fechas. La comprobacion de
    reproducibilidad habria dado falso negativo entre entornos sin que nada
    hubiera cambiado.

    Tambien se ordena el inventario antes de resumirlo: `os.walk` no garantiza
    el mismo orden en dos sistemas de archivos distintos, y con el orden
    cambiaba la huella.

    Y se normaliza el separador de la ruta. Windows produce
    `Categoria\\documento.pdf` y Linux `Categoria/documento.pdf`: con el
    separador nativo, los mismos archivos daban huellas distintas en el equipo
    y dentro del contenedor. Fue exactamente lo que se midio al desplegar.

    Leer los 134 documentos para resumirlos cuesta 0,06 segundos medidos, asi
    que no compensa ahorrarselo a cambio de una huella que solo mira tamanos:
    una edicion que dejara el archivo del mismo tamano pasaria inadvertida.

    Args:
        documentos: Inventario ya calculado. Si se omite, se recalcula.

    Returns:
        Cadena del tipo `134docs-a1b2c3d4`: numero de documentos y los primeros
        ocho caracteres del resumen SHA-256.
    """
    inventario = documentos if documentos is not None else inventariar()

    def _portable(doc):
        """
        Normaliza la ruta de un documento para que la huella sea portable.

        Args:
            doc: Documento del inventario, con su clave `relativa`.

        Returns:
            La ruta relativa con barras normales. Sin esto, el mismo corpus
            daba una version distinta en Windows y dentro del contenedor, y
            el indice se reconstruia entero sin motivo.
        """
        return doc["relativa"].replace("\\", "/")

    resumen = hashlib.sha256()
    for doc in sorted(inventario, key=_portable):
        resumen.update(_portable(doc).encode("utf-8", "replace"))
        try:
            with open(doc["ruta"], "rb") as f:
                while True:
                    trozo = f.read(1 << 20)
                    if not trozo:
                        break
                    resumen.update(trozo)
        except OSError:
            # Un archivo que no se puede leer entra por su tamano, para que su
            # desaparicion o su cambio de tamano si alteren la version.
            resumen.update(str(doc["bytes"]).encode())
    return f"{len(inventario)}docs-{resumen.hexdigest()[:8]}"


# ==============================================================================
# LECTURA: SACAR EL TEXTO DE CADA FORMATO
# ==============================================================================

#: Marca que cierra la cabecera de procedencia que escribe `core/importar.py`.
#: Ver `_sin_cabecera_procedencia` para por que hay que quitarla.
CIERRE_CABECERA = "---\n\n"


def _sin_cabecera_procedencia(texto: str) -> str:
    """
    Retira la cabecera de procedencia de una fuente importada.

    Los archivos que crea `core/importar.py` empiezan con unas lineas que dicen
    de donde salio el documento (titulo, cuaderno, url, huella). Esa cabecera es
    METADATO, no contenido de la fuente, y si se indexa ocurren dos cosas malas:
    aparece en los resultados de busqueda como si fuera texto del documento, y
    sus palabras (url, huella, fechas) compiten en la puntuacion con el texto
    real.

    Se detecta por la presencia de la linea `huella:` dentro de la cabecera, no
    solo por los guiones, para no recortar por error un documento que empiece
    con una linea de guiones.

    Args:
        texto: Contenido completo del archivo.

    Returns:
        El texto sin la cabecera. Si el archivo no tiene cabecera de
        procedencia, se devuelve tal cual.
    """
    if not texto.startswith("titulo:") or "\nhuella:" not in texto[:800]:
        return texto
    corte = texto.rfind(CIERRE_CABECERA, 0, 1200)
    return texto[corte + len(CIERRE_CABECERA):] if corte != -1 else texto


def _leer_texto_plano(ruta: str) -> list:
    """
    Lee los formatos que ya son texto: .txt, .md y .csv.

    Args:
        ruta: Ruta del archivo.

    Returns:
        Lista con una sola tupla `(1, contenido)`, ya sin la cabecera de
        procedencia. Estos formatos no tienen paginas, asi que se declara una
        unica pagina en vez de inventar una division que el archivo no tiene.
    """
    with open(ruta, "r", encoding="utf-8", errors="replace") as fh:
        return [(1, _sin_cabecera_procedencia(fh.read()))]


def _leer_docx(ruta: str) -> list:
    """
    Lee un .docx sin dependencias externas.

    Un .docx es un ZIP que contiene `word/document.xml`. Se extraen los nodos
    `<w:t>` (text runs) y se unen por parrafo. No se necesita python-docx para
    esto, y evitar la dependencia mantiene el proyecto instalable con lo que ya
    trae el entorno de QGIS.

    Args:
        ruta: Ruta del archivo .docx.

    Returns:
        Lista con una sola tupla `(1, texto)`. Word no expone saltos de pagina
        fiables en el XML, asi que no se puede paginar con honestidad y se
        prefiere declarar una sola pagina antes que inventar numeros.
    """
    espacio = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
    with zipfile.ZipFile(ruta) as zf:
        if "word/document.xml" not in zf.namelist():
            return []
        arbol = ET.fromstring(zf.read("word/document.xml"))

    parrafos = []
    for parrafo in arbol.iter(f"{espacio}p"):
        trozos = [nodo.text for nodo in parrafo.iter(f"{espacio}t") if nodo.text]
        if trozos:
            parrafos.append("".join(trozos))
    return [(1, "\n".join(parrafos))]


def _leer_pdf(ruta: str) -> list:
    """
    Lee un .pdf pagina a pagina con PyPDF2.

    La paginacion importa: el numero de pagina es parte de la cita, y sin el la
    referencia no se puede comprobar. Por eso cada pagina se devuelve por
    separado en lugar de concatenar todo el documento.

    Se prefiere `pypdf`, que es el proyecto mantenido, y se cae a `PyPDF2` si
    no esta: PyPDF2 quedo sin mantenimiento y pypdf es su sucesor directo, con
    la misma interfaz. El entorno de QGIS trae hoy PyPDF2, asi que el respaldo
    no es teorico.

    Args:
        ruta: Ruta del archivo .pdf.

    Returns:
        Lista de tuplas `(numero_de_pagina, texto)`. Lista vacia si no hay
        ninguna de las dos librerias o si el PDF es una imagen escaneada sin
        capa de texto.
    """
    try:
        from pypdf import PdfReader
    except ImportError:
        try:
            from PyPDF2 import PdfReader
        except ImportError:
            return []

    paginas = []
    try:
        with open(ruta, "rb") as fh:
            lector = PdfReader(fh)
            for numero, pagina in enumerate(lector.pages, start=1):
                try:
                    texto = pagina.extract_text() or ""
                except Exception:
                    texto = ""
                if texto.strip():
                    paginas.append((numero, texto))
    except Exception:
        return []
    return paginas


#: Lector por extension. Anadir un formato nuevo es anadir una entrada aqui.
def _leer_excel(ruta: str) -> list:
    """
    Extrae el contenido de un libro de Excel, una pagina por hoja.

    POR QUE UNA PAGINA POR HOJA
    ---------------------------
    Un libro suele traer una hoja por tema: poblacion, coberturas, red
    hidrica. Tratar cada hoja como una pagina hace que la cita diga de que
    hoja salio el dato, igual que una cita de PDF dice de que pagina.

    Las filas se escriben separadas por " | ". No es decoracion: sin un
    separador visible, "Palmichal 282 1045.03" se lee como un numero largo y
    el troceado parte por donde no debe. Las celdas vacias se omiten, que en
    una tabla de Excel son mayoria.

    Args:
        ruta: Ruta del libro.

    Returns:
        Lista de tuplas `(numero_de_hoja, texto)`.
    """
    import pandas as pd

    salida = []
    libro = pd.ExcelFile(ruta)
    for numero, hoja in enumerate(libro.sheet_names, start=1):
        tabla = libro.parse(hoja, header=None, dtype=str)
        lineas = [f"[Hoja: {hoja}]"]
        for _, fila in tabla.iterrows():
            celdas = [str(v).strip() for v in fila.tolist()
                      if v is not None and str(v).strip().lower() not in ("", "nan")]
            if celdas:
                lineas.append(" | ".join(celdas))
        if len(lineas) > 1:
            salida.append((numero, "\n".join(lineas)))
    return salida


#: Formatos de imagen que el modelo sabe mirar.
TIPOS_IMAGEN = {
    ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png",
    ".webp": "image/webp", ".heic": "image/heic", ".heif": "image/heif",
    ".gif": "image/png",
}

#: Lo que se le pide al modelo al transcribir una imagen del corpus.
#:
#: Pide TRANSCRIBIR antes que describir, y por un motivo: una foto de una
#: tabla, una ficha de campo o un rotulo vale por lo que dice, no por lo que
#: se ve. Y se le prohibe interpretar: si el agente luego cita esta ficha,
#: tiene que estar citando lo que ponia la imagen, no lo que el modelo supuso.
INSTRUCCION_IMAGEN = (
    "Transcribe TODO el texto visible en esta imagen, respetando su orden y "
    "su estructura (si hay una tabla, mantenla como tabla). Despues, en una "
    "linea aparte que empiece por 'DESCRIPCION:', di brevemente que se ve. "
    "No interpretes ni anadas datos que no aparezcan. Si no hay texto, "
    "escribe solo la descripcion."
)


def _leer_imagen(ruta: str) -> list:
    """
    Transcribe una imagen a texto usando el modelo, para poder indexarla.

    POR QUE PASA POR EL MODELO
    --------------------------
    No hay OCR instalado, y aunque lo hubiera, una foto de una ficha de campo
    con letra manuscrita o una tabla fotografiada se leen mucho mejor con un
    modelo que con un OCR clasico.

    CUIDADO CON EL COSTE
    --------------------
    Cada imagen es UNA llamada al modelo, y la capa gratuita es estrecha. No
    es lo mismo anadir cinco fotos que una carpeta con quinientas: eso ultimo
    agota la cuota del dia.

    Args:
        ruta: Ruta de la imagen.

    Returns:
        Lista con una tupla `(1, texto)`, o vacia si no se pudo transcribir.
        Devolver vacio hace que el importador la rechace diciendo por que, en
        vez de meter una ficha en blanco que luego se cite sin contenido.
    """
    clave = settings.api_key_gemini()
    if not clave:
        return []

    tipo = TIPOS_IMAGEN.get(os.path.splitext(ruta)[1].lower())
    if tipo is None:
        return []

    from google import genai
    from google.genai import types

    with open(ruta, "rb") as f:
        datos = f.read()

    cliente = genai.Client(api_key=clave)
    respuesta = cliente.models.generate_content(
        model=settings.modelo_llm(),
        contents=[types.Part.from_bytes(data=datos, mime_type=tipo),
                  types.Part(text=INSTRUCCION_IMAGEN)],
    )
    texto = (respuesta.text or "").strip()
    return [(1, texto)] if texto else []


LECTORES = {
    ".txt": _leer_texto_plano,
    ".md": _leer_texto_plano,
    ".csv": _leer_texto_plano,
    # Formatos de texto con marcas: se indexan tal cual. El troceador ya
    # colapsa espacios, y las etiquetas sobrantes pesan poco frente al
    # contenido.
    ".json": _leer_texto_plano,
    ".xml": _leer_texto_plano,
    ".html": _leer_texto_plano,
    ".htm": _leer_texto_plano,
    ".log": _leer_texto_plano,
    ".docx": _leer_docx,
    ".pdf": _leer_pdf,
    ".xlsx": _leer_excel,
    ".xls": _leer_excel,
}
LECTORES.update({ext: _leer_imagen for ext in TIPOS_IMAGEN})

EXTENSIONES_LEIBLES.update(LECTORES)


def contar_fichas_de(ruta: str) -> int:
    """
    Cuenta cuantas fichas indexables saldrian de un archivo ya descargado.

    No gasta cuota: trocear y filtrar es trabajo local. Lo que cuesta cuota es
    convertir cada ficha en vector, y eso viene despues. Por eso conviene
    contar ANTES de decidir.

    Args:
        ruta: Archivo a medir.

    Returns:
        Cuantas fichas pasarian el filtro. 0 si no se pudo leer.
    """
    from core import vectorial

    try:
        paginas = leer_documento(ruta) or []
    except Exception:
        return 0
    cuantas = 0
    for _pagina, texto in paginas:
        for trozo in trocear(texto or ""):
            if vectorial.merece_vector(trozo):
                cuantas += 1
    return cuantas


def estimar_fichas(ruta_muestra: str, bytes_totales: int) -> dict:
    """
    Estima las fichas de un archivo grande a partir de su principio.

    Se midio contra la realidad: con una muestra de 4 MB de un CSV de 434 MB
    salia una densidad de 1.485 fichas/MB, y recorrer el archivo entero
    confirmo el orden de magnitud. Para archivos por lineas la estimacion es
    fiable, porque la forma de una fila no cambia a lo largo del archivo.

    Args:
        ruta_muestra: El trozo inicial ya descargado.
        bytes_totales: Lo que pesa el archivo completo en origen.

    Returns:
        Diccionario con `fichas` (estimadas), `densidad` (fichas por MB) y
        `muestra_mb`. Si la muestra no se pudo leer, `fichas` vale -1, que
        significa "no se sabe", no "ninguna": tratar la ignorancia como un
        cero haria entrar el archivo creyendo que sale gratis.
    """
    mega = 1024 * 1024
    try:
        leidos = os.path.getsize(ruta_muestra)
    except OSError:
        return {"fichas": -1, "densidad": 0.0, "muestra_mb": 0.0}
    if leidos <= 0:
        return {"fichas": -1, "densidad": 0.0, "muestra_mb": 0.0}

    en_la_muestra = contar_fichas_de(ruta_muestra)
    if not en_la_muestra:
        return {"fichas": -1, "densidad": 0.0,
                "muestra_mb": round(leidos / mega, 1)}

    densidad = en_la_muestra / (leidos / mega)
    return {
        "fichas": int(densidad * (bytes_totales / mega)),
        "densidad": round(densidad, 1),
        "muestra_mb": round(leidos / mega, 1),
    }


def leer_documento(ruta: str) -> list:
    """
    Extrae el texto de un documento, pagina a pagina.

    Args:
        ruta: Ruta del documento.

    Returns:
        Lista de tuplas `(numero_de_pagina, texto)`. Lista vacia si el formato
        no se sabe leer o si el documento no tiene capa de texto.
    """
    lector = LECTORES.get(os.path.splitext(ruta)[1].lower())
    if lector is None:
        return []
    try:
        paginas = lector(ruta)
    except Exception as exc:
        # POR QUE SE ANOTA EN VEZ DE CALLAR
        # ---------------------------------
        # Esta funcion devolvia [] ante cualquier fallo, sin dejar rastro. Un
        # documento ilegible y una libreria que falta producian exactamente el
        # mismo resultado: silencio.
        #
        # Se midio lo que costo: `openpyxl` no estaba instalado en el
        # contenedor, asi que TODOS los .xlsx del corpus se leian como vacios.
        # La sincronizacion informaba de "2 nuevos" y terminaba sin errores
        # mientras esos documentos llevaban dias con cero fichas en el indice.
        # Nadie podia verlo, porque no habia nada que ver.
        #
        # Un fallo tiene que dejar rastro aunque no se quiera interrumpir el
        # trabajo por el. Se sigue devolviendo [] para que un documento roto no
        # tumbe una indexacion de dos horas, pero ahora queda anotado y
        # `resumen()` lo saca a la superficie.
        anotar_fallo_de_lectura(ruta, exc)
        return []
    if not paginas:
        anotar_fallo_de_lectura(
            ruta, "el lector no devolvio texto (documento vacio o ilegible)")
    return paginas


#: Documentos que no se pudieron leer, con el motivo. Vive en memoria: es un
#: diagnostico de la pasada actual, no un registro historico.
FALLOS_DE_LECTURA = {}


def anotar_fallo_de_lectura(ruta: str, motivo) -> None:
    """
    Deja constancia de que un documento no se pudo leer.

    Args:
        ruta: Documento afectado.
        motivo: Excepcion o texto explicativo.
    """
    texto = f"{type(motivo).__name__}: {motivo}" if isinstance(
        motivo, BaseException) else str(motivo)
    FALLOS_DE_LECTURA[os.path.basename(ruta)] = texto[:200]


def olvidar_fallos_de_lectura() -> None:
    """Vacia la lista de fallos, al empezar una pasada nueva."""
    FALLOS_DE_LECTURA.clear()


# ==============================================================================
# TROCEADO: DE PAGINA A FICHA
# ==============================================================================

def _limpiar(texto: str) -> str:
    """
    Colapsa espacios y saltos de linea sobrantes sin alterar el contenido.

    Los PDF arrastran guiones de division invisibles y espaciados irregulares
    que, si no se normalizan, parten palabras y las vuelven imposibles de
    encontrar en el indice.

    Args:
        texto: Texto crudo recien extraido del documento.

    Returns:
        El mismo texto con los espacios colapsados. No se cambia ninguna
        palabra: solo se normaliza el espaciado.
    """
    texto = texto.replace("­", "")          # guion de division invisible
    texto = re.sub(r"[ \t]+", " ", texto)
    texto = re.sub(r"\n{3,}", "\n\n", texto)
    return texto.strip()


def trocear(texto: str) -> list:
    """
    Parte un texto largo en fichas que se solapan.

    Se corta preferentemente al final de una frase para que la ficha no empiece
    a media oracion.

    DONDE SE BUSCA ESE FINAL DE FRASE, Y POR QUE IMPORTA
    ----------------------------------------------------
    Solo en el ULTIMO TERCIO de la ventana. Buscarlo en toda la ventana parecia
    mas flexible y era un error: con textos de frases cortas, el corte caia
    cerca del principio, la ficha salia diminuta y el avance se reducia a un
    caracter por vuelta. El resultado eran miles de fichas de 150 caracteres
    solapadas entre si.

    No es un problema estetico. Cada copia de un mismo parrafo cuenta como una
    evidencia distinta en la busqueda, de modo que un documento que repite una
    frase parece respaldarla muchas veces. Se midio en el primer lote real: 223
    fichas donde debian salir unas 35, con el texto inflado 2,1 veces.

    Limitar la busqueda al ultimo tercio garantiza que cada ficha avance al
    menos dos tercios del tamano objetivo.

    Args:
        texto: Texto de una pagina ya limpio.

    Returns:
        Lista de cadenas. Los fragmentos por debajo de `TAMANO_MINIMO_FICHA` se
        descartan por considerarse encabezados o pies de pagina.
    """
    texto = _limpiar(texto)
    if len(texto) <= TAMANO_FICHA:
        return [texto] if len(texto) >= TAMANO_MINIMO_FICHA else []

    #: A partir de aqui se admite cortar. Antes de este punto la ficha seria
    #: demasiado corta y el avance, demasiado lento.
    zona_de_corte = (TAMANO_FICHA * 2) // 3

    fichas, inicio = [], 0
    while inicio < len(texto):
        fin = min(inicio + TAMANO_FICHA, len(texto))
        if fin < len(texto):
            desde = inicio + zona_de_corte
            punto = texto.rfind(". ", desde, fin)
            if punto != -1:
                fin = punto + 1
            else:
                espacio = texto.rfind(" ", desde, fin)
                if espacio != -1:
                    fin = espacio
        trozo = texto[inicio:fin].strip()
        if len(trozo) >= TAMANO_MINIMO_FICHA:
            fichas.append(trozo)
        if fin >= len(texto):
            break
        # El max() es un seguro: aunque un texto extrano hiciera que `fin`
        # quedara corto, el recorrido siempre avanza y nunca se estanca.
        inicio = max(fin - SOLAPE_FICHA, inicio + zona_de_corte // 2)
    return fichas


# ==============================================================================
# INDICE: BUSQUEDA POR PALABRAS (BM25)
# ==============================================================================

#: Palabras demasiado comunes para discriminar. Se excluyen del indice para que
#: no inflen la puntuacion de fichas que solo comparten conectores.
VACIAS = {
    "DE", "DEL", "LA", "LAS", "EL", "LOS", "UN", "UNA", "UNOS", "UNAS", "Y", "E",
    "O", "U", "EN", "QUE", "SE", "POR", "CON", "PARA", "SU", "SUS", "AL", "LO",
    "ES", "SON", "COMO", "MAS", "PERO", "ESTE", "ESTA", "ESTOS", "ESTAS", "ENTRE",
    "THE", "OF", "AND", "IN", "TO", "FOR", "A",
}


def normalizar_palabra(palabra: str) -> str:
    """
    Forma canonica de una palabra para el indice: mayusculas y sin tildes.

    No se pliega la enye, por el mismo motivo que en `core/textos`: en los
    nombres propios del dominio la 'n' con virgulilla es una letra distinta.

    Args:
        palabra: Palabra tal como aparece en el texto.

    Returns:
        La palabra en mayusculas, sin marcas diacriticas salvo la virgulilla.
    """
    descompuesta = unicodedata.normalize("NFKD", palabra.upper())
    sin_tildes = "".join(c for c in descompuesta
                         if not unicodedata.combining(c) or c == "̃")
    return unicodedata.normalize("NFC", sin_tildes)


def palabras_literales(texto: str) -> list:
    """
    Parte un texto en TODAS sus palabras normalizadas, sin descartar ninguna.

    Se diferencia de `tokenizar` en que no elimina las palabras vacias. Eso la
    hace inservible para el indice, pero imprescindible para comprobar que dos
    terminos aparecen literalmente seguidos: si se quitaran las vacias, un
    "Polites de otho" se leeria como "POLITES OTHO" y se daria por bueno un
    binomio que el documento nunca escribio.

    Args:
        texto: Texto libre.

    Returns:
        Lista de palabras normalizadas en el orden original.
    """
    crudas = re.findall(r"[0-9A-Za-zÁÉÍÓÚÜÑáéíóúüñ]+", texto)
    return [normalizar_palabra(c) for c in crudas]


def menciona_literalmente(texto: str, termino: str) -> bool:
    """
    Comprueba si un texto nombra un termino con sus palabras completas.

    Es la pieza que impide dos errores distintos y ambos graves:

      * El PREFIJO. Comparar cadenas sueltas hace que "POLITES OTHO" se
        encuentre dentro de "POLITES OTHONIS", y el sistema daria por
        documentada una especie que la fuente nunca nombro. Por eso se rodean
        ambos lados de espacios: se compara " POLITES OTHO " contra
        " ... POLITES OTHONIS ... ", que ya no casa.

      * La SEPARACION. Se compara contra la secuencia literal de palabras, sin
        quitar las vacias, para que un "Polites de otho" no se lea como si el
        documento hubiera escrito el binomio.

    Args:
        texto: Texto de la ficha.
        termino: Palabra o secuencia de palabras que debe aparecer.

    Returns:
        True solo si el termino aparece con sus palabras completas, juntas y en
        ese orden.
    """
    partes = palabras_literales(termino)
    if not partes:
        return False
    secuencia = " " + " ".join(palabras_literales(texto)) + " "
    return (" " + " ".join(partes) + " ") in secuencia


def tokenizar(texto: str) -> list:
    """
    Parte un texto en palabras indexables.

    Args:
        texto: Texto libre.

    Returns:
        Lista de palabras normalizadas, sin las vacias y sin las de una sola
        letra. Los numeros se conservan: en este dominio "3877" o "2024" son
        datos, no ruido.
    """
    crudas = re.findall(r"[0-9A-Za-zÁÉÍÓÚÜÑáéíóúüñ]+", texto)
    palabras = []
    for cruda in crudas:
        palabra = normalizar_palabra(cruda)
        if len(palabra) > 1 and palabra not in VACIAS:
            palabras.append(palabra)
    return palabras + _palabras_recompuestas(texto)


def _palabras_recompuestas(texto: str) -> list:
    """
    Recupera las palabras que el PDF partio por el guion de fin de linea.

    Al extraer el texto de un PDF, "plantaciones" partido entre dos lineas
    llega como "planta- ciones", y asi ya no se encuentra buscando
    "plantaciones". En el corpus del proyecto hay 379 casos repartidos en 46
    documentos.

    POR QUE SE ANADE EN VEZ DE REPARAR EL TEXTO
    -------------------------------------------
    Porque no se puede distinguir con certeza una palabra partida de una
    palabra compuesta legitima: "ensenanza- aprendizaje" tiene la misma forma
    que "planta- ciones", y unirla daria "ensenanzaaprendizaje", que no existe.

    Reparar el texto obligaria a acertar siempre. Anadir la forma unida al
    INDICE no obliga a nada: la ficha se sigue mostrando y citando tal como
    esta en el documento, y simplemente se vuelve localizable tambien por la
    palabra entera. Un token de mas no estorba; una palabra mal unida si.

    Args:
        texto: Texto de la ficha, sin normalizar.

    Returns:
        Lista de las formas unidas encontradas, ya normalizadas. Vacia si el
        texto no tiene ninguna palabra partida.
    """
    unidas = []
    for izquierda, derecha in re.findall(
            r"([A-Za-zÁÉÍÓÚÜÑáéíóúüñ]{2,})-\s+([a-záéíóúüñ]{2,})", texto):
        palabra = normalizar_palabra(izquierda + derecha)
        if len(palabra) > 1 and palabra not in VACIAS:
            unidas.append(palabra)
    return unidas


def construir_fichas(progreso=None) -> dict:
    """
    Lee todo el corpus y produce las fichas con su procedencia.

    Args:
        progreso: Funcion opcional `(fraccion, mensaje)` para informar del
            avance a la interfaz. Se llama una vez por documento.

    Returns:
        Diccionario con:
          - `version`: huella del corpus con la que se construyeron las fichas.
          - `documentos`: numero de documentos inventariados.
          - `ilegibles`: lista de nombres de los que no se pudo extraer texto.
          - `fichas`: lista de diccionarios con `id`, `documento`, `categoria`,
            `pagina` y `texto`.
    """
    inventario = inventariar()
    fichas, ilegibles = [], []
    total = max(len(inventario), 1)

    for numero, doc in enumerate(inventario):
        if progreso:
            progreso(numero / total, f"Leyendo {doc['nombre']}")
        if not doc["legible"]:
            ilegibles.append(doc["nombre"])
            continue
        paginas = leer_documento(doc["ruta"])
        if not paginas:
            ilegibles.append(doc["nombre"])
            continue
        for pagina, texto in paginas:
            for trozo in trocear(texto):
                fichas.append({
                    "id": len(fichas),
                    "documento": doc["nombre"],
                    "categoria": doc["categoria"],
                    "pagina": pagina,
                    "paginada": doc["paginada"],
                    "texto": trozo,
                })

    if progreso:
        progreso(1.0, f"{len(fichas)} fichas de {len(inventario)} documentos")

    return {
        "version": version_corpus(inventario),
        "documentos": len(inventario),
        "ilegibles": ilegibles,
        "fichas": fichas,
    }


def _indexar(fichas: list) -> dict:
    """
    Construye el indice invertido y las frecuencias que necesita BM25.

    Args:
        fichas: Lista de fichas de `construir_fichas`.

    Returns:
        Diccionario con `invertido` (palabra -> {id_ficha: frecuencia}),
        `longitudes` (id_ficha -> numero de palabras) y `longitud_media`.
    """
    invertido, longitudes = {}, {}
    for ficha in fichas:
        palabras = tokenizar(ficha["texto"])
        longitudes[ficha["id"]] = len(palabras)
        for palabra in palabras:
            entrada = invertido.setdefault(palabra, {})
            entrada[ficha["id"]] = entrada.get(ficha["id"], 0) + 1
    media = sum(longitudes.values()) / len(longitudes) if longitudes else 0.0
    return {"invertido": invertido, "longitudes": longitudes, "longitud_media": media}


# ==============================================================================
# CARGA Y CACHE
# ==============================================================================

_memoria = {"corpus": None}


def ruta_cache() -> str:
    """
    Ubicacion del archivo de cache del indice.

    La cache vive DENTRO de la carpeta del corpus, no en la raiz del proyecto.
    Cuando estaba en la raiz, cualquier proceso que trabajara con otro corpus
    —las pruebas, por ejemplo, que montan corpus temporales— sobrescribia la
    cache del corpus real y obligaba a reconstruirlo entero. Poniendola junto a
    los documentos, cada corpus tiene la suya.

    El nombre empieza por punto para que `inventariar` la ignore y la cache no
    se indexe a si misma.

    Returns:
        Ruta absoluta del archivo de cache dentro de la carpeta del corpus.
    """
    return os.path.join(carpeta_corpus(), "." + ARCHIVO_CACHE_CORPUS)


def cargar(forzar: bool = False, progreso=None) -> dict:
    """
    Devuelve el corpus indexado, reconstruyendolo solo si hace falta.

    El orden de preferencia es: memoria del proceso, cache en disco (si su
    version coincide con la del corpus actual) y, como ultimo recurso, lectura
    completa de los documentos.

    Args:
        forzar: True reconstruye aunque la cache sea valida.
        progreso: Funcion opcional `(fraccion, mensaje)`.

    Returns:
        Diccionario con `version`, `documentos`, `ilegibles`, `fichas` e
        `indice`. Si no hay corpus, `fichas` viene vacia y `version` es
        "0docs-" seguido de la huella del conjunto vacio.
    """
    if not forzar and _memoria["corpus"] is not None:
        return _memoria["corpus"]

    esperada = version_corpus()
    cache = ruta_cache()

    if not forzar and os.path.exists(cache):
        try:
            with open(cache, "r", encoding="utf-8") as fh:
                guardado = json.load(fh)
            if guardado.get("version") == esperada:
                guardado["indice"] = _indexar(guardado["fichas"])
                _memoria["corpus"] = guardado
                return guardado
        except (OSError, ValueError, KeyError):
            pass  # cache corrupta o de otra version: se reconstruye

    corpus = construir_fichas(progreso=progreso)
    try:
        with open(cache, "w", encoding="utf-8") as fh:
            json.dump(corpus, fh, ensure_ascii=False)
    except OSError:
        pass  # sin cache se funciona igual, solo mas lento

    corpus["indice"] = _indexar(corpus["fichas"])
    _memoria["corpus"] = corpus
    return corpus


def olvidar() -> None:
    """Vacia la copia en memoria. Util en pruebas y tras cambiar el corpus."""
    _memoria["corpus"] = None


# ==============================================================================
# BUSQUEDA
# ==============================================================================

def buscar(consulta: str, limite: int = 8, categoria: str = None,
           debe_contener: str = None) -> list:
    """
    Busca fichas pertinentes por palabras, con puntuacion BM25.

    BM25 premia las palabras raras y penaliza las fichas largas, que es lo que
    hace falta aqui: "hospedera" debe pesar mucho mas que "especie", y una ficha
    de 900 caracteres no debe ganar solo por contener mas palabras.

    PARA QUE SIRVE `debe_contener`
    ------------------------------
    BM25 suma puntos por CUALQUIER palabra de la consulta. Buscar
    "Polites planta hospedera oruga" devuelve con gusto una ficha que habla de
    las hospederas de *Morpho helenor* y que nunca nombra a Polites, solo
    porque contiene "planta hospedera". La cita seria correcta y la informacion,
    de otra especie: el peor error posible en un informe, porque parece bien
    respaldado.

    Con `debe_contener` se exige ademas que la ficha nombre literalmente ese
    termino. Si no queda ninguna, se devuelve lista vacia y quien llame debe
    declarar la ausencia, no mostrar evidencia ajena.

    Args:
        consulta: Texto libre de la pregunta.
        limite: Numero maximo de fichas a devolver.
        categoria: Si se indica, solo se consideran fichas de esa categoria.
        debe_contener: Si se indica, la ficha debe nombrar ese termino con sus
            palabras completas (tipicamente el genero o el binomio).

    Returns:
        Lista de fichas ordenadas de mas a menos pertinente. Cada una es la
        ficha original con una clave `puntuacion` anadida. Lista vacia si no
        hay corpus, si ninguna ficha contiene las palabras buscadas o si
        ninguna de las candidatas nombra `debe_contener`.
    """
    corpus = cargar()
    if not corpus["fichas"]:
        return []

    indice = corpus["indice"]
    palabras = tokenizar(consulta)
    if not palabras:
        return []

    total = len(corpus["fichas"])
    media = indice["longitud_media"] or 1.0
    k1, b = 1.5, 0.75
    puntos = {}

    for palabra in palabras:
        apariciones = indice["invertido"].get(palabra)
        if not apariciones:
            continue
        idf = math.log(1 + (total - len(apariciones) + 0.5) / (len(apariciones) + 0.5))
        for id_ficha, frecuencia in apariciones.items():
            longitud = indice["longitudes"].get(id_ficha, 0)
            denominador = frecuencia + k1 * (1 - b + b * longitud / media)
            puntos[id_ficha] = puntos.get(id_ficha, 0.0) + idf * frecuencia * (k1 + 1) / denominador

    resultados = []
    for id_ficha, puntuacion in sorted(puntos.items(), key=lambda x: -x[1]):
        ficha = corpus["fichas"][id_ficha]
        if categoria and ficha["categoria"] != categoria:
            continue
        if debe_contener and not menciona_literalmente(ficha["texto"], debe_contener):
            continue
        resultados.append(dict(ficha, puntuacion=round(puntuacion, 3)))
        if len(resultados) >= limite:
            break
    return resultados


def buscar_binomio(binomio: str, limite: int = 12) -> list:
    """
    Busca un nombre cientifico exigiendo coincidencia EXACTA.

    A diferencia de `buscar`, aqui no hay tolerancia de ningun tipo. Se exigen
    dos condiciones, y la segunda es la que de verdad importa:

      1. Que todas las palabras del nombre esten en la misma ficha.
      2. Que aparezcan JUNTAS y en ese orden.

    Sin la segunda condicion, una ficha que mencionara el genero `Polites` en
    una linea y la palabra `otho` en otra se daria por buena, y el informe
    citaria una fuente que nunca nombro a esa especie.

    `Magneuptychia` y `Megeuptychia` son generos distintos; devolver uno cuando
    se pidio el otro produciria un informe taxonomico equivocado con apariencia
    de correcto.

    Si solo se da el genero, se devuelven las fichas que lo mencionan.

    Args:
        binomio: Nombre cientifico, por ejemplo "Polites otho". Se admite
            tambien un unico termino (el genero).
        limite: Numero maximo de fichas a devolver.

    Returns:
        Lista de fichas que contienen el nombre completo. Lista vacia si no
        aparece en ningun documento, que es un resultado valido y debe
        declararse como tal, no rellenarse con lo que se le parezca.
    """
    corpus = cargar()
    if not corpus["fichas"]:
        return []

    partes = palabras_literales(binomio)
    if not partes:
        return []

    indice = corpus["indice"]["invertido"]
    conjuntos = []
    for parte in partes:
        apariciones = indice.get(parte)
        if not apariciones:
            return []  # falta una de las palabras: no hay coincidencia exacta
        conjuntos.append(set(apariciones))

    # Primer filtro: las palabras conviven en la misma ficha.
    comunes = set.intersection(*conjuntos)
    if not comunes:
        return []

    # Segundo filtro, el decisivo: aparecen juntas, en orden y como palabras
    # completas. Lo resuelve `menciona_literalmente`, que es tambien la que usa
    # `buscar(debe_contener=...)`: una sola definicion de "nombrar algo".
    buscado = " ".join(partes)
    resultados = []
    for id_ficha in sorted(comunes):
        ficha = corpus["fichas"][id_ficha]
        if menciona_literalmente(ficha["texto"], buscado):
            resultados.append(dict(ficha))
        if len(resultados) >= limite:
            break
    return resultados


def resumen() -> dict:
    """
    Estado del corpus, para mostrarlo en la interfaz o en un diagnostico.

    Returns:
        Diccionario con `carpeta`, `disponible`, `version`, `documentos`,
        `fichas`, `categorias` (lista de tuplas) e `ilegibles`.
    """
    inventario = inventariar()
    corpus = cargar() if inventario else {"version": version_corpus([]),
                                          "fichas": [], "ilegibles": []}
    return {
        "carpeta": carpeta_corpus(),
        "disponible": bool(inventario),
        "version": corpus["version"],
        "documentos": len(inventario),
        "fichas": len(corpus["fichas"]),
        "categorias": categorias(inventario),
        "ilegibles": corpus.get("ilegibles", []),
        # Documentos que fallaron al leerse en esta pasada, con el motivo. Un
        # "No module named 'openpyxl'" aqui vale mas que cualquier revision
        # manual: dice exactamente que falta y en que documento.
        "fallos_de_lectura": dict(FALLOS_DE_LECTURA),
        # Documentos que se ven pero NO entran al indice por tamano. Se dicen
        # con su peso: "no aparece" sin motivo es lo que hace perder una tarde.
        "demasiado_grandes": [
            {"nombre": d["nombre"],
             "mb": round(d["bytes"] / (1024 * 1024), 1)}
            for d in inventario if d.get("demasiado_grande")
        ],
        "tope_mb": TAMANO_MAXIMO_DOCUMENTO // (1024 * 1024),
        # El limite que decide de verdad. Se informa aparte del de megas
        # porque son dos cosas distintas: uno protege el ancho de banda, el
        # otro la cuota de embeddings, que es lo escaso.
        "presupuesto_fichas": PRESUPUESTO_FICHAS,
    }


if __name__ == "__main__":
    estado = resumen()
    print("CORPUS DOCUMENTAL")
    print("=" * 70)
    print(f"  Carpeta    : {estado['carpeta']}")
    print(f"  Disponible : {'si' if estado['disponible'] else 'NO'}")
    print(f"  Version    : {estado['version']}")
    print(f"  Documentos : {estado['documentos']}")
    print(f"  Fichas     : {estado['fichas']}")
    if estado["categorias"]:
        print("\n  Categorias:")
        for nombre, cuantos in estado["categorias"]:
            print(f"    {cuantos:4d}  {nombre}")
    if estado["ilegibles"]:
        print(f"\n  Sin texto extraible ({len(estado['ilegibles'])}):")
        for nombre in estado["ilegibles"][:10]:
            print(f"    - {nombre}")
    if not estado["disponible"]:
        print("\n  No hay documentos. Deposite los archivos en subcarpetas de")
        print(f"  {estado['carpeta']}, una subcarpeta por categoria tematica.")
