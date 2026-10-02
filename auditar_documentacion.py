"""
Auditor de documentacion del proyecto.
===============================================================================

Comprueba que todo el codigo este documentado, en dos niveles:

  NIVEL 1 (existencia)  Cada modulo, clase y funcion tiene docstring.
  NIVEL 2 (calidad)     Cada funcion que recibe parametros los describe bajo
                        "Args:", cada una que devuelve algo tiene "Returns:" y
                        cada una que lanza excepciones tiene "Raises:".

El nivel 2 es el que importa: un docstring de una linea que no dice que recibe
ni que devuelve no le sirve a quien llega nuevo al codigo.

Uso::

    python auditar_documentacion.py
    python auditar_documentacion.py --ruta OTRA_CARPETA
    python auditar_documentacion.py --detalle      # listado archivo por archivo

Devuelve codigo de salida 1 si encuentra algo sin documentar, para poder usarlo
como comprobacion automatica antes de entregar.

EXCLUSIONES
-----------
`cargar_capas_qgis.py` esta excluido a proposito: se ejecuta dentro de la
consola de Python de QGIS Desktop y esta marcado como zona intocable. Si algun
dia se decide documentarlo, basta con sacarlo de `ARCHIVOS_EXCLUIDOS`.
"""

import ast
import os
import argparse


#: Carpetas que nunca se auditan.
CARPETAS_IGNORADAS = {".venv", "venv", "__pycache__", ".git", "temp_uploads",
                      "node_modules"}

#: Archivos excluidos de la auditoria, con el motivo.
ARCHIVOS_EXCLUIDOS = {
    "cargar_capas_qgis.py": "zona intocable: se ejecuta dentro de QGIS Desktop",
}

#: Parametros que no cuentan como argumentos a documentar.
PARAMETROS_IMPLICITOS = {"self", "cls"}


def parametros_de(nodo) -> list:
    """
    Extrae los nombres de los parametros de una funcion.

    Args:
        nodo: Nodo `FunctionDef` o `AsyncFunctionDef` del arbol sintactico.

    Returns:
        Lista de nombres de parametro, sin `self` ni `cls`, incluyendo
        `*args` y `**kwargs` si los hay.
    """
    a = nodo.args
    nombres = [p.arg for p in (a.posonlyargs + a.args + a.kwonlyargs)]
    if a.vararg:
        nombres.append(a.vararg.arg)
    if a.kwarg:
        nombres.append(a.kwarg.arg)
    return [n for n in nombres if n not in PARAMETROS_IMPLICITOS]


