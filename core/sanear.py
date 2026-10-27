"""
Saneamiento del corpus tras una exportacion de cuaderno.
===============================================================================

POR QUE EXISTE ESTE MODULO
--------------------------
Una exportacion de Gemini Notebook no deja una bibliografia limpia: deja lo que
el servicio tenia a mano. En la primera exportacion real del proyecto venian,
mezclados en la misma carpeta:

  * Los archivos comprimidos de la propia exportacion (9,1 MB que no son texto).
  * Siete documentos VACIOS, porque la extraccion de esos PDF fallo.
  * Siete grupos de documentos DUPLICADOS, algunos con titulos distintos.
  * Informes redactados por la IA del cuaderno, junto a las fuentes reales.

Este modulo separa todo eso. No borra nada: lo aparta a `_archivo/` para que se
pueda revisar.

LOS TRES PROBLEMAS, Y POR QUE NINGUNO ES COSMETICO
--------------------------------------------------
1. DUPLICADOS. El mismo texto repetido cuenta como varias evidencias distintas
   en la busqueda. Un parrafo que aparece cuatro veces parece respaldado por
   cuatro fuentes independientes. Peor aun: en la exportacion habia cuatro
   archivos con contenido IDENTICO y titulos distintos, de modo que citar uno
   de esos titulos seria atribuir el texto a un documento que no lo contiene.

2. INFORMES GENERADOS MEZCLADOS CON FUENTES. Los archivos de las carpetas
   "Reports" los redacto la IA del cuaderno a partir de las fuentes. Si el
   agente los cita como si fueran bibliografia cientifica, acaba citandose a si
   mismo: una afirmacion sin respaldo real parece documentada. Se conservan,
   porque son utiles, pero en una categoria que dice lo que son.

3. ARCHIVOS VACIOS Y COMPRIMIDOS. No aportan texto, pero cuentan en el
   inventario y en la huella de version, de modo que dan una idea equivocada de
   cuanta bibliografia hay cargada.

COMO SE USA
-----------
    python -m core.sanear            # muestra lo que haria, sin tocar nada
    python -m core.sanear --aplicar  # lo ejecuta

La primera forma es la de por defecto a proposito: mover documentos de sitio es
una operacion que conviene leer antes de ejecutar.
"""

import hashlib
import os
import shutil

from config import settings
from core import corpus


#: Extensiones que no son bibliografia aunque esten en la carpeta: los
#: comprimidos de la propia exportacion y las imagenes sin texto.
NO_SON_FUENTES = {".zip", ".png", ".jpg", ".jpeg", ".gif"}

#: Archivos de inventario que genera la exportacion. Son metadatos utiles para
#: entender que contenia el cuaderno, pero no son fuentes que citar.
MANIFIESTOS = {"source-artifact-map.csv"}

#: Carpetas del export que contienen texto REDACTADO POR LA IA del cuaderno y
#: no documentos originales.
CARPETAS_GENERADAS = {"reports", "interactive reports", "custom files"}

#: Donde se aparta lo descartado. Vive fuera de `corpus/` para que deje de
#: indexarse, pero sigue en el disco por si hay que revisarlo.
CARPETA_DESCARTE = os.path.join("_archivo", "corpus_descartado")


def huella_archivo(ruta: str) -> str:
    """
    Resumen del contenido de un archivo, para detectar duplicados exactos.

    Args:
        ruta: Ruta del archivo.

    Returns:
        Los primeros 16 caracteres del SHA-256, o cadena vacia si no se pudo
        leer.
    """
    try:
        with open(ruta, "rb") as fh:
            return hashlib.sha256(fh.read()).hexdigest()[:16]
    except OSError:
        return ""


def es_generado(ruta: str, raiz: str) -> bool:
    """
    Indica si un archivo procede de una carpeta de informes de la IA.

    Args:
        ruta: Ruta del archivo.
        raiz: Raiz del corpus.

    Returns:
        True si alguna carpeta de su camino es una de las que la exportacion
        usa para el material redactado por el modelo.
    """
    relativa = os.path.relpath(ruta, raiz)
    partes = [p.lower() for p in relativa.split(os.sep)[:-1]]
    return any(p in CARPETAS_GENERADAS for p in partes)


