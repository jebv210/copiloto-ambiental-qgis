"""
De donde salen los documentos que el agente estudia.
===============================================================================

POR QUE EXISTE ESTE MODULO
--------------------------
Hoy los documentos estan en la carpeta `corpus/` del proyecto. Manana estaran
en una carpeta compartida de Google Drive, en cuanto haya acceso. El resto del
sistema (troceado, embeddings, Qdrant, busqueda) no deberia enterarse de ese
cambio.

Este modulo es la unica pieza que sabe DE DONDE vienen los archivos. Todo lo
demas recibe la misma lista de documentos, venga de donde venga.

COMO SE CAMBIA DE FUENTE
------------------------
Una variable en el `.env`:

    FUENTE_CORPUS=local     ->  carpeta corpus/ del proyecto   (hoy)
    FUENTE_CORPUS=drive     ->  carpeta compartida de Drive    (cuando haya acceso)

No hace falta tocar codigo en ningun otro archivo.

EL CONTRATO
-----------
Una fuente devuelve una lista de diccionarios, uno por documento:

    {
        "nombre"    : nombre del archivo, sirve para citarlo
        "categoria" : coleccion tematica a la que pertenece
        "ruta"      : ruta LOCAL donde se puede leer el archivo
        "huella"    : identificador del contenido; si cambia, hay que reindexar
    }

La clave es `huella`. Es lo que permite reindexar solo lo que cambio en vez de
reprocesar los 134 documentos cada noche. En local se calcula del tamano y la
fecha de modificacion; en Drive sera el `md5Checksum` que da su API.
"""

import hashlib
import os
import threading
import time

from config import settings
from core import corpus


#: Categoria de lo que cuelga directamente de la carpeta, sin subcarpeta.
#:
#: Antes se llamaba "Sin categoria", que se leia como un defecto del
#: archivo. No lo es: estar en la raiz es un sitio tan valido como
#: cualquier subcarpeta.
RAIZ = "Raiz de la carpeta"


# ==============================================================================
# FUENTE DE DRIVE: la unica que hay
# ==============================================================================

#: Cuantas descargas lleva este proceso. Solo sirve para dar a cada una un
#: nombre temporal propio; ver `FuenteDrive._nombre_temporal`.
#: Archivo que declara que hay un estudio bajando documentos ahora mismo.
#:
#: POR QUE HACE FALTA UN CARTEL EN LA PUERTA
#: -----------------------------------------
#: La carpeta de descargas tiene un nombre fijo, `data/copiloto_drive`, y
#: cualquier proceso de la maquina puede borrarla. Mientras un estudio la
#: esta llenando, eso le cuesta el trabajo entero: los documentos siguen
#: apareciendo en la lista, pero ya no estan en el disco cuando se van a
#: leer, y el informe acaba diciendo "354 de 367 no llegaron a bajarse".
#:
#: Paso dos veces el 9 de noviembre de 2026, y las dos veces lo provoco una
#: bateria de pruebas corriendo a la vez que el cron. La leccion no es "no
#: correr las pruebas": es que la carpeta tiene que saber defenderse, porque
#: manana sera otra cosa -- un script, una limpieza de disco, una persona.
#:
#: El cartel lleva el PID de quien estudia. Solo ese proceso puede retirar
#: lo bajado; a cualquier otro se le dice que no y se explica por que.
MARCA_ESTUDIO = "estudio_en_marcha.json"

#: A partir de cuantas horas un cartel se considera olvidado. Si el proceso
#: que estudiaba murio sin retirarlo, la carpeta quedaria blindada para
#: siempre y nadie podria limpiarla nunca.
HORAS_PARA_OLVIDAR_LA_MARCA = 12


def _ruta_de_la_marca() -> str:
    """
    Donde vive el cartel de "hay un estudio en marcha".

    Va en `data/`, FUERA de la carpeta de descargas: si viviera dentro, el
    primer borrado se lo llevaria por delante y el cartel no protegeria de
    lo unico de lo que tiene que proteger.

    Returns:
        Ruta absoluta del archivo.
    """
    return settings.ruta_proyecto("data", MARCA_ESTUDIO)


def empezar_estudio() -> None:
    """
    Cuelga el cartel: este proceso esta bajando documentos.

    No levanta si no puede escribirlo. Un estudio no se tira por no poder
    poner un aviso; como mucho se queda sin proteccion, que es exactamente
    como estaba antes.
    """
    import json as _json

    try:
        os.makedirs(os.path.dirname(_ruta_de_la_marca()), exist_ok=True)
        with open(_ruta_de_la_marca(), "w", encoding="utf-8") as fh:
            _json.dump({"pid": os.getpid(), "desde": time.time()}, fh)
    except OSError:
        pass


def terminar_estudio() -> None:
    """
    Retira el cartel. Se llama SIEMPRE, tambien cuando el estudio se cae.
    """
    try:
        os.remove(_ruta_de_la_marca())
    except OSError:
        pass


def estudio_de_otro() -> str:
    """
    Dice si hay un estudio de OTRO proceso bajando documentos ahora mismo.

    Returns:
        El motivo por el que no se puede tocar la carpeta, o cadena vacia
        si no hay ninguno. Un cartel de este mismo proceso devuelve vacio:
        la limpieza del propio estudio es justo la que SI tiene que pasar.
    """
    import json as _json

    try:
        with open(_ruta_de_la_marca(), encoding="utf-8") as fh:
            marca = _json.load(fh)
    except (OSError, ValueError):
        return ""

    if marca.get("pid") == os.getpid():
        return ""

    horas = (time.time() - float(marca.get("desde") or 0)) / 3600.0
    if horas > HORAS_PARA_OLVIDAR_LA_MARCA:
        # El proceso que estudiaba ya no existe. Se retira el cartel y se
        # deja pasar: blindar la carpeta para siempre seria peor.
        terminar_estudio()
        return ""

    return (f"hay un estudio en marcha desde hace {horas * 60:.0f} minutos "
            f"(proceso {marca.get('pid')}) que esta usando esta carpeta")


_CUANTAS_DESCARGAS = 0

#: Protege el contador de arriba. Son dos lineas de trabajo, pero sin el
#: candado dos hilos pueden leer el mismo numero y volver a chocar.
_CERROJO_TEMPORAL = threading.Lock()


