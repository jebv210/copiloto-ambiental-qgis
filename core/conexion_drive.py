# -*- coding: utf-8 -*-
"""
Conectar Google Drive desde la interfaz, sin tocar archivos a mano.
===============================================================================

QUE RESUELVE
------------
Conectar Drive obligaba a editar el `.env`, dejar el JSON de la cuenta de
servicio en la ruta correcta y recrear los contenedores. Cuatro pasos fuera de
la aplicacion, y uno de ellos (`--force-recreate`, no `restart`) nada evidente:
quien usaba `restart` veia que el cambio "no funcionaba" sin saber por que.

Aqui el usuario pega su JSON, pega el identificador de la carpeta, y el sistema
hace el resto: guarda la credencial donde corresponde, PRUEBA la conexion de
verdad y solo entonces cambia la fuente.

LA REGLA QUE MANDA
------------------
**La fuente solo cambia si la conexion funciona.** Si Drive no responde, o la
carpeta no esta compartida, o el JSON no sirve, el corpus local sigue como
estaba y el agente sigue trabajando igual. Nunca se deja el sistema apuntando a
una fuente que no se ha comprobado.

Y al reves: con Drive conectado, el corpus local NO se borra. Los documentos
bajados se guardan dentro de `corpus/` como una carpeta mas, asi que el agente
estudia de los dos sitios sin enterarse de la diferencia.

SOBRE LA CREDENCIAL
-------------------
El JSON de la cuenta de servicio es una contrasena. Aqui:
  - se guarda en `config/credenciales_drive.json`, que esta en `.gitignore`;
  - se escriben permisos restrictivos donde el sistema operativo los respeta;
  - NUNCA se devuelve su contenido, ni entero ni en parte. Lo unico que sale
    de aqui es el correo de la cuenta, que no es secreto: es justo lo que hay
    que compartirle a quien administre la carpeta.

===============================================================================
"""

import json
import os
import re
import stat

#: Nombre del archivo donde se guarda la credencial. Coincide con lo que
#: documenta `documentos/Credenciales_Drive.md` y con lo que ignora git.
NOMBRE_CREDENCIAL = "credenciales_drive.json"

#: Claves que tiene cualquier JSON de cuenta de servicio de Google. Si falta
#: alguna, lo pegado no es lo que se cree que es.
CLAVES_OBLIGATORIAS = ("type", "project_id", "private_key", "client_email")

#: Un identificador de carpeta de Drive: letras, numeros, guiones y guiones
#: bajos. Sirve para avisar de que se pego la direccion entera en vez del
#: identificador, que es el error mas comun.
PATRON_CARPETA = re.compile(r"^[A-Za-z0-9_-]{10,}$")


def carpeta_credenciales() -> str:
    """
    Carpeta donde se guarda la credencial.

    Returns:
        Ruta absoluta de `config/`, creada si no existe.
    """
    from config import settings

    destino = settings.ruta_proyecto("config")
    os.makedirs(destino, exist_ok=True)
    return destino


def ruta_credencial() -> str:
    """
    Ruta completa del archivo de credenciales.

    Returns:
        Ruta absoluta de `config/credenciales_drive.json`.
    """
    return os.path.join(carpeta_credenciales(), NOMBRE_CREDENCIAL)


def extraer_identificador(texto: str) -> str:
    """
    Saca el identificador de la carpeta de lo que haya pegado el usuario.

    Admite el identificador suelto o la direccion completa del navegador,
    porque pegar la direccion entera es lo que hace casi todo el mundo:

        https://drive.google.com/drive/folders/1A2b3C4d5E6f7G8h9I0j
        https://drive.google.com/drive/u/0/folders/1A2b3C...?usp=sharing

    Args:
        texto: Lo pegado.

    Returns:
        El identificador, o cadena vacia si no se reconoce nada.
    """
    texto = (texto or "").strip()
    if not texto:
        return ""
    encontrado = re.search(r"/folders/([A-Za-z0-9_-]+)", texto)
    if encontrado:
        return encontrado.group(1)
    # Pudo pegar el identificador suelto, quiza con parametros detras.
    suelto = texto.split("?")[0].rstrip("/").split("/")[-1]
    return suelto if PATRON_CARPETA.match(suelto) else ""


