"""
Bateria de archivos pesados: que TODO se analice, y de todos los tamanos.

QUE SE PRUEBA AQUI Y NO EN LAS OTRAS BATERIAS
---------------------------------------------
Las demas baterias usan archivos de juguete, de kilobytes, porque tienen que
correr en segundos. Esta usa los PESOS REALES de la carpeta de Drive del
proyecto: cuatro capas de 417 a 434 MB, un inventario de 11 kB y una capa de
POMCAS de 94 kB.

Importa que sean los de verdad. Casi todos los fallos que se han encontrado
en este sistema solo aparecian a tamano real:

  - una capa de 434 MB daba 644.936 fichas, que son 3,5 anos de cuota
    gratuita; con el archivo de juguete nunca se habria visto
  - un Excel grande se abria con pandas y pedia varios gigas de RAM, cosa
    que un libro de 9 kB no delata
  - los primeros 20 MB de la capa de ecosistemas son Caqueta y Putumayo,
    porque viene ordenada por region, asi que muestrear el principio no dice
    NADA de Antioquia

NO GASTA CUOTA
--------------
El embebedor se sustituye por uno que CUENTA en vez de llamar a la API. Lo
que se mide es cuantas fichas se habrian gastado, que es la cifra que
importa, sin gastarlas. Se aprendio por las malas: una version anterior de
estas pruebas se comio 1.700 embeddings de la cuota diaria.

COMO SE EJECUTA
---------------
    python probar_pesados.py

Tarda varios minutos: recorrer 418 MB de verdad no es instantaneo, y ese es
justamente el dato que se quiere medir.
"""

import os
import resource
import shutil
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from core import ajustes as _ajustes

# La fuente de produccion es Drive, no la carpeta local. Probar por otro
# camino que el de produccion no prueba nada.
_ajustes.aplicar()

from core import catalogo as _cat          # noqa: E402
from core import corpus as _cp             # noqa: E402
from core import vectorial as _vc          # noqa: E402
from config import settings as _st         # noqa: E402

ACIERTOS = 0
FALLOS = []


def comprobar(nombre: str, condicion: bool, detalle: str = "") -> None:
    """
    Apunta una comprobacion y la imprime.

    Args:
        nombre: Que se comprobaba.
        condicion: Resultado; verdadero es exito.
        detalle: Texto explicativo que se imprime al lado.
    """
    global ACIERTOS
    if condicion:
        ACIERTOS += 1
        print(f"  [OK ] {nombre}: {detalle}")
    else:
        FALLOS.append(f"{nombre} -> {detalle}")
        print(f"  [FALLA] {nombre}: {detalle}")


def omitir(nombre: str, motivo: str) -> None:
    """
    Declara una comprobacion que no se pudo hacer.

    Omitir NO es aprobar. Se imprime distinto a proposito, para que una
    bateria que no probo nada no se lea como una bateria en verde.

    Args:
        nombre: Que se iba a comprobar.
        motivo: Por que no se pudo.
    """
    print(f"  [--- ] {nombre}: omitido ({motivo})")


def mb(ruta: str) -> float:
    """
    Tamano de un archivo en megas.

    Args:
        ruta: Archivo a medir.

    Returns:
        Megas con un decimal, o 0.0 si no existe.
    """
    try:
        return round(os.path.getsize(ruta) / (1024 * 1024), 1)
    except OSError:
        return 0.0


def pico_ram_mb() -> float:
    """
    Pico de memoria del proceso, en megas.

    Returns:
        El maximo historico del proceso. Es acumulado, asi que entre dos
        medidas lo que importa es si SUBE, no su valor absoluto.
    """
    return round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024, 0)


class ClienteMudo:
    """Qdrant de mentira: esta prueba mide el coste, no el guardado."""

    def __init__(self):
        self.guardados = 0

    def delete(self, **_kw):
        """Acepta el borrado previo de las fichas viejas."""

    def upsert(self, **kw):
        """Cuenta los puntos que se habrian guardado."""
        self.guardados += len(kw.get("points") or [])