class FuenteDrive:
    """
    Lee los documentos de una carpeta compartida de Google Drive.

    ESTA IMPLEMENTADA Y SIN ESTRENAR: el codigo esta completo, pero hasta que
    no haya una carpeta compartida no se ha podido probar contra Drive de
    verdad. `disponible()` dice que falta por si acaso.

    QUE HACE FALTA PARA ENCENDERLA (ver documentos/Credenciales_Drive.md)
    --------------------------------------------------------------------
      1. Una cuenta de servicio de Google Cloud con permiso de SOLO LECTURA
         sobre la carpeta, y su archivo de credenciales en `GOOGLE_CREDENCIALES`.
      2. El identificador de la carpeta en `DRIVE_CARPETA_ID`. Se saca de la
         direccion de la carpeta en el navegador.
      3. Compartir la carpeta con el correo de la cuenta de servicio. Tener el
         archivo de credenciales no da acceso por si solo.

    LOS NATIVOS DE GOOGLE SI ENTRAN
    -------------------------------
    Los Documentos, Hojas de calculo y Presentaciones de Google se exportan al
    vuelo segun la tabla `EXPORTACIONES`, porque no son archivos con bytes
    propios y `get_media` falla con ellos.

    Esto no es un detalle: es el unico puente OFICIAL que existe entre Gemini
    Notebook y este proyecto. Notebook no tiene API publica, pero su boton
    "Exportar a Documentos" deja la nota como Documento de Google en Drive. Si
    cae en la carpeta que vigila el proyecto, entra sola en la siguiente
    sincronizacion.

    LO QUE SIGUE SIN ENTRAR
    -----------------------
    Un formato sin lector (.pptx, .dwg, .shp suelto) no entra, pero YA NO
    desaparece: queda en `descartados` con su motivo, para que el agente pueda
    decir que existe en vez de responder "no tengo informacion".
    """

    nombre = "drive"

    #: Como se exporta cada tipo nativo de Google a un formato que si se sabe
    #: leer: `mimeType de Drive -> (mimeType de exportacion, extension)`.
    #:
    #: POR QUE HACE FALTA
    #: ------------------
    #: Un Documento de Google no es un archivo: es una fila en una base de
    #: datos de Google. No tiene bytes que bajar ni extension en el nombre, y
    #: `files().get_media()` -- que es como se baja todo lo demas -- devuelve
    #: un error con el. Hay que pedirle a Google que lo CONVIERTA primero,
    #: con `files().export_media()`.
    #:
    #: POR QUE IMPORTA EN ESTE PROYECTO
    #: --------------------------------
    #: Es el unico puente oficial que existe entre Gemini Notebook y este
    #: sistema. Notebook no tiene API publica, pero si tiene "Exportar a
    #: Documentos", y lo que sale de ahi es un Documento de Google guardado
    #: en Drive. Sin esta tabla, esas notas se veian en la carpeta y el
    #: proyecto las saltaba en silencio.
    #:
    #: Cada destino se elige por el lector que ya existe en `corpus`:
    #: Documentos a .docx (`_leer_docx`), Hojas a .xlsx (`_leer_excel`) y
    #: Presentaciones a .pdf (`_leer_pdf`, que conserva la paginacion para
    #: poder citar la diapositiva). No hay lector de .pptx.
    EXPORTACIONES = {
        "application/vnd.google-apps.document": (
            "application/vnd.openxmlformats-officedocument"
            ".wordprocessingml.document", ".docx"),
        "application/vnd.google-apps.spreadsheet": (
            "application/vnd.openxmlformats-officedocument"
            ".spreadsheetml.sheet", ".xlsx"),
        "application/vnd.google-apps.presentation": (
            "application/pdf", ".pdf"),
    }

    #: La UNICA autorizacion que este proyecto le pide a Google.
    #:
    #: Esta aqui, en una constante, y no escrita dentro del metodo que abre la
    #: conexion, porque hay tres sitios que necesitan saberla: quien se
    #: conecta, quien prueba la conexion desde la interfaz y quien revisa la
    #: seguridad. Con la cadena repetida en tres sitios, cambiarla en dos y
    #: olvidar el tercero daria un sistema que pide mas permiso del que dice
    #: pedir.
    #:
    #: `drive.readonly` es solo lectura: con ella el agente NO puede crear,
    #: modificar, mover ni borrar nada en Drive. Lo impide Google, no el
    #: codigo.
    ALCANCE = "https://www.googleapis.com/auth/drive.readonly"

    def __init__(self, carpeta_id: str = None, credenciales: str = None):
        """
        Args:
            carpeta_id: Identificador de la carpeta compartida.
            credenciales: Ruta al JSON de la cuenta de servicio.
        """
        self.carpeta_id = carpeta_id or settings.drive_carpeta_id()
        self.credenciales = credenciales or settings.google_credenciales()

    #: Carpeta donde se dejan los archivos que se bajan de Drive. El
    #: troceador trabaja sobre archivos locales, asi que hay que materializarlos.
    #: Tiene que estar declarada en `corpus.CARPETAS_DE_DESCARGA`, porque el
    #: inventario se salta ese nivel al calcular la categoria. Si no, todo lo
    #: bajado de Drive acabaria en una sola categoria con este nombre en vez
    #: de en la carpeta de Drive que le corresponde.
    #: Nombre de la carpeta temporal donde se materializan los archivos.
    #:
    #: NADA SE QUEDA EN EL EQUIPO
    #: --------------------------
    #: El troceador trabaja sobre archivos locales, asi que para leer un
    #: documento de Drive hay que bajarlo. Pero bajarlo no es conservarlo: se
    #: deja en una carpeta temporal del sistema y se borra en cuanto se
    #: termina de leer. Ver `limpiar()`.
    #:
    #: Antes se bajaba dentro de `corpus/`, y alli se quedaba. Con una carpeta
    #: de Drive de 1,8 GB eso llenaba el disco del equipo con copias de algo
    #: que ya vive en Drive, y ademas hacia que el corpus local y la fuente
    #: remota se mezclaran sin que se notara.
    SUBCARPETA = "_drive"

    def carpeta_temporal(self) -> str:
        """
        Carpeta donde se materializan los archivos mientras se leen.
        Usa data/copiloto_drive dentro de la carpeta persistente data/ del proyecto.

        CREARLA PUEDE FALLAR AUNQUE SE PIDA `exist_ok`
        ----------------------------------------------
        `exist_ok=True` no significa "no falles nunca": significa "no falles
        si al terminar hay una carpeta ahi". Si otro hilo esta borrando esa
        misma carpeta en ese instante -- la limpieza del final de un estudio
        anterior, que con 3,2 GB tarda lo suyo --, el `mkdir` choca con la
        carpeta a medio borrar, la comprobacion de despues ya no la ve, y
        salta `[Errno 17] File exists`.

        Paso de verdad, y no en un sitio inocente: tumbo una sincronizacion
        entera en el primer segundo. El informe lo dijo bien -- "Fallo la
        sincronizacion: File exists" --, pero el motivo real era una carrera
        de tres milisegundos, no un problema de permisos ni de disco.

        Se reintenta. Un choque asi se resuelve solo en cuanto el otro
        termina; lo que no puede es costar un estudio de seis horas.

        Returns:
            Ruta absoluta, creada si no existe.

        Raises:
            OSError: Si despues de reintentar sigue sin poder crearse. Ahi
                ya no es una carrera: es que el disco no deja.
        """
        destino = settings.ruta_proyecto("data", "copiloto_drive")
        for intento in range(INTENTOS_CARPETA):
            try:
                os.makedirs(destino, exist_ok=True)
                return destino
            except FileExistsError:
                # La carpeta existe pero no se vio como carpeta: alguien la
                # esta borrando. Si al mirar ya es una carpeta, sirve.
                if os.path.isdir(destino):
                    return destino
            except OSError:
                if intento == INTENTOS_CARPETA - 1:
                    raise
            time.sleep(ESPERA_CARPETA)
        os.makedirs(destino, exist_ok=True)
        return destino

    @staticmethod
    def _nombre_temporal(ruta: str) -> str:
        """
        Inventa un nombre temporal que no pueda chocar con otra descarga.

        DOS DESCARGAS DEL MISMO ARCHIVO SE PISABAN
        ------------------------------------------
        El temporal se llamaba `<archivo>.parcial`, el mismo para todos. Dos
        hilos bajando ese archivo a la vez escribian en el MISMO temporal;
        el primero en terminar lo renombraba y el segundo se encontraba sin
        temporal que renombrar. El estudio entero moria con esto:

            Fallo la sincronizacion: [Errno 2] No such file or directory:
            '.../Las mariposas dan alas a nuevas prendas.pdf.parcial'
            -> '.../Las mariposas dan alas a nuevas prendas.pdf'

        Un archivo de 400 KB que ya estaba bajado tumbo una pasada de 367
        documentos. Con el proceso y el hilo en el nombre, cada descarga
        escribe en el suyo y la que llegue segunda solo repite el trabajo.

        Args:
            ruta: Destino definitivo del archivo.

        Returns:
            Ruta del archivo temporal donde escribir mientras se baja.
        """
        # EL NUMERO DE HILO NO BASTA: SE REUTILIZA
        # ----------------------------------------
        # `threading.get_ident()` solo promete ser unico entre hilos VIVOS.
        # Al morir uno, su numero se reparte otra vez, y se vio: ocho hilos
        # seguidos dieron dos nombres distintos. Por eso va tambien un
        # contador, que no se repite mientras el proceso viva.
        with _CERROJO_TEMPORAL:
            global _CUANTAS_DESCARGAS
            _CUANTAS_DESCARGAS += 1
            turno = _CUANTAS_DESCARGAS
        return (f"{ruta}.{os.getpid()}-{threading.get_ident()}-{turno}"
                f".parcial")

    @staticmethod
    def _dejar_en_su_sitio(temporal: str, ruta: str) -> None:
        """
        Pone el temporal en su nombre definitivo, perdiendo bien las carreras.

        Args:
            temporal: Archivo recien bajado.
            ruta: Nombre definitivo.

        Raises:
            OSError: Si no se pudo dejar el archivo y tampoco lo dejo nadie.
        """
        try:
            os.replace(temporal, ruta)
        except OSError:
            # Que el temporal ya no este solo importa si el archivo tampoco
            # llego: si otro lo dejo entero en su sitio, el trabajo esta
            # hecho y reventar aqui seria tirar un estudio por haber bajado
            # dos veces lo mismo.
            if not (os.path.exists(ruta) and os.path.getsize(ruta) > 0):
                raise
            try:
                os.remove(temporal)
            except OSError:
                pass

    def asegurar_descargado(self, doc: dict) -> str:
        """
        Descarga el documento a disco si aun no existe en la carpeta local.

        Returns:
            Ruta absoluta del archivo descargado, o vacia si fallo.
        """
        ruta = doc.get("ruta")
        if ruta and os.path.exists(ruta) and os.path.getsize(ruta) > 0:
            return ruta
        archivo_id = doc.get("id")
        if not archivo_id or not ruta:
            try:
                servicio = self._servicio()
                for arch, _ in self._recorrer(servicio, self.carpeta_id, ""):
                    if arch.get("name") == doc.get("nombre") or doc.get("nombre", "").startswith(arch.get("name", "")):
                        archivo_id = arch.get("id")
                        break
            except Exception:
                pass
        if not archivo_id or not ruta:
            return ""
        try:
            servicio = self._servicio()
            os.makedirs(os.path.dirname(ruta), exist_ok=True)
            self._descargar(servicio, archivo_id, ruta, doc.get("exportar_como", ""))
            return ruta if os.path.exists(ruta) else ""
        except Exception:
            return ""

    def limpiar(self) -> dict:
        """
        Borra del equipo todo lo que se bajo de Drive.

        Se llama al terminar de indexar. Lo que el agente aprende vive en el
        indice vectorial, que son numeros, no en copias de los archivos: una
        vez leido un documento, conservarlo en disco no aporta nada y si
        ocupa espacio y multiplica donde puede escaparse informacion.

        Returns:
            Diccionario con `archivos` y `megas` liberados.
        """
        import shutil

        # NO SE BORRA LO QUE OTRO ESTA USANDO
        # -----------------------------------
        # Si hay un estudio de otro proceso llenando esta carpeta, llevarsela
        # por delante le cuesta el trabajo entero. Se dice que no y se explica
        # quien la esta usando; quien llamo decide.
        _ocupada = estudio_de_otro()
        if _ocupada:
            return {"archivos": 0, "megas": 0.0, "rechazado": _ocupada}

        carpeta = self.carpeta_temporal()
        archivos, bytes_totales = 0, 0
        for raiz, _, nombres in os.walk(carpeta):
            for nombre in nombres:
                try:
                    bytes_totales += os.path.getsize(
                        os.path.join(raiz, nombre))
                    archivos += 1
                except OSError:
                    pass
        shutil.rmtree(carpeta, ignore_errors=True)
        return {"archivos": archivos,
                "megas": round(bytes_totales / (1024 * 1024), 1)}

    def _servicio(self):
        """
        Abre la conexion con la API de Drive.

        Returns:
            El servicio de Drive listo para consultar.

        Raises:
            RuntimeError: Si faltan las librerias o las credenciales, diciendo
                exactamente que falta en lugar de un error de importacion.
        """
        try:
            from googleapiclient.discovery import build
        except ImportError as exc:
            raise RuntimeError(
                "Faltan las librerias de Google. Instalalas con: "
                "pip install google-api-python-client google-auth"
            ) from exc

        if not self.credenciales or not os.path.exists(self.credenciales):
            raise RuntimeError(
                "No se encuentra el archivo de credenciales de Google. "
                "Conectalo desde la pestana 'Drive y Estudio'."
            )
        return build("drive", "v3", credentials=self._credenciales(),
                     cache_discovery=False)

    def _credenciales(self):
        """
        Construye las credenciales, sean del tipo que sean.

        HAY DOS FORMAS DE ENTRAR A DRIVE, Y LAS DOS VALEN
        -------------------------------------------------
        - `authorized_user`: un cliente OAuth con su ID, su secreto y un
          permiso que la persona concedio una vez. Es lo que se obtiene al
          crear un "ID de cliente de OAuth" en Google Cloud, y la unica que se
          puede configurar escribiendo dos campos en un formulario.
        - `service_account`: una identidad propia, sin persona detras. No
          caduca y no pide permiso a nadie, pero su credencial es un archivo.

        Las dos funcionan sin intervencion una vez configuradas: el permiso de
        OAuth se renueva solo con el token de refresco, que no caduca mientras
        se use. Por eso el cron de las 02:00 funciona con cualquiera de las
        dos.

        Returns:
            Credenciales listas para `build`.

        Raises:
            RuntimeError: Si el archivo no es de ninguno de los dos tipos.
        """
        import json

        with open(self.credenciales, encoding="utf-8") as f:
            datos = json.load(f)

        tipo = datos.get("type")
        if tipo == "authorized_user":
            from google.oauth2.credentials import Credentials

            return Credentials(
                token=None,
                refresh_token=datos.get("refresh_token"),
                token_uri=datos.get("token_uri",
                                    "https://oauth2.googleapis.com/token"),
                client_id=datos.get("client_id"),
                client_secret=datos.get("client_secret"),
                scopes=[self.ALCANCE])

        if tipo == "service_account":
            from google.oauth2 import service_account

            return service_account.Credentials.from_service_account_info(
                datos, scopes=[self.ALCANCE])

        raise RuntimeError(
            f"El archivo de credenciales es de tipo {tipo!r}, que no se "
            f"reconoce. Vuelve a conectar Drive desde la interfaz.")

    def disponible(self) -> tuple:
        """
        Comprueba que se pueda llegar a la carpeta compartida.

        Returns:
            Tupla `(listo, motivo)`.
        """
        if not self.carpeta_id:
            # Se nombra la PESTANA, no la variable. Quien lee esto suele ser
            # quien usa la aplicacion, no quien la despliega, y existe un
            # formulario que hace la conexion entera: mandarle a editar un
            # archivo de configuracion es mandarle por el camino largo.
            return False, ("Drive no esta conectado todavia. Se conecta "
                           "desde la pestana 'Fuente de documentos': se pega "
                           "la direccion de la carpeta compartida y las "
                           "credenciales, y el sistema comprueba el acceso "
                           "antes de guardar nada.")
        if not self.credenciales or not os.path.exists(self.credenciales):
            # NO SE NOMBRAN VARIABLES DE ENTORNO EN LO QUE LEE LA PERSONA
            # -----------------------------------------------------------
            # Quien ve este aviso esta usando la aplicacion, no
            # desplegandola. Decirle "indica su ruta en GOOGLE_CREDENCIALES"
            # le manda a editar un archivo de configuracion del servidor
            # para resolver algo que la pestana hace sola, y encima le
            # obliga a conocer un nombre que no tiene por que saber.
            return False, ("Faltan las credenciales de Google. Se cargan en "
                           "la pestana 'Fuente de documentos', pegando el "
                           "cliente de OAuth o el archivo de la cuenta de "
                           "servicio. Sea cual sea, basta con permiso de "
                           "SOLO LECTURA sobre la carpeta.")
        try:
            servicio = self._servicio()
            servicio.files().get(fileId=self.carpeta_id,
                                 fields="id,name",
                                 supportsAllDrives=True).execute()
        except Exception as exc:
            return False, (f"No se pudo abrir la carpeta de Drive ({exc}). "
                           "Comprueba que la cuenta de servicio tenga acceso: "
                           "hay que compartirle la carpeta por su correo.")
        return True, f"Carpeta de Drive {self.carpeta_id}."

    def documentos(self, descargar: bool = True) -> list:
        """
        Lista los documentos de la carpeta compartida y los deja en disco.

        Recorre tambien las subcarpetas, y usa el nombre de la subcarpeta de
        primer nivel como categoria, igual que hace la fuente local: asi las
        citas salen con la misma forma venga el documento de donde venga.

        Solo descarga lo que cambio. Drive da un `md5Checksum` por archivo, y
        esa es la huella: si coincide con la del archivo ya bajado, no se
        vuelve a pedir.

        Returns:
            Lista de diccionarios con `nombre`, `categoria`, `ruta` y `huella`.
        """
        # SOLO SE GUARDA EL RECUENTO, NUNCA UNA DESCARGA
        # ----------------------------------------------
        # Con `descargar=True` hay que mirar de verdad: ahi se decide que
        # se baja y que no, y contestar con lo de hace medio minuto podria
        # saltarse un archivo recien subido. Contar, en cambio, es una foto
        # que aguanta perfectamente ese medio minuto.
        if not descargar:
            guardado = _inventario_guardado(_identidad_de_la_fuente())
            if guardado is not None:
                self.descartados = [dict(d) for d in guardado["descartados"]]
                return [dict(d) for d in guardado["valor"]]

        servicio = self._servicio()
        destino = self.carpeta_temporal()
        os.makedirs(destino, exist_ok=True)

        salida = []
        # Lo que se ve en Drive pero NO entra. Se guarda para poder decir que
        # falta y por que, en vez de que desaparezca sin explicacion.
        self.descartados = []
        for archivo, categoria in self._recorrer(servicio, self.carpeta_id, ""):
            nombre = archivo["name"]
            extension = os.path.splitext(nombre)[1].lower()

            # LOS NATIVOS DE GOOGLE SE EXPORTAN
            # ---------------------------------
            # Su nombre en Drive no lleva extension, asi que el filtro de
            # abajo los descartaba en silencio. Se le anade la extension del
            # formato al que se van a convertir, y asi el resto del camino
            # -- el filtro, el lector, el troceado -- funciona igual que con
            # un archivo subido.
            exportar_como = ""
            conversion = self.EXPORTACIONES.get(archivo.get("mimeType", ""))
            if conversion:
                exportar_como, extension = conversion
                if not nombre.lower().endswith(extension):
                    nombre += extension

            if extension not in corpus.EXTENSIONES_LEIBLES:
                # NO se salta en silencio. Antes era un `continue` pelado, y
                # un archivo en un formato que no se sabe leer desaparecia
                # sin dejar constancia: el agente no podia ni decir que
                # existia. Se apunta con su motivo, como todo lo demas.
                self.descartados.append({
                    "nombre": nombre,
                    "mb": round(int(archivo.get("size") or 0)
                                / (1024 * 1024), 1),
                    "motivo": (f"no hay lector para '{extension}'"
                               if extension
                               else "el archivo no tiene extension"),
                    "categoria": categoria or RAIZ,
                })
                continue

            # El tope de tamano se aplica ANTES de descargar, con el dato que
            # da Google.
            #
            # POR QUE IMPORTA QUE SEA ANTES
            # -----------------------------
            # El limite existia, pero solo en el inventario de la carpeta
            # local: la fuente de Drive no lo miraba. Resultado: una carpeta
            # con cuatro CSV de 450 MB se bajaba entera y se troceaba entera.
            # Se vio en marcha, con la indexacion atascada horas en
            # "[1/7] e_eccmc_100K_2024_3.csv".
            #
            # Preguntar el tamano a Google cuesta un campo mas en una consulta
            # que ya se hacia. Descubrirlo despues de bajar 450 MB cuesta la
            # descarga entera.
            try:
                peso = int(archivo.get("size") or 0)
            except (TypeError, ValueError):
                peso = 0
            # QUE SE INDEXA Y QUE NO
            # ----------------------
            # Lo decide cuantas FICHAS costaria, no cuantos megas ocupa. Cada
            # ficha es una llamada a la API de embeddings, y la cuota es el
            # recurso escaso: un informe de 200 MB con pocas fichas entra, y
            # un CSV de 30 MB con 45.000 no.
            motivo, estimado = self._decidir(servicio, archivo, nombre, peso,
                                             destino, descargar)
            if motivo:
                # No entra como bibliografia, pero NO desaparece. Se le hace
                # una ficha de catalogo con los primeros megas: que columnas
                # trae, con que separador y si tiene coordenadas. Asi el
                # agente puede decir "ese archivo existe y trae estos campos"
                # en vez de "no tengo informacion", que se lee como "ese
                # archivo no existe".
                ficha = {
                    "nombre": nombre,
                    "mb": round(peso / (1024 * 1024), 1),
                    "motivo": motivo,
                    "categoria": categoria or RAIZ,
                }
                if estimado and estimado.get("fichas", -1) >= 0:
                    ficha["fichas_estimadas"] = estimado["fichas"]
                    ficha["densidad"] = estimado["densidad"]
                if descargar:
                    ficha["catalogo"] = self._catalogar(
                        servicio, archivo["id"], nombre, destino)
                self.descartados.append(ficha)
                continue

            carpeta_cat = os.path.join(destino, categoria or RAIZ)
            os.makedirs(carpeta_cat, exist_ok=True)
            ruta = os.path.join(carpeta_cat, nombre)

            # La huella lleva pegada la version con que se sabe leer ese
            # formato. Sin eso, mejorar un lector no sirve de nada para los
            # archivos que ya estaban: su md5 no cambia, asi que nadie los
            # vuelve a mirar y el indice conserva la lectura vieja.
            huella = ((archivo.get("md5Checksum")
                       or archivo.get("modifiedTime", ""))
                      + corpus.sufijo_de_lectura(nombre))
            # `descargar=False` sirve para MIRAR que hay sin traerselo.
            #
            # Se midio el motivo: el endpoint que pinta el estado de la fuente
            # llamaba a este metodo, y con Drive conectado eso descargaba la
            # carpeta ENTERA cada vez que alguien abria la aplicacion. Tardaba
            # 77 segundos y la pantalla se quedaba en blanco mientras tanto.
            # Contar archivos no deberia costar una descarga.
            no_se_pudo = ""
            if descargar and not self._ya_esta(ruta, huella):
                # UN ARCHIVO QUE NO BAJA NO PUEDE COSTAR EL ESTUDIO ENTERO
                # --------------------------------------------------------
                # Esta llamada iba a pelo. Cualquier tropiezo en UNA sola
                # descarga -- la red que se corta, Drive que responde 500,
                # el temporal que desaparece porque otro borro la carpeta --
                # subia hasta arriba y tiraba la pasada completa, incluidos
                # los trescientos documentos que ya estaban bajados y los
                # que ya se habian indexado.
                #
                # Se vio asi, el 9 de noviembre de 2026, y lo unico que la
                # pantalla llego a decir fue:
                #
                #   [Errno 2] No such file or directory:
                #   '.../8. Aldana-2010-Oil-palm-pest-manual.pdf.1-...-496.parcial'
                #   -> '.../8. Aldana-2010-Oil-palm-pest-manual.pdf'
                #
                # Tres horas de trabajo perdidas por un archivo de los 367.
                #
                # Ahora se apunta cual fallo y por que, y se sigue. El
                # documento NO desaparece: entra en la lista con su motivo,
                # el indexador lo cuenta entre los que no llegaron a bajarse
                # y el informe final lo dice. Un estudio incompleto que se
                # explica vale mucho mas que ningun estudio.
                try:
                    self._descargar(servicio, archivo["id"], ruta,
                                    exportar_como)
                except Exception as exc:
                    # SI LA CARPETA DESAPARECIO, SE REHACE Y SE REINTENTA
                    # ---------------------------------------------------
                    # El caso que de verdad ocurre no es "este archivo no se
                    # deja bajar": es que algo borro la carpeta entera por
                    # debajo y a partir de ahi fallan TODOS. Apuntar
                    # trescientos fallos uno a uno seria exacto e inutil.
                    # Se rehace la carpeta y se le da una segunda
                    # oportunidad al archivo; con eso el estudio se levanta
                    # solo en vez de volver con las manos vacias.
                    try:
                        os.makedirs(os.path.dirname(ruta), exist_ok=True)
                        self._descargar(servicio, archivo["id"], ruta,
                                        exportar_como)
                    except Exception as otra:
                        no_se_pudo = f"{type(otra).__name__}: {str(otra)[:160]}"

            salida.append({
                "no_se_pudo_bajar": no_se_pudo,
                "id": archivo.get("id"),
                "nombre": nombre,
                "categoria": categoria or RAIZ,
                "ruta": ruta,
                "huella": huella,
                "mimeType": archivo.get("mimeType", ""),
                "exportar_como": exportar_como,
            })

        if not descargar:
            _guardar_inventario(_identidad_de_la_fuente(), salida,
                                self.descartados)
        return salida

    def inventario(self) -> dict:
        """
        Recorre la carpeta entera y devuelve TODO lo que hay, con su ruta.

        EN QUE SE DIFERENCIA DE `documentos()`
        --------------------------------------
        `documentos()` devuelve lo que va a ENTRAR al indice: filtra por
        formato, por tamano y por coste. Eso es lo correcto para indexar y
        es justo lo que estorba cuando alguien pregunta "que hay en el
        Drive". La respuesta a esa pregunta no puede ser una lista
        filtrada: tiene que ser la carpeta tal como es, con sus 46
        subcarpetas y sus 425 archivos, incluidos los que no se pueden
        leer y los que no caben.

        Ademas guarda la RUTA completa de cada archivo, no solo la
        subcarpeta de primer nivel que usa el catalogo como categoria. Sin
        la ruta, dos archivos llamados igual en carpetas distintas son
        indistinguibles, y "esta en Normatividad" no se puede decir.

        No descarga nada: solo pregunta a Drive que hay.

        Returns:
            Diccionario con `carpetas` (lista de rutas) y `archivos` (lista
            de diccionarios con `ruta`, `nombre`, `carpeta`, `mb` y
            `extension`). Con `error` si no se pudo consultar.
        """
        guardado = _arbol_guardado(_identidad_de_la_fuente())
        if guardado is not None:
            return guardado

        try:
            servicio = self._servicio()
        except Exception as exc:
            return {"error": str(exc)[:200], "carpetas": [], "archivos": []}

        carpetas, archivos = [], []

        def bajar(carpeta_id, ruta):
            """
            Recorre una carpeta y todo lo que cuelga de ella.

            Args:
                carpeta_id: Identificador de la carpeta en Drive.
                ruta: Camino acumulado hasta ella.
            """
            pagina = None
            while True:
                respuesta = servicio.files().list(
                    q=f"'{carpeta_id}' in parents and trashed = false",
                    fields=("nextPageToken, files(id,name,mimeType,size,"
                            "modifiedTime)"),
                    pageSize=500, pageToken=pagina,
                    supportsAllDrives=True,
                    includeItemsFromAllDrives=True,
                ).execute()

                for archivo in respuesta.get("files", []):
                    nombre = archivo["name"]
                    dentro = f"{ruta}/{nombre}"
                    if archivo["mimeType"] == CARPETA_DRIVE:
                        carpetas.append(dentro)
                        bajar(archivo["id"], dentro)
                        continue
                    bytes_ = int(archivo.get("size") or 0)
                    archivos.append({
                        "ruta": dentro,
                        "nombre": nombre,
                        "carpeta": ruta or "/",
                        "mb": round(bytes_ / (1024 * 1024), 2),
                        "extension": os.path.splitext(nombre)[1].lower(),
                        "modificado": (archivo.get("modifiedTime") or "")[:10],
                    })

                pagina = respuesta.get("nextPageToken")
                if not pagina:
                    break

        try:
            bajar(self.carpeta_id, "")
        except Exception as exc:
            return {"error": str(exc)[:200], "carpetas": carpetas,
                    "archivos": archivos}

        arbol = {"carpetas": sorted(carpetas),
                 "archivos": sorted(archivos, key=lambda a: a["ruta"]),
                 "error": ""}
        _guardar_arbol(_identidad_de_la_fuente(), arbol)
        return arbol

    def _recorrer(self, servicio, carpeta_id: str, categoria: str):
        """
        Recorre la carpeta y sus subcarpetas, devolviendo archivo y categoria.

        Args:
            servicio: Servicio de Drive.
            carpeta_id: Carpeta por la que empezar.
            categoria: Categoria heredada. Vacia en el primer nivel.

        Yields:
            Tuplas `(archivo, categoria)`.
        """
        pagina = None
        while True:
            respuesta = servicio.files().list(
                q=f"'{carpeta_id}' in parents and trashed = false",
                fields=("nextPageToken, files(id,name,mimeType,"
                        "md5Checksum,modifiedTime,size)"),
                pageSize=200, pageToken=pagina,
                supportsAllDrives=True, includeItemsFromAllDrives=True,
            ).execute()

            for archivo in respuesta.get("files", []):
                if archivo["mimeType"] == "application/vnd.google-apps.folder":
                    # La subcarpeta de PRIMER nivel es la categoria; las de
                    # dentro la heredan, igual que en la fuente local.
                    yield from self._recorrer(
                        servicio, archivo["id"], categoria or archivo["name"])
                else:
                    yield archivo, categoria

            pagina = respuesta.get("nextPageToken")
            if not pagina:
                break

    @staticmethod
    def _ya_esta(ruta: str, huella: str) -> bool:
        """
        Dice si el archivo ya esta bajado y sin cambios.

        Args:
            ruta: Donde deberia estar.
            huella: `md5Checksum` que da Drive.

        Returns:
            True si no hace falta volver a descargarlo.

        LIMITE CON LOS NATIVOS DE GOOGLE
        -------------------------------
        Un Documento o una Hoja de Google no traen `md5Checksum`: no son
        archivos, son filas en una base de datos de Google. La huella cae
        entonces en `modifiedTime`, que nunca va a coincidir con el MD5 del
        .docx exportado, asi que esta funcion devuelve False y el archivo se
        vuelve a exportar en cada pasada.

        Es la decision deliberada: entre reconvertir un Documento, que pesa
        kilobytes, y arriesgarse a servir una version vieja, se prefiere lo
        primero. Y en produccion no cambia nada, porque lo descargado de
        Drive se borra despues de cada sincronizacion.
        """
        if not os.path.exists(ruta) or not huella:
            return False
        try:
            digest = hashlib.md5()
            with open(ruta, "rb") as f:
                for trozo in iter(lambda: f.read(1 << 20), b""):
                    digest.update(trozo)
            return digest.hexdigest() == huella
        except OSError:
            return False

    def _decidir(self, servicio, archivo: dict, nombre: str, peso: int,
                 destino: str, descargar: bool) -> tuple:
        """
        Decide si un archivo entra al indice, y por que no si no entra.

        EL ORDEN IMPORTA
        ----------------
        1. Lo que pase del tope de descarga ni se baja. Es un guardia de ancho
           de banda y memoria, no de cuota: bajar 2 GB para luego descartarlos
           seria tirar el tiempo en cada sincronizacion.
        2. Lo pequeno entra sin preguntar. Por debajo del umbral ningun
           formato puede dar mas fichas que el presupuesto, asi que estimar
           seria gastar una consulta para confirmar lo obvio.
        3. Lo demas se estima leyendo su principio, y se compara con el
           presupuesto de fichas.

        De los formatos que NO se pueden estimar por muestra (PDF, DOCX,
        XLSX: truncados no se abren) no se decide aqui. Se bajan y se cuentan
        exactamente al indexarlos, que no cuesta cuota.

        Args:
            servicio: Servicio de Drive.
            archivo: Metadatos del archivo en Drive.
            nombre: Nombre del archivo.
            peso: Tamano en bytes.
            destino: Carpeta temporal de trabajo.
            descargar: Si esta permitido traerse datos ahora mismo.

        Returns:
            Tupla `(motivo, estimacion)`. El motivo viene vacio si el archivo
            entra; si no, explica por que se aparta.
        """
        mega = 1024 * 1024

        # Con 900 caracteres por ficha, ni el texto mas denso llega al
        # presupuesto por debajo de este tamano.
        umbral = corpus.PRESUPUESTO_FICHAS * corpus.TAMANO_FICHA
        if peso <= umbral:
            return "", None

        extension = os.path.splitext(nombre)[1].lower()
        if extension not in corpus.FORMATOS_ESTIMABLES:
            # No se puede estimar por muestra. Aqui si manda el tope de
            # descarga, porque para contar sus fichas hay que bajarlo entero.
            if peso > corpus.TAMANO_MAXIMO_DOCUMENTO:
                tope = corpus.TAMANO_MAXIMO_DOCUMENTO // mega
                return (f"pesa {peso // mega} MB, no se puede medir sin "
                        f"bajarlo entero y el tope de descarga son "
                        f"{tope} MB"), None
            # Se decide despues de bajarlo, contando sus fichas de verdad.
            return "", None

        # Formato estimable: la muestra son 4 MB, asi que el tamano total da
        # igual. Un CSV de 2 GB se descarta por lo que CUESTA, que es el
        # motivo verdadero, y no por pesar mucho, que es solo un sintoma.
        estimado = {"fichas": -1, "densidad": 0.0, "muestra_mb": 0.0}
        if descargar:
            muestra = os.path.join(destino, f"_estimar_{nombre}")
            try:
                if self.asomarse(servicio, archivo["id"], muestra,
                                 cuantos_bytes=corpus.MUESTRA_PARA_ESTIMAR):
                    estimado = corpus.estimar_fichas(muestra, peso)
            finally:
                if os.path.exists(muestra):
                    try:
                        os.remove(muestra)
                    except OSError:
                        pass

        # Si no se pudo estimar -- porque solo se esta mirando sin descargar, o
        # porque la muestra no se dejo leer -- NO se deja pasar sin mas. El
        # tope de descarga sigue mandando: se midio que dejarlo pasar colaba un
        # archivo de 450 MB entero por no haber sabido medirlo.
        if estimado["fichas"] > corpus.PRESUPUESTO_FICHAS:
            return (f"daria unas {estimado['fichas']:,} fichas "
                    f"({estimado['densidad']} por MB) y el presupuesto son "
                    f"{corpus.PRESUPUESTO_FICHAS:,}").replace(",", "."), estimado

        # Bajarlo entero sigue costando ancho de banda y memoria. Este tope es
        # lo unico que queda del limite en megas.
        #
        # OJO CON LO QUE SE AFIRMA AQUI. Cuando solo se esta LISTANDO la
        # carpeta (`descargar=False`) no se ha bajado la muestra, asi que no se
        # han medido las fichas. Decir "cabria por fichas" en ese caso es
        # afirmar algo que no se ha comprobado, y aparecia tal cual en el
        # diagnostico que lee el usuario: los cuatro CSV salian como "cabria
        # por fichas, pero pesa 434 MB" cuando al medirlos de verdad daban
        # 644.936 fichas, 322 veces el presupuesto.
        if estimado["fichas"] < 0:
            # No se pudo medir. No se asume que sea gratis: por encima de lo
            # que se admite bajar a ciegas, se aparta y se dice exactamente
            # eso, sin afirmar nada sobre su coste.
            if peso > corpus.TAMANO_SIN_MEDIR:
                return (f"pesa {peso // mega} MB y no se ha podido medir "
                        f"cuantas fichas daria; a ciegas solo se bajan hasta "
                        f"{corpus.TAMANO_SIN_MEDIR // mega} MB"), estimado
            return "", estimado

        if peso > corpus.TAMANO_MAXIMO_DOCUMENTO:
            tope = corpus.TAMANO_MAXIMO_DOCUMENTO // (1024 ** 3)
            return (f"cabria por fichas ({estimado['fichas']:,}), pero pesa "
                    f"{peso // mega} MB y el tope de descarga son "
                    f"{tope} GB").replace(",", "."), estimado

        # Medido y dentro del presupuesto: entra.
        return "", estimado

    def _resumir_capa(self, servicio, archivo_id: str, nombre: str,
                      destino: str) -> dict:
        """
        Baja una capa tabular entera, la resume agrupando y la borra.

        POR QUE SE BAJA ENTERA
        ----------------------
        Porque resumir no es muestrear. Se midio: los primeros 20 MB de la
        capa de ecosistemas son Caqueta y Putumayo -- el archivo viene
        ordenado por region --, asi que una muestra del principio no dice
        nada de Antioquia. Para saber que hay en la cuenca hay que recorrerlo
        todo.

        Sale barato en lo que importa: recorrer 434 MB tarda unos 30 segundos
        entre bajada y lectura, y gasta CERO embeddings. Lo caro seria
        indexar sus 644.936 fichas, que son 3,5 anos de cuota gratuita.

        El resultado se guarda en disco: la sincronizacion diaria no vuelve a
        bajar 1,7 GB si las capas no han cambiado.

        Args:
            servicio: Servicio de Drive.
            archivo_id: Identificador del archivo.
            nombre: Nombre del archivo.
            destino: Carpeta temporal de trabajo.

        Returns:
            Lo que devuelve `catalogo.resumir_tabla`, o un diccionario con
            `error` si no se pudo.
        """
        import json as _json

        from core import catalogo as _cat

        extension = os.path.splitext(nombre)[1].lower()
        if extension not in _cat.FORMATOS_RESUMIBLES:
            # La lista la manda `catalogo`, que es quien tiene los lectores en
            # streaming. Antes aqui habia una tupla escrita a mano con solo
            # .csv, .tsv y .txt, y por eso un Excel grande no llegaba nunca a
            # resumirse: se bajaba, se descartaba y no quedaba nada de el.
            return {"lineas": [],
                    "error": f"no hay lector en streaming para {extension}"}

        guardado = os.path.join(self._carpeta_resumenes(), nombre + ".json")
        if os.path.exists(guardado):
            try:
                with open(guardado, "r", encoding="utf-8") as fh:
                    anterior = _json.load(fh)
                # Un resumen guardado con una version anterior del resumidor
                # se descarta y se rehace. Sin esta comprobacion, arreglar
                # `resumir_tabla` no servia de nada para las capas que ya
                # tenian resumen: seguian sirviendo el texto viejo.
                if anterior.get("version") == _cat.VERSION_RESUMEN:
                    return anterior
            except (OSError, ValueError):
                pass  # resumen corrupto: se rehace

        completo = os.path.join(destino, f"_resumir_{nombre}")
        try:
            self._descargar(servicio, archivo_id, completo)
            resumen = _cat.resumir_tabla(completo)
        except Exception as exc:
            return {"lineas": [], "error": str(exc)[:160]}
        finally:
            if os.path.exists(completo):
                try:
                    os.remove(completo)
                except OSError:
                    pass

        try:
            with open(guardado, "w", encoding="utf-8") as fh:
                _json.dump(resumen, fh, ensure_ascii=False)
        except OSError:
            pass  # sin cache se recalcula; molesto, no grave
        return resumen

    @staticmethod
    def _carpeta_resumenes() -> str:
        """
        Carpeta donde se guardan los resumenes ya calculados.

        Returns:
            Ruta dentro de `data/`, que sobrevive al contenedor.
        """
        base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        carpeta = os.path.join(base, "data", "resumenes_capas")
        os.makedirs(carpeta, exist_ok=True)
        return carpeta

    def _catalogar(self, servicio, archivo_id: str, nombre: str,
                   destino: str) -> dict:
        """
        Describe un archivo demasiado grande leyendo solo su principio.

        Se baja una muestra de dos megas, se le pregunta a `core.catalogo` que
        hay dentro y se borra la muestra. El archivo de 430 MB nunca llega al
        equipo entero.

        Args:
            servicio: Servicio de Drive.
            archivo_id: Identificador del archivo en Drive.
            nombre: Nombre del archivo, para nombrar la muestra.
            destino: Carpeta temporal donde dejar la muestra.

        Returns:
            Diccionario con `columnas`, `total_columnas`, `delimitador`,
            `filas_en_la_muestra`, `apto_para_qgis` y las columnas de
            coordenadas si las hay. Diccionario con `error` si no se pudo.
        """
        from core import catalogo as _cat

        muestra = os.path.join(destino, f"_muestra_{nombre}")
        try:
            if not self.asomarse(servicio, archivo_id, muestra):
                return {"error": "no se pudo leer el principio del archivo"}

            ficha = _cat.describir_dataset(muestra)
            # Ademas de la cabecera, el RESUMEN de la tabla entera. Es lo que
            # permite que una capa de 434 MB "entre" al indice: no fila a
            # fila, que es imposible, sino agrupada.
            resumen = self._resumir_capa(servicio, archivo_id, nombre, destino)
            return {
                "resumen": resumen,
                "columnas": ficha["columnas"],
                "total_columnas": ficha["total_columnas"],
                "delimitador": ficha["delimitador_nombre"],
                # Las filas se cuentan SOBRE LA MUESTRA, no sobre el archivo:
                # decir "2.400 filas" de un archivo de 430 MB seria mentir por
                # un factor de doscientos. Se dice de donde sale el numero.
                "filas_en_la_muestra": ficha["filas_estimadas"],
                "columna_latitud": ficha["columna_latitud"],
                "columna_longitud": ficha["columna_longitud"],
                "apto_para_qgis": ficha["apto_para_qgis"],
            }
        except Exception as exc:
            return {"error": str(exc)[:160]}
        finally:
            if os.path.exists(muestra):
                try:
                    os.remove(muestra)
                except OSError:
                    pass

    @staticmethod
    def asomarse(servicio, archivo_id: str, ruta: str,
                 cuantos_bytes: int = 2 * 1024 * 1024) -> bool:
        """
        Baja solo el principio de un archivo, sin traerselo entero.

        POR QUE EXISTE
        --------------
        En la carpeta hay cuatro CSV de unos 430 MB cada uno: 1,7 GB que el
        tope del corpus descarta, y con razon, porque son capas de datos y no
        bibliografia. Pero descartarlos del todo hacia que el agente
        contestara "no tengo informacion sobre e_eccmc_100K_2024", como si el
        archivo no existiera. Existe, esta en Drive, y lo que no se puede es
        LEERLO ENTERO.

        Con los dos primeros megas basta para saber que trae dentro: el
        separador, las columnas, si tiene coordenadas y cuantas filas
        aproximadas. Eso cabe en una ficha y responde la pregunta de verdad.

        Args:
            servicio: Servicio de Drive.
            archivo_id: Identificador del archivo.
            ruta: Destino local de la muestra.
            cuantos_bytes: Cuanto traerse. Dos megas cubren de sobra la
                cabecera y unas cuantas miles de filas.

        Returns:
            True si se pudo traer la muestra.
        """
        from googleapiclient.http import MediaIoBaseDownload

        # Aqui NO se exporta. Un nativo de Google no admite descarga
        # parcial -- no hay bytes que recortar hasta que Google lo
        # convierte --, y tampoco hace falta: Drive no le da `size`, asi
        # que nunca llega al camino de la muestra, que solo se usa para
        # decidir sobre archivos grandes.
        peticion = servicio.files().get_media(
            fileId=archivo_id, supportsAllDrives=True)
        temporal = FuenteDrive._nombre_temporal(ruta)
        try:
            with open(temporal, "wb") as f:
                # El primer trozo y se corta: `next_chunk` pide justamente ese
                # rango al servidor, asi que del archivo de 430 MB viajan dos.
                bajada = MediaIoBaseDownload(f, peticion,
                                             chunksize=cuantos_bytes)
                bajada.next_chunk()
            FuenteDrive._dejar_en_su_sitio(temporal, ruta)
            return True
        except Exception:
            if os.path.exists(temporal):
                os.remove(temporal)
            return False

    @staticmethod
    def _descargar(servicio, archivo_id: str, ruta: str,
                   exportar_como: str = "") -> None:
        """
        Baja un archivo de Drive al disco.

        Args:
            servicio: Servicio de Drive.
            archivo_id: Identificador del archivo.
            ruta: Destino local.
            exportar_como: mimeType al que convertirlo, para los
                nativos de Google. Vacio para un archivo normal.
        """
        from googleapiclient.http import MediaIoBaseDownload

        if exportar_como:
            # Un nativo de Google no tiene bytes propios: es una fila
            # en una base de datos de Google, no un archivo. Hay que
            # pedirle que lo CONVIERTA al vuelo; `get_media` falla con
            # el. `export_media` no admite `supportsAllDrives`.
            peticion = servicio.files().export_media(
                fileId=archivo_id, mimeType=exportar_como)
        else:
            peticion = servicio.files().get_media(
                fileId=archivo_id, supportsAllDrives=True)
        # Se escribe en un temporal y se renombra al final: si la descarga se
        # corta, no queda un archivo a medias que parezca bueno.
        temporal = FuenteDrive._nombre_temporal(ruta)
        try:
            with open(temporal, "wb") as f:
                bajada = MediaIoBaseDownload(f, peticion)
                terminado = False
                while not terminado:
                    _, terminado = bajada.next_chunk()
        except Exception:
            if os.path.exists(temporal):
                try:
                    os.remove(temporal)
                except OSError:
                    pass
            raise
        FuenteDrive._dejar_en_su_sitio(temporal, ruta)