def revisar_json(contenido: str) -> tuple:
    """
    Comprueba que lo pegado sea un JSON de cuenta de servicio.

    Se valida ANTES de escribirlo en disco: guardar primero y descubrir
    despues que no sirve deja una credencial invalida en el proyecto y la
    sensacion de que algo quedo a medias.

    Args:
        contenido: El JSON pegado por el usuario.

    Returns:
        Tupla `(datos, error)`. `datos` es el diccionario del JSON y `error`
        un texto explicativo; solo uno de los dos tiene valor.
    """
    texto = (contenido or "").strip()
    if not texto:
        return None, "No has pegado nada."

    try:
        datos = json.loads(texto)
    except json.JSONDecodeError as exc:
        return None, (
            f"Esto no es un JSON valido ({exc.msg}, linea {exc.lineno}). "
            f"Pega el archivo COMPLETO que descargaste de Google Cloud, "
            f"incluidas las llaves de apertura y cierre.")

    if not isinstance(datos, dict):
        return None, "El JSON no tiene la forma esperada."

    faltan = [c for c in CLAVES_OBLIGATORIAS if not datos.get(c)]
    if faltan:
        return None, (
            f"Al JSON le faltan campos obligatorios: {', '.join(faltan)}. "
            f"Comprueba que descargaste la clave de una CUENTA DE SERVICIO y "
            f"no otra cosa.")

    if datos.get("type") != "service_account":
        return None, (
            f"El archivo es de tipo '{datos.get('type')}', no de una cuenta "
            f"de servicio. Con credenciales de usuario el acceso caduca y "
            f"pide confirmacion en el navegador, y de madrugada no hay quien "
            f"la de.")

    return datos, ""


def probar_conexion(ruta_json: str, carpeta_id: str) -> dict:
    """
    Se conecta a Drive de verdad y mira que hay en la carpeta.

    No se limita a comprobar que la credencial tenga buena pinta: pide el
    listado. Es la unica forma de distinguir las dos situaciones que mas se
    confunden -- credencial correcta pero carpeta NO compartida, frente a
    credencial invalida -- y que dan errores parecidos si solo se mira el
    archivo.

    Args:
        ruta_json: Archivo de la cuenta de servicio.
        carpeta_id: Identificador de la carpeta compartida.

    Returns:
        Diccionario con `conecta`, `motivo`, `archivos`, `carpetas` y
        `cuenta`.
    """
    salida = {"conecta": False, "motivo": "", "archivos": 0, "carpetas": 0,
              "cuenta": ""}
    try:
        with open(ruta_json, encoding="utf-8") as f:
            salida["cuenta"] = (json.load(f) or {}).get("client_email", "")
    except Exception:
        pass

    try:
        from googleapiclient.discovery import build
    except ImportError as exc:
        salida["motivo"] = (
            f"Falta una libreria de Google en el servidor ({exc.name}). "
            f"Esto no se arregla desde aqui: hay que reconstruir la imagen.")
        return salida

    try:
        from core.fuentes import FuenteDrive

        # La credencial se construye con `FuenteDrive`, que sabe distinguir
        # las de OAuth de las de cuenta de servicio.
        #
        # Antes aqui se llamaba directamente a `from_service_account_file`, y
        # al conectar con OAuth esa funcion no entendia el archivo: Google
        # devolvia "not in the expected form" y la interfaz lo traducia a "la
        # clave privada no tiene el formato correcto", un consejo que no tenia
        # nada que ver con lo que estaba pasando.
        #
        # Probar la conexion con el MISMO codigo que la usara despues es
        # ademas lo correcto: si la prueba y el uso real se construyen por
        # caminos distintos, una puede pasar y el otro fallar.
        servicio = build(
            "drive", "v3",
            credentials=FuenteDrive(credenciales=ruta_json)._credenciales(),
            cache_discovery=False)
        respuesta = servicio.files().list(
            q=f"'{carpeta_id}' in parents and trashed = false",
            fields="files(id,name,mimeType)", pageSize=100,
            supportsAllDrives=True, includeItemsFromAllDrives=True,
        ).execute()
    except Exception as exc:
        salida["motivo"] = _traducir_fallo(exc, salida["cuenta"])
        return salida

    elementos = respuesta.get("files", [])
    carpetas = [e for e in elementos
                if e.get("mimeType") == "application/vnd.google-apps.folder"]
    salida.update({
        "conecta": True,
        "archivos": len(elementos) - len(carpetas),
        "carpetas": len(carpetas),
        "motivo": "",
    })
    if not elementos:
        salida["motivo"] = (
            "La conexion funciona, pero la carpeta esta vacia o no contiene "
            "nada que esta cuenta pueda ver.")
    return salida


