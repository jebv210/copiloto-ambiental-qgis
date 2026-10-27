"""
Importacion de fuentes externas al corpus local.
===============================================================================

POR QUE EXISTE ESTE MODULO
--------------------------
El conocimiento del proyecto vive hoy en dos cuadernos de Gemini Notebook, en
la nube. El objetivo es que el agente funcione SIN INTERNET, asi que ese
contenido tiene que bajar al disco. Este modulo es la puerta de entrada: todo
lo que se traiga de fuera pasa por aqui.

QUE APORTA, SI AL FINAL SOLO ESCRIBE ARCHIVOS
---------------------------------------------
Tres cosas que a mano se olvidan y que, sin ellas, el corpus deja de ser
fiable:

  1. PROCEDENCIA. Cada archivo nace con una cabecera que dice de que cuaderno
     salio, con que titulo, de que direccion y en que fecha. Un documento sin
     procedencia no se puede citar, y el sistema entero se apoya en citar.

  2. VALIDACION. Un texto demasiado corto no es una fuente: es un error de
     extraccion disfrazado. Se rechaza antes de escribirlo, y se dice por que.

  3. DUPLICADOS. Las mismas fuentes aparecen en los dos cuadernos. Se detectan
     por el contenido, no por el nombre, para que no se cuente dos veces la
     misma evidencia ni se infle artificialmente el respaldo de una afirmacion.

LO QUE ESTE MODULO NO HACE
--------------------------
No navega ni descarga. Recibe texto ya extraido y lo archiva bien. La
extraccion depende de la interfaz del cuaderno, que cambia; separarla mantiene
esta parte estable.
"""

import datetime
import hashlib
import os
import re

from core import corpus


#: Longitud minima para aceptar un texto como fuente. Por debajo de esto suele
#: tratarse de un titulo suelto o de una extraccion que fallo a medias: darlo
#: por bueno meteria ruido que luego aparece citado en los informes.
LONGITUD_MINIMA = 400

#: Caracteres que Windows no admite en un nombre de archivo.
PROHIBIDOS = r'[<>:"/\\|?*\x00-\x1f]'


def nombre_seguro(titulo: str, maximo: int = 90) -> str:
    """
    Convierte el titulo de una fuente en un nombre de archivo valido.

    Args:
        titulo: Titulo tal como aparece en el cuaderno de origen.
        maximo: Longitud maxima del nombre, sin contar la extension.

    Returns:
        Un nombre utilizable en Windows, sin caracteres prohibidos ni espacios
        repetidos. Si el titulo queda vacio tras limpiarlo, devuelve "fuente".
    """
    limpio = re.sub(PROHIBIDOS, " ", titulo or "")
    limpio = " ".join(limpio.split()).strip(" .")
    limpio = limpio[:maximo].strip()
    return limpio or "fuente"


def huella(texto: str) -> str:
    """
    Calcula la huella del contenido, para detectar duplicados.

    Se normalizan los espacios antes de resumir, de modo que dos extracciones
    del mismo documento con distinto formateo se reconozcan como la misma.

    Args:
        texto: Contenido de la fuente.

    Returns:
        Los primeros 16 caracteres del resumen SHA-256.
    """
    normalizado = " ".join((texto or "").split()).lower()
    return hashlib.sha256(normalizado.encode("utf-8", "replace")).hexdigest()[:16]


def huellas_existentes() -> dict:
    """
    Lee las huellas de todo lo ya importado.

    Returns:
        Diccionario `huella -> nombre de archivo`. Solo mira los .md que tengan
        cabecera de procedencia; un documento que el usuario copiara a mano no
        participa en la deteccion de duplicados.
    """
    vistas = {}
    for doc in corpus.inventariar():
        if doc["extension"] != ".md":
            continue
        try:
            with open(doc["ruta"], "r", encoding="utf-8", errors="replace") as fh:
                cabecera = fh.read(900)
        except OSError:
            continue
        hallada = re.search(r"^huella:\s*([0-9a-f]{16})\s*$", cabecera, re.M)
        if hallada:
            vistas[hallada.group(1)] = doc["nombre"]
    return vistas


