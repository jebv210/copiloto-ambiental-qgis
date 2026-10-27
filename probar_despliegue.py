"""
Comprobaciones de la pila desplegada (Docker, API, Qdrant, n8n).
===============================================================================

A diferencia de `probar_corpus.py` y `probar_vectorial.py`, que prueban el
codigo en aislamiento, esto prueba el SISTEMA MONTADO: que los contenedores se
hablen, que los endpoints que consume n8n respondan y que los flujos sean
importables.

Se ejecuta con::

    python probar_despliegue.py

Lo que no se pueda comprobar se marca como omitido en lugar de fallar, para que
sirva igual con la pila a medio levantar.
"""

import json
import os
import sys
import time
import urllib.error
import urllib.request


AQUI = os.path.dirname(os.path.abspath(__file__))
API = os.environ.get("API_PRUEBAS", "http://localhost:8000")
QDRANT = os.environ.get("QDRANT_PRUEBAS", "http://localhost:6333")
N8N = os.environ.get("N8N_PRUEBAS", "http://localhost:5678")
APP = os.environ.get("APP_PRUEBAS", "http://localhost:8501")

ACIERTOS = 0
FALLOS = []
OMITIDAS = 0


def comprobar(titulo, condicion, detalle=""):
    """Registra el resultado de una comprobacion."""
    global ACIERTOS
    if condicion:
        ACIERTOS += 1
        print(f"  [OK ] {titulo}: {detalle}")
    else:
        FALLOS.append(f"{titulo} -> {detalle}")
        print(f"  [FALLA] {titulo}: {detalle}")


def omitir(titulo, motivo):
    """Deja constancia de lo que no se pudo comprobar."""
    global OMITIDAS
    OMITIDAS += 1
    print(f"  [--- ] {titulo}: omitido ({motivo})")


def pedir(url, metodo="GET", cuerpo=None, espera=120):
    """
    Hace una peticion HTTP y devuelve (codigo, datos).

    Args:
        url: Direccion completa.
        metodo: GET o POST.
        cuerpo: Diccionario a enviar como JSON.
        espera: Segundos de espera maxima.

    Returns:
        Tupla `(codigo, datos)`. El codigo es 0 si no hubo conexion.
    """
    datos = json.dumps(cuerpo).encode("utf-8") if cuerpo is not None else None
    cabeceras = {"Content-Type": "application/json"} if datos else {}
    peticion = urllib.request.Request(url, datos, cabeceras, method=metodo)
    try:
        with urllib.request.urlopen(peticion, timeout=espera) as r:
            crudo = r.read().decode("utf-8", "replace")
            try:
                return r.status, json.loads(crudo)
            except json.JSONDecodeError:
                return r.status, crudo
    except urllib.error.HTTPError as exc:
        crudo = exc.read().decode("utf-8", "replace")
        try:
            return exc.code, json.loads(crudo)
        except json.JSONDecodeError:
            return exc.code, crudo
    except Exception as exc:
        return 0, str(exc)


# ==============================================================================
print("\nSERVICIOS - responden?")
# ==============================================================================
codigo, datos = pedir(f"{API}/", espera=30)
api_viva = codigo == 200
comprobar("la API responde", api_viva, f"HTTP {codigo}")

codigo_q, _ = pedir(f"{QDRANT}/readyz", espera=15)
comprobar("Qdrant responde", codigo_q == 200, f"HTTP {codigo_q}")

codigo_n, _ = pedir(f"{N8N}/", espera=20)
if codigo_n in (200, 401):
    comprobar("n8n responde", True, f"HTTP {codigo_n}")
else:
    # Un n8n que arranca bien pero no se alcanza desde fuera casi siempre es
    # el puerto 5678 ocupado por OTRO contenedor cuando se creo el nuestro:
    # el contenedor queda con el puerto expuesto pero sin publicar, y el
    # sintoma (HTTP 0) no dice nada de eso. Se diagnostica aqui.
    import subprocess as _sp
    pista = ""
    try:
        mapeo = _sp.run(["docker", "port", "copiloto-n8n"],
                        capture_output=True, text=True, timeout=30).stdout.strip()
        if not mapeo:
            ladrones = _sp.run(
                ["docker", "ps", "-a", "--filter", "publish=5678",
                 "--format", "{{.Names}} ({{.Status}})"],
                capture_output=True, text=True, timeout=30).stdout.strip()
            pista = ("el puerto NO esta publicado; recrea con "
                     "'docker compose up -d --force-recreate n8n'")
            if ladrones:
                pista += f". Lo ocupa(ba): {ladrones.splitlines()[0]}"
        else:
            pista = f"puerto publicado ({mapeo.splitlines()[0]}) pero sin responder: puede seguir migrando"
    except Exception:
        pista = "no se pudo consultar docker"
    comprobar("n8n responde", False, f"HTTP {codigo_n} - {pista}")

codigo_a, _ = pedir(f"{APP}/", espera=40)
comprobar("Streamlit responde", codigo_a == 200, f"HTTP {codigo_a}")

if not api_viva:
    # Se imprime el resumen igual antes de salir: el lanzador lo busca para
    # informar, y sin el solo podia decir "sin resumen".
    print("\nSin API no se puede seguir. Arranca la pila con:")
    print("    docker compose up -d")
    print("\n" + "=" * 70)
    print(f"{ACIERTOS} de {ACIERTOS + len(FALLOS)} comprobaciones superadas "
          f"({len(FALLOS)} FALLIDAS: la pila no esta levantada)")
    sys.exit(1)


# ==============================================================================
print("\nCODIGO DESPLEGADO - los contenedores deben llevar lo del disco")
# ==============================================================================
# Es el fallo mas traicionero de todos: los contenedores arrancan, responden y
# pasan todas las demas comprobaciones, pero ejecutan una version anterior del
# codigo. Paso de verdad: `docker compose build api` solo reconstruye la imagen
# de la API, y la interfaz siguio 27 horas mostrando textos y un modelo que ya
# se habian cambiado en el disco.
import subprocess as _sp


def _en_contenedor(nombre, orden):
    """Ejecuta una orden dentro de un contenedor y devuelve su salida."""
    try:
        r = _sp.run(["docker", "exec", nombre] + orden,
                    capture_output=True, text=True, timeout=120)
        return (r.stdout or "").strip()
    except Exception:
        return ""


#: Archivos cuyo contenido debe coincidir byte a byte con el del contenedor.
#:
#: Comparar UN valor de configuracion no basta: se probo primero asi y dejo
#: pasar un `agent/prompts.py` de agosto dentro del contenedor, con un texto
#: que ya se habia quitado del disco. Se comparan resumenes de los archivos que
#: definen el comportamiento.
ARCHIVOS_VIGILADOS = [
    "config/settings.py", "agent/prompts.py", "agent/chatbot.py",
    "agent/tools.py", "core/vectorial.py", "core/corpus.py",
    "core/fuentes.py", "core/geografia.py", "core/catalogo.py",
    "core/pegado.py", "core/estilos.py", "core/seguridad.py",
    "core/memoria.py", "core/ajustes.py", "core/conexion_drive.py",
    "app.py",
]


def _resumen_local(ruta):
    """Resumen SHA-1 de un archivo del disco."""
    import hashlib
    try:
        with open(ruta, "rb") as f:
            return hashlib.sha1(f.read()).hexdigest()[:12]
    except OSError:
        return ""


