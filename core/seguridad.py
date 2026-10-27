# -*- coding: utf-8 -*-
"""
Revision de seguridad de las credenciales del proyecto.
===============================================================================

QUE REVISA
----------
Las dos llaves que el sistema maneja, y que hasta ahora se usaban sin que nadie
comprobara nada:

  1. La API Key de Gemini. Es una llave de pago: quien la tenga gasta tu cuota.
  2. El archivo de la cuenta de servicio de Google. Es una contrasena: quien lo
     tenga lee todo lo que esa cuenta tenga compartido.

QUE NO HACE, Y POR QUE
----------------------
No imprime ninguna credencial, ni entera ni "casi entera". Un diagnostico que
muestra la clave para "ayudar a comprobarla" la deja escrita en la pantalla, en
las capturas y en los registros. Aqui solo se dicen cosas SOBRE la credencial
-- su longitud, su prefijo, si esta en un sitio peligroso -- nunca la
credencial.

Tampoco bloquea nada. Avisa. Decidir es de quien lee.

===============================================================================
"""

import os
import re
import stat

#: Prefijos con los que Google emite sus claves. No son secretos ni
#: identifican a nadie: son la parte publica del formato.
#:
#: HAY DOS, Y LA PRIMERA VERSION DE ESTE ARCHIVO SOLO CONOCIA UNO
#: -------------------------------------------------------------
#: Se escribio dando por hecho que todas empiezan por "AIza" y miden 39
#: caracteres. Al revisarlo contra la clave real del proyecto, salto un aviso
#: de "esto no parece una clave de Gemini"... sobre una clave que funciona
#: perfectamente: empieza por "AQ." y mide 53. Google emite los dos formatos.
#:
#: Un aviso de seguridad que grita con algo correcto es peor que no tener
#: aviso: ensena a ignorarlos, y el dia que salte uno de verdad tampoco se
#: mirara. Por eso aqui se admiten los dos y la comprobacion de longitud se
#: hace POR FORMATO.
FORMATOS_GEMINI = {
    "AIza": 39,     # formato clasico
    "AQ.": 53,      # formato nuevo de AI Studio
}

#: Margen de holgura en la longitud. Google puede cambiarla sin avisar, asi
#: que se avisa solo cuando la diferencia es grande.
HOLGURA_LARGO = 8

#: Valores que la gente deja puestos de ejemplo y luego olvida.
CLAVES_DE_RELLENO = {
    "", "tu_api_key", "tu-api-key", "cambiame", "changeme", "xxx",
    "your_api_key_here", "pon_aqui_tu_clave", "none", "null",
}

def _alcance_esperado() -> str:
    """
    La unica autorizacion que el proyecto debe pedirle a Drive.

    Se lee de `FuenteDrive.ALCANCE` en vez de repetir la cadena aqui. Una
    revision de seguridad que guarda su propia copia del valor correcto puede
    acabar comparando contra algo que el codigo ya no usa, y dar por bueno un
    permiso que nadie reviso.

    Returns:
        La URL del alcance.
    """
    try:
        from core.fuentes import FuenteDrive
        return FuenteDrive.ALCANCE
    except Exception:
        return "https://www.googleapis.com/auth/drive.readonly"


#: Copia para los mensajes. El valor que MANDA es `_alcance_esperado()`.
ALCANCE_ESPERADO = _alcance_esperado()

#: Autorizaciones que permitirian ESCRIBIR o BORRAR en Drive. Si aparece
#: alguna, es un aviso serio: el agente podria modificar la carpeta compartida.
ALCANCES_PELIGROSOS = (
    "https://www.googleapis.com/auth/drive",
    "https://www.googleapis.com/auth/drive.file",
    "https://www.googleapis.com/auth/drive.appdata",
)


