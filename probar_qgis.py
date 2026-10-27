"""
Comprobaciones del script de carga de capas en QGIS.
===============================================================================

QUE COMPRUEBA
-------------
Que `cargar_capas_qgis.py` hace su trabajo y, sobre todo, que NO dibuja
basura cuando el archivo de entrada no es lo que parece.

Esta bateria NO modifica `cargar_capas_qgis.py`. Lo ejecuta tal cual, en un
QGIS sin interfaz grafica, sobre cartografia DANE de verdad: la que ya hay en
el proyecto. El municipio no esta escrito en el codigo -- se usa el area de
estudio generada si existe, y si no, uno cualquiera de la cartografia
nacional. La salida dice con cual se ejecuto.

POR QUE EXISTE
--------------
Dos razones concretas, las dos medidas:

  1. Los Excel que empiezan a llegar a `corpus/` traen una columna llamada
     "Longitud Total (km)". En castellano "longitud" es a la vez *length* y
     *longitude*, y 46 de sus 127 valores caen entre 3 y 8, que es justo el
     rango de latitudes de Colombia. Un detector de coordenadas descuidado
     tomaria el largo de una quebrada por una latitud y sembraria el mapa de
     puntos falsos. Se comprueba que el script los rechaza.

  2. El script dibuja TODO lo que trae el CSV: no comprueba que los puntos
     caigan dentro del municipio. Eso es correcto -- el filtrado lo hace quien
     genera el CSV --, pero conviene que quede escrito y comprobado, porque es
     lo primero que se malinterpreta al leer el mapa.

COMO SE USA
-----------
    python probar_qgis.py

Si QGIS no se puede importar, la bateria se OMITE en vez de fallar: no todas
las maquinas donde se revisa el proyecto tienen QGIS instalado.

===============================================================================
"""

import os
import random
import shutil
import sys
import tempfile

AQUI = os.path.dirname(os.path.abspath(__file__))

#: El script que se pone a prueba. No se toca: solo se ejecuta.
SCRIPT = "cargar_capas_qgis.py"

#: Los tres archivos que el script espera encontrar, con el nombre exacto.
#: Este es el contrato: si alguien los renombra, el script deja de verlos.
ARCHIVO_DEPARTAMENTOS = "colombia_departamentos.geojson"
ARCHIVO_MUNICIPIOS = "municipios_filtrados.geojson"
ARCHIVO_BIODIVERSIDAD = "biodiversidad_filtrada.csv"

#: De donde se saca el poligono del municipio para armar la prueba.
CACHE_MUNICIPIOS = "cache_municipios_nacional.geojson"

#: QUE MUNICIPIO SE USA PARA LA PRUEBA, Y POR QUE NO VA ESCRITO AQUI
#: ----------------------------------------------------------------
#: Ninguno en concreto. Lo que se comprueba es el SCRIPT, y el script se porta
#: igual con cualquier poligono: ni mira el nombre ni el codigo del municipio.
#: Escribir aqui un municipio seria un dato quemado, y ademas una pista falsa,
#: porque daria a entender que la prueba solo vale para ese sitio.
#:
#: Asi que se coge el area de estudio que haya generado la interfaz web si ya
#: existe, y si no, el primer municipio de la cartografia nacional. La prueba
#: dice en su salida con cual se ejecuto, para que el resultado sea
#: reproducible y quede claro.
#:
#: La variable de entorno PROBAR_QGIS_MUNICIPIO permite fijar uno concreto
#: (por su codigo de municipio) cuando se quiera repetir una prueba exacta.
VARIABLE_MUNICIPIO = "PROBAR_QGIS_MUNICIPIO"

ACIERTOS = 0
FALLOS = []
OMITIDAS = 0

#: La QgsApplication se guarda aqui para que el recolector de basura
#: no la destruya a mitad de la ejecucion. Si se deja como variable
#: local, QGIS se cae en cuanto Python la considera prescindible.
_APP = None


def comprobar(nombre, condicion, detalle=""):
    """
    Anota el resultado de una comprobacion.

    Args:
        nombre: Que se estaba comprobando.
        condicion: Resultado. Verdadero es exito.
        detalle: Texto que se imprime al lado, para poder leer el porque.
    """
    global ACIERTOS
    if condicion:
        ACIERTOS += 1
        print(f"  [OK ] {nombre}: {detalle}")
    else:
        FALLOS.append(f"{nombre} -> {detalle}")
        print(f"  [FALLA] {nombre}: {detalle}")