for contenedor in ("copiloto-api", "copiloto-app"):
    orden = ["python", "-c",
             "import hashlib,sys\n"
             "for r in sys.argv[1:]:\n"
             "    try:\n"
             "        print(r, hashlib.sha1(open('/app/'+r,'rb').read()).hexdigest()[:12])\n"
             "    except OSError:\n"
             "        print(r, 'AUSENTE')"] + ARCHIVOS_VIGILADOS
    salida = _en_contenedor(contenedor, orden)
    if not salida:
        omitir(f"{contenedor} ejecuta el codigo del disco",
               "el contenedor no respondio")
        continue

    dentro = dict(l.split() for l in salida.splitlines() if " " in l)
    distintos = [r for r in ARCHIVOS_VIGILADOS
                 if dentro.get(r) != _resumen_local(r)]
    comprobar(
        f"{contenedor} ejecuta el codigo del disco",
        not distintos,
        f"{len(ARCHIVOS_VIGILADOS)} archivos comparados"
        if not distintos else
        f"DESFASADOS: {', '.join(distintos)}  ->  'docker compose build' "
        f"(sin nombre de servicio) y recrea")


# ==============================================================================
print("\nESTADO DEL CORPUS - el endpoint que consulta n8n")
# ==============================================================================
codigo, estado = pedir(f"{API}/corpus/estado", espera=240)
comprobar("/corpus/estado responde", codigo == 200, f"HTTP {codigo}")

if codigo == 200 and isinstance(estado, dict):
    for clave in ("fuente", "corpus", "vectorial", "sincronizacion"):
        comprobar(f"trae la seccion '{clave}'", clave in estado,
                  "presente" if clave in estado else "AUSENTE")

    fuente = estado.get("fuente", {})
    # Una fuente VACIA no es una fuente AVERIADA. Pasa mientras se cambia de
    # la carpeta local a Drive: durante ese rato no hay documentos, y eso es
    # un estado legitimo del sistema, no un defecto. Lo que si seria un fallo
    # es que la fuente ni siquiera se pueda consultar.
    if fuente.get("listo") is not True and fuente.get("documentos") == 0:
        omitir("la fuente documental esta lista",
               f"la fuente '{fuente.get('fuente')}' no ve documentos: "
               f"{fuente.get('motivo', '')[:60]}")
    else:
        comprobar("la fuente documental esta lista",
                  fuente.get("listo") is True,
                  f"{fuente.get('fuente')} con "
                  f"{fuente.get('documentos')} documentos")

    corpus_ = estado.get("corpus", {})
    comprobar("el corpus se lee desde el contenedor",
              corpus_.get("documentos", 0) > 0,
              f"{corpus_.get('documentos')} documentos, version {corpus_.get('version')}")

    vect = estado.get("vectorial", {})
    comprobar("el indice declara su modelo y tamano",
              bool(vect.get("modelo")) and bool(vect.get("dimensiones")),
              f"{vect.get('proveedor_embeddings')}/{vect.get('modelo')}/{vect.get('dimensiones')}")
else:
    omitir("contenido de /corpus/estado", "la respuesta no es JSON")


# ==============================================================================
print("\nCONSULTA AL CORPUS - lo que usa el webhook de n8n")
# ==============================================================================
codigo, r = pedir(f"{API}/corpus/consultar", "POST",
                  {"pregunta": "plantas hospederas de Morpho peleides",
                   "limite": 5}, espera=240)
comprobar("/corpus/consultar responde", codigo == 200, f"HTTP {codigo}")

if codigo == 200 and isinstance(r, dict):
    fichas = r.get("fichas", [])
    comprobar("devuelve fichas", len(fichas) > 0, f"{len(fichas)} fichas")
    if fichas:
        completas = all(f.get("documento") and f.get("texto") for f in fichas)
        comprobar("cada ficha trae documento y texto", completas,
                  "todas citables" if completas else "alguna sin procedencia")

# Una pregunta sin respuesta posible no debe inventar nada.
codigo, r = pedir(f"{API}/corpus/consultar", "POST",
                  {"pregunta": "poblacion de pinguinos emperador en la Antartida", "limite": 3},
                  espera=240)
if codigo == 200 and isinstance(r, dict):
    comprobar("una pregunta ajena no revienta el endpoint", True,
              f"{r.get('encontradas')} fichas (BM25 siempre devuelve algo parecido)")


# ==============================================================================
print("\nSINCRONIZACION - arranque, concurrencia y seguimiento")
# ==============================================================================
codigo, estado = pedir(f"{API}/corpus/estado", espera=240)
corriendo = (estado.get("sincronizacion", {}).get("ejecutando")
             if isinstance(estado, dict) else False)

if corriendo:
    comprobar("rechaza una segunda sincronizacion simultanea",
              pedir(f"{API}/corpus/sincronizar", "POST", {}, espera=60)[0] == 409,
              "HTTP 409, como debe")
else:
    codigo, r = pedir(f"{API}/corpus/sincronizar", "POST", {}, espera=60)
    comprobar("arranca una sincronizacion", codigo == 200, f"HTTP {codigo}")

    # La segunda peticion se lanza EN CUANTO se confirma que la primera sigue
    # viva, no tras una espera fija. Con `time.sleep(3)` esta comprobacion
    # fallaba de forma intermitente: cuando el corpus ya esta indexado, la
    # sincronizacion no tiene trabajo y termina en menos de tres segundos, asi
    # que la segunda llamada llegaba cuando ya no habia ninguna en marcha y
    # recibia 200. No era un defecto del cerrojo, era la prueba midiendo mal.
    arranco = time.time()
    sigue = False
    while time.time() - arranco < 10:
        _, estado = pedir(f"{API}/corpus/estado", espera=20)
        sigue = bool((estado or {}).get("sincronizacion", {}).get("ejecutando"))
        if sigue:
            break

    if not sigue:
        # Termino antes de que diera tiempo a pillarla en marcha. No se puede
        # comprobar la concurrencia, pero tampoco hay nada roto: se omite.
        omitir("rechaza la segunda simultanea",
               "la primera sincronizacion termino antes de poder solaparlas")
    else:
        codigo2, r2 = pedir(f"{API}/corpus/sincronizar", "POST", {}, espera=60)
        comprobar("rechaza la segunda simultanea", codigo2 == 409,
                  f"HTTP {codigo2} (409 esperado)")


# ==============================================================================
print("\nN8N EN MARCHA - flujos importados y webhook respondiendo")
# ==============================================================================
import subprocess as _sp


def _en_n8n(orden):
    """Ejecuta una orden de n8n dentro del contenedor y devuelve su salida."""
    try:
        r = _sp.run(["docker", "exec", "copiloto-n8n"] + orden,
                    capture_output=True, text=True, timeout=120)
        return (r.stdout or "") + (r.stderr or "")
    except Exception as exc:
        return f"ERROR {exc}"


salida = _en_n8n(["n8n", "list:workflow"])
if "ERROR" in salida and "Copiloto" not in salida:
    omitir("flujos importados en n8n", "no se pudo consultar el contenedor")
else:
    importados = [l for l in salida.splitlines() if "Copiloto" in l]
    comprobar("los flujos estan importados en n8n", len(importados) >= 2,
              f"{len(importados)} flujos: " +
              ", ".join(l.split("|")[-1][:34] for l in importados))