def _traducir_fallo(error: Exception, cuenta: str) -> str:
    """
    Convierte el error de Google en algo accionable.

    Los mensajes de la API dicen que fallo, no que hacer. "File not found" con
    una credencial correcta casi siempre significa que la carpeta no esta
    compartida, que es un problema distinto y con otra solucion.

    Args:
        error: La excepcion que lanzo la API.
        cuenta: Correo de la cuenta de servicio, para poder nombrarlo.

    Returns:
        Explicacion con el siguiente paso.
    """
    texto = str(error)
    minusculas = texto.lower()

    # El consejo cambia segun COMO se entre, y darlo al reves confunde mas que
    # no darlo: con OAuth no hay ninguna cuenta de servicio a la que compartir
    # nada, porque se entra como la persona que autorizo.
    if cuenta and cuenta.endswith(".gserviceaccount.com"):
        _comparte = (f"Comparte la carpeta en Drive con {cuenta}, dandole "
                     f"permiso de Lector.")
    else:
        _comparte = ("Comprueba que la cuenta de Google con la que autorizaste "
                     "tenga acceso a esa carpeta.")

    if "not found" in minusculas or "404" in texto:
        return (f"Google dice que no encuentra la carpeta. {_comparte} "
                f"Comprueba tambien que el identificador sea el correcto: "
                f"es lo que va despues de '/folders/' en la direccion.")
    if "permission" in minusculas or "403" in texto:
        return (f"Google rechaza el acceso. {_comparte} Comprueba ademas que "
                f"la API de Drive este habilitada en el proyecto de Google "
                f"Cloud.")
    if "invalid_grant" in minusculas or "invalid jwt" in minusculas:
        return ("La credencial fue rechazada. Suele pasar si la clave se "
                "revoco, o si la hora del equipo esta muy desajustada.")
    if ("not in the expected form" in minusculas
            or "could not deserialize" in minusculas
            or "no key could be detected" in minusculas):
        # Google solo dice que el formato no es el esperado, sin decir cual es
        # el fallo real. Casi siempre es la clave privada: al copiarla de una
        # pagina o de un chat, los saltos de linea se pierden y queda una sola
        # linea que ya no es una clave valida.
        return ("La clave privada del JSON no tiene el formato correcto. "
                "Suele pasar al copiar el contenido de un sitio que se come "
                "los saltos de linea. Abre el archivo .json que descargaste "
                "de Google Cloud con un editor de texto y pega su contenido "
                "TAL CUAL, sin reformatear.")
    if "ssl" in minusculas or "network" in minusculas or "timed out" in minusculas:
        return "No se pudo alcanzar a Google. Comprueba la conexion a internet."
    return f"No se pudo conectar: {texto[:220]}"


def conectar(contenido_json: str, carpeta_pegada: str) -> dict:
    """
    Guarda la credencial, prueba la conexion y, si funciona, cambia la fuente.

    El orden importa y es deliberado: primero se valida lo pegado, luego se
    guarda, luego se PRUEBA contra Google, y solo si responde se cambia la
    fuente del corpus. Si algo falla por el camino, el sistema se queda como
    estaba, leyendo de la carpeta local.

    Args:
        contenido_json: El JSON de la cuenta de servicio, pegado.
        carpeta_pegada: Identificador de la carpeta, o su direccion completa.

    Returns:
        Diccionario con `conectado`, `motivo`, `cuenta`, `carpeta_id`,
        `archivos` y `carpetas`.
    """
    from core import ajustes

    fallo = {"conectado": False, "cuenta": "", "carpeta_id": "",
             "archivos": 0, "carpetas": 0}

    datos, error = revisar_json(contenido_json)
    if error:
        return {**fallo, "motivo": error}

    carpeta_id = extraer_identificador(carpeta_pegada)
    if not carpeta_id:
        return {**fallo, "motivo": (
            "No reconozco el identificador de la carpeta. Pega la direccion "
            "completa de la carpeta en Drive, o solo la parte que va despues "
            "de '/folders/'.")}

    # --- Se prueba en un TEMPORAL antes de tocar la credencial buena ------
    #
    # POR QUE NO SE ESCRIBE DIRECTAMENTE
    # ----------------------------------
    # La version anterior guardaba la credencial y DESPUES probaba. Si la
    # prueba fallaba, la credencial mala ya habia pisado a la que funcionaba y
    # el sistema se quedaba sin acceso a Drive por haber intentado conectar.
    # Se vio en la practica: una prueba automatica con datos inventados dejo
    # sin credencial una conexion que estaba funcionando.
    #
    # Primero se valida contra Google en un archivo aparte; solo si responde
    # se pone en su sitio.
    temporal = ruta_credencial() + ".probando"
    try:
        with open(temporal, "w", encoding="utf-8") as f:
            json.dump(datos, f, ensure_ascii=False, indent=2)
        if os.name != "nt":
            os.chmod(temporal, stat.S_IRUSR | stat.S_IWUSR)
    except OSError as exc:
        return {**fallo, "motivo": f"No se pudo guardar la credencial: {exc}"}

    prueba = probar_conexion(temporal, carpeta_id)
    if not prueba["conecta"]:
        # La credencial que hubiera antes se queda intacta.
        try:
            os.remove(temporal)
        except OSError:
            pass
        return {**fallo, "motivo": prueba["motivo"],
                "cuenta": prueba.get("cuenta", ""), "carpeta_id": carpeta_id}

    destino = ruta_credencial()
    os.replace(temporal, destino)

    # --- Solo ahora se cambia la fuente -------------------------------------
    ajustes.guardar({
        "FUENTE_CORPUS": "drive",
        "DRIVE_CARPETA_ID": carpeta_id,
        "GOOGLE_CREDENCIALES": destino,
    })

    return {
        "conectado": True,
        "motivo": prueba.get("motivo", ""),
        "cuenta": prueba.get("cuenta", ""),
        "carpeta_id": carpeta_id,
        "archivos": prueba.get("archivos", 0),
        "carpetas": prueba.get("carpetas", 0),
    }