# ==============================================================================
# ELECCION DE FUENTE
# ==============================================================================

#: Fuentes registradas. Anadir una nueva es anadir una entrada aqui.
#: Fuentes registradas. Anadir una nueva es anadir una entrada aqui.
#:
#: YA NO HAY FUENTE LOCAL, Y ES DELIBERADO
#: ---------------------------------------
#: Habia una `FuenteLocal` que leia una carpeta `corpus/` del propio
#: proyecto. Se quito junto con la carpeta. El motivo no es de limpieza:
#: esa carpeta era un camino por el que el agente podia acabar estudiando
#: lo que hubiera en el disco del servidor sin que nadie lo subiera a
#: Drive. Se encontro de verdad -- un "informacion total.zip" olvidado que
#: habria entrado al indice solo en cuanto se anadio el lector de .zip.
#:
#: Drive es la unica fuente. Si no esta configurada, el sistema se niega y
#: lo dice; no hay segunda puerta.
FUENTES = {
    "drive": FuenteDrive,
}


def obtener(nombre: str = None):
    """
    Devuelve la fuente de documentos configurada.

    Args:
        nombre: Fuerza una fuente concreta. Por defecto, la del `.env`.

    Returns:
        Instancia de la fuente, ya construida.

    Raises:
        ValueError: Si el nombre pedido no esta registrado. El mensaje dice
            cuales valen, para no dejar al usuario adivinando.
    """
    elegida = (nombre or settings.fuente_corpus()).strip().lower()
    if elegida == "local":
        # Mensaje propio: una configuracion antigua puede seguir pidiendo
        # "local", y un "valor desconocido" no explicaria por que.
        raise ValueError(
            "La fuente 'local' ya no existe: se retiro junto con la carpeta "
            "corpus/ para que el agente no pueda estudiar nada que no este "
            "en Drive. Conecta Drive desde la pestana 'Fuente de "
            "documentos'."
        )
    if elegida not in FUENTES:
        raise ValueError(
            f"Fuente de corpus desconocida: '{elegida}'. "
            f"Valores admitidos: {', '.join(sorted(FUENTES))}."
        )
    return FUENTES[elegida]()