def omitir(motivo):
    """
    Marca la bateria como omitida y termina sin error.

    Args:
        motivo: Por que no se pudo comprobar.
    """
    print(f"\n  OMITIDA: {motivo}")
    print("\n" + "=" * 70)
    print("0 de 0 comprobaciones superadas, bateria omitida")
    sys.exit(0)


# ==============================================================================
# 1. ARRANCAR QGIS SIN INTERFAZ GRAFICA
# ==============================================================================
# El script usa `iface` (la ventana de QGIS) solo para encuadrar la vista, y
# ya trae su propia guarda `if iface is None: return`. Por eso se puede
# ejecutar entero sin abrir QGIS Desktop.

def arrancar_qgis():
    """
    Pone en marcha un QGIS sin ventana.

    Returns:
        La instancia de `QgsApplication`, o None si QGIS no esta disponible.
    """
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

    # QGIS reparte sus modulos de Python fuera del arbol del interprete.
    raiz = os.path.abspath(os.path.join(sys.prefix, "..", ".."))
    for sufijo in ("apps/qgis-ltr/python", "apps/qgis/python"):
        ruta = os.path.join(raiz, *sufijo.split("/"))
        if os.path.isdir(ruta) and ruta not in sys.path:
            sys.path.insert(0, ruta)

    try:
        from qgis.core import QgsApplication
    except Exception as exc:
        return None, f"no se pudo importar qgis.core ({type(exc).__name__})"

    for sufijo in ("apps/qgis-ltr", "apps/qgis"):
        prefijo = os.path.join(raiz, *sufijo.split("/"))
        if os.path.isdir(prefijo):
            QgsApplication.setPrefixPath(prefijo, True)
            break

    app = QgsApplication([], False)
    app.initQgis()
    return app, ""


# ==============================================================================
# 2. CONSTRUIR UN JUEGO DE DATOS CON CARTOGRAFIA REAL
# ==============================================================================
# Los poligonos salen de la cartografia DANE que el proyecto ya tiene. Los
# puntos de biodiversidad son sembrados, porque todavia no hay un CSV del area
# de estudio -- y precisamente por eso conviene esta prueba: comprueba la
# tuberia completa sin esperar a que lleguen los datos reales.
#
# Los puntos se siembran dentro de la CAJA que envuelve al municipio, no dentro
# del poligono. Es deliberado: algunos caen fuera, y eso es lo que permite
# comprobar que el script dibuja todo sin filtrar.

def _elegir_municipio(rasgos):
    """
    Decide con que poligono se hace la prueba, sin dato quemado.

    Por orden de preferencia:

      1. El que diga la variable de entorno `PROBAR_QGIS_MUNICIPIO`, por si se
         quiere repetir una ejecucion concreta.
      2. El area de estudio que haya generado la interfaz web, si ya existe:
         probar con el territorio real del proyecto es mejor que con uno
         cualquiera.
      3. El primero de la cartografia nacional, ordenado por codigo para que
         salga siempre el mismo y la prueba sea reproducible.

    Args:
        rasgos: Los municipios de la cartografia nacional.

    Returns:
        Lista con un unico rasgo, o lista vacia si no hay ninguno.
    """
    import json

    pedido = os.environ.get(VARIABLE_MUNICIPIO, "").strip()
    if pedido:
        elegido = [r for r in rasgos
                   if str(r["properties"].get("MPIO_CCDGO", "")) == pedido]
        if elegido:
            return elegido[:1]

    # El area de estudio ya generada, si la hay.
    try:
        from config import settings
        ruta = settings.ruta_municipios_salida()
        if ruta and os.path.exists(ruta):
            with open(ruta, encoding="utf-8") as f:
                capa = json.load(f)
            propios = capa.get("features") or []
            if propios:
                return propios[:1]
    except Exception:
        # Si la configuracion o la capa fallan, se sigue con la opcion 3: la
        # prueba no depende de cual sea el municipio.
        pass

    return sorted(
        rasgos,
        key=lambda r: (str(r["properties"].get("DPTO_CCDGO", "")),
                       str(r["properties"].get("MPIO_CCDGO", ""))))[:1]