def desconectar(borrar_credencial: bool = False) -> dict:
    """
    Vuelve a leer de la carpeta local.

    Lo ya descargado de Drive NO se borra: sigue en `corpus/` y el agente lo
    conserva. Desconectar es dejar de ir a buscar cosas nuevas, no olvidar lo
    aprendido.

    Args:
        borrar_credencial: Si ademas se elimina el archivo de la cuenta de
            servicio. Por defecto se conserva, para poder reconectar sin
            volver a pegarlo todo.

    Returns:
        Diccionario con `desconectado` y `credencial_borrada`.
    """
    from core import ajustes

    ajustes.guardar({"FUENTE_CORPUS": "local",
                     "DRIVE_CARPETA_ID": None,
                     "GOOGLE_CREDENCIALES": None})

    borrada = False
    if borrar_credencial:
        try:
            os.remove(ruta_credencial())
            borrada = True
        except OSError:
            borrada = False

    return {"desconectado": True, "credencial_borrada": borrada}


def estado() -> dict:
    """
    Describe la conexion con Drive para pintarla en la interfaz.

    Returns:
        Diccionario con `activo`, `hay_credencial`, `cuenta`, `proyecto` y
        `carpeta_id`. No devuelve nunca el contenido de la credencial.
    """
    from config import settings

    ruta = ruta_credencial()
    datos = {"activo": settings.fuente_corpus() == "drive",
             "hay_credencial": os.path.exists(ruta),
             "cuenta": "", "proyecto": "",
             "carpeta_id": settings.drive_carpeta_id() or ""}
    if datos["hay_credencial"]:
        try:
            with open(ruta, encoding="utf-8") as f:
                cred = json.load(f)
            datos["cuenta"] = cred.get("client_email", "")
            datos["proyecto"] = cred.get("project_id", "")
        except Exception:
            datos["hay_credencial"] = False
    return datos


#: Direccion a la que Google pide el token. Es fija y publica; va aqui porque
#: una credencial armada a partir de campos sueltos tiene que incluirla.
URI_TOKEN = "https://oauth2.googleapis.com/token"


def normalizar_clave_privada(clave: str) -> str:
    """
    Arregla los saltos de linea de una clave privada pegada a mano.

    POR QUE HACE FALTA
    ------------------
    Una clave privada son varias lineas entre `-----BEGIN PRIVATE KEY-----` y
    `-----END PRIVATE KEY-----`. Al copiarla de un chat, de una pagina o de una
    celda de hoja de calculo, los saltos de linea se pierden o se convierten en
    `\n` literales. Google entonces responde "Service account info was not in
    the expected form", que es cierto y no dice nada util.

    Esta funcion deshace los dos destrozos mas comunes:
      - `\n` escritos como dos caracteres, que vuelven a ser saltos de verdad.
      - Saltos de Windows (`\r\n`), que se normalizan.

    Lo que NO hace es inventar los saltos de una clave pegada en una sola linea
    sin separadores: eso no se puede reconstruir, y se avisa en vez de
    adivinar.

    Args:
        clave: La clave privada tal como se pego.

    Returns:
        La clave con sus saltos de linea de verdad.
    """
    texto = (clave or "").strip().strip('"').strip("'")
    texto = texto.replace("\n", "\n").replace("\r\n", "\n").replace("\r", "\n")
    return texto.strip()