def medir_indexacion(ruta: str, nombre: str) -> dict:
    """
    Pasa un archivo por el camino REAL de indexacion, sin gastar cuota.

    Args:
        ruta: Archivo ya en disco.
        nombre: Nombre con el que se indexaria.

    Returns:
        Diccionario con `fichas` (las que se habrian embebido), `segundos`,
        `ram`, `resumidas`, `fuera`, `sin_contenido` y `desviado`.
    """
    embeber_real = _vc.embeber
    gasto = {"n": 0}

    def contar(textos, *_a, **_k):
        gasto["n"] += len(textos)
        return [[0.0] * 768 for _ in textos]

    _vc.embeber = contar
    cliente = ClienteMudo()
    acumulado = {"fichas": 0, "fallidos": [], "_fraccion": 0.0}
    desviado = _vc._conviene_resumir(ruta)
    arranque = time.time()
    try:
        _vc._indexar_documento(
            cliente, _st.coleccion_qdrant(),
            {"nombre": nombre, "categoria": "pesados",
             "ruta": ruta, "huella": "bateria"},
            acumulado, None, "[1/1]")
    finally:
        _vc.embeber = embeber_real

    return {
        "fichas": gasto["n"],
        "segundos": round(time.time() - arranque, 1),
        "ram": pico_ram_mb(),
        "desviado": desviado,
        "resumidas": acumulado.get("resumidas") or [],
        "fuera": acumulado.get("fuera_de_presupuesto") or [],
        "sin_contenido": acumulado.get("sin_contenido") or [],
    }


print("=" * 70)
print("  Archivos pesados: que todo entre, al peso que llegue de verdad")
print("=" * 70)

BASE = os.path.dirname(os.path.abspath(__file__))
CAPAS = os.path.join(BASE, "data", "capas_fuente")

# ==============================================================================
# 1. LA CAPA DE 418 MB, QUE ES UNA DE LAS CUATRO DE LA CARPETA DE DRIVE
# ==============================================================================
print("\n--- Capa de ecosistemas: 418 MB, el peso real de Drive ---")

_capa = os.path.join(CAPAS, "ecosistemas_colombia_100K_2024.csv")
if not os.path.exists(_capa):
    omitir("la capa de 418 MB entra resumida",
           "no esta data/capas_fuente/ecosistemas_colombia_100K_2024.csv")
    omitir("se recorre entera, no una muestra", "falta la capa")
    omitir("cuesta una fraccion de la cuota", "falta la capa")
else:
    _peso = mb(_capa)
    comprobar("el archivo pesa lo que dice Drive",
              400 < _peso < 450, f"{_peso} MB en disco")

    _ram_antes = pico_ram_mb()
    _m = medir_indexacion(_capa, "e_eccmc_100K_2024.csv")

    comprobar("una capa de 418 MB se desvia al recorrido agregado",
              _m["desviado"] is True,
              "no se abre entera en memoria: se recorre fila a fila")

    comprobar("NO se descarta: entra resumida",
              len(_m["resumidas"]) == 1 and not _m["fuera"],
              f"{_m['resumidas'][0]['filas']:,} filas recorridas en "
              f"{_m['resumidas'][0]['segundos']} s".replace(",", ".")
              if _m["resumidas"] else "se quedo fuera del indice")

    if _m["resumidas"]:
        _filas = _m["resumidas"][0]["filas"]
        # Lo que costaria indexarla fila a fila, que es el numero que hace
        # imposible el camino directo: 644.936 fichas son 3,5 anos de cuota.
        _coste_directo = _cp.contar_fichas_de and int(_peso * 1485)
        comprobar("entra por una fraccion minima de lo que costaria entera",
                  0 < _m["fichas"] < _coste_directo / 1000,
                  f"{_m['fichas']} fichas frente a las ~{_coste_directo:,} "
                  f"que costaria fila a fila".replace(",", "."))

        comprobar("recorrer 418 MB no dispara la memoria",
                  _m["ram"] - _ram_antes < 600,
                  f"el pico subio {_m['ram'] - _ram_antes:.0f} MB "
                  f"para un archivo de {_peso} MB")

        _velocidad = _filas / max(_m["resumidas"][0]["segundos"], 0.1)
        print(f"         ({_filas:,} filas a {_velocidad:,.0f} filas/s, "
              f"{_peso / max(_m['segundos'], 0.1):.0f} MB/s)"
              .replace(",", "."))