#: Ultimo `estado()` calculado, con el momento en que se calculo. Listar Drive
#: cuesta mas de un segundo de red, y la interfaz lo pedia en CADA recarga:
#: al pegar una imagen, al marcar una casilla, al escribir. La carpeta no
#: cambia entre dos pulsaciones de tecla, asi que se guarda un rato.
_ULTIMO_ESTADO = {"cuando": 0.0, "valor": None, "quien": None}


def _identidad_de_la_fuente() -> tuple:
    """
    Dice QUE fuente es la activa, para no servirle lo guardado a otra.

    Sin esto, cambiar de Drive a la carpeta local (o de carpeta de Drive)
    seguia devolviendo el estado anterior hasta medio minuto despues: la
    interfaz mostraba los documentos de la fuente que se acababa de
    abandonar.

    Returns:
        Tupla con el nombre de la fuente y la carpeta a la que apunta.
    """
    import os as _os

    return (_os.environ.get("FUENTE_CORPUS", ""),
            _os.environ.get("DRIVE_CARPETA_ID", ""),
            _os.environ.get("GOOGLE_CREDENCIALES", ""))

#: Cuantos segundos vale el estado guardado. Medio minuto es bastante menos
#: que lo que tarda una persona en subir algo a Drive y volver a la pestana,
#: y evita cientos de consultas durante una conversacion.
SEGUNDOS_DE_CACHE = 30