def _huella(valor: str) -> str:
    """
    Describe una credencial sin revelarla.

    Args:
        valor: La credencial.

    Returns:
        Algo como `AIza…(39 caracteres)`. Nunca el contenido.
    """
    if not valor:
        return "(vacia)"
    valor = str(valor)
    cabeza = valor[:4] if len(valor) > 8 else ""
    return f"{cabeza}…({len(valor)} caracteres)"


def revisar_api_key(clave: str = None) -> dict:
    """
    Revisa la API Key sin llamar al servicio ni mostrarla.

    Es una revision de FORMA y de EXPOSICION. Que la clave funcione se
    comprueba aparte, con `agent.chatbot.validar_api_key`, que si llama al
    servicio. Las dos cosas son distintas: una clave puede funcionar
    perfectamente y estar a la vez subida a un repositorio.

    Args:
        clave: Clave a revisar. Si no se indica, la del entorno.

    Returns:
        Diccionario con `hay_clave`, `huella`, `avisos` (lista de textos) y
        `grave` (True si algun aviso merece accion inmediata).
    """
    from config import settings

    clave = clave if clave is not None else settings.api_key_gemini()
    clave = (clave or "").strip()
    avisos = []
    grave = False

    if clave.lower() in CLAVES_DE_RELLENO:
        return {"hay_clave": False, "huella": "(sin clave)",
                "avisos": ["No hay ninguna clave configurada. El agente "
                           "respondera en modo simulador."],
                "grave": False}

    formato = next((p for p in FORMATOS_GEMINI if clave.startswith(p)), None)
    if formato is None:
        avisos.append(
            f"La clave no empieza por ninguno de los prefijos que usa Google "
            f"({', '.join(FORMATOS_GEMINI)}). Comprueba que no has pegado una "
            f"clave de otro servicio.")
    elif abs(len(clave) - FORMATOS_GEMINI[formato]) > HOLGURA_LARGO:
        avisos.append(
            f"Longitud inusual para una clave '{formato}': {len(clave)} "
            f"caracteres frente a los {FORMATOS_GEMINI[formato]} habituales. "
            f"Puede haberse cortado al copiar.")

    if clave != clave.strip() or " " in clave:
        avisos.append("La clave tiene espacios. Casi siempre es un error de "
                      "copiado y hace que falle sin decir por que.")
        grave = True

    # --- Esta expuesta donde no debe? ------------------------------------
    avisos_exposicion, grave_exposicion = _revisar_exposicion(clave)
    avisos.extend(avisos_exposicion)
    grave = grave or grave_exposicion

    return {"hay_clave": True, "huella": _huella(clave),
            "avisos": avisos, "grave": grave}


def _revisar_exposicion(clave: str):
    """
    Busca la clave en sitios desde donde podria escaparse.

    Args:
        clave: La credencial a buscar.

    Returns:
        Tupla `(avisos, grave)`.
    """
    from config import settings

    avisos, grave = [], False
    raiz = settings.ruta_proyecto()

    # 1. El .env tiene que estar ignorado por git. Es LA comprobacion que
    #    importa: un .env versionado publica la clave en cuanto se sube.
    env = os.path.join(raiz, ".env")
    if os.path.exists(env):
        if not _esta_ignorado_por_git(env):
            avisos.append(
                "El archivo .env NO esta ignorado por git. Si subes el "
                "repositorio, la clave se publica. Anadelo a .gitignore YA.")
            grave = True
        if _es_legible_por_todos(env):
            avisos.append(
                "El archivo .env es legible por cualquier usuario del equipo.")

    # 2. La clave no debe aparecer en archivos que SI se versionan.
    sospechosos = []
    for nombre in (".env.example", "docker-compose.yml", "README.md",
                   "ARQUITECTURA.md"):
        ruta = os.path.join(raiz, nombre)
        if not os.path.exists(ruta):
            continue
        try:
            with open(ruta, encoding="utf-8", errors="ignore") as f:
                if clave and clave in f.read():
                    sospechosos.append(nombre)
        except OSError:
            continue
    if sospechosos:
        avisos.append(
            f"La clave aparece escrita dentro de: {', '.join(sospechosos)}. "
            f"Esos archivos SI se versionan. Quitala y rota la clave.")
        grave = True

    return avisos, grave