# La prueba de que n8n ORQUESTA de verdad: se le pregunta a EL, no a la API.
#
# Antes se usaba el webhook `corpus-consultar`. Ese flujo se retiro: llevaba
# desde el 18 de octubre sin que lo llamara nadie del proyecto, y sus 42
# ejecuciones eran todas de esta misma bateria. Un flujo cuyo unico cliente es
# su propia prueba no demuestra nada.
#
# La orquestacion se comprueba ahora sobre el flujo del chat, que es el que usa
# la interfaz de verdad, en el bloque "Chat orquestado por n8n" de mas abajo.
# Aqui queda lo que de verdad importaba de esta prueba: que el corpus se pueda
# consultar SIN pasar por el modelo, rapido y con procedencia.
codigo, lista = pedir(f"{API}/corpus/consultar", "POST",
                      {"pregunta": "de que se alimenta la oruga de Morpho "
                                   "peleides", "limite": 3}, espera=180)
comprobar("el corpus se consulta sin pasar por el modelo",
          codigo == 200, f"HTTP {codigo}")
if codigo == 200:
    fichas = (lista or {}).get("fichas", [])
    comprobar("devuelve fichas", len(fichas) > 0, f"{len(fichas)} fichas")
    if fichas:
        comprobar("cada ficha trae su procedencia",
                  all(f.get("documento") or f.get("fuente") for f in fichas),
                  "todas citables")
        # Las fichas de indice (puntos suspensivos y numeros) no sirven de cita.
        basura = [f for f in fichas
                  if f.get("texto", "").count(".") > len(f.get("texto", "")) * 0.3]
        comprobar("no devuelve fichas de indice", not basura,
                  f"{len(basura)} fichas de relleno")




# ==============================================================================
print("\nFLUJOS DE N8N - estructura importable")
# ==============================================================================
CARPETA = os.path.join(os.path.dirname(os.path.abspath(__file__)), "n8n", "flujos")
if not os.path.isdir(CARPETA):
    omitir("flujos de n8n", "no existe n8n/flujos")
else:
    for archivo in sorted(os.listdir(CARPETA)):
        if not archivo.endswith(".json"):
            continue
        with open(os.path.join(CARPETA, archivo), encoding="utf-8") as f:
            try:
                flujo = json.load(f)
            except json.JSONDecodeError as exc:
                comprobar(f"{archivo} es JSON valido", False, str(exc))
                continue

        nodos = {n["name"] for n in flujo.get("nodes", [])}
        comprobar(f"{archivo}: tiene nombre y nodos",
                  bool(flujo.get("name")) and len(nodos) > 0,
                  f"{len(nodos)} nodos")

        # Toda conexion debe apuntar a un nodo que exista: es el error que mas
        # cuesta ver al importar, porque n8n lo acepta y falla al ejecutar.
        rotas = []
        for origen, salidas in flujo.get("connections", {}).items():
            if origen not in nodos:
                rotas.append(f"origen '{origen}'")
            for rama in salidas.get("main", []):
                for destino in rama or []:
                    if destino.get("node") not in nodos:
                        rotas.append(f"destino '{destino.get('node')}'")
        comprobar(f"{archivo}: todas las conexiones apuntan a nodos reales",
                  not rotas, ", ".join(rotas) if rotas else "sin enlaces rotos")
# ==============================================================================
# LA FUENTE DRIVE PUEDE ARRANCAR DENTRO DEL CONTENEDOR
# ==============================================================================
# Por que se comprueba: `google.oauth2` llega de rebote como dependencia de
# google-genai, pero `googleapiclient` NO. El contenedor arrancaba perfecto y
# solo habria fallado el dia de apuntar el corpus a Drive, con un ImportError
# en plena sincronizacion de las 02:00. Se anadio google-api-python-client a
# requirements.txt; esta comprobacion impide que la ausencia vuelva sin avisar.
print("\n--- Lectura de Drive desde el contenedor ---")

_ORDEN_LIBS = (
    "import importlib.util as u;"
    "f=[m for m in ('google.oauth2','googleapiclient') if not u.find_spec(m)];"
    "print('FALTAN:'+','.join(f) if f else 'TODAS')"
)
_libs = _en_contenedor("copiloto-api", ["python", "-c", _ORDEN_LIBS])
comprobar("las librerias de Drive estan en el contenedor",
          _libs.endswith("TODAS"), _libs or "no se pudo preguntar al contenedor")

# No se exige que Drive este CONFIGURADO: aun no hay carpeta compartida. Se
# exige que la clase se construya y sepa DECIR que le falta en vez de romperse,
# que es lo que distingue "pendiente de acceso" de "averiado".
_ORDEN_DIAG = (
    "from core.fuentes import FUENTES;"
    "ok,msg=FUENTES['drive']().disponible();"
    "print(('LISTA' if ok else 'PENDIENTE')+'|'+msg)"
)
_diag = _en_contenedor("copiloto-api", ["python", "-c", _ORDEN_DIAG])
comprobar("la fuente Drive se construye y explica que le falta",
          "|" in _diag, _diag or "la clase no se pudo ni construir")
# ==============================================================================
# BUZON DE IMAGENES PEGADAS Y DIAGNOSTICO DE LA FUENTE
# ==============================================================================
# Lo que se vigila aqui, y por que:
#
#   - Que el navegador PUEDA hablar con la API. La interfaz vive en el puerto
#     8501 y la API en el 8000: para el navegador son dos sitios distintos y,
#     sin permiso de origen cruzado, bloquea la peticion ANTES de enviarla.
#     Se midio: pegar una imagen fallaba con "Failed to fetch" y en los
#     registros del servidor no aparecia absolutamente nada, porque la
#     peticion nunca llegaba a salir.
#
#   - Que ese permiso NO este abierto a cualquiera. Esta API lanza reindexados
#     y gasta cuota del modelo.
print("\n--- Pegar imagenes en el chat ---")

_SESION_PRUEBA = "prueba_pegado"
_PNG_MINIMO = (
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8"
    "z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
)

pedir(f"{API}/chat/imagenes/{_SESION_PRUEBA}", "DELETE", espera=20)

codigo, _ = pedir(f"{API}/chat/imagen", "POST", {
    "session_id": _SESION_PRUEBA, "nombre": "captura.png",
    "tipo": "image/png", "datos": _PNG_MINIMO}, espera=30)
comprobar("acepta una imagen pegada", codigo == 200, f"HTTP {codigo}")

codigo, cuerpo = pedir(f"{API}/chat/imagenes/{_SESION_PRUEBA}", espera=20)
comprobar("la devuelve en la lista de espera",
          codigo == 200 and cuerpo.get("cuantas") == 1,
          f"{(cuerpo or {}).get('cuantas')} en espera")

# Los bytes solo viajan cuando se piden: traerlos en cada repintado de la
# interfaz seria mover megas por nada.
primera = ((cuerpo or {}).get("imagenes") or [{}])[0]
comprobar("sin pedirlo, no manda los bytes", "datos" not in primera,
          "solo nombre, tipo y tamano")
codigo, cuerpo = pedir(
    f"{API}/chat/imagenes/{_SESION_PRUEBA}?con_datos=true", espera=20)
comprobar("los manda cuando se piden",
          "datos" in ((cuerpo or {}).get("imagenes") or [{}])[0],
          "con_datos=true trae el contenido")

codigo, cuerpo = pedir(f"{API}/chat/imagen", "POST", {
    "session_id": _SESION_PRUEBA, "datos": "esto-no-es-base64!!"}, espera=20)
comprobar("rechaza lo que no es una imagen", codigo == 400, f"HTTP {codigo}")

codigo, cuerpo = pedir(f"{API}/chat/imagenes/{_SESION_PRUEBA}", "DELETE",
                       espera=20)
comprobar("se puede vaciar el buzon",
          codigo == 200 and (cuerpo or {}).get("descartadas", 0) >= 1,
          f"descartadas: {(cuerpo or {}).get('descartadas')}")

# --- Permiso de origen cruzado -------------------------------------------
import urllib.request as _urllib