def preparar_datos(destino):
    """
    Deja en `destino` los tres archivos del contrato.

    Args:
        destino: Carpeta donde montar la prueba.

    Returns:
        Tupla `(cuantos_puntos, mensaje_de_error)`. El mensaje esta vacio si
        todo fue bien.
    """
    import json

    origen_cache = os.path.join(AQUI, CACHE_MUNICIPIOS)
    origen_dptos = os.path.join(AQUI, ARCHIVO_DEPARTAMENTOS)
    if not os.path.exists(origen_cache):
        return 0, f"falta {CACHE_MUNICIPIOS}"
    if not os.path.exists(origen_dptos):
        return 0, f"falta {ARCHIVO_DEPARTAMENTOS}"

    with open(origen_cache, encoding="utf-8") as f:
        nacional = json.load(f)

    municipio = _elegir_municipio(nacional["features"])
    if not municipio:
        return 0, "la cartografia nacional no trae ningun municipio"
    etiqueta = municipio[0]["properties"]
    print(f"  municipio de la prueba: "
          f"{etiqueta.get('MPIO_CNMBR', '?')} "
          f"({etiqueta.get('DPTO_CNMBR', '?')}), "
          f"codigo {etiqueta.get('DPTO_CCDGO', '?')}"
          f"{etiqueta.get('MPIO_CCDGO', '?')}")

    with open(os.path.join(destino, ARCHIVO_MUNICIPIOS), "w",
              encoding="utf-8") as f:
        json.dump({"type": "FeatureCollection", "features": municipio}, f)

    shutil.copy(origen_dptos, os.path.join(destino, ARCHIVO_DEPARTAMENTOS))

    # Puntos dentro de la caja que envuelve al municipio. Que algunos caigan
    # fuera del poligono es DELIBERADO: sirve para comprobar que el script
    # dibuja todo y no filtra.
    geo = municipio[0]["geometry"]
    if geo["type"] == "Polygon":
        vertices = [c for anillo in geo["coordinates"] for c in anillo]
    else:
        vertices = [c for p in geo["coordinates"] for anillo in p for c in anillo]
    equis = [v[0] for v in vertices]
    yes = [v[1] for v in vertices]

    random.seed(1)   # misma muestra en cada ejecucion
    especies = ["Morpho peleides", "Heliconius erato", "Danaus plexippus",
                "Caligo memnon", "Dryas iulia"]
    filas = ["gbifID,species,decimalLatitude,decimalLongitude,eventDate,country"]
    for i in range(60):
        filas.append(
            f"{3000000 + i},{random.choice(especies)},"
            f"{random.uniform(min(yes), max(yes)):.6f},"
            f"{random.uniform(min(equis), max(equis)):.6f},2024-05-15,CO")
    escribir(os.path.join(destino, ARCHIVO_BIODIVERSIDAD),
             "\n".join(filas) + "\n")
    return len(filas) - 1, ""


def escribir(ruta, texto):
    """
    Escribe un archivo de texto y lo cierra.

    Se hace con `with` a proposito: dejando el archivo abierto, la escritura se
    queda en el bufer y el siguiente lector ve el contenido viejo. Paso en la
    primera version de esta bateria y dio un fallo que no existia.

    Args:
        ruta: Donde escribir.
        texto: Contenido completo.
    """
    with open(ruta, "w", encoding="utf-8", newline="\n") as f:
        f.write(texto)


# ==============================================================================
# 3. LAS COMPROBACIONES
# ==============================================================================