def revisar_clave_privada(clave: str) -> str:
    """
    Comprueba que la clave privada tenga la forma que Google espera.

    Se valida ANTES de intentar conectar: un mensaje que dice exactamente que
    falta ahorra una vuelta entera de prueba y error.

    Args:
        clave: Clave ya normalizada.

    Returns:
        Texto con el problema, o cadena vacia si esta bien.
    """
    if not clave:
        return "No has pegado la clave privada."
    if "BEGIN PRIVATE KEY" not in clave:
        return ("A la clave le falta la linea "
                "'-----BEGIN PRIVATE KEY-----'. Copia el valor del campo "
                "`private_key` ENTERO, incluidas esas lineas de apertura y "
                "cierre.")
    if "END PRIVATE KEY" not in clave:
        return ("A la clave le falta la linea '-----END PRIVATE KEY-----'. "
                "Parece que se corto al copiar.")
    if clave.count("\n") < 3:
        return ("La clave esta en una sola linea. Tiene que conservar sus "
                "saltos de linea: copiala directamente del archivo .json que "
                "descargaste, sin pasarla por un editor que los quite.")
    return ""


def conectar_por_campos(correo: str, clave_privada: str, proyecto: str,
                        carpeta_pegada: str) -> dict:
    """
    Conecta con Drive a partir de los campos sueltos de la cuenta de servicio.

    Es la misma conexion que `conectar()`, pero sin pedir que se pegue el JSON
    entero. Una cuenta de servicio tiene su "cliente" (el correo) y su
    "secreto" (la clave privada): con esos dos, mas el proyecto, se reconstruye
    la credencial.

    POR QUE NO SE PIDE UN `client_id` Y UN `client_secret` DE OAUTH
    ---------------------------------------------------------------
    Son de otro mecanismo. Un `client_id`/`client_secret` de OAuth sirve para
    pedirle permiso a una PERSONA, y esa persona tiene que aceptar en una
    ventana del navegador. Aqui quien lee Drive es el cron, a las dos de la
    madrugada, y no hay nadie para aceptar nada. Por eso se usa una cuenta de
    servicio, que se identifica sola.

    Args:
        correo: `client_email` de la cuenta de servicio.
        clave_privada: `private_key`, con sus saltos de linea.
        proyecto: `project_id`. Opcional: solo sirve para mostrarlo.
        carpeta_pegada: Identificador de la carpeta o su direccion.

    Returns:
        Lo mismo que `conectar()`.
    """
    fallo = {"conectado": False, "cuenta": "", "carpeta_id": "",
             "archivos": 0, "carpetas": 0}

    correo = (correo or "").strip()
    if not correo:
        return {**fallo, "motivo": "Falta el correo de la cuenta de servicio."}
    if "@" not in correo or not correo.endswith(".gserviceaccount.com"):
        return {**fallo, "motivo": (
            f"'{correo}' no parece el correo de una cuenta de servicio. "
            f"Tienen esta forma: "
            f"mi-agente@mi-proyecto.iam.gserviceaccount.com")}

    clave = normalizar_clave_privada(clave_privada)
    problema = revisar_clave_privada(clave)
    if problema:
        return {**fallo, "motivo": problema, "cuenta": correo}

    # Se arma el JSON que Google espera y se reutiliza el camino de siempre,
    # para que las dos formas de conectar hagan exactamente lo mismo a partir
    # de aqui.
    credencial = {
        "type": "service_account",
        "project_id": (proyecto or "").strip() or correo.split("@")[-1]
                      .replace(".iam.gserviceaccount.com", ""),
        "private_key": clave,
        "client_email": correo,
        "token_uri": URI_TOKEN,
    }
    return conectar(json.dumps(credencial), carpeta_pegada)


# ==============================================================================
# CONEXION CON UN CLIENTE DE OAUTH (ID + SECRETO)
# ==============================================================================
# POR QUE ESTA FORMA, SI YA HABIA OTRA
# ------------------------------------
# Una cuenta de servicio se configura con un ARCHIVO, y no hay manera de
# reducirla a dos campos de texto: su secreto es una clave RSA de varias
# lineas. Un cliente de OAuth si tiene exactamente dos datos cortos -- un ID y
# un secreto -- que se leen en la consola de Google y se escriben en un
# formulario.
#
# COMO FUNCIONA SIN QUE NADIE ESTE DELANTE A LAS 02:00
# ----------------------------------------------------
# OAuth pide permiso a una persona UNA VEZ. A cambio devuelve un "token de
# refresco", que no caduca mientras se use y permite pedir accesos nuevos sin
# volver a preguntar. La persona autoriza hoy, y el cron de las dos de la
# madrugada funciona desde entonces solo.
#
# El permiso que se pide es el mismo de siempre: `drive.readonly`. Solo
# lectura.