#: Cuantas veces se reintenta crear la carpeta de descargas.
#:
#: Tres bastan: la carrera que la rompe dura lo que tarda un `rmtree` en
#: soltar el inodo, milisegundos. Si a la tercera sigue fallando, no es una
#: carrera y hay que verlo.
INTENTOS_CARPETA = 3

#: Segundos entre un intento y el siguiente.
ESPERA_CARPETA = 0.5

#: Como llama Drive a una carpeta.
CARPETA_DRIVE = "application/vnd.google-apps.folder"

#: Cuanto vale un inventario de la fuente antes de volver a pedirlo.
#:
#: POR QUE HACE FALTA, MEDIDO
#: --------------------------
#: Contar lo que hay en Drive no descarga nada, pero son varias llamadas
#: paginadas a Google: con 161 archivos tarda unos diez segundos. El
#: problema era QUIEN lo pedia y CUANTAS veces.
#:
#: `estado()` ya lo guardaba treinta segundos, pero el panel de la fuente
#: llamaba a `documentos(descargar=False)` por su cuenta, saltandose esa
#: caja. Resultado: cada vez que Streamlit redibujaba la pagina -- y
#: redibuja entera ante cualquier clic, incluido el que dispara pegar una
#: imagen -- se listaba Drive otra vez. Se midio desde el contenedor de la
#: interfaz: 10,3 s en `/corpus/estado` y 9,5 s mas en `/corpus/fuente`,
#: veinte segundos de pagina congelada por pegar una captura.
#:
#: El inventario solo cambia cuando cambia Drive. Medio minuto de retraso
#: en un panel no le hace dano a nadie; veinte segundos de espera, si. Y
#: cuando SI cambia -- al sincronizar o al cambiar de fuente --,
#: `olvidar_estado()` lo tira.
#:
#: DE 45 SEGUNDOS A UN CUARTO DE HORA
#: ----------------------------------
#: Cuarenta y cinco segundos bastaban para una pagina que se redibuja sola,
#: pero no para una CONVERSACION: entre dos preguntas pasan minutos, asi que
#: la caja estaba siempre vencida y cada turno del chat volvia a recorrer
#: Drive entero. Medido en el contenedor: 10,91 s de los 11,4 s que costaba
#: `fuentes.estado()`, pagados DENTRO de la espera de quien pregunta. De los
#: 25 segundos que tardaba en contestar una pregunta con imagen, once eran
#: esto.
#:
#: El tope no es el mecanismo, es la red de seguridad: lo que de verdad
#: invalida el inventario es `olvidar_estado()`, que se llama al sincronizar
#: y al cambiar de fuente. Y si alguien sube algo a Drive y no quiere
#: esperar, el panel de la fuente tiene su boton de "Volver a probar", que
#: refresca a la fuerza.
SEGUNDOS_DE_INVENTARIO = 900