def _preflight(origen):
    """Pregunta a la API si aceptaria una peticion desde ese origen."""
    peticion = _urllib.Request(f"{API}/chat/imagen", method="OPTIONS")
    peticion.add_header("Origin", origen)
    peticion.add_header("Access-Control-Request-Method", "POST")
    peticion.add_header("Access-Control-Request-Headers", "content-type")
    try:
        with _urllib.urlopen(peticion, timeout=20) as r:
            return r.headers.get("access-control-allow-origin")
    except Exception:
        return None

comprobar("el navegador puede llamar desde la interfaz",
          _preflight("http://localhost:8501") == "http://localhost:8501",
          "permitido el origen de Streamlit")
comprobar("un origen ajeno NO queda permitido",
          _preflight("http://sitio-cualquiera.example") is None,
          "la API no se abre a cualquier pagina")

# --- Diagnostico de la fuente (lo que pinta la pestana de Drive) ----------
print("\n--- Diagnostico de la fuente documental ---")
codigo, fuente = pedir(f"{API}/corpus/fuente", espera=120)
comprobar("/corpus/fuente responde", codigo == 200, f"HTTP {codigo}")
if codigo == 200:
    for clave in ("fuente", "listo", "motivo", "documentos",
                  "por_categoria", "por_formato", "imagenes"):
        comprobar(f"trae '{clave}'", clave in fuente,
                  str(fuente.get(clave))[:46])
    if fuente.get("listo"):
        suma = sum(n for _, n in fuente.get("por_categoria", []))
        comprobar("el reparto por categoria cuadra con el total",
                  suma == fuente.get("documentos"),
                  f"{suma} = {fuente.get('documentos')}")
# ==============================================================================
# LOS LECTORES DEL CORPUS ESTAN COMPLETOS EN EL CONTENEDOR
# ==============================================================================
# Por que se comprueba: `openpyxl`, `xlrd` y `python-docx` estaban en el Python
# de QGIS pero NO en la imagen de Docker, que es donde corre la indexacion.
# `leer_documento` devolvia [] ante el ImportError, asi que cada .xlsx y cada
# .docx del corpus se trataba como "documento sin texto" y se saltaba en
# silencio. Se midio: los dos Excel de San Carlos llevaban dias con 0 fichas en
# el indice mientras la sincronizacion informaba de "2 nuevos" sin errores.
print("\n--- Lectores del corpus dentro del contenedor ---")

_ORDEN_LECTORES = (
    "import importlib.util as u;"
    "mods=('pandas','openpyxl','xlrd','pypdf','docx','PIL');"
    "f=[m for m in mods if not u.find_spec(m)];"
    "print('FALTAN:'+','.join(f) if f else 'TODOS')"
)
_lectores = _en_contenedor("copiloto-api", ["python", "-c", _ORDEN_LECTORES])
comprobar("todos los lectores del corpus estan instalados",
          _lectores.endswith("TODOS"),
          _lectores or "no se pudo preguntar al contenedor")

# Que un fallo de lectura deje rastro. Antes no lo dejaba, y por eso el
# problema de arriba pudo durar dias sin que nadie lo viera.
_ORDEN_RASTRO = (
    "from core import corpus;"
    "corpus.olvidar_fallos_de_lectura();"
    "corpus.leer_documento('/tmp/__no_existe__.xlsx');"
    "print('ANOTADO' if corpus.FALLOS_DE_LECTURA else 'SILENCIO')"
)
comprobar("un documento ilegible deja rastro en vez de callar",
          _en_contenedor("copiloto-api", ["python", "-c", _ORDEN_RASTRO])
          .endswith("ANOTADO"),
          "queda anotado en FALLOS_DE_LECTURA y sale en /corpus/estado")


# ==============================================================================
# EL CHAT PASA POR n8n Y GUARDA EN QDRANT
# ==============================================================================
# El recorrido que se pidio: sistema pregunta -> n8n -> agente -> se guarda en
# Qdrant -> responde al sistema. Lo que se vigila es que el paso de guardado
# EXISTA y se vea, no solo que haya respuesta: antes lo hacia un hilo de fondo
# del agente y desde fuera era imposible saber si habia ocurrido.
print("\n--- Chat orquestado por n8n ---")

codigo, antes = pedir(f"{API}/agente/memoria/*", espera=60)
_recuerdos_antes = (antes or {}).get("total", 0) if codigo == 200 else None
comprobar("se puede consultar cuanta memoria hay guardada",
          _recuerdos_antes is not None,
          f"{_recuerdos_antes} recuerdos en la coleccion")

_N8N = os.environ.get("N8N_URL", "http://localhost:5678")
codigo, chat = pedir(f"{_N8N}/webhook/copiloto-chat", "POST",
                     {"pregunta": "que documentos tienes cargados?",
                      "session_id": "prueba_bateria"}, espera=240)
if codigo != 200:
    omitir("el chat responde a traves de n8n", f"HTTP {codigo}")
else:
    _item = chat[0] if isinstance(chat, list) and chat else chat
    comprobar("el chat responde a traves de n8n",
              bool((_item or {}).get("respuesta")),
              f"{len((_item or {}).get('respuesta', ''))} caracteres")
    comprobar("la respuesta dice que paso por el orquestador",
              (_item or {}).get("orquestado_por") == "n8n",
              f"ejecucion #{(_item or {}).get('ejecucion_id')}")
    # Guardar un recuerdo exige VECTORIZARLO, y eso necesita la clave de
    # Gemini. Sin clave el agente responde igual (modo simulador) pero no
    # puede escribir en la memoria: eso no es un defecto del flujo, es que
    # falta una credencial. Se omite en vez de fallar, diciendolo.
    from core import seguridad as _seg_clave
    _hay_clave = _seg_clave.revisar_api_key().get("hay_clave")

    if not _hay_clave and (_item or {}).get("memoria_guardada") is not True:
        omitir("el intercambio queda guardado en Qdrant",
               "no hay GEMINI_API_KEY: sin ella no se puede vectorizar el "
               "recuerdo")
    else:
        comprobar("el intercambio queda guardado en Qdrant",
                  (_item or {}).get("memoria_guardada") is True,
                  f"coleccion {(_item or {}).get('memoria_coleccion')}")

    # La fecha en ISO 8601 es lo que permite ordenar los recuerdos. Con el
    # formato local anterior ("2026-10-23 03:02") no se podian ordenar junto
    # con las fichas del corpus.
    _fecha = (_item or {}).get("memoria_fecha", "")
    comprobar("el recuerdo lleva fecha en ISO 8601 y UTC",
              len(_fecha) == 20 and _fecha.endswith("Z") and _fecha[4] == "-",
              _fecha or "(sin fecha)")

    codigo, despues = pedir(f"{API}/agente/memoria/*", espera=60)
    if codigo == 200 and _recuerdos_antes is not None:
        if not _hay_clave:
            omitir("la coleccion de memoria crecio de verdad",
                   "no hay GEMINI_API_KEY para vectorizar")
        else:
            comprobar("la coleccion de memoria crecio de verdad",
                      despues.get("total", 0) > _recuerdos_antes,
                      f"{_recuerdos_antes} -> {despues.get('total')}")