#: Donde Google pregunta a la persona si autoriza.
URL_CONSENTIMIENTO = "https://accounts.google.com/o/oauth2/v2/auth"


def url_de_autorizacion(client_id: str, redireccion: str,
                        llave: str = "") -> str:
    """
    Arma el enlace que lleva a la pantalla de permiso de Google.

    Args:
        client_id: ID del cliente de OAuth.
        redireccion: URI de redireccion, que debe estar dada de alta en el
            cliente de Google Cloud EXACTAMENTE igual.
        llave: Identificador de un solo uso que Google devuelve en `state`.
            Es lo que permite reconocer la vuelta y recuperar con que cliente
            y para que carpeta se habia salido.

    Returns:
        La direccion completa a la que hay que ir.
    """
    from urllib.parse import urlencode

    from core.fuentes import FuenteDrive

    parametros = {
        "client_id": (client_id or "").strip(),
        "redirect_uri": redireccion,
        "response_type": "code",
        "scope": FuenteDrive.ALCANCE,
        # `offline` es lo que hace que Google entregue el token de refresco.
        # Sin esto el permiso dura una hora y el cron dejaria de funcionar esa
        # misma noche.
        "access_type": "offline",
        # Google solo devuelve el token de refresco la PRIMERA vez que alguien
        # autoriza una aplicacion. Si ya la habia autorizado antes, no lo
        # vuelve a mandar y la conexion quedaria a medias sin decir por que.
        # `prompt=consent` fuerza a que lo pregunte y lo devuelva siempre.
        "prompt": "consent",
        "include_granted_scopes": "true",
    }
    if llave:
        parametros["state"] = llave
    return URL_CONSENTIMIENTO + "?" + urlencode(parametros)


def revisar_cliente(client_id: str, client_secret: str) -> str:
    """
    Comprueba que el ID y el secreto tengan la forma que da Google.

    Se valida antes de mandar a nadie a la pantalla de permiso: descubrir alli
    que el ID estaba mal obliga a volver, y el error que muestra Google en esa
    pagina no explica cual de los dos datos falla.

    Args:
        client_id: ID del cliente.
        client_secret: Secreto del cliente.

    Returns:
        Texto con el problema, o cadena vacia si estan bien.
    """
    cid = (client_id or "").strip()
    sec = (client_secret or "").strip()

    if not cid:
        return "Falta el ID de cliente."
    if not cid.endswith(".apps.googleusercontent.com"):
        return ("El ID de cliente tiene que acabar en "
                "'.apps.googleusercontent.com'. Copialo del campo "
                "'ID de cliente' de la consola de Google.")
    if not sec:
        return "Falta el secreto del cliente."
    if sec.startswith("*"):
        return ("Has copiado el secreto tal como lo OCULTA la consola "
                "(****hGYQ). Pulsa el icono de copiar que hay a su derecha "
                "para obtener el valor completo.")
    if len(sec) < 12:
        return ("El secreto parece incompleto (" + str(len(sec)) +
                " caracteres). Comprueba que lo copiaste entero.")
    return ""


def canjear_codigo(client_id: str, client_secret: str, codigo: str,
                   redireccion: str) -> dict:
    """
    Cambia el codigo de un solo uso por un permiso permanente.

    Google devuelve el codigo en la direccion a la que redirige despues de que
    la persona acepte. Ese codigo vale una vez y caduca en minutos; aqui se
    cambia por el token de refresco, que es el que se guarda.

    Args:
        client_id: ID del cliente.
        client_secret: Secreto del cliente.
        codigo: El valor del parametro `code` de la redireccion.
        redireccion: La MISMA URI que se uso al pedir el permiso. Si no
            coincide letra por letra, Google lo rechaza.

    Returns:
        Diccionario con `ok`, `refresh_token` y `motivo`.
    """
    import urllib.error
    import urllib.parse
    import urllib.request

    datos = urllib.parse.urlencode({
        "code": codigo,
        "client_id": (client_id or "").strip(),
        "client_secret": (client_secret or "").strip(),
        "redirect_uri": redireccion,
        "grant_type": "authorization_code",
    }).encode("utf-8")

    peticion = urllib.request.Request(
        URI_TOKEN, data=datos,
        headers={"Content-Type": "application/x-www-form-urlencoded"})
    try:
        with urllib.request.urlopen(peticion, timeout=40) as r:
            respuesta = json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        try:
            detalle = json.loads(exc.read().decode("utf-8"))
        except Exception:
            detalle = {}
        return {"ok": False, "refresh_token": "",
                "motivo": _traducir_oauth(detalle, redireccion)}
    except Exception as exc:
        return {"ok": False, "refresh_token": "",
                "motivo": "No se pudo hablar con Google: " + str(exc)[:160]}

    refresco = respuesta.get("refresh_token", "")
    if not refresco:
        return {"ok": False, "refresh_token": "", "motivo": (
            "Google autorizo el acceso pero no devolvio el token de refresco. "
            "Pasa cuando ya habias autorizado esta aplicacion antes. Entra en "
            "https://myaccount.google.com/permissions, quita el permiso a "
            "esta aplicacion y vuelve a intentarlo.")}
    return {"ok": True, "refresh_token": refresco, "motivo": ""}