#: Lo ultimo que se vio en la fuente, con cuando se vio y de quien era.
_ULTIMO_INVENTARIO = {"cuando": 0.0, "quien": None, "valor": None,
                      "descartados": None}

#: Lo mismo para el recorrido completo con rutas, que cuesta igual de caro.
_ULTIMO_ARBOL = {"cuando": 0.0, "quien": None, "valor": None}


def _inventario_guardado(quien: tuple):
    """
    Devuelve el inventario guardado si todavia vale.

    Args:
        quien: Identidad de la fuente, para no servir el inventario de una
            carpeta de Drive cuando se ha cambiado a otra.

    Returns:
        Diccionario con `valor` y `descartados`, o None si no sirve.
    """
    import time as _time

    if _ULTIMO_INVENTARIO["valor"] is None:
        return None
    if _ULTIMO_INVENTARIO["quien"] != quien:
        return None
    if _time.time() - _ULTIMO_INVENTARIO["cuando"] >= SEGUNDOS_DE_INVENTARIO:
        return None
    return _ULTIMO_INVENTARIO


def _guardar_inventario(quien: tuple, documentos: list,
                        descartados: list) -> None:
    """
    Guarda el inventario recien hecho.

    Args:
        quien: Identidad de la fuente.
        documentos: Lo que se vio.
        descartados: Lo que se vio y no entra, con su motivo.
    """
    import time as _time

    _ULTIMO_INVENTARIO["cuando"] = _time.time()
    _ULTIMO_INVENTARIO["quien"] = quien
    _ULTIMO_INVENTARIO["valor"] = [dict(d) for d in documentos]
    _ULTIMO_INVENTARIO["descartados"] = [dict(d) for d in (descartados or [])]