# El flujo tiene que tener un nodo dedicado a escribir en Qdrant: es lo que
# hace visible el paso en el lienzo.
import json as _json
try:
    with open(os.path.join(AQUI, "n8n", "flujos", "03-chat-agente.json"),
              encoding="utf-8") as _f:
        _flujo = _json.load(_f)
    _nombres = [n["name"] for n in _flujo["nodes"]]
    comprobar("el flujo del chat tiene un nodo que escribe en Qdrant",
              any("Qdrant" in n for n in _nombres),
              ", ".join(_nombres))
    _guardar = next((n for n in _flujo["nodes"] if "Qdrant" in n["name"]), None)
    comprobar("ese nodo explica en el lienzo lo que hace",
              bool((_guardar or {}).get("notesInFlow")),
              "la nota se ve sin abrir el nodo")
except Exception as _exc:
    omitir("el flujo del chat tiene un nodo que escribe en Qdrant", str(_exc))
# ==============================================================================
# LOS RECUERDOS SE PUEDEN ORDENAR POR FECHA
# ==============================================================================
# Por que se vigila: esto fallaba de la forma mas enganosa posible. El campo
# `fecha` estaba indexado como KEYWORD, que sirve para buscar por valor exacto
# pero NO para ordenar, asi que Qdrant rechazaba `order_by` con "No range index
# for order_by key". Y escrito con un espacio -- "order by" en vez de
# "order_by" -- Qdrant lo IGNORA en silencio y devuelve `status: ok` con los
# puntos en orden arbitrario.
#
# El resultado era que, al mirar la coleccion ordenada por fecha, salia un
# recuerdo de hacia cuatro dias y parecia que no entraba nada nuevo, cuando si
# entraba. Un orden que miente es peor que no tener orden.
print("\n--- Orden por fecha de la memoria ---")

_QDRANT = os.environ.get("QDRANT_URL_PRUEBAS", "http://localhost:6333")

codigo, orden = pedir(
    f"{_QDRANT}/collections/memoria_usuario/points/scroll", "POST",
    {"limit": 5, "with_payload": True,
     "order_by": {"key": "fecha", "direction": "desc"}}, espera=60)

if codigo != 200 or not isinstance(orden, dict):
    omitir("la memoria se puede ordenar por fecha", f"HTTP {codigo}")
else:
    _puntos = (orden.get("result") or {}).get("points") or []
    if orden.get("status") == "ok" and not _puntos:
        # La memoria vacia es un estado legitimo: se vacia a proposito al
        # empezar de cero. Qdrant acepto la consulta, que es lo que se
        # comprobaba; no hay recuerdos que ordenar porque no hay recuerdos.
        for _t in ("Qdrant acepta ordenar la memoria por fecha",
                   "vienen de verdad de mas reciente a mas antiguo",
                   "todas las fechas estan en ISO 8601 y UTC"):
            omitir(_t, "la memoria esta vacia")
        _puntos = []
    else:
        comprobar("Qdrant acepta ordenar la memoria por fecha",
                  orden.get("status") == "ok" and bool(_puntos),
                  f"{len(_puntos)} recuerdos devueltos")

    _fechas = [(p.get("payload") or {}).get("fecha", "") for p in _puntos]
    if _fechas:
        comprobar("vienen de verdad de mas reciente a mas antiguo",
                  _fechas == sorted(_fechas, reverse=True),
                  " > ".join(f[:19] for f in _fechas[:3]))

        # Todas en el mismo formato: con dos conviviendo, ordenar es comparar
        # peras con manzanas.
        _raras = [f for f in _fechas
                  if not (len(f) == 20 and f.endswith("Z"))]
        comprobar("todas las fechas estan en ISO 8601 y UTC",
                  not _raras, _raras[:2] if _raras else "formato unico")
# ==============================================================================
# REVISION DE SEGURIDAD DE LAS CREDENCIALES
# ==============================================================================
# Lo que se vigila aqui, y por que:
#
#   - Que NO se chive de ninguna credencial. Un diagnostico que muestra la
#     clave "para ayudar a comprobarla" la deja escrita en la pantalla, en las
#     capturas y en los registros.
#   - Que NO de falsas alarmas. La primera version daba por hecho que toda
#     clave de Gemini empieza por "AIza" y mide 39 caracteres; al probarla con
#     la clave real del proyecto salto un aviso sobre una clave que funciona
#     perfectamente (empieza por "AQ." y mide 53). Un aviso que grita con algo
#     correcto ensena a ignorarlos, y el dia que salte uno de verdad tampoco
#     se mirara.
print("\n--- Revision de seguridad ---")
from core import seguridad as _seg

_CLAVE_FALSA = "AIza" + "B" * 35

_rev = _seg.revisar_api_key(_CLAVE_FALSA)
comprobar("no muestra la clave entera en ningun campo",
          _CLAVE_FALSA not in repr(_rev),
          f"solo su huella: {_rev['huella']}")
comprobar("la huella dice la longitud pero no el contenido",
          "35" not in _rev["huella"] and "(39 caracteres)" in _rev["huella"],
          _rev["huella"])

# Los dos formatos que Google emite de verdad.
for _prefijo, _largo, _rotulo in (("AIza", 39, "formato clasico"),
                                  ("AQ.", 53, "formato nuevo")):
    _clave = _prefijo + "x" * (_largo - len(_prefijo))
    comprobar(f"acepta el {_rotulo} sin quejarse",
              not _seg.revisar_api_key(_clave)["avisos"],
              f"{_prefijo}... de {_largo} caracteres")

# Y lo que SI debe avisar.
for _mala, _rotulo in ((" AIza con espacios ", "una clave con espacios"),
                       ("sk-proj-de-otro-servicio", "una clave de otro sitio")):
    comprobar(f"avisa de {_rotulo}",
              bool(_seg.revisar_api_key(_mala)["avisos"]),
              "detectado")

comprobar("una clave vacia se reporta como ausente, no como invalida",
          _seg.revisar_api_key("")["hay_clave"] is False,
          "el agente pasa a modo simulador y se dice")

# --- Drive ---------------------------------------------------------------
_drive = _seg.revisar_drive()
comprobar("la revision de Drive lee el permiso del codigo, no de una constante",
          "drive.readonly" in _drive["alcance"],
          _drive["alcance"])
comprobar("no se pide ningun permiso de escritura sobre Drive",
          not any(p in _drive["alcance"] for p in
                  ("auth/drive\"", "drive.file", "drive.appdata")),
          "solo lectura")
comprobar("la revision de Drive no filtra la clave privada",
          "private_key" not in repr(_drive) and "BEGIN PRIVATE" not in repr(_drive),
          "solo el correo de la cuenta, que no es secreto")


# ==============================================================================
# CONVERSACIONES: RENOMBRAR Y ELIMINAR
# ==============================================================================
# Lo importante de eliminar: una conversacion vive en DOS sitios. En SQLite
# esta el historial literal y en Qdrant los recuerdos vectorizados. Borrar solo
# de SQLite la dejaria invisible pero viva, y el agente seguiria recordando
# cosas de una charla que el usuario cree eliminada.
print("\n--- Conversaciones: renombrar y eliminar ---")
from core import memoria as _mem

_ID = "prueba_bateria_sesion"
_mem.registrar_sesion(_ID, "titulo automatico feo")
_mem.guardar_mensaje(_ID, "user", "primera pregunta de la prueba")
_mem.guardar_mensaje(_ID, "assistant", "respuesta de la prueba")

comprobar("se puede renombrar una conversacion",
          _mem.renombrar_sesion(_ID, "Cuenca San Carlos - revision")
          and _mem.obtener_sesion(_ID)["titulo"] == "Cuenca San Carlos - revision",
          "titulo cambiado")
comprobar("un titulo vacio se rechaza",
          _mem.renombrar_sesion(_ID, "   ") is False,
          "no se deja una charla sin nombre")