def cabecera_procedencia(titulo: str, cuaderno: str, url: str,
                         categoria: str, texto: str) -> str:
    """
    Construye la cabecera que encabeza cada fuente importada.

    Va en texto plano y legible a proposito: quien abra el archivo dentro de
    un ano tiene que poder saber de donde salio sin ejecutar nada.

    Args:
        titulo: Titulo de la fuente en el cuaderno de origen.
        cuaderno: Nombre del cuaderno del que procede.
        url: Direccion del cuaderno.
        categoria: Categoria tematica asignada.
        texto: Contenido, para calcular la huella y el tamano.

    Returns:
        La cabecera, lista para anteponer al contenido.
    """
    return (
        f"titulo: {titulo}\n"
        f"cuaderno: {cuaderno}\n"
        f"url: {url}\n"
        f"categoria: {categoria}\n"
        f"importado: {datetime.date.today().isoformat()}\n"
        f"huella: {huella(texto)}\n"
        f"caracteres: {len(texto)}\n"
        "---\n"
        "NOTA: texto extraido del cuaderno de origen para que el agente pueda\n"
        "consultarlo sin conexion. El documento original sigue siendo de sus\n"
        "autores; esta copia es de trabajo.\n"
        "---\n\n"
    )


def importar_fuente(titulo: str, texto: str, categoria: str,
                    cuaderno: str, url: str, carpeta: str = None) -> dict:
    """
    Valida un texto extraido y lo archiva en el corpus con su procedencia.

    Args:
        titulo: Titulo de la fuente.
        texto: Contenido extraido.
        categoria: Subcarpeta tematica donde debe quedar.
        cuaderno: Nombre del cuaderno de origen.
        url: Direccion del cuaderno de origen.
        carpeta: Raiz del corpus. Si se omite, la del proyecto.

    Returns:
        Diccionario con `estado` ("importada", "duplicada" o "rechazada"),
        `motivo`, `ruta` (si se escribio) y `huella`. Nunca lanza excepcion:
        una importacion fallida se describe, no rompe el lote.
    """
    base = carpeta or corpus.carpeta_corpus()
    texto = (texto or "").strip()
    marca = huella(texto)

    if len(texto) < LONGITUD_MINIMA:
        return {"estado": "rechazada", "huella": marca, "ruta": None,
                "motivo": (f"Solo {len(texto)} caracteres; el minimo es "
                           f"{LONGITUD_MINIMA}. Suele indicar que la extraccion "
                           "no llego a cargar el documento completo.")}

    ya = huellas_existentes().get(marca)
    if ya:
        return {"estado": "duplicada", "huella": marca, "ruta": None,
                "motivo": f"Mismo contenido que '{ya}', ya importado."}

    destino = os.path.join(base, nombre_seguro(categoria))
    os.makedirs(destino, exist_ok=True)
    ruta = os.path.join(destino, nombre_seguro(titulo) + ".md")
    contador = 2
    while os.path.exists(ruta):
        ruta = os.path.join(destino, f"{nombre_seguro(titulo)} ({contador}).md")
        contador += 1

    try:
        with open(ruta, "w", encoding="utf-8") as fh:
            fh.write(cabecera_procedencia(titulo, cuaderno, url, categoria, texto))
            fh.write(texto)
    except OSError as exc:
        return {"estado": "rechazada", "huella": marca, "ruta": None,
                "motivo": f"No se pudo escribir el archivo: {exc}"}

    corpus.olvidar()  # el corpus cambio: la cache en memoria ya no vale
    return {"estado": "importada", "huella": marca, "ruta": ruta,
            "motivo": f"{len(texto)} caracteres en '{categoria}'."}