def _traducir_oauth(detalle: dict, redireccion: str) -> str:
    """
    Convierte el error de OAuth en algo que diga que hacer.

    Args:
        detalle: Cuerpo del error que devolvio Google.
        redireccion: La URI que se uso, para poder nombrarla.

    Returns:
        Explicacion con el siguiente paso.
    """
    codigo = (detalle.get("error") or "").lower()
    descripcion = detalle.get("error_description", "")

    if codigo == "redirect_uri_mismatch":
        return ("La direccion de retorno no coincide. En tu cliente de Google "
                "Cloud tiene que estar dada de alta EXACTAMENTE asi, con la "
                "barra final incluida:\n\n" + redireccion)
    if codigo == "invalid_client":
        return ("Google no reconoce el ID o el secreto del cliente. "
                "Comprueba que copiaste el secreto COMPLETO: la consola lo "
                "muestra oculto (****hGYQ) y hay que usar el boton de copiar.")
    if codigo == "invalid_grant":
        return ("El codigo de autorizacion ya caduco o ya se habia usado. "
                "Vuelve a pulsar 'Autorizar en Google'.")
    if codigo == "access_denied":
        return "Se rechazo el permiso en la pantalla de Google."
    return ("Google devolvio '" + (codigo or "un error") + "'. " +
            descripcion)[:300]


def conectar_con_oauth(client_id: str, client_secret: str,
                       refresh_token: str, carpeta_pegada: str) -> dict:
    """
    Guarda el permiso de OAuth, prueba la conexion y cambia la fuente.

    Igual que las demas formas de conectar: primero se PRUEBA contra Google y
    solo si responde se cambia la fuente del corpus. Si falla, el sistema
    sigue leyendo de la carpeta local.

    Args:
        client_id: ID del cliente de OAuth.
        client_secret: Secreto del cliente.
        refresh_token: Token de refresco obtenido al autorizar.
        carpeta_pegada: Identificador de la carpeta o su direccion.

    Returns:
        Lo mismo que `conectar()`.
    """
    from core import ajustes

    fallo = {"conectado": False, "cuenta": "", "carpeta_id": "",
             "archivos": 0, "carpetas": 0}

    carpeta_id = extraer_identificador(carpeta_pegada)
    if not carpeta_id:
        return dict(fallo, motivo=(
            "No reconozco el identificador de la carpeta. Pega la direccion "
            "completa de la carpeta en Drive."))

    credencial = {
        "type": "authorized_user",
        "client_id": (client_id or "").strip(),
        "client_secret": (client_secret or "").strip(),
        "refresh_token": refresh_token,
        "token_uri": URI_TOKEN,
    }

    # Igual que en `conectar()`: se prueba en un temporal para no pisar una
    # credencial que funcione si la nueva resulta no servir.
    temporal = ruta_credencial() + ".probando"
    try:
        with open(temporal, "w", encoding="utf-8") as f:
            json.dump(credencial, f, ensure_ascii=False, indent=2)
        if os.name != "nt":
            os.chmod(temporal, stat.S_IRUSR | stat.S_IWUSR)
    except OSError as exc:
        return dict(fallo, motivo="No se pudo guardar el permiso: " + str(exc))

    prueba = probar_conexion(temporal, carpeta_id)
    if not prueba["conecta"]:
        try:
            os.remove(temporal)
        except OSError:
            pass
        return dict(fallo, motivo=prueba["motivo"], carpeta_id=carpeta_id)

    destino = ruta_credencial()
    os.replace(temporal, destino)

    ajustes.guardar({
        "FUENTE_CORPUS": "drive",
        "DRIVE_CARPETA_ID": carpeta_id,
        "GOOGLE_CREDENCIALES": destino,
    })
    return {
        "conectado": True,
        "motivo": prueba.get("motivo", ""),
        "cuenta": prueba.get("cuenta", ""),
        "carpeta_id": carpeta_id,
        "archivos": prueba.get("archivos", 0),
        "carpetas": prueba.get("carpetas", 0),
    }