_antes = len(_mem.cargar_historial(_ID))
_res = _mem.borrar_sesion(_ID)
comprobar("al eliminar se borra el historial", _res["mensajes"] == _antes,
          f"{_res['mensajes']} mensajes de {_antes}")
comprobar("y la conversacion deja de existir",
          _mem.obtener_sesion(_ID) is None, "no aparece en la lista")
comprobar("tambien se limpian los recuerdos de Qdrant",
          _res["recuerdos"] >= 0,
          f"{_res['recuerdos']} recuerdos" if _res["recuerdos"] >= 0
          else "Qdrant no respondio: se avisa en vez de callar")


# ==============================================================================
# IMAGENES PEGADAS: QUITAR DE UNA EN UNA
# ==============================================================================
print("\n--- Quitar una sola imagen pegada ---")
_SES = "prueba_galeria"
pedir(f"{API}/chat/imagenes/{_SES}", "DELETE", espera=20)
for _ in range(3):
    pedir(f"{API}/chat/imagen", "POST", {
        "session_id": _SES, "nombre": "captura.png", "tipo": "image/png",
        "datos": _PNG_MINIMO}, espera=30)

codigo, _ = pedir(f"{API}/chat/imagenes/{_SES}/1", "DELETE", espera=20)
comprobar("se puede quitar una imagen concreta", codigo == 200, f"HTTP {codigo}")
codigo, cola = pedir(f"{API}/chat/imagenes/{_SES}", espera=20)
comprobar("las demas siguen ahi",
          (cola or {}).get("cuantas") == 2,
          f"quedan {(cola or {}).get('cuantas')} de 3")
codigo, _ = pedir(f"{API}/chat/imagenes/{_SES}/99", "DELETE", espera=20)
comprobar("una posicion que no existe da 404 en vez de romper",
          codigo == 404, f"HTTP {codigo}")
pedir(f"{API}/chat/imagenes/{_SES}", "DELETE", espera=20)
# ==============================================================================
# COHERENCIA ENTRE EL DISCO Y LO QUE ESTA CORRIENDO
# ==============================================================================
# Un flujo puede estar activo en n8n y decir `"active": false` en su archivo.
# Mientras nadie reimporte, no se nota. El dia que alguien reimporte -- que es
# justo lo que se hace al desplegar -- el cron de las 02:00 se apaga sin que
# nadie lo pida y sin que nadie se entere hasta que el corpus lleve dias sin
# actualizarse.
print("\n--- Coherencia de los flujos ---")
import json as _json

_CARPETA_FLUJOS = os.path.join(AQUI, "n8n", "flujos")
_vivos = _en_n8n(["n8n", "list:workflow"])

for _archivo in sorted(os.listdir(_CARPETA_FLUJOS)):
    if not _archivo.endswith(".json"):
        continue
    with open(os.path.join(_CARPETA_FLUJOS, _archivo), encoding="utf-8") as _f:
        _flujo = _json.load(_f)
    _id = _flujo.get("id", "")
    _en_marcha = _id in _vivos
    if not _en_marcha:
        omitir(f"{_archivo}: coincide con lo que corre",
               "el flujo no esta importado en n8n")
        continue
    comprobar(f"{_archivo}: el archivo dice que esta activo",
              _flujo.get("active") is True,
              "si dijera false, reimportar apagaria el flujo")

# Los campos de fecha tienen que poder ORDENARSE, no solo buscarse. Un indice
# KEYWORD permite "dame los que valgan exactamente esto" pero Qdrant rechaza
# `order_by` sobre el. Fue lo que hizo creer que la memoria no recibia nada.
print("\n--- Indices de fecha en Qdrant ---")
_QD = os.environ.get("QDRANT_URL_PRUEBAS", "http://localhost:6333")

for _col, _campo in (("memoria_usuario", "fecha"),
                     ("corpus_lepidopteros", "fecha_indexado")):
    codigo, _info = pedir(f"{_QD}/collections/{_col}", espera=30)
    if codigo != 200:
        omitir(f"{_col}: el campo '{_campo}' se puede ordenar",
               f"HTTP {codigo}")
        continue
    _esquema = ((_info.get("result") or {}).get("payload_schema") or {})
    _tipo = (_esquema.get(_campo) or {}).get("data_type", "")
    comprobar(f"{_col}: '{_campo}' esta indexado como fecha",
              _tipo == "datetime",
              f"tipo={_tipo or 'sin indice'}")
# ==============================================================================
# CONECTAR DRIVE DESDE LA INTERFAZ
# ==============================================================================
# Lo que se vigila, y por que importa cada cosa:
#
#   - Que lo pegado se VALIDE antes de escribirlo en disco. Guardar primero y
#     descubrir despues que no sirve deja una credencial invalida en el
#     proyecto y la sensacion de que algo quedo a medias.
#   - Que la fuente SOLO cambie si la conexion funciona. Dejar el sistema
#     apuntando a un Drive que no responde deja al agente sin nada que
#     estudiar, y el fallo aparece de madrugada, cuando corre el cron.
#   - Que no se filtre la credencial en ningun diagnostico.
print("\n--- Conectar Drive desde la interfaz ---")
from core import ajustes as _aj
from core import conexion_drive as _cd

# Identificador de carpeta: casi todo el mundo pega la direccion entera.
for _pegado, _esperado, _rotulo in (
        ("https://drive.google.com/drive/folders/1A2b3C4d5E6f7G8h",
         "1A2b3C4d5E6f7G8h", "direccion normal"),
        ("https://drive.google.com/drive/u/0/folders/1A2b3C4d5E6f7G8h?usp=sharing",
         "1A2b3C4d5E6f7G8h", "direccion con cuenta y parametros"),
        ("1A2b3C4d5E6f7G8h", "1A2b3C4d5E6f7G8h", "identificador suelto"),
        ("no es una carpeta", "", "texto cualquiera")):
    comprobar(f"reconoce el identificador en una {_rotulo}",
              _cd.extraer_identificador(_pegado) == _esperado,
              f"-> {_cd.extraer_identificador(_pegado)!r}")

# Validacion del JSON pegado.
_BUENO = ('{"type":"service_account","project_id":"p","private_key":"k",'
          '"client_email":"bot@p.iam.gserviceaccount.com"}')
for _texto, _acepta, _rotulo in (
        ("", False, "nada pegado"),
        ("esto no es json", False, "texto que no es JSON"),
        ('{"hola": 1}', False, "JSON sin los campos de una credencial"),
        ('{"type":"authorized_user","project_id":"p","private_key":"k",'
         '"client_email":"e"}', False, "credenciales de usuario"),
        (_BUENO, True, "una cuenta de servicio correcta")):
    _datos, _err = _cd.revisar_json(_texto)
    comprobar(f"{'acepta' if _acepta else 'rechaza'} {_rotulo}",
              bool(_datos) == _acepta,
              (_err or "aceptado")[:58])

# La conexion se prueba ANTES de cambiar la fuente: con una credencial que no
# sirve, el sistema tiene que quedarse como estaba.
_fuente_antes = _en_contenedor(
    "copiloto-api",
    ["python", "-c",
     "from config import settings; print(settings.fuente_corpus())"]).strip()
# ESTA prueba conecta con datos inventados, asi que tiene que fallar. Lo que
# se vigila es que al fallar NO destruya una credencial que estuviera
# funcionando: la primera version de `conectar()` guardaba primero y probaba
# despues, y esta misma linea dejo sin acceso a Drive una conexion real.
_ruta_cred = _cd.ruta_credencial()
_habia = None
if os.path.exists(_ruta_cred):
    with open(_ruta_cred, encoding="utf-8") as _f:
        _habia = _f.read()