def importar_lote(fuentes: list, cuaderno: str, url: str,
                  carpeta: str = None) -> dict:
    """
    Importa varias fuentes y resume el resultado.

    Args:
        fuentes: Lista de diccionarios con `titulo`, `texto` y `categoria`.
        cuaderno: Nombre del cuaderno de origen.
        url: Direccion del cuaderno de origen.
        carpeta: Raiz del corpus. Si se omite, la del proyecto.

    Returns:
        Diccionario con los recuentos `importadas`, `duplicadas` y
        `rechazadas`, y la lista `detalle` con el resultado de cada una.
    """
    #: El estado que devuelve `importar_fuente` esta en singular; el resumen
    #: cuenta en plural. Se traduce con una tabla explicita en vez de pegar una
    #: "s", que es la clase de atajo que produce claves inexistentes.
    PLURAL = {"importada": "importadas", "duplicada": "duplicadas",
              "rechazada": "rechazadas"}

    resumen = {"importadas": 0, "duplicadas": 0, "rechazadas": 0, "detalle": []}
    for fuente in fuentes:
        salida = importar_fuente(fuente.get("titulo", ""), fuente.get("texto", ""),
                                 fuente.get("categoria", "Sin categoria"),
                                 cuaderno, url, carpeta)
        resumen[PLURAL[salida["estado"]]] += 1
        salida["titulo"] = fuente.get("titulo", "")
        resumen["detalle"].append(salida)
    return resumen


def procedencias() -> list:
    """
    Lista lo ya importado, leyendo la cabecera de cada archivo.

    Returns:
        Lista de diccionarios con `archivo`, `titulo`, `cuaderno`, `categoria`,
        `importado` y `caracteres`, ordenada por categoria y titulo.
    """
    filas = []
    for doc in corpus.inventariar():
        if doc["extension"] != ".md":
            continue
        try:
            with open(doc["ruta"], "r", encoding="utf-8", errors="replace") as fh:
                cabecera = fh.read(900)
        except OSError:
            continue
        if "huella:" not in cabecera:
            continue
        fila = {"archivo": doc["nombre"], "categoria": doc["categoria"]}
        for campo in ("titulo", "cuaderno", "importado", "caracteres"):
            hallado = re.search(rf"^{campo}:\s*(.+)$", cabecera, re.M)
            fila[campo] = hallado.group(1).strip() if hallado else ""
        filas.append(fila)
    filas.sort(key=lambda f: (f["categoria"].upper(), f["titulo"].upper()))
    return filas


def agregar_archivo(ruta: str, categoria: str, carpeta: str = None) -> dict:
    """
    Mete un archivo del disco en el corpus, con su procedencia.

    Es la via para anadir una fuente que YA es un archivo (un PDF, un Word, un
    texto). Para texto suelto que viene de otro sitio, usa `importar_fuente`.

    Qué hace y qué no:
      * Extrae el texto para comprobar que el archivo se puede leer. Un PDF
        escaneado sin capa de texto se rechaza aqui, no mas tarde cuando el
        agente lo cite vacio.
      * Copia el archivo ORIGINAL a la carpeta de la categoria. Se conserva el
        original y no el texto extraido, para que una mejora futura del lector
        de PDF aproveche a los documentos ya cargados.
      * No duplica: si ya hay un archivo con ese nombre en esa categoria, lo
        dice y no lo pisa.

    Args:
        ruta: Archivo a anadir.
        categoria: Subcarpeta tematica. Se crea si no existe.
        carpeta: Raiz del corpus. Si se omite, la del proyecto.

    Returns:
        Diccionario con `estado` ("agregado", "duplicado" o "rechazado"),
        `motivo` y `ruta` del destino.
    """
    import shutil

    base = carpeta or corpus.carpeta_corpus()
    if not os.path.isfile(ruta):
        return {"estado": "rechazado", "ruta": None,
                "motivo": f"No existe el archivo {ruta}."}

    extension = os.path.splitext(ruta)[1].lower()
    if extension not in corpus.EXTENSIONES_LEIBLES:
        return {"estado": "rechazado", "ruta": None,
                "motivo": (f"La extension {extension} no se sabe leer. "
                           f"Admitidas: {', '.join(sorted(corpus.EXTENSIONES_LEIBLES))}.")}

    try:
        trozos = corpus.leer_documento(ruta)
    except Exception as exc:
        return {"estado": "rechazado", "ruta": None,
                "motivo": f"No se pudo leer: {exc}"}

    caracteres = sum(len(t) for _, t in trozos)
    if caracteres < LONGITUD_MINIMA:
        return {"estado": "rechazado", "ruta": None,
                "motivo": (f"Solo se extrajeron {caracteres} caracteres; el "
                           f"minimo es {LONGITUD_MINIMA}. Si es un PDF "
                           f"escaneado, necesita OCR antes.")}

    destino_dir = os.path.join(base, categoria)
    os.makedirs(destino_dir, exist_ok=True)
    destino = os.path.join(destino_dir, os.path.basename(ruta))
    if os.path.exists(destino):
        return {"estado": "duplicado", "ruta": destino,
                "motivo": "Ya hay un archivo con ese nombre en esa categoria."}

    shutil.copy2(ruta, destino)
    return {"estado": "agregado", "ruta": destino,
            "motivo": f"{caracteres:,} caracteres legibles.".replace(",", ".")}