def _esta_ignorado_por_git(ruta: str) -> bool:
    """
    Pregunta a git si un archivo esta ignorado.

    Se le pregunta a git en vez de leer el .gitignore a mano porque las reglas
    de ignorado tienen negaciones, orden y archivos globales: reimplementarlas
    daria respuestas equivocadas justo en los casos raros.

    Args:
        ruta: Archivo a comprobar.

    Returns:
        True si git lo ignora. True tambien si no hay repositorio, porque
        entonces no hay nada que publicar.
    """
    import subprocess

    try:
        proceso = subprocess.run(
            ["git", "check-ignore", "-q", ruta],
            cwd=os.path.dirname(ruta) or ".",
            capture_output=True, timeout=15)
        if proceso.returncode == 0:
            return True
        # 1 = no ignorado. 128 = no es un repositorio.
        return proceso.returncode == 128
    except Exception:
        return True


def _es_legible_por_todos(ruta: str) -> bool:
    """
    Dice si un archivo tiene permiso de lectura para cualquiera.

    En Windows los permisos POSIX no significan lo mismo, asi que alli no se
    avisa: seria un aviso falso en todos los casos.

    Args:
        ruta: Archivo a comprobar.

    Returns:
        True solo si el sistema usa permisos POSIX y el archivo es legible
        por otros.
    """
    if os.name == "nt":
        return False
    try:
        return bool(os.stat(ruta).st_mode & stat.S_IROTH)
    except OSError:
        return False