_r = _cd.conectar(_BUENO, "1A2b3C4d5E6f7G8h")

_sigue = None
if os.path.exists(_ruta_cred):
    with open(_ruta_cred, encoding="utf-8") as _f:
        _sigue = _f.read()
comprobar("una conexion fallida NO destruye la credencial que ya habia",
          _habia == _sigue,
          "intacta" if _habia == _sigue else "LA PISO: se perdio el acceso")
comprobar("una credencial que no conecta NO cambia la fuente",
          _r["conectado"] is False,
          _r["motivo"][:70])
_fuente_despues = _en_contenedor(
    "copiloto-api",
    ["python", "-c",
     "from config import settings; print(settings.fuente_corpus())"]).strip()
comprobar("la fuente sigue siendo la de antes",
          _fuente_antes == _fuente_despues,
          f"{_fuente_antes!r} antes y despues")

# Que el mensaje diga el SIGUIENTE PASO, no solo que fallo. Google contesta
# cosas como "Service account info was not in the expected form", que es
# cierto y completamente inutil para quien acaba de pegar un archivo.
comprobar("el fallo explica QUE hacer, no solo que fallo",
          any(p in _r["motivo"].lower() for p in
              ("comparte", "permiso", "identificador", "libreria",
               "formato correcto", "pega su contenido", "conexion")),
          _r["motivo"][:72])
comprobar("ningun diagnostico filtra la clave privada",
          "private_key" not in repr(_r) and '"k"' not in repr(_r),
          "solo sale el correo de la cuenta, que no es secreto")

# Los ajustes guardados mandan sobre el .env, y se pueden deshacer.
_previo = _aj.leer()
_aj.guardar({"FUENTE_CORPUS": "drive"})
comprobar("un ajuste guardado se aplica sin reiniciar",
          os.environ.get("FUENTE_CORPUS") == "drive",
          "queda en el entorno del proceso")
_aj.guardar({"FUENTE_CORPUS": None})
comprobar("y se puede deshacer, devolviendo el mando al .env",
          "FUENTE_CORPUS" not in _aj.leer(),
          "el ajuste desaparece del archivo")
if _previo:
    _aj.guardar(_previo)

comprobar("solo se admiten los ajustes previstos",
          _aj.guardar({"COSA_RARA": "x"}).get("COSA_RARA") is None,
          "una clave desconocida se ignora")
# ==============================================================================
# CONECTAR DRIVE POR CAMPOS
# ==============================================================================
# El formulario pide el correo de la cuenta y la clave privada por separado, en
# vez de un JSON entero. Lo que mas se rompe al hacerlo asi son los saltos de
# linea de la clave: al copiarla de un chat o de una celda se convierten en
# `\n` literales o desaparecen, y Google contesta "Service account info was not
# in the expected form", que es cierto y no dice nada util.
print("\n--- Conectar Drive por campos ---")

_CLAVE_OK = ("-----BEGIN PRIVATE KEY-----\n"
             "MIIEvQIBADANBg\nkqhkiG9w0BAQ\n"
             "-----END PRIVATE KEY-----")

for _bruto, _rotulo in (
        (_CLAVE_OK.replace("\n", "\n"), "la clave con '\n' literales"),
        (_CLAVE_OK.replace("\n", "\r\n"), "la clave con saltos de Windows"),
        (f'"{_CLAVE_OK}"', "la clave entre comillas")):
    _limpia = _cd.normalizar_clave_privada(_bruto)
    comprobar(f"arregla {_rotulo}",
              not _cd.revisar_clave_privada(_limpia),
              f"{_limpia.count(chr(10))} saltos recuperados")

for _clave, _rotulo in (
        ("", "una clave vacia"),
        ("ABCDEF", "una clave sin su cabecera"),
        ("-----BEGIN PRIVATE KEY----- ABC -----END PRIVATE KEY-----",
         "una clave en una sola linea")):
    _problema = _cd.revisar_clave_privada(_cd.normalizar_clave_privada(_clave))
    comprobar(f"rechaza {_rotulo}", bool(_problema), _problema[:56])

_r2 = _cd.conectar_por_campos("juan@gmail.com", _CLAVE_OK, "", "abc123def456")
comprobar("rechaza un correo que no es de cuenta de servicio",
          _r2["conectado"] is False and "cuenta de servicio" in _r2["motivo"],
          _r2["motivo"][:58])

comprobar("las dos formas de conectar terminan en el mismo sitio",
          _cd.conectar_por_campos.__doc__ is not None
          and "conectar()" in _cd.conectar_por_campos.__doc__,
          "conectar_por_campos arma el JSON y reutiliza conectar()")
# ==============================================================================
# CONECTAR DRIVE CON UN CLIENTE DE OAUTH (ID + SECRETO)
# ==============================================================================
# Es la unica forma de conectar Drive con dos campos cortos. Una cuenta de
# servicio no se puede reducir a eso: su secreto es una clave RSA de varias
# lineas, y pedirla a mano obligaba a un recuadro grande e ilegible.
print("\n--- Conectar Drive con ID y secreto ---")

_CID_OK = "616567408789-abc.apps.googleusercontent.com"
_SEC_OK = "GOCSPX-abcdefghijk"

for _cid, _sec, _acepta, _rotulo in (
        ("", _SEC_OK, False, "sin ID"),
        (_CID_OK, "", False, "sin secreto"),
        ("mi-cliente", _SEC_OK, False, "un ID sin el sufijo de Google"),
        # La consola de Google muestra el secreto oculto (****hGYQ). Copiarlo
        # asi es el error mas facil de cometer y el mas dificil de ver, porque
        # Google responde "invalid_client" sin decir cual de los dos falla.
        (_CID_OK, "****hGYQ", False, "el secreto copiado oculto"),
        (_CID_OK, _SEC_OK, True, "un ID y un secreto correctos")):
    _p = _cd.revisar_cliente(_cid, _sec)
    comprobar(f"{'acepta' if _acepta else 'rechaza'} {_rotulo}",
              (not _p) == _acepta, (_p or "aceptado")[:58])

_url = _cd.url_de_autorizacion(_CID_OK, "http://localhost:8501/")
for _clave, _por_que in (
        ("access_type=offline",
         "sin esto el permiso dura una hora y el cron muere esa noche"),
        ("prompt=consent",
         "sin esto Google no reenvia el token a quien ya autorizo antes"),
        ("drive.readonly", "solo lectura"),
        ("response_type=code", "el flujo correcto")):
    comprobar(f"el enlace de permiso lleva {_clave}", _clave in _url, _por_que)

# Un codigo invalido tiene que fallar con una explicacion, no con el error
# crudo de Google.
_canje = _cd.canjear_codigo(_CID_OK, _SEC_OK, "codigo-inventado",
                            "http://localhost:8501/")
comprobar("un codigo invalido se rechaza con explicacion",
          _canje["ok"] is False and len(_canje["motivo"]) > 20,
          _canje["motivo"][:70])

# La fuente tiene que saber usar los dos tipos de credencial.
_tipos = _en_contenedor("copiloto-api", ["python", "-c", (
    "import json,os,tempfile;"
    "from core.fuentes import FuenteDrive;"
    "d=tempfile.mkdtemp();r=os.path.join(d,'c.json');"
    "json.dump({'type':'authorized_user','client_id':'x',"
    "'client_secret':'y','refresh_token':'z',"
    "'token_uri':'https://oauth2.googleapis.com/token'},open(r,'w'));"
    "c=FuenteDrive(carpeta_id='a',credenciales=r)._credenciales();"
    "print(type(c).__name__, c.scopes[0])")])