# ==============================================================================
# LO QUE QUEDA A MEDIAS MIENTRAS SE AUTORIZA EN GOOGLE
# ==============================================================================
# POR QUE NO VALE `st.session_state`
# ----------------------------------
# Autorizar implica SALIR de la aplicacion: el navegador se va a Google y
# vuelve. En esa vuelta, Streamlit puede abrir una sesion nueva -- otro
# websocket -- y `st.session_state` nace vacio. Se vio en la practica: Google
# devolvia el codigo en la direccion y la pagina lo ignoraba, porque ya no
# sabia de que cliente ni de que carpeta era.
#
# Asi que lo pendiente se guarda en disco, con una llave de un solo uso que
# viaja a Google en el parametro `state` y vuelve con el codigo. Al volver, esa
# llave es lo que permite recuperar el resto.
#
# La llave sirve ademas para lo que fue inventada: si lo que vuelve no trae una
# llave que nosotros emitimos, no es una vuelta nuestra y se ignora.

#: Cuanto se conserva una autorizacion a medias. Pasado ese tiempo se
#: descarta: una espera de hace horas es basura, y guardar un secreto mas
#: tiempo del necesario no tiene ningun sentido.
MINUTOS_DE_ESPERA = 20


def _ruta_pendientes() -> str:
    """
    Archivo donde esperan las autorizaciones a medias.

    Returns:
        Ruta absoluta de `data/oauth_pendiente.json`.
    """
    from config import settings

    carpeta = settings.ruta_proyecto("data")
    os.makedirs(carpeta, exist_ok=True)
    return os.path.join(carpeta, "oauth_pendiente.json")


def _leer_pendientes() -> dict:
    """
    Lee las autorizaciones a medias, descartando las caducadas.

    Returns:
        Diccionario {llave: datos}. Vacio si no hay nada o el archivo no se
        puede leer.
    """
    import time

    try:
        with open(_ruta_pendientes(), encoding="utf-8") as f:
            datos = json.load(f)
    except Exception:
        return {}
    if not isinstance(datos, dict):
        return {}
    limite = time.time() - MINUTOS_DE_ESPERA * 60
    return {k: v for k, v in datos.items()
            if isinstance(v, dict) and v.get("creado", 0) > limite}


def _guardar_pendientes(datos: dict) -> None:
    """
    Escribe las autorizaciones a medias.

    Args:
        datos: Diccionario {llave: datos}.
    """
    destino = _ruta_pendientes()
    temporal = destino + ".parcial"
    with open(temporal, "w", encoding="utf-8") as f:
        json.dump(datos, f, ensure_ascii=False)
    if os.name != "nt":
        os.chmod(temporal, stat.S_IRUSR | stat.S_IWUSR)
    os.replace(temporal, destino)


def anotar_pendiente(client_id: str, client_secret: str, carpeta: str) -> str:
    """
    Guarda una autorizacion que esta a punto de salir hacia Google.

    Args:
        client_id: ID del cliente de OAuth.
        client_secret: Secreto del cliente.
        carpeta: Carpeta de Drive, tal como se pego.

    Returns:
        La llave de un solo uso que hay que mandarle a Google en `state`.
    """
    import secrets
    import time

    llave = secrets.token_urlsafe(24)
    pendientes = _leer_pendientes()
    pendientes[llave] = {
        "client_id": (client_id or "").strip(),
        "client_secret": (client_secret or "").strip(),
        "carpeta": (carpeta or "").strip(),
        "creado": time.time(),
    }
    _guardar_pendientes(pendientes)
    return llave


def recoger_pendiente(llave: str) -> dict:
    """
    Recupera una autorizacion a medias y la borra.

    Se borra al recogerla: la llave vale una sola vez, igual que el codigo que
    acompana. Dejarla viva permitiria repetir la operacion.

    Args:
        llave: La que volvio de Google en `state`.

    Returns:
        Los datos guardados, o diccionario vacio si la llave no existe o
        caduco.
    """
    pendientes = _leer_pendientes()
    datos = pendientes.pop((llave or "").strip(), {})
    if datos:
        _guardar_pendientes(pendientes)
        datos.pop("creado", None)
    return datos
