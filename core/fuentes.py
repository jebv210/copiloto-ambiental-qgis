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

from config import settings
from core import corpus


# ==============================================================================
# FUENTE LOCAL: la carpeta corpus/ del proyecto
# ==============================================================================

class FuenteLocal:
    """
    Lee los documentos de la carpeta `corpus/` del proyecto.

    Es la fuente que se usa hoy. Se apoya en `corpus.inventariar()`, que ya
    sabe que extensiones son legibles, que archivos no son fuente (LEEME,
    README) y como deducir la categoria a partir de la subcarpeta.
    """

    nombre = "local"

    def __init__(self, carpeta: str = None):
        """
        Args:
            carpeta: Carpeta a inventariar. Por defecto, la del proyecto.
        """
        self.carpeta = carpeta or corpus.carpeta_corpus()

    def disponible(self) -> tuple:
        """
        Comprueba que la carpeta exista y tenga documentos.

        Returns:
            Tupla `(listo, motivo)`.
        """
        if not os.path.isdir(self.carpeta):
            return False, f"No existe la carpeta {self.carpeta}."
        if not self.documentos():
            return False, f"La carpeta {self.carpeta} no tiene documentos legibles."
        return True, f"Carpeta local {self.carpeta}."

    def documentos(self, descargar: bool = True) -> list:
        """
        Inventaria los documentos disponibles.

        Returns:
            Lista de diccionarios con `nombre`, `categoria`, `ruta` y `huella`.
        """
        salida = []
        for doc in corpus.inventariar(self.carpeta):
            # `inventariar` lista TODO lo que encuentra y marca si sabe leerlo.
            # Aqui solo interesan los legibles: cualquier otro archivo que
            # aparezca en la carpeta no es bibliografia y no debe contarse como
            # documento ni intentar trocearse.
            if not doc.get("legible"):
                continue
            salida.append({
                "nombre": doc["nombre"],
                "categoria": doc["categoria"],
                "ruta": doc["ruta"],
                "huella": self._huella(doc["ruta"]),
            })
        return salida

    @staticmethod
    def _huella(ruta: str) -> str:
        """
        Identifica el contenido de un archivo sin tener que leerlo entero.

        Se usan tamano y fecha de modificacion porque abrir y digerir 134
        archivos solo para saber si cambiaron costaria mas que reindexarlos.
        Si el archivo se edita, cualquiera de los dos valores cambia.

        Args:
            ruta: Ruta del archivo.

        Returns:
            Huella corta en hexadecimal.
        """
        try:
            st = os.stat(ruta)
            crudo = f"{st.st_size}-{int(st.st_mtime)}"
        except OSError:
            crudo = ruta
        return hashlib.sha1(crudo.encode("utf-8")).hexdigest()[:16]


# ==============================================================================
# FUENTE DRIVE: pendiente de acceso
# ==============================================================================