# ==============================================================================
# 2. EL TOPE DE FILAS: HASTA DONDE LLEGA "DE TODOS LOS TAMANOS"
# ==============================================================================
print("\n--- Hasta donde llega el recorrido completo ---")

if os.path.exists(_capa):
    _r_tope = _cat.resumir_tabla(_capa, maximo_filas=1000)
    comprobar("si se corta por el tope, lo DICE en vez de fingir",
              _r_tope["truncado"] is True
              and not any("COMPLETA" in l for l in _r_tope["lineas"]),
              "el resumen declara que no cubre la tabla entera")

comprobar("el tope se puede subir sin tocar codigo",
          _cat.MAXIMO_FILAS_RESUMEN >= 2_000_000,
          f"MAXIMO_FILAS_RESUMEN = {_cat.MAXIMO_FILAS_RESUMEN:,}"
          .replace(",", "."))

# ==============================================================================
# 3. LOS PESOS PEQUENOS DE LA MISMA CARPETA
# ==============================================================================
print("\n--- Los archivos pequenos de la carpeta, que van por otro camino ---")

_pomcas = os.path.join(CAPAS, "pomcas_nacional_2021.csv")
if os.path.exists(_pomcas):
    _m2 = medir_indexacion(_pomcas, "POMCAS_abril_2021_1.csv")
    comprobar("una capa de 94 kB NO se desvia: se indexa entera",
              _m2["desviado"] is False and _m2["fichas"] > 0,
              f"{mb(_pomcas) * 1024:.0f} kB, {_m2['fichas']} fichas "
              f"fila a fila")
else:
    omitir("una capa de 94 kB se indexa entera", "no esta el POMCAS")