def cuerpo_propio(nodo):
    """
    Recorre el cuerpo de una funcion sin entrar en funciones anidadas.

    Sin esta distincion, un `return` o un `raise` que estuviera dentro de una
    closure se le atribuiria a la funcion que la contiene, y el auditor pediria
    documentar un retorno que esa funcion no tiene.

    Args:
        nodo: Nodo cuyo cuerpo se quiere recorrer.

    Yields:
        Los nodos hijos, en profundidad, saltando las definiciones anidadas de
        funciones y clases.
    """
    for hijo in ast.iter_child_nodes(nodo):
        if isinstance(hijo, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            continue
        yield hijo
        yield from cuerpo_propio(hijo)


def devuelve_valor(nodo) -> bool:
    """
    Indica si una funcion devuelve algo distinto de None.

    Args:
        nodo: Nodo de la funcion.

    Returns:
        True si tiene al menos un `return <valor>` propio. Un `return` a secas
        o un `return None` no cuentan: no hay nada que documentar.
    """
    for sub in cuerpo_propio(nodo):
        if isinstance(sub, ast.Return) and sub.value is not None:
            if isinstance(sub.value, ast.Constant) and sub.value.value is None:
                continue
            return True
    return False


def lanza_excepcion(nodo) -> bool:
    """
    Indica si una funcion lanza excepciones de forma explicita.

    Args:
        nodo: Nodo de la funcion.

    Returns:
        True si contiene un `raise` propio (no dentro de una funcion anidada).
    """
    return any(isinstance(sub, ast.Raise) for sub in cuerpo_propio(nodo))


def revisar_funcion(nodo, nombre: str) -> list:
    """
    Comprueba que el docstring de una funcion este completo.

    Args:
        nodo: Nodo de la funcion.
        nombre: Nombre completo (con la clase o funcion que la contiene).

    Returns:
        Lista de textos describiendo lo que falta. Vacia si esta completa.
    """
    doc = ast.get_docstring(nodo)
    if not doc:
        return ["sin docstring"]

    fallos = []

    parametros = parametros_de(nodo)
    if parametros:
        if "Args:" not in doc:
            fallos.append(f"falta la seccion Args ({', '.join(parametros)})")
        else:
            # Se aisla el bloque Args para no dar por descrito un parametro
            # que en realidad solo se menciona en Returns.
            bloque = doc.split("Args:", 1)[1]
            for corte in ("Returns:", "Raises:", "Yields:"):
                bloque = bloque.split(corte, 1)[0]
            ausentes = [p for p in parametros if p not in bloque]
            if ausentes:
                fallos.append(f"parametros sin describir: {', '.join(ausentes)}")

    if devuelve_valor(nodo) and "Returns:" not in doc and "Yields:" not in doc:
        fallos.append("falta la seccion Returns")

    if lanza_excepcion(nodo) and "Raises:" not in doc:
        fallos.append("falta la seccion Raises")

    return fallos


def revisar_archivo(ruta: str, relativa: str) -> dict:
    """
    Audita un archivo Python completo.

    Args:
        ruta: Ruta absoluta del archivo.
        relativa: Ruta relativa, para mostrarla en el informe.

    Returns:
        Diccionario con `modulo_documentado`, `total`, `completas` y
        `problemas` (lista de `(linea, nombre, [fallos])`).

    Raises:
        SyntaxError: Si el archivo no se puede analizar. Quien llame decide si
            lo reporta o lo omite.
    """
    with open(ruta, encoding="utf-8") as f:
        arbol = ast.parse(f.read(), filename=relativa)

    resultado = {
        "modulo_documentado": bool(ast.get_docstring(arbol)),
        "total": 0,
        "completas": 0,
        "problemas": [],
    }

    def recorrer(nodo, prefijo=""):
        """
        Recorre el arbol sintactico acumulando resultados.

        Entra tambien en los bloques `if`, `with` y `try`, para no dejar sin
        auditar las funciones definidas dentro de ellos.

        Args:
            nodo: Nodo del arbol a recorrer.
            prefijo: Nombre acumulado de los contenedores, para que un metodo
                aparezca como `MiClase.mi_metodo` y no solo como `mi_metodo`.

        Returns:
            None. Escribe en el diccionario `resultado` del ambito exterior.
        """
        for hijo in ast.iter_child_nodes(nodo):
            if isinstance(hijo, ast.ClassDef):
                resultado["total"] += 1
                nombre = f"{prefijo}{hijo.name}"
                if ast.get_docstring(hijo):
                    resultado["completas"] += 1
                else:
                    resultado["problemas"].append(
                        (hijo.lineno, f"clase {nombre}", ["sin docstring"])
                    )
                recorrer(hijo, prefijo=f"{nombre}.")

            elif isinstance(hijo, (ast.FunctionDef, ast.AsyncFunctionDef)):
                resultado["total"] += 1
                nombre = f"{prefijo}{hijo.name}"
                fallos = revisar_funcion(hijo, nombre)
                if fallos:
                    resultado["problemas"].append((hijo.lineno, nombre, fallos))
                else:
                    resultado["completas"] += 1
                recorrer(hijo, prefijo=f"{nombre}.")

            else:
                recorrer(hijo, prefijo)

    recorrer(arbol)
    return resultado


def auditar(carpeta: str) -> dict:
    """
    Audita recursivamente todos los archivos Python de una carpeta.

    Args:
        carpeta: Carpeta raiz a auditar.

    Returns:
        Diccionario `{ruta_relativa: resultado_de_revisar_archivo}`.
    """
    resultados = {}

    for raiz, subcarpetas, archivos in os.walk(carpeta):
        subcarpetas[:] = [s for s in subcarpetas if s not in CARPETAS_IGNORADAS]

        for archivo in sorted(archivos):
            if not archivo.endswith(".py") or archivo in ARCHIVOS_EXCLUIDOS:
                continue

            completa = os.path.join(raiz, archivo)
            relativa = os.path.relpath(completa, carpeta)
            try:
                resultados[relativa] = revisar_archivo(completa, relativa)
            except SyntaxError as exc:
                resultados[relativa] = {
                    "modulo_documentado": False, "total": 0, "completas": 0,
                    "problemas": [(getattr(exc, "lineno", 0), "(archivo)",
                                   [f"error de sintaxis: {exc.msg}"])],
                }

    return resultados


def main() -> int:
    """
    Punto de entrada de linea de comandos.

    Returns:
        0 si todo esta documentado, 1 si queda algo pendiente. Ese codigo
        permite encadenarlo en una comprobacion automatica.
    """
    parser = argparse.ArgumentParser(
        description="Verifica que todo el codigo del proyecto este documentado."
    )
    parser.add_argument(
        "--ruta", default=os.path.dirname(os.path.abspath(__file__)),
        help="Carpeta a auditar. Por defecto, la del proyecto.",
    )
    parser.add_argument(
        "--detalle", action="store_true",
        help="Muestra el recuento archivo por archivo.",
    )
    argumentos = parser.parse_args()

    resultados = auditar(argumentos.ruta)

    total = sum(r["total"] for r in resultados.values())
    completas = sum(r["completas"] for r in resultados.values())
    modulos_sin_doc = [a for a, r in resultados.items()
                       if not r["modulo_documentado"]]
    pendientes = {a: r["problemas"] for a, r in resultados.items()
                  if r["problemas"]}

    print("=" * 78)
    print("AUDITORIA DE DOCUMENTACION")
    print("=" * 78)
    print(f"Archivos revisados     : {len(resultados)}")
    print(f"Modulos sin docstring  : {len(modulos_sin_doc)}")
    for archivo in modulos_sin_doc:
        print(f"    - {archivo}")
    print(f"Clases y funciones     : {total}")
    print(f"  documentadas         : {completas}")
    print(f"  incompletas          : {total - completas}")
    if total:
        print(f"  cobertura            : {completas / total * 100:.1f}%")

    if ARCHIVOS_EXCLUIDOS:
        print("\nExcluidos a proposito:")
        for archivo, motivo in ARCHIVOS_EXCLUIDOS.items():
            print(f"    - {archivo}  ({motivo})")

    if pendientes:
        print("\n--- PENDIENTES ---")
        for archivo in sorted(pendientes):
            print(f"\n{archivo}")
            for linea, nombre, fallos in pendientes[archivo]:
                print(f"  linea {linea:<5} {nombre}")
                for fallo in fallos:
                    print(f"      - {fallo}")

    if argumentos.detalle:
        print("\n--- DETALLE POR ARCHIVO ---")
        for archivo in sorted(resultados):
            r = resultados[archivo]
            marca = "OK" if not r["problemas"] and r["modulo_documentado"] else "!!"
            print(f"  [{marca}] {archivo:<40} "
                  f"{r['completas']:>3}/{r['total']:<3} documentadas")

    print("\n" + "=" * 78)
    if not pendientes and not modulos_sin_doc:
        print("Todo el codigo esta documentado.")
        print("=" * 78)
        return 0

    print("Queda documentacion pendiente (ver el listado de arriba).")
    print("=" * 78)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