def cuaderno_de(ruta: str, raiz: str) -> str:
    """
    Deduce de que cuaderno procede un documento.

    Args:
        ruta: Ruta del archivo.
        raiz: Raiz del corpus.

    Returns:
        El nombre de la carpeta de primer nivel, que en una exportacion es el
        nombre del cuaderno. "Sin cuaderno" si el archivo esta suelto.
    """
    partes = os.path.relpath(ruta, raiz).split(os.sep)
    return partes[0] if len(partes) > 1 else "Sin cuaderno"


def planificar(raiz: str = None) -> dict:
    """
    Decide que hacer con cada archivo del corpus, sin tocar nada.

    El orden de las comprobaciones importa: primero se aparta lo que no es
    texto, luego lo vacio, luego los duplicados, y solo lo que sobrevive se
    reclasifica. Asi un archivo vacio nunca se cuenta como duplicado de otro
    archivo vacio.

    Args:
        raiz: Raiz del corpus. Si se omite, la del proyecto.

    Returns:
        Diccionario con las listas `descartar` (tuplas `(ruta, motivo)`) y
        `mover` (tuplas `(ruta, categoria_destino)`), mas el recuento
        `conservados`.
    """
    base = raiz or corpus.carpeta_corpus()
    descartar, mover, vistas = [], [], {}

    archivos = []
    for carpeta, _, nombres in os.walk(base):
        for nombre in nombres:
            if nombre.upper() in corpus.ARCHIVOS_NO_FUENTE:
                continue
            archivos.append(os.path.join(carpeta, nombre))
    archivos.sort()

    for ruta in archivos:
        nombre = os.path.basename(ruta)
        extension = os.path.splitext(nombre)[1].lower()

        if extension in NO_SON_FUENTES:
            descartar.append((ruta, "no es texto (comprimido o imagen)"))
            continue
        if nombre.lower() in MANIFIESTOS:
            descartar.append((ruta, "manifiesto de la exportacion, no una fuente"))
            continue
        try:
            if os.path.getsize(ruta) == 0:
                descartar.append((ruta, "vacio: la extraccion del PDF fallo"))
                continue
        except OSError:
            continue

        marca = huella_archivo(ruta)
        if marca and marca in vistas:
            descartar.append((ruta, f"contenido identico a '{os.path.basename(vistas[marca])}'"))
            continue
        if marca:
            vistas[marca] = ruta

        # Solo se reorganiza lo que viene ANIDADO dentro de una exportacion
        # (cuaderno / subcarpeta / archivo). Un documento que ya esta puesto
        # directamente en una categoria tematica se respeta: puede haberlo
        # colocado ahi una persona a proposito, y moverlo seria deshacer su
        # trabajo sin motivo.
        partes = os.path.relpath(ruta, base).split(os.sep)
        if len(partes) < 3:
            continue
        cuaderno = partes[0]
        sufijo = "Informes generados" if es_generado(ruta, base) else "Fuentes"
        destino = f"{cuaderno} - {sufijo}"
        if os.path.basename(os.path.dirname(ruta)) != destino:
            mover.append((ruta, destino))

    return {"descartar": descartar, "mover": mover,
            "conservados": len(archivos) - len(descartar)}


def aplicar(plan: dict, raiz: str = None) -> dict:
    """
    Ejecuta un plan de saneamiento.

    Args:
        plan: Resultado de `planificar`.
        raiz: Raiz del corpus. Si se omite, la del proyecto.

    Returns:
        Diccionario con los recuentos `descartados`, `movidos` y la lista
        `errores` con lo que no se pudo mover y por que.
    """
    base = raiz or corpus.carpeta_corpus()
    descarte = settings.ruta_proyecto(CARPETA_DESCARTE)
    descartados = movidos = 0
    errores = []

    for ruta, motivo in plan["descartar"]:
        carpeta = os.path.join(descarte, _carpeta_de_motivo(motivo))
        os.makedirs(carpeta, exist_ok=True)
        destino = _sin_colision(os.path.join(carpeta, os.path.basename(ruta)))
        try:
            shutil.move(ruta, destino)
            descartados += 1
        except OSError as exc:
            errores.append(f"{os.path.basename(ruta)}: {exc}")

    for ruta, categoria in plan["mover"]:
        carpeta = os.path.join(base, categoria)
        os.makedirs(carpeta, exist_ok=True)
        destino = _sin_colision(os.path.join(carpeta, os.path.basename(ruta)))
        try:
            shutil.move(ruta, destino)
            movidos += 1
        except OSError as exc:
            errores.append(f"{os.path.basename(ruta)}: {exc}")

    _quitar_carpetas_vacias(base)
    corpus.olvidar()
    return {"descartados": descartados, "movidos": movidos, "errores": errores}