def main():
    """
    Monta el escenario, ejecuta el script y comprueba el resultado.

    Returns:
        Codigo de salida: 0 si todo paso, 1 si algo fallo.
    """
    print("=" * 70)
    print("  Script de QGIS: carga de capas y deteccion de coordenadas")
    print("=" * 70)

    if not os.path.exists(os.path.join(AQUI, SCRIPT)):
        omitir(f"no se encuentra {SCRIPT}")

    global _APP
    _APP, error = arrancar_qgis()
    app = _APP
    if app is None:
        omitir(f"QGIS no esta disponible aqui: {error}")

    from qgis.core import QgsProject, QgsVectorLayer

    trabajo = tempfile.mkdtemp(prefix="probar_qgis_")
    anterior = os.getcwd()
    try:
        cuantos, error = preparar_datos(trabajo)
        if error:
            omitir(f"no se pudo armar el escenario: {error}")
        shutil.copy(os.path.join(AQUI, SCRIPT), trabajo)
        os.chdir(trabajo)

        # --- El script completo, tal como se ejecuta con el boton Play ------
        print("\n--- Ejecucion completa del script ---")
        import importlib.util
        especificacion = importlib.util.spec_from_file_location("cq", SCRIPT)
        cq = importlib.util.module_from_spec(especificacion)
        especificacion.loader.exec_module(cq)   # el script se lanza al cargarse

        capas = {c.name(): c for c in QgsProject.instance().mapLayers().values()}
        comprobar("carga las tres capas del area de estudio",
                  len(capas) == 3, f"{len(capas)} capas: {', '.join(sorted(capas))}")
        comprobar("todas las capas son validas",
                  all(c.isValid() for c in capas.values()),
                  "ninguna quedo invalida")

        puntos = next((c for n, c in capas.items() if "iodiversidad" in n), None)
        comprobar("dibuja todos los registros del CSV",
                  puntos is not None and puntos.featureCount() == cuantos,
                  f"{puntos.featureCount() if puntos else 0} de {cuantos}")

        dptos = next((c for n, c in capas.items() if "epartamentos" in n), None)
        comprobar("los departamentos son los 33 de Colombia",
                  dptos is not None and dptos.featureCount() == 33,
                  f"{dptos.featureCount() if dptos else 0}")

        # --- La trampa de los Excel que llegan al corpus --------------------
        # Cabecera REAL de Inventario_Completo_127_Quebradas_San_Carlos.xlsx.
        print("\n--- Deteccion de coordenadas: casos que deben RECHAZARSE ---")
        ruta_csv = os.path.join(trabajo, ARCHIVO_BIODIVERSIDAD)
        with open(ruta_csv, encoding="utf-8") as f:
            original = f.read()

        escribir(ruta_csv,
                 "N,Nombre del Drenaje / Quebrada,Longitud Total (km),"
                 "Longitud Total (m),Numero de Tramos,Regimen / Flujo\n"
                 "1,Canada El Limon,1.103,1102.86,1,5101\n"
                 "2,Canada Sonadora,8.111,8110.93,1,5101\n")
        comprobar("'Longitud Total (km)' NO se toma por una longitud geografica",
                  cq.cargar_capa_puntos(QgsProject.instance(), ruta_csv) is None,
                  "es el largo del cauce en km, no una coordenada")

        escribir(ruta_csv, "id,especie,latitud\n1,Morpho peleides,6.18\n")
        comprobar("exige latitud Y longitud, no una sola",
                  cq.cargar_capa_puntos(QgsProject.instance(), ruta_csv) is None,
                  "con una sola columna no se puede situar nada")

        comprobar("un archivo que no existe avisa y no revienta",
                  cq.cargar_capa_puntos(
                      QgsProject.instance(),
                      os.path.join(trabajo, "no_existe.csv")) is None,
                  "devuelve None con mensaje")

        # --- Casos que SI deben reconocerse --------------------------------
        print("\n--- Deteccion de coordenadas: casos que deben ACEPTARSE ---")
        for nombre, contenido, esperados in [
            ("Darwin Core de GBIF",
             "gbifID,species,decimalLatitude,decimalLongitude\n"
             "1,Morpho peleides,6.18,-74.99\n2,Dryas iulia,6.20,-75.01\n", 2),
            ("nombres en castellano",
             "id,especie,latitud,longitud\n1,Morpho peleides,6.18,-74.99\n", 1),
            ("abreviados lat/lon",
             "id,sp,lat,lon\n1,Caligo memnon,6.18,-74.99\n", 1),
            ("separado por punto y coma",
             "id;especie;decimalLatitude;decimalLongitude\n"
             "1;Heliconius erato;6.18;-74.99\n", 1),
        ]:
            escribir(ruta_csv, contenido)
            capa = cq.cargar_capa_puntos(QgsProject.instance(), ruta_csv)
            comprobar(f"reconoce {nombre}",
                      capa is not None and capa.featureCount() == esperados,
                      f"{capa.featureCount() if capa else 0} de {esperados} puntos")

        escribir(ruta_csv, "gbifID,species,decimalLatitude,decimalLongitude\n")
        capa = cq.cargar_capa_puntos(QgsProject.instance(), ruta_csv)
        comprobar("un CSV con cabecera y sin filas no revienta",
                  capa is None or capa.featureCount() == 0,
                  "0 puntos, sin excepcion")

        # --- Lo que el script NO hace, y conviene tener escrito -------------
        print("\n--- Alcance del script: dibuja, no filtra ---")
        escribir(ruta_csv, original)
        puntos = cq.cargar_capa_puntos(QgsProject.instance(), ruta_csv)
        municipio = QgsVectorLayer(
            os.path.join(trabajo, ARCHIVO_MUNICIPIOS), "m", "ogr")
        poligono = next(municipio.getFeatures()).geometry()
        dentro = sum(1 for r in puntos.getFeatures()
                     if poligono.contains(r.geometry()))
        total = puntos.featureCount()
        comprobar("dibuja TODO el CSV sin comprobar el poligono",
                  dentro < total,
                  f"{dentro} de {total} caen dentro del municipio; el "
                  f"filtrado es de quien genera el CSV")

        # ----------------------------------------------------------------
        print("\n--- Donde busca los archivos que genera el sistema ---")
        # El sistema dejo de escribir en la raiz del proyecto y pasa a
        # escribir en `data/`, porque la raiz NO esta montada en los
        # contenedores: una capa escrita ahi quedaba encerrada y no la veia
        # ni la interfaz ni QGIS. El script tiene que encontrarla en los dos
        # sitios: en el nuevo, y en el viejo para quien ya tenga capas
        # generadas antes del cambio.
        banco = os.path.join(trabajo, "banco_rutas")
        os.makedirs(os.path.join(banco, "data"), exist_ok=True)

        comprobar("sin el archivo en ningun sitio, apunta a 'data/'",
                  cq.localizar(banco, ARCHIVO_MUNICIPIOS).endswith(
                      os.path.join("data", ARCHIVO_MUNICIPIOS)),
                  "el aviso manda al sitio donde hoy se genera")

        en_raiz = os.path.join(banco, ARCHIVO_MUNICIPIOS)
        escribir(en_raiz, '{"type":"FeatureCollection","features":[]}')
        comprobar("una capa antigua en la raiz se sigue encontrando",
                  cq.localizar(banco, ARCHIVO_MUNICIPIOS) == en_raiz,
                  "quien no mueva nada no se queda sin capas")

        en_data = os.path.join(banco, "data", ARCHIVO_MUNICIPIOS)
        escribir(en_data, '{"type":"FeatureCollection","features":[]}')
        comprobar("si esta en los dos sitios, manda 'data/'",
                  cq.localizar(banco, ARCHIVO_MUNICIPIOS) == en_data,
                  "gana la que el sistema acaba de generar")

        # La carpeta se reconocia solo por `biodiversidad_filtrada.csv`, que
        # dejo de generarse al cambiar la pestana de area de estudio. La
        # deteccion fallaba siempre, aun estando dentro de la carpeta.
        anterior_cwd = os.getcwd()
        try:
            os.chdir(banco)
            carpeta_hallada, segura = cq.detectar_carpeta_proyecto()
            comprobar("la carpeta se reconoce por la capa municipal",
                      segura,
                      f"hallada sin necesitar '{ARCHIVO_BIODIVERSIDAD}'")
        finally:
            os.chdir(anterior_cwd)

    finally:
        os.chdir(anterior)
        # Las capas hay que soltarlas ANTES de borrar los archivos: si no, sus
        # proveedores quedan apuntando a rutas que este bloque esta por borrar.
        QgsProject.instance().clear()
        shutil.rmtree(trabajo, ignore_errors=True)
        # NO se llama a `app.exitQgis()`. Cerrar un QGIS sin interfaz en Windows
        # revienta con "Segmentation fault" al descargar las bibliotecas de Qt,
        # DESPUES de que las comprobaciones ya corrieron. Se midio: las 13 pasan
        # y el proceso devolvia 139 igualmente, asi que el lanzador leia un
        # fallo inexistente. El proceso se cierra con `os._exit` al final del
        # archivo, que devuelve el codigo real sin pasar por ese desmontaje.

    print("\n" + "=" * 70)
    total = ACIERTOS + len(FALLOS)
    if FALLOS:
        print(f"{ACIERTOS} de {total} superadas, {len(FALLOS)} FALLIDAS:\n")
        for f in FALLOS:
            print("  -", f)
        return 1
    print(f"{ACIERTOS} de {total} comprobaciones superadas")
    return 0


if __name__ == "__main__":
    codigo = main()

    # POR QUE NO SE USA sys.exit() AQUI
    # ---------------------------------
    # Cerrar un QGIS sin interfaz deja a Qt descargando sus bibliotecas al
    # terminar el interprete, y en Windows eso acaba en "Segmentation fault"
    # DESPUES de que las comprobaciones ya corrieron y se imprimieron. Se midio:
    # las 13 pasan y el proceso devuelve 139 de todas formas, asi que el
    # lanzador leeria un fallo que no existe.
    #
    # No se esta tapando un fallo del script que se prueba: el reventon es del
    # desmontaje de Qt, posterior al resultado. `os._exit` devuelve el codigo
    # real sin pasar por ese desmontaje.
    sys.stdout.flush()
    sys.stderr.flush()
    os._exit(codigo)