def _arbol_guardado(quien: tuple):
    """
    Devuelve el recorrido completo guardado, si todavia vale.

    Args:
        quien: Identidad de la fuente.

    Returns:
        El arbol, o None si hay que volver a pedirlo.
    """
    import time as _time

    if _ULTIMO_ARBOL["valor"] is None or _ULTIMO_ARBOL["quien"] != quien:
        return None
    if _time.time() - _ULTIMO_ARBOL["cuando"] >= SEGUNDOS_DE_INVENTARIO:
        return None
    return _ULTIMO_ARBOL["valor"]


def _guardar_arbol(quien: tuple, arbol: dict) -> None:
    """
    Guarda el recorrido completo recien hecho.

    Args:
        quien: Identidad de la fuente.
        arbol: Lo que devolvio el recorrido.
    """
    import time as _time

    _ULTIMO_ARBOL["cuando"] = _time.time()
    _ULTIMO_ARBOL["quien"] = quien
    _ULTIMO_ARBOL["valor"] = arbol


def olvidar_estado() -> None:
    """
    Tira el estado guardado para que la proxima consulta mire de verdad.

    Se llama despues de sincronizar o de cambiar de fuente: ahi SI ha podido
    cambiar lo que hay, y seguir contestando con lo de antes seria mentir.
    """
    _ULTIMO_ESTADO["cuando"] = 0.0
    _ULTIMO_ESTADO["valor"] = None
    _ULTIMO_ESTADO["quien"] = None
    _ULTIMO_INVENTARIO["cuando"] = 0.0
    _ULTIMO_INVENTARIO["valor"] = None
    _ULTIMO_INVENTARIO["quien"] = None
    _ULTIMO_INVENTARIO["descartados"] = None
    _ULTIMO_ARBOL["cuando"] = 0.0
    _ULTIMO_ARBOL["valor"] = None
    _ULTIMO_ARBOL["quien"] = None