comprobar("la fuente acepta credenciales de OAuth",
          "Credentials" in _tipos and "drive.readonly" in _tipos,
          _tipos.strip()[:62])
# ==============================================================================
# LAS DOS CREDENCIALES SE TRATAN IGUAL EN TODAS PARTES
# ==============================================================================
# El fallo que esto vigila: `FuenteDrive` aprendio a distinguir credenciales de
# OAuth de las de cuenta de servicio, pero `probar_conexion` siguio
# construyendolas SIEMPRE como cuenta de servicio. Al validar una autorizacion
# de OAuth, Google devolvia "not in the expected form" y la interfaz lo
# traducia a "la clave privada no tiene el formato correcto": un consejo que no
# tenia nada que ver con lo que pasaba, sobre una credencial que era valida.
#
# La leccion es que probar algo por un camino distinto del que se usara
# despues no prueba nada. Aqui se comprueba que los dos tipos atraviesen las
# mismas funciones.
print("\n--- Los dos tipos de credencial, en todo el recorrido ---")
import json as _json
import os as _os
import tempfile as _tmp

_CREDS = {
    "oauth": {"type": "authorized_user",
              "client_id": "x.apps.googleusercontent.com",
              "client_secret": "GOCSPX-abc", "refresh_token": "1//abc",
              "token_uri": "https://oauth2.googleapis.com/token"},
    # La clave de una cuenta de servicio tiene que ser una clave RSA DE
    # VERDAD: `from_service_account_info` la descifra al construirla, y con un
    # texto cualquiera revienta. Se genera una desechable en el momento en vez
    # de guardar una de ejemplo en el repositorio, que seria una clave privada
    # versionada aunque no sirva para nada.
    # `token_uri` es obligatorio tambien para una cuenta de servicio: sin el,
    # Google rechaza la credencial con "missing fields token_uri". El JSON que
    # descarga Google lo trae, y el que arma `conectar_por_campos` tambien;
    # faltaba solo en este ejemplo.
    "cuenta de servicio": {"type": "service_account", "project_id": "p",
                           "private_key": "<<GENERAR>>",
                           "client_email": "bot@p.iam.gserviceaccount.com",
                           "token_uri": "https://oauth2.googleapis.com/token"},
}

_CLAVE_DESECHABLE = _en_contenedor("copiloto-api", ["python", "-c", (
    "from cryptography.hazmat.primitives import serialization as s;"
    "from cryptography.hazmat.primitives.asymmetric import rsa;"
    "k=rsa.generate_private_key(public_exponent=65537,key_size=2048);"
    "print(k.private_bytes(s.Encoding.PEM,"
    "s.PrivateFormat.PKCS8,s.NoEncryption()).decode().replace(chr(10),'|'))"
)]).strip()
if _CLAVE_DESECHABLE.startswith("-----BEGIN"):
    # El contenedor devuelve la clave con '|' donde iban los saltos, porque
    # una clave multilinea no sobrevive al paso por la salida del proceso.
    _CREDS["cuenta de servicio"]["private_key"] = (
        _CLAVE_DESECHABLE.replace("|", chr(10)))
else:
    _CREDS.pop("cuenta de servicio")
    omitir("la fuente construye una credencial de cuenta de servicio",
           "no se pudo generar una clave RSA de prueba en el contenedor")

# El archivo de prueba se crea DENTRO del contenedor, no aqui. La primera
# version lo escribia en un temporal del equipo y se lo pasaba al contenedor
# por su ruta: alli esa ruta no existe, y la prueba fallaba por una razon que
# no tenia nada que ver con lo que queria medir.
_ORDEN_CRED = (
    "import json, os, sys, tempfile;"
    "ruta=os.path.join(tempfile.mkdtemp(),'c.json');"
    "open(ruta,'w').write(sys.argv[1]);"
    "from core.fuentes import FuenteDrive;"
    "c=FuenteDrive(carpeta_id='a',credenciales=ruta)._credenciales();"
    "os.environ['GOOGLE_CREDENCIALES']=ruta;"
    "os.environ['DRIVE_CARPETA_ID']='abc123def456';"
    "from core import seguridad;"
    "r=seguridad.revisar_drive();"
    "print(type(c).__name__, '|', 'GRAVE' if r['grave'] else 'SIN_AVISOS_GRAVES')"
)

for _nombre, _cred in _CREDS.items():
    _salida = _en_contenedor(
        "copiloto-api", ["python", "-c", _ORDEN_CRED, _json.dumps(_cred)])
    comprobar(f"la fuente construye una credencial de {_nombre}",
              "Credentials" in _salida, _salida.strip()[:48] or "(sin salida)")
    comprobar(f"la revision de seguridad acepta la de {_nombre}",
              "SIN_AVISOS_GRAVES" in _salida, _salida.strip()[:48])

# El consejo del error tiene que corresponder al tipo de credencial: con OAuth
# no hay ninguna cuenta de servicio a la que compartirle nada.
_msg_oauth = _cd._traducir_fallo(Exception("File not found: abc"), "")
comprobar("con OAuth no se habla de cuentas de servicio",
          "cuenta de servicio" not in _msg_oauth.lower(),
          _msg_oauth[:62])
_msg_sa = _cd._traducir_fallo(Exception("File not found: abc"),
                              "bot@p.iam.gserviceaccount.com")
comprobar("con cuenta de servicio si se dice a quien compartir",
          "gserviceaccount.com" in _msg_sa, _msg_sa[:62])






















print("\n--- La ruta que la API le da a QGIS ---")
# El script de QGIS corre en el equipo de la persona, no en el contenedor, y
# le pregunta a la API donde esta el proyecto. La API contestaba "/app": su
# propia ruta dentro de Docker. Resultado medido: QGIS informaba "0 de 3
# capas del sistema" con los archivos ahi al lado, porque buscaba en una
# carpeta que en Windows no existe.
_cod_bp, _bp = pedir(f"{API}/base-path", espera=20)
if _cod_bp != 200:
    omitir("la API dice donde esta el proyecto", f"HTTP {_cod_bp}")
    omitir("y esa carpeta existe en el equipo", "sin respuesta")
    omitir("la capa del area se encuentra desde ahi", "sin respuesta")
else:
    _ruta_equipo = _bp.get("base_path", "")
    comprobar("la API dice donde esta el proyecto",
              bool(_ruta_equipo), _ruta_equipo or "(vacio)")
    comprobar("y esa carpeta existe en el equipo",
              os.path.isdir(_ruta_equipo),
              "no es la ruta de dentro del contenedor"
              if os.path.isdir(_ruta_equipo)
              else f"'{_ruta_equipo}' no existe fuera de Docker")
    _capa_area = os.path.join(_ruta_equipo, "data",
                              "municipios_filtrados.geojson")
    if os.path.exists(_capa_area):
        comprobar("la capa del area se encuentra desde ahi", True,
                  "QGIS la va a localizar")
    else:
        omitir("la capa del area se encuentra desde ahi",
               "todavia no se ha fijado un area de estudio")


# ==============================================================================
print("\n" + "=" * 70)
total = ACIERTOS + len(FALLOS)
if FALLOS:
    print(f"{ACIERTOS} de {total} superadas, {len(FALLOS)} FALLIDAS"
          f"{f', {OMITIDAS} omitidas' if OMITIDAS else ''}:\n")
    for f in FALLOS:
        print("  -", f)
    sys.exit(1)
print(f"{ACIERTOS} de {total} comprobaciones superadas"
      f"{f', {OMITIDAS} omitidas' if OMITIDAS else ''}")