RAIZ = "Raiz de la carpeta"


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

    LIMITE CONOCIDO
    ---------------
    Los archivos nativos de Google (Documentos, Hojas de calculo, Presentaciones)
    NO se leen: su nombre en Drive no lleva extension, asi que el filtro por
    `EXTENSIONES_LEIBLES` los descarta. Solo entran archivos subidos de verdad
    (.pdf, .docx, .xlsx, imagenes...). Si la informacion llega como Documento de
    Google hay que exportarla antes, o anadir aqui la exportacion por mimeType.
    """

    nombre = "drive"

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

        Vive en el area temporal del sistema, NO dentro del proyecto. Asi
        queda claro que es de paso: ni se versiona, ni se respalda, ni se
        confunde con documentacion propia.

        Returns:
            Ruta absoluta, creada si no existe.
        """
        import tempfile

        destino = os.path.join(tempfile.gettempdir(), "copiloto_drive")
        os.makedirs(destino, exist_ok=True)
        return destino

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
            return False, ("Falta DRIVE_CARPETA_ID en el .env: el identificador "
                           "de la carpeta compartida, que sale de su direccion "
                           "en el navegador.")
        if not self.credenciales or not os.path.exists(self.credenciales):
            return False, ("Falta el archivo de credenciales de Google. Crea "
                           "una cuenta de servicio con permiso de SOLO LECTURA "
                           "sobre la carpeta e indica su ruta en "
                           "GOOGLE_CREDENCIALES.")
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
            La misma forma que `FuenteLocal.documentos()`.
        """
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
            if extension not in corpus.EXTENSIONES_LEIBLES:
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

            huella = archivo.get("md5Checksum") or archivo.get("modifiedTime", "")
            # `descargar=False` sirve para MIRAR que hay sin traerselo.
            #
            # Se midio el motivo: el endpoint que pinta el estado de la fuente
            # llamaba a este metodo, y con Drive conectado eso descargaba la
            # carpeta ENTERA cada vez que alguien abria la aplicacion. Tardaba
            # 77 segundos y la pantalla se quedaba en blanco mientras tanto.
            # Contar archivos no deberia costar una descarga.
            if descargar and not self._ya_esta(ruta, huella):
                self._descargar(servicio, archivo["id"], ruta)

            salida.append({
                "nombre": nombre,
                "categoria": categoria or RAIZ,
                "ruta": ruta,
                "huella": huella,
            })
        return salida

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
        if extension not in (".csv", ".tsv", ".txt"):
            return {"lineas": [], "error": "no es una tabla de texto"}

        guardado = os.path.join(self._carpeta_resumenes(), nombre + ".json")
        if os.path.exists(guardado):
            try:
                with open(guardado, "r", encoding="utf-8") as fh:
                    return _json.load(fh)
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

        peticion = servicio.files().get_media(fileId=archivo_id,
                                              supportsAllDrives=True)
        temporal = ruta + ".parcial"
        try:
            with open(temporal, "wb") as f:
                # El primer trozo y se corta: `next_chunk` pide justamente ese
                # rango al servidor, asi que del archivo de 430 MB viajan dos.
                bajada = MediaIoBaseDownload(f, peticion,
                                             chunksize=cuantos_bytes)
                bajada.next_chunk()
            os.replace(temporal, ruta)
            return True
        except Exception:
            if os.path.exists(temporal):
                os.remove(temporal)
            return False

    @staticmethod
    def _descargar(servicio, archivo_id: str, ruta: str) -> None:
        """
        Baja un archivo de Drive al disco.

        Args:
            servicio: Servicio de Drive.
            archivo_id: Identificador del archivo.
            ruta: Destino local.
        """
        from googleapiclient.http import MediaIoBaseDownload

        peticion = servicio.files().get_media(fileId=archivo_id,
                                              supportsAllDrives=True)
        # Se escribe en un temporal y se renombra al final: si la descarga se
        # corta, no queda un archivo a medias que parezca bueno.
        temporal = ruta + ".parcial"
        with open(temporal, "wb") as f:
            bajada = MediaIoBaseDownload(f, peticion)
            terminado = False
            while not terminado:
                _, terminado = bajada.next_chunk()
        os.replace(temporal, ruta)


# ==============================================================================
# ELECCION DE FUENTE
# ==============================================================================

#: Fuentes registradas. Anadir una nueva es anadir una entrada aqui.
FUENTES = {
    "local": FuenteLocal,
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


def olvidar_estado() -> None:
    """
    Tira el estado guardado para que la proxima consulta mire de verdad.

    Se llama despues de sincronizar o de cambiar de fuente: ahi SI ha podido
    cambiar lo que hay, y seguir contestando con lo de antes seria mentir.
    """
    _ULTIMO_ESTADO["cuando"] = 0.0
    _ULTIMO_ESTADO["valor"] = None
    _ULTIMO_ESTADO["quien"] = None


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