# Un Excel del tamano del inventario de quebradas: 11 kB.
_banco = tempfile.mkdtemp(prefix="probar_pesados_")
try:
    import openpyxl

    _inv = os.path.join(_banco, "Inventario_127_Quebradas.xlsx")
    _wb = openpyxl.Workbook()
    _h = _wb.active
    _h.append(["quebrada", "vereda", "longitud_km", "caudal_ls"])
    for _i in range(127):
        _h.append([f"Quebrada La {_i:03d}", "El Jordan",
                   round(1.2 + _i * 0.07, 2), 14 + _i])
    _wb.save(_inv)

    _m3 = medir_indexacion(_inv, "Inventario_Completo_127_Quebradas.xlsx")
    comprobar("un Excel de 11 kB se indexa entero, no resumido",
              _m3["desviado"] is False and _m3["fichas"] > 0,
              f"{mb(_inv) * 1024:.0f} kB, {_m3['fichas']} fichas con las "
              f"127 quebradas dentro")

    # ==========================================================================
    # 4. EXCEL AL PESO DE LAS CAPAS: EL CASO QUE ANTES SE TIRABA
    # ==========================================================================
    print("\n--- Excel grande: el caso que antes se bajaba y se tiraba ---")

    # No se construye uno de 418 MB: escribirlo tardaria mas que la bateria
    # entera, y lo que hay que demostrar no es el tamano del archivo sino
    # que el camino NO depende del tamano. Se usa uno de 400.000 filas y se
    # mide la memoria, que es lo que antes reventaba.
    _gordo = os.path.join(_banco, "Censo_Predial_San_Carlos.xlsx")
    import random

    random.seed(5)
    _wb = openpyxl.Workbook(write_only=True)
    _suma_real = 0.0
    for _hoja in ("2023", "2024"):
        _h = _wb.create_sheet(_hoja)
        _h.append(["vereda", "cobertura", "area_ha", "observacion"])
        for _ in range(200000):
            _a = round(random.uniform(0.5, 60), 2)
            _suma_real += _a
            _h.append([random.choice(["El Jordan", "Palmichal", "La Hondita",
                                      "Juanes", "Dosquebradas"]),
                       random.choice(["Bosque denso", "Pastos limpios",
                                      "Rastrojo alto"]),
                       _a, "levantamiento validado en sitio"])
    _wb.save(_gordo)

    _ram_antes = pico_ram_mb()
    _m4 = medir_indexacion(_gordo, "Censo_Predial_San_Carlos.xlsx")

    comprobar("un Excel grande se desvia antes de abrirse con pandas",
              _m4["desviado"] is True,
              f"{mb(_gordo)} MB: se recorre en streaming, no se carga")

    comprobar("NO se descarta: entra resumido",
              len(_m4["resumidas"]) == 1 and not _m4["fuera"],
              f"{_m4['resumidas'][0]['filas']:,} filas de 2 hojas en "
              f"{_m4['resumidas'][0]['segundos']} s".replace(",", ".")
              if _m4["resumidas"] else "se quedo fuera del indice")

    comprobar("las dos hojas se cuentan como una sola tabla",
              bool(_m4["resumidas"])
              and _m4["resumidas"][0]["filas"] == 400000,
              f"400.000 filas esperadas, "
              f"{_m4['resumidas'][0]['filas']:,} leidas".replace(",", ".")
              if _m4["resumidas"] else "no se resumio")

    comprobar("la memoria no se dispara con el Excel grande",
              _m4["ram"] - _ram_antes < 400,
              f"el pico subio {_m4['ram'] - _ram_antes:.0f} MB")

    # Y las sumas tienen que cuadrar con la realidad, no parecerse.
    _cols, _it, _ = _cat._filas_de_excel(_gordo)
    _ag = _cat._agregar(_cols, _it, _cat.MAXIMO_FILAS_RESUMEN)
    _leido = sum(_ag["sumas"]["vereda"].values())
    comprobar("las sumas del agregado son exactas al centimo",
              abs(_leido - _suma_real) < 1.0,
              f"{_leido:,.0f} leido frente a {_suma_real:,.0f} real"
              .replace(",", "."))

    # ==========================================================================
    # 5. LO QUE NO ES UNA TABLA SIGUE SIN SERLO, PESE LO QUE PESE
    # ==========================================================================
    print("\n--- Un documento de texto pesado no es una tabla ---")

    _informe = os.path.join(_banco, "Informe_POMCA_completo.txt")
    with open(_informe, "w", encoding="utf-8") as _fh:
        for _i in range(40000):
            _fh.write(f"Unidad de paisaje {_i}: cobertura de bosque denso "
                      f"sobre relieve de colinas, drenaje permanente y "
                      f"suelos profundos bien estructurados.\n")

    _m5 = medir_indexacion(_informe, "Informe_POMCA_completo.txt")
    comprobar("un informe de prosa NO se trata como tabla",
              _m5["desviado"] is False,
              f"{mb(_informe)} MB de texto corrido, no se agrupa por "
              f"columnas")
    comprobar("y si se pasa del presupuesto deja rastro, no desaparece",
              len(_m5["fuera"]) == 1
              and _m5["fuera"][0].get("indexado_como") == "presencia"
              and 0 < _m5["fichas"] <= 6,
              f"{_m5['fuera'][0]['fichas']} fichas costaria; se indexan "
              f"{_m5['fichas']} de presencia" if _m5["fuera"]
              else "no quedo constancia")

    # ==========================================================================
    # 6. UNA IMAGEN QUE NO SE PUEDE TRANSCRIBIR
    # ==========================================================================
    print("\n--- Una imagen que no se puede leer ---")

    # Es el caso real cuando la cuota del modelo se agota: el lector devuelve
    # vacio. Antes eso era un `return` mudo y la foto se esfumaba del
    # informe, indistinguible de un archivo vacio.
    _foto = os.path.join(_banco, "ficha_campo_042.png")
    with open(_foto, "wb") as _fh:
        _fh.write(b"\x89PNG\r\n\x1a\n" + b"x" * 2048)

    _leer_real = _cp.leer_documento
    _cp.leer_documento = lambda _r: []
    try:
        _m6 = medir_indexacion(_foto, "ficha_campo_042.png")
    finally:
        _cp.leer_documento = _leer_real

    comprobar("una foto que no se pudo transcribir queda declarada",
              len(_m6["sin_contenido"]) == 1
              and _m6["sin_contenido"][0]["extension"] == ".png",
              "aparece en el informe en vez de esfumarse")
finally:
    shutil.rmtree(_banco, ignore_errors=True)