def _consola() -> int:
    """
    Linea de ordenes para anadir fuentes al corpus y ver lo que hay.

    Se usa asi::

        python -m core.importar                              # que hay importado
        python -m core.importar guia.pdf -c "Guias de campo"
        python -m core.importar carpeta/ -c "Catalogos"      # todo lo legible

    Returns:
        0 si todo fue bien, 1 si algo se rechazo.
    """
    import argparse

    parser = argparse.ArgumentParser(
        description="Anade documentos al corpus que estudia el agente.")
    parser.add_argument("origen", nargs="?",
                        help="Archivo o carpeta a anadir. Sin esto, solo informa.")
    parser.add_argument("-c", "--categoria", default="Sin categoria",
                        help="Subcarpeta tematica donde dejarlo.")
    argumentos = parser.parse_args()

    if not argumentos.origen:
        _informar()
        return 0

    origen = argumentos.origen
    candidatos = []
    if os.path.isdir(origen):
        for raiz, _, archivos in os.walk(origen):
            candidatos += [os.path.join(raiz, a) for a in sorted(archivos)]
    else:
        candidatos = [origen]

    print(f"Anadiendo a la categoria '{argumentos.categoria}':\n")
    cuenta = {"agregado": 0, "duplicado": 0, "rechazado": 0}
    for ruta in candidatos:
        resultado = agregar_archivo(ruta, argumentos.categoria)
        cuenta[resultado["estado"]] = cuenta.get(resultado["estado"], 0) + 1
        marca = {"agregado": "OK ", "duplicado": "== ", "rechazado": "NO "}
        print(f"  [{marca[resultado['estado']]}] {os.path.basename(ruta)[:52]}")
        if resultado["estado"] != "agregado":
            print(f"         {resultado['motivo'][:72]}")

    print(f"\n  {cuenta['agregado']} agregados, {cuenta['duplicado']} ya "
          f"estaban, {cuenta['rechazado']} rechazados.")
    if cuenta["agregado"]:
        print("\n  Para que el agente los estudie, reconstruye el indice:")
        print("    curl -X POST http://localhost:8000/corpus/sincronizar \\")
        print('         -H "Content-Type: application/json" -d "{}"')
        print("  (o espera al flujo de n8n de las 02:00)")
    return 1 if cuenta["rechazado"] else 0


def _informar() -> None:
    """Imprime que fuentes importadas hay en el corpus."""
    filas = procedencias()
    estado = corpus.resumen()
    print("CORPUS DEL AGENTE")
    print("=" * 74)
    print(f"  Carpeta    : {corpus.carpeta_corpus()}")
    print(f"  Documentos : {estado['documentos']}")
    print(f"  Fichas     : {estado['fichas']}")
    print(f"  Version    : {estado['version']}")
    for nombre, cuantos in estado.get("categorias", []):
        print(f"      {cuantos:4d}  {nombre}")
    if filas:
        print(f"\n  {len(filas)} fuentes con procedencia registrada.")
    print("\n  Para anadir:  python -m core.importar <archivo|carpeta> "
          "-c \"Categoria\"")


if __name__ == "__main__":
    import sys
    sys.exit(_consola())