def revisar_drive() -> dict:
    """
    Revisa el acceso a Google Drive sin conectarse.

    Mira el archivo de la cuenta de servicio y la configuracion, y comprueba
    lo que de verdad importa: que la autorizacion sea de SOLO LECTURA y que la
    llave no este donde pueda escaparse.

    Returns:
        Diccionario con `activo`, `estado`, `cuenta`, `proyecto`, `alcance`,
        `avisos` y `grave`. `cuenta` es el correo de la cuenta de servicio, que
        no es secreto: hay que compartirselo a quien administre la carpeta.
    """
    import json

    from config import settings

    fuente = settings.fuente_corpus()
    ruta = (settings.google_credenciales() or "").strip()
    carpeta = (settings.drive_carpeta_id() or "").strip()

    datos = {"activo": fuente == "drive", "estado": "", "cuenta": "",
             "proyecto": "", "alcance": ALCANCE_ESPERADO,
             "avisos": [], "grave": False}

    if not ruta and not carpeta:
        datos["estado"] = "sin configurar"
        if datos["activo"]:
            datos["avisos"].append(
                "La fuente es 'drive' pero no hay ni carpeta ni credenciales.")
            datos["grave"] = True
        return datos

    if not carpeta:
        datos["avisos"].append("Falta DRIVE_CARPETA_ID.")
    if not ruta:
        datos["avisos"].append("Falta GOOGLE_CREDENCIALES.")
        datos["estado"] = "incompleto"
        return datos

    if not os.path.exists(ruta):
        datos["estado"] = "credenciales no encontradas"
        datos["avisos"].append(
            f"No existe el archivo indicado en GOOGLE_CREDENCIALES. "
            f"Recuerda que dentro de Docker la ruta es la del CONTENEDOR "
            f"(/app/config/...), no la de Windows.")
        datos["grave"] = datos["activo"]
        return datos

    # --- El archivo existe: se mira por dentro, sin sacar la llave -------
    try:
        with open(ruta, encoding="utf-8") as f:
            cred = json.load(f)
    except Exception as exc:
        datos["estado"] = "credenciales ilegibles"
        datos["avisos"].append(f"No se pudo leer el JSON: {str(exc)[:120]}")
        datos["grave"] = True
        return datos

    datos["cuenta"] = cred.get("client_email", "")
    datos["proyecto"] = cred.get("project_id", "")
    datos["estado"] = "configurado"

    # Hay DOS formas validas de entrar, y cada una se revisa a su manera.
    # Avisar de que "no es una cuenta de servicio" ante una credencial de
    # OAuth perfectamente valida seria una alarma falsa, y una alarma falsa
    # ensena a ignorar las de verdad.
    tipo = cred.get("type")
    if tipo == "authorized_user":
        datos["cuenta"] = cred.get("client_id", "")[:46]
        if not cred.get("refresh_token"):
            datos["avisos"].append(
                "Al permiso de OAuth le falta el token de refresco. Sin el, "
                "el acceso caduca en una hora y el cron de las 02:00 fallaria. "
                "Vuelve a autorizar desde la pestana.")
            datos["grave"] = True
        if not cred.get("client_secret"):
            datos["avisos"].append("Al permiso de OAuth le falta el secreto "
                                   "del cliente.")
            datos["grave"] = True
    elif tipo == "service_account":
        if "private_key" not in cred:
            datos["avisos"].append("Al JSON le falta la clave privada.")
            datos["grave"] = True
    else:
        datos["avisos"].append(
            f"La credencial es de tipo {tipo!r}, que no se reconoce. "
            f"Vuelve a conectar Drive desde la pestana.")
        datos["grave"] = True

    # --- Donde esta la llave ----------------------------------------------
    if not _esta_ignorado_por_git(ruta):
        datos["avisos"].append(
            "El archivo de credenciales NO esta ignorado por git. Es una "
            "contrasena: si subes el repositorio, la publicas.")
        datos["grave"] = True
    if _es_legible_por_todos(ruta):
        datos["avisos"].append(
            "El archivo de credenciales es legible por cualquier usuario del "
            "equipo.")

    # --- La autorizacion que pide el codigo --------------------------------
    alcances = _alcances_en_el_codigo()
    peligrosos = [a for a in alcances if a in ALCANCES_PELIGROSOS]
    if peligrosos:
        datos["avisos"].append(
            f"El codigo pide permisos de ESCRITURA en Drive: "
            f"{', '.join(peligrosos)}. Con eso el agente podria modificar o "
            f"borrar la carpeta compartida.")
        datos["grave"] = True
    elif ALCANCE_ESPERADO not in alcances:
        datos["avisos"].append(
            "No se encontro el permiso de solo lectura en el codigo. "
            "Comprueba core/fuentes.py.")
    datos["alcance"] = ", ".join(alcances) or "(no declarado)"

    return datos


def _alcances_en_el_codigo() -> list:
    """
    Lee que autorizaciones de Google pide el codigo.

    Se leen del archivo en vez de confiar en una constante: lo que importa es
    lo que el programa va a pedir de verdad, no lo que la documentacion diga
    que pide.

    Returns:
        Lista de URLs de alcance encontradas en `core/fuentes.py`.
    """
    from config import settings

    ruta = os.path.join(settings.ruta_proyecto(), "core", "fuentes.py")
    try:
        with open(ruta, encoding="utf-8") as f:
            contenido = f.read()
    except OSError:
        return []
    return sorted(set(re.findall(
        r"https://www\.googleapis\.com/auth/[A-Za-z0-9._\-]+", contenido)))


def revision_completa() -> dict:
    """
    Revisa las dos credenciales de una vez.

    Returns:
        Diccionario con `gemini`, `drive` y `grave` (True si cualquiera de las
        dos tiene un aviso serio).
    """
    gemini = revisar_api_key()
    drive = revisar_drive()
    return {"gemini": gemini, "drive": drive,
            "grave": bool(gemini.get("grave") or drive.get("grave"))}