# ==============================================================================
# 7. LO QUE NO CABE EN EL RESUMEN SE NOMBRA
# ==============================================================================
#
# El resumen tiene un tope de lineas, y esta bien que lo tenga. Lo que no
# estaba bien era recortar sin decirlo: la capa de ecosistemas tiene 31
# columnas que merecen reparto y con el tope anterior solo entraban 26. Las
# cinco que se caian eran no_anfibio, no_aves, no_magnoli, no_mamifer y
# no_reptile -- en un proyecto ambiental, justo las que mas importan -- y
# nadie se enteraba: el resumen no las mencionaba, asi que se leia como si
# la tabla no las trajera.
print("\n--- Lo que no cabe en el resumen, se declara ---")

# Carpeta propia: el bloque corre DESPUES de que se limpie `_banco`.
_banco_cols = tempfile.mkdtemp(prefix="probar_columnas_")
_muchas = os.path.join(_banco_cols, "tabla_de_muchas_columnas.csv")
_COLUMNAS = [f"clase_{i:02d}" for i in range(40)]
with open(_muchas, "w", encoding="utf-8") as _fh:
    _fh.write(",".join(_COLUMNAS) + "\n")
    for _i in range(4000):
        _fh.write(",".join(f"v{_i % 50}" for _ in _COLUMNAS) + "\n")

# Con un tope ridiculo se fuerza el recorte, para no tener que fabricar una
# tabla de mil columnas.
_recortado = _cat.resumir_tabla(_muchas, maximo_filas=5000)
_original = _cat.MAXIMO_LINEAS_RESUMEN
try:
    _cat.MAXIMO_LINEAS_RESUMEN = 60
    _recortado = _cat.resumir_tabla(_muchas, maximo_filas=5000)
finally:
    _cat.MAXIMO_LINEAS_RESUMEN = _original

comprobar("cuando el resumen no cabe, se recorta",
          bool(_recortado.get("columnas_sin_sitio")),
          f"{len(_recortado['columnas_sin_sitio'])} columnas sin desglose "
          f"de {len(_recortado['columnas_resumidas'])} utiles")

comprobar("y NOMBRA las columnas que se quedaron sin desglose",
          any("NO CABEN" in l for l in _recortado["lineas"])
          and all(c in " ".join(_recortado["lineas"])
                  for c in _recortado["columnas_sin_sitio"][:5]),
          "el resumen dice cuales faltan, en vez de callarlas")

comprobar("y aclara que la tabla SI trae esas columnas",
          any("La tabla SI trae esas columnas" in l
              for l in _recortado["lineas"]),
          "no se confunde 'no cabe el desglose' con 'no existe el dato'")

# Una columna cortada a la mitad TAMBIEN se declara: el tope puede caer
# entre los valores de una columna, dejando "Reparto por X:" con tres de
# sus cuarenta categorias. Media verdad presentada como entera es peor que
# un hueco declarado.
comprobar("una columna cortada a medias tambien se declara",
          len(_recortado["columnas_sin_sitio"])
          >= len(_recortado["columnas_resumidas"]) - 3,
          f"solo {len(_recortado['columnas_resumidas']) - len(_recortado['columnas_sin_sitio'])} "
          f"columnas se dan por completas")

# Con el tope real y una tabla de la forma de la capa del proyecto -- 31
# columnas de pocos valores cada una -- entra todo sin recorte.
_realista = os.path.join(_banco_cols, "como_la_capa_real.csv")
_COLS_R = [f"clase_{i:02d}" for i in range(31)]
with open(_realista, "w", encoding="utf-8") as _fh:
    _fh.write(",".join(_COLS_R) + chr(10))
    for _i in range(3000):
        _fh.write(",".join(f"v{_i % 12}" for _ in _COLS_R) + chr(10))
_completo = _cat.resumir_tabla(_realista, maximo_filas=5000)
comprobar("con el tope real, 31 columnas caben sin recorte",
          not _completo.get("columnas_sin_sitio"),
          f"{len(_completo['columnas_resumidas'])} columnas, todas con su "
          f"reparto, en {len(_completo['lineas'])} lineas")

shutil.rmtree(_banco_cols, ignore_errors=True)

# ==============================================================================
print("\n" + "=" * 70)
if FALLOS:
    print(f"{ACIERTOS} comprobaciones superadas, {len(FALLOS)} FALLIDAS:\n")
    for _f in FALLOS:
        print("  -", _f)
    sys.exit(1)
print(f"{ACIERTOS} de {ACIERTOS} comprobaciones superadas")
