"""
Auditor de rutas y datos escritos a mano ("quemados") en el proyecto.
===============================================================================

Recorre el codigo fuente buscando literales que aten el proyecto a un
computador, a una carpeta o a un dato concreto. Sirve como control de calidad:
ejecutelo despues de cualquier cambio para comprobar que no se colo una ruta
absoluta nueva.

Uso::

    python buscar_rutas.py                  # revisa la carpeta del proyecto
    python buscar_rutas.py --ruta OTRA      # revisa otra carpeta

QUE SE CORRIGIO DE LA VERSION ANTERIOR
--------------------------------------
1. Tenia la ruta del proyecto escrita a mano::

       proyecto_dir = r"C:\\UNVIERSIDADES\\MANUELA\\PRACTICAS II\\proyecto"

   Es decir: el detector de rutas quemadas tenia una ruta quemada. Ahora la
   deduce de la ubicacion del propio archivo.

2. Se excluia a si mismo y a los archivos .md de la revision, con lo que
   ocultaba justo los hallazgos que mas facil era pasar por alto. Ahora se
   revisa todo y los archivos de documentacion se marcan con severidad baja,
   pero se marcan.
"""

import os
import re
import argparse


#: Patrones que delatan un valor atado al entorno de una persona.
#: Cada entrada es (etiqueta, expresion regular, severidad).
PATRONES = [
    ("Ruta absoluta de Windows",
     r"[\"'][A-Za-z]:\\\\?[^\"'\n]{3,}", "ALTA"),
    ("Ruta absoluta de Unix",
     r"[\"']/(?:home|Users|mnt|opt)/[^\"'\n]{3,}", "ALTA"),
    ("Carpeta de perfil de usuario",
     r"Users[\\/][A-Za-z0-9._-]+", "ALTA"),
    ("Nombre de carpeta del entorno de la practica",
     r"UNVIERSIDADES|PRACTICAS II|Productos entregados", "ALTA"),
    ("Version concreta de QGIS",
     r"QGIS\s*3\.\d+\.\d+", "MEDIA"),
    ("Identificador de descarga de GBIF",
     r"\d{7}-\d{15,}", "MEDIA"),
    ("Direccion de red fija",
     r"https?://(?:127\.0\.0\.1|localhost)(?::\d+)?", "MEDIA"),
    # Exige comillas: sin ellas, una linea tan inocente como
    # `api_key=st.session_state.api_key` se marcaba como secreto filtrado.
    ("Posible clave de API",
     r"(?:api[_-]?key|token|secret)\s*[=:]\s*[\"'][A-Za-z0-9_\-\.]{20,}[\"']",
     "ALTA"),
]

#: Si la linea contiene alguno de estos fragmentos, el hallazgo se ignora:
#: son las formas correctas de construir rutas o de documentar el problema.
EXENCIONES = [
    "os.path.dirname", "os.path.join", "os.path.abspath", "__file__",
    "BASE_DIR", "PRACTICAS_DIR", "settings.", "ruta_proyecto",
    "# ANTES", "ANTES ", "QUE SE DESQUEMO", "quemad",
]

#: Fragmentos que bajan un hallazgo a severidad INFO. Son rutas que SI deben
#: estar en el codigo:
#:   - ejemplos ficticios que se le muestran al usuario en un mensaje de ayuda;
#:   - ubicaciones de instalacion estandar usadas como punto de partida de una
#:     BUSQUEDA, no como ruta fija, y sobrescribibles por variable de entorno.
PATRONES_LEGITIMOS = [
    "C:\\\\ruta", "C:\\ruta", "C:\\\\datos", "C:\\datos",   # ejemplos ficticios
    "Program Files", "OSGeo4W", "SystemRoot", "WINDIR",     # raices de busqueda
]

#: Carpetas que nunca se revisan.
#: `_archivo` guarda material retirado del proyecto: sus rutas antiguas ya no
#: afectan a nada y reportarlas solo produce ruido.
CARPETAS_IGNORADAS = {".venv", "venv", ".git", "__pycache__", "temp_uploads",
                      "node_modules", "_archivo"}

#: Archivos que genera el propio sistema. Su contenido son datos, no codigo,
#: asi que auditarlos no aporta nada.
#:
#: `capas_extra/rutas.txt` entra aqui por un motivo distinto: su contenido son
#: rutas absolutas A PROPOSITO. Es el archivo donde el usuario apunta donde
#: tiene sus propias capas, precisamente para NO tener que escribirlas en el
#: codigo. Marcarlo seria contar como problema justo la solucion.
ARCHIVOS_GENERADOS = {
    "cache_catalogo.json", "cache_municipios_nacional.geojson",
    "biodiversidad_filtrada.csv", "municipios_filtrados.geojson",
    "rutas.txt",
}

#: Extensiones que se revisan.
EXTENSIONES = (".py", ".bat", ".ps1", ".toml", ".md", ".json", ".cfg", ".txt")

#: Archivos cuyos hallazgos se reportan con severidad rebajada: son
#: documentacion o plantillas, donde un ejemplo de ruta es legitimo.
ARCHIVOS_INFORMATIVOS = {"README.md", ".env.example", "buscar_rutas.py"}