def _carpeta_de_motivo(motivo: str) -> str:
    """
    Agrupa los descartes por motivo, para poder revisarlos despues.

    Args:
        motivo: Texto del motivo.

    Returns:
        Nombre corto de carpeta.
    """
    if motivo.startswith("contenido identico"):
        return "duplicados"
    if motivo.startswith("vacio"):
        return "vacios"
    if motivo.startswith("manifiesto"):
        return "manifiestos"
    return "no son texto"


def _sin_colision(ruta: str) -> str:
    """
    Evita sobrescribir si ya existe un archivo con ese nombre.

    Args:
        ruta: Ruta de destino deseada.

    Returns:
        La misma ruta, o una con un sufijo numerico libre.
    """
    if not os.path.exists(ruta):
        return ruta
    raiz, extension = os.path.splitext(ruta)
    contador = 2
    while os.path.exists(f"{raiz} ({contador}){extension}"):
        contador += 1
    return f"{raiz} ({contador}){extension}"


def _quitar_carpetas_vacias(base: str) -> None:
    """
    Elimina las carpetas que quedaron sin archivos tras mover el contenido.

    Args:
        base: Raiz del corpus.
    """
    for carpeta, _, _ in sorted(os.walk(base), key=lambda x: -len(x[0])):
        if carpeta == base:
            continue
        try:
            if not os.listdir(carpeta):
                os.rmdir(carpeta)
        except OSError:
            pass


if __name__ == "__main__":
    import sys

    ejecutar = "--aplicar" in sys.argv
    plan = planificar()

    print("SANEAMIENTO DEL CORPUS")
    print("=" * 74)
    print(f"  Carpeta: {corpus.carpeta_corpus()}")
    print()

    if plan["descartar"]:
        print(f"SE APARTARIAN {len(plan['descartar'])} ARCHIVOS a {CARPETA_DESCARTE}")
        por_motivo = {}
        for ruta, motivo in plan["descartar"]:
            por_motivo.setdefault(_carpeta_de_motivo(motivo), []).append((ruta, motivo))
        for grupo, items in sorted(por_motivo.items()):
            print(f"\n  {grupo} ({len(items)}):")
            for ruta, motivo in items[:40]:
                print(f"    {os.path.basename(ruta)[:52]:<54} {motivo[:44]}")
    else:
        print("No hay nada que apartar.")

    if plan["mover"]:
        destinos = {}
        for _, categoria in plan["mover"]:
            destinos[categoria] = destinos.get(categoria, 0) + 1
        print(f"\nSE REORGANIZARIAN {len(plan['mover'])} ARCHIVOS en categorias:")
        for categoria, cuantos in sorted(destinos.items()):
            print(f"    {cuantos:4d}  {categoria}")

    print(f"\n  Quedarian {plan['conservados']} documentos utiles en el corpus.")

    if ejecutar:
        print("\nAPLICANDO...")
        salida = aplicar(plan)
        print(f"  apartados: {salida['descartados']}   movidos: {salida['movidos']}")
        for error in salida["errores"]:
            print(f"  ERROR {error}")
        estado = corpus.resumen()
        print(f"\n  Corpus ahora: {estado['documentos']} documentos, "
              f"{estado['fichas']} fichas, version {estado['version']}")
        for nombre, cuantos in estado["categorias"]:
            print(f"    {cuantos:4d}  {nombre}")
    else:
        print("\n  Esto es solo una simulacion. Para ejecutarlo:")
        print("      python -m core.sanear --aplicar")