def estado(refrescar: bool = False) -> dict:
    """
    Describe la fuente activa y si se puede usar ahora mismo.

    Sirve para el diagnostico de la interfaz y para el endpoint que consulta
    n8n antes de lanzar una sincronizacion.

    Returns:
        Diccionario con `fuente`, `listo`, `motivo` y `documentos`.
    """
    import time as _time

    quien = _identidad_de_la_fuente()
    if not refrescar and _ULTIMO_ESTADO["valor"] is not None:
        fresco = _time.time() - _ULTIMO_ESTADO["cuando"] < SEGUNDOS_DE_CACHE
        if fresco and _ULTIMO_ESTADO["quien"] == quien:
            return dict(_ULTIMO_ESTADO["valor"])

    try:
        fuente = obtener()
    except ValueError as exc:
        return {"fuente": "?", "listo": False, "motivo": str(exc), "documentos": 0}

    listo, motivo = fuente.disponible()
    cuantos = 0
    reparto = []
    if listo:
        try:
            # Sin descargar: esto solo CUENTA. Es el diagnostico que pinta
            # la interfaz al abrirse, y pedir la descarga aqui hacia que
            # arrancar la aplicacion con Drive conectado se trajera la carpeta
            # entera antes de mostrar nada.
            fichas = fuente.documentos(descargar=False)
            cuantos = len(fichas)
            # El reparto sale del mismo recorrido que ya se hizo para contar,
            # asi que no cuesta ni una llamada mas. Importa tenerlo aqui
            # porque con Drive la carpeta local se borra despues de leer, y
            # entonces el reparto del corpus local describe una carpeta vacia.
            conteo = {}
            for ficha in fichas:
                categoria = ficha.get("categoria") or RAIZ
                conteo[categoria] = conteo.get(categoria, 0) + 1
            reparto = sorted(conteo.items())
        except Exception as exc:
            listo, motivo = False, f"No se pudo inventariar: {exc}"
    respuesta = {
        "fuente": fuente.nombre,
        "listo": listo,
        "motivo": motivo,
        "documentos": cuantos,
        "por_categoria": reparto,
        "en_el_equipo": fuente.nombre == "local",
        # Lo que la fuente aparto. Sin esto, la interfaz decia "4 documentos"
        # sobre una carpeta de 8 y no habia forma de saber que paso con los
        # otros cuatro.
        "descartados": list(getattr(fuente, "descartados", []) or []),
    }
    _ULTIMO_ESTADO["cuando"] = _time.time()
    _ULTIMO_ESTADO["valor"] = dict(respuesta)
    _ULTIMO_ESTADO["quien"] = quien
    return respuesta