def lineas_de_prosa(ruta_archivo: str) -> set:
    """
    Localiza las lineas de un archivo Python que son comentario o texto.

    POR QUE IMPORTA:
    una ruta absoluta dentro de un docstring que EXPLICA por que esa ruta ya no
    se usa no es un problema, es documentacion. Sin esta distincion el auditor
    marcaba en rojo sus propios comentarios explicativos, y un informe lleno de
    falsos positivos deja de leerse.

    Se usa `tokenize`, que marca exactamente que tramos del archivo son
    comentarios y cadenas de texto (docstrings incluidos).

    Args:
        ruta_archivo: Ruta del archivo a analizar.

    Returns:
        Conjunto de numeros de linea (base 1) ocupados por prosa. Conjunto
        vacio si el archivo no es Python o no se puede tokenizar.
    """
    if not ruta_archivo.endswith(".py"):
        return set()

    import tokenize
    prosa = set()
    try:
        with open(ruta_archivo, "rb") as f:
            for token in tokenize.tokenize(f.readline):
                if token.type in (tokenize.COMMENT, tokenize.STRING):
                    prosa.update(range(token.start[0], token.end[0] + 1))
    except (OSError, tokenize.TokenError, SyntaxError, IndentationError):
        return set()
    return prosa


def revisar_archivo(ruta_archivo: str):
    """
    Busca patrones sospechosos dentro de un archivo.

    Cada hallazgo se clasifica en tres severidades:
        ALTA  : ruta o dato atado al entorno, en codigo ejecutable.
        MEDIA : version o direccion fija que conviene revisar.
        INFO  : aparece en documentacion, en un ejemplo o en un archivo
                informativo. Se reporta para que quede a la vista, pero no es
                un problema.

    Args:
        ruta_archivo: Ruta del archivo a revisar.

    Returns:
        Lista de diccionarios `{linea, severidad, tipo, texto}`.
    """
    hallazgos = []
    nombre = os.path.basename(ruta_archivo)
    informativo = nombre in ARCHIVOS_INFORMATIVOS
    prosa = lineas_de_prosa(ruta_archivo)

    try:
        with open(ruta_archivo, "r", encoding="utf-8", errors="replace") as f:
            lineas = f.readlines()
    except OSError:
        return hallazgos

    for numero, linea in enumerate(lineas, start=1):
        if any(exencion in linea for exencion in EXENCIONES):
            continue

        # Una ruta legitima (ejemplo ficticio o raiz de busqueda) se reporta
        # igualmente, pero como INFO: sigue siendo visible para quien audite,
        # sin ensuciar el listado de problemas reales.
        legitima = any(frag in linea for frag in PATRONES_LEGITIMOS)
        es_prosa = numero in prosa

        for etiqueta, patron, severidad in PATRONES:
            if re.search(patron, linea):
                hallazgos.append({
                    "linea": numero,
                    "severidad": ("INFO"
                                  if (informativo or legitima or es_prosa)
                                  else severidad),
                    "tipo": etiqueta,
                    "texto": linea.strip()[:140],
                })
                break  # un hallazgo por linea basta

    return hallazgos


def auditar(carpeta: str) -> dict:
    """
    Revisa recursivamente una carpeta de proyecto.

    Args:
        carpeta: Carpeta raiz a auditar.

    Returns:
        Diccionario `{ruta_relativa: [hallazgos]}`.
    """
    resultados = {}

    for raiz, subcarpetas, archivos in os.walk(carpeta):
        subcarpetas[:] = [s for s in subcarpetas if s not in CARPETAS_IGNORADAS]

        for archivo in archivos:
            if not archivo.endswith(EXTENSIONES):
                continue
            if archivo in ARCHIVOS_GENERADOS:
                continue

            completa = os.path.join(raiz, archivo)
            hallazgos = revisar_archivo(completa)
            if hallazgos:
                resultados[os.path.relpath(completa, carpeta)] = hallazgos

    return resultados


def main():
    """Punto de entrada de linea de comandos."""
    parser = argparse.ArgumentParser(
        description="Detecta rutas y datos escritos a mano en el proyecto."
    )
    parser.add_argument(
        "--ruta",
        default=os.path.dirname(os.path.abspath(__file__)),
        help="Carpeta a auditar. Por defecto, la del propio proyecto.",
    )
    parser.add_argument(
        "--solo-altas", action="store_true",
        help="Muestra unicamente los hallazgos de severidad ALTA.",
    )
    argumentos = parser.parse_args()

    print("=" * 78)
    print(f"AUDITORIA DE RUTAS Y DATOS QUEMADOS")
    print(f"Carpeta: {argumentos.ruta}")
    print("=" * 78)

    resultados = auditar(argumentos.ruta)
    total = 0

    for archivo, hallazgos in sorted(resultados.items()):
        visibles = [
            h for h in hallazgos
            if not argumentos.solo_altas or h["severidad"] == "ALTA"
        ]
        if not visibles:
            continue

        print(f"\n{archivo}")
        for hallazgo in visibles:
            print(f"  [{hallazgo['severidad']:<5}] linea {hallazgo['linea']:<5} "
                  f"{hallazgo['tipo']}")
            print(f"          {hallazgo['texto']}")
            total += 1

    print("\n" + "=" * 78)
    if total == 0:
        print("Sin hallazgos. Ninguna ruta ni dato atado al entorno.")
    else:
        print(f"{total} hallazgos. Revise si cada uno es legitimo "
              f"(documentacion, plantilla) o debe moverse a config/settings.py.")
    print("=" * 78)


if __name__ == "__main__":
    main()
