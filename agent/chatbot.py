"""
Agente conversacional de evaluacion de impacto ambiental.
===============================================================================

MODOS DE FUNCIONAMIENTO
-----------------------
El agente funciona en dos modos y cae de uno a otro automaticamente:

  * MODO LLM (Gemini)  -> hay una API Key valida. El modelo razona y decide
    que herramientas de `agent/tools.py` ejecutar.
  * MODO SIMULADOR     -> no hay clave, o fallo la llamada. Un motor de reglas
    deterministico reconoce la intencion del mensaje y ejecuta la herramienta
    correspondiente. Responde siempre, aunque sin capacidad de razonamiento.

El modo simulador existe para que la sustentacion del proyecto no dependa de
que haya internet ni saldo de API.

QUE SE DESQUEMO
---------------
  * La clave ya no se lee unicamente del `.env`: la interfaz web puede pasarla
    por parametro, lo que permite validarla y mostrar el estado en pantalla.
  * Se elimino el centinela `"tu_clave_api_aqui"`. Ahora se valida la clave de
    verdad, contra el servicio.
  * El nombre del modelo sale de `config.settings.modelo_llm()`.
  * Se elimino una respuesta enlatada que afirmaba que los datos eran "en su
    totalidad del departamento de Boyaca, con foco en el municipio de Maripi y
    la cuenca del Rio Carare-Minero". Era un resto del proyecto anterior y
    contradecia todo lo demas. Ahora esa pregunta la responde la herramienta
    `describir_area_de_estudio`, que lee los archivos reales.
"""

import re
import threading
import time

from config import settings
from agent.prompts import construir_system_prompt
from agent.tools import (
    HERRAMIENTAS,
    consultar_linea_base,
    calcular_impacto_conesa,
    procesar_lote_excel_csv,
    consultar_biodiversidad_local,
    describir_area_de_estudio,
)


# ==============================================================================
# VALIDACION DE LA API KEY
# ==============================================================================

def validar_api_key(clave: str):
    """
    Comprueba contra el servicio si una API Key de Gemini sirve.

    Hace una llamada real y barata (listar los modelos disponibles) en lugar de
    limitarse a mirar si la cadena tiene pinta de clave. Es la unica forma de
    saber si la clave esta activa, tiene cuota y corresponde a un proyecto con
    la API habilitada.

    Args:
        clave: La API Key a verificar.

    Returns:
        Tupla `(valida, mensaje)`:
            valida  : True si la clave funciona.
            mensaje : texto listo para mostrar en la interfaz, en verde si
                      `valida` es True y en rojo si es False.
    """
    if not clave or not str(clave).strip():
        return False, "No se ha indicado ninguna API Key."

    clave = str(clave).strip()

    try:
        from google import genai
    except ImportError:
        return False, (
            "Falta la libreria 'google-genai'. Instalela con: "
            "pip install google-genai"
        )

    try:
        cliente = genai.Client(api_key=clave)
        # `models.list()` devuelve un paginador perezoso: hay que consumir al
        # menos un elemento para que la peticion HTTP se ejecute de verdad.
        modelos = cliente.models.list()
        nombres = []
        for modelo in modelos:
            nombres.append(getattr(modelo, "name", ""))
            if len(nombres) >= 1:
                break

        if not nombres:
            return False, (
                "La clave fue aceptada pero la cuenta no tiene modelos "
                "disponibles."
            )

        return True, f"API Key valida. Modelo configurado: {settings.modelo_llm()}"

    except Exception as exc:
        detalle = str(exc)
        # Se traducen los errores mas frecuentes a un mensaje entendible.
        if "API_KEY_INVALID" in detalle or "API key not valid" in detalle:
            return False, "La API Key es incorrecta o fue revocada."
        if "PERMISSION_DENIED" in detalle or "403" in detalle:
            return False, (
                "La clave existe pero no tiene permiso sobre la API de "
                "Gemini. Habilite 'Generative Language API' en el proyecto."
            )
        if "RESOURCE_EXHAUSTED" in detalle or "429" in detalle:
            return False, "La clave supero su cuota de uso."
        if "getaddrinfo" in detalle or "Connection" in detalle:
            return False, "Sin conexion a internet para verificar la clave."
        return False, f"No se pudo validar la clave: {detalle[:200]}"


# ==============================================================================
# AGENTE
# ==============================================================================

class ConesaAgent:
    """
    Orquestador del agente conversacional.

    Atributos:
        modo (str): "LLM (Gemini)" o "Simulador Offline (Reglas)".
        modelo (str): Nombre del modelo configurado.
        cliente: Cliente de Google GenAI, o None en modo simulador.
        detalle_estado (str): Explicacion de por que esta en el modo en que esta.
    """

    def __init__(self, api_key: str = None, modelo: str = None):
        """
        Prepara el agente y decide en que modo va a operar.

        Args:
            api_key: Clave a usar.
                - `None` -> se toma la del entorno / archivo `.env`.
                - `""`   -> el usuario borro la clave a proposito: se fuerza el
                  modo simulador. Es importante distinguir estos dos casos: si
                  una cadena vacia cayera al `.env`, borrar el campo en la
                  interfaz no tendria ningun efecto visible.
                - cualquier otro valor -> se usa esa clave.
            modelo: Nombre del modelo. Si es None se usa el de la configuracion.
        """
        self.local = None
        #: Turnos de la conversacion que se le reenvian al modelo.
        self._historial = []
        self.cliente = None
        self.modo = "Simulador Offline (Reglas)"
        self.detalle_estado = "Sin API Key: el agente responde con reglas locales."

        # Solo hay un proveedor. Si Gemini no esta disponible se responde con
        # el motor de reglas, avisando en grande de que eso NO es una
        # respuesta: busca palabras clave y no consulta la bibliografia.
        if api_key is None:
            api_key = settings.api_key_gemini() or ""
        self.api_key = str(api_key).strip()
        self.modelo = modelo or settings.modelo_llm()

        if not self.api_key:
            return

        try:
            from google import genai
            self.cliente = genai.Client(api_key=self.api_key)
            self.modo = "LLM (Gemini)"
            self.detalle_estado = f"Conectado al modelo {self.modelo}."
        except ImportError:
            self.detalle_estado = (
                "La libreria 'google-genai' no esta instalada; se usa el "
                "motor de reglas local."
            )
        except Exception as exc:
            self.detalle_estado = (
                f"No se pudo inicializar el cliente de Gemini ({exc}); se usa "
                f"el motor de reglas local."
            )

    # --------------------------------------------------------------------------

    def responder(self, mensaje_usuario: str, imagenes=None) -> str:
        """
        Punto de entrada unico del agente.

        Args:
            mensaje_usuario: Texto escrito por la persona.
            imagenes: Lista opcional de imagenes a analizar. Cada elemento
                puede ser una ruta de archivo o una tupla `(bytes, tipo)`,
                por ejemplo `(datos, "image/jpeg")`. El modelo las mira junto
                con la pregunta.

        Returns:
            Respuesta del agente en formato Markdown.
        """
        if self.modo.startswith("LLM") and self.cliente is not None:
            return self._responder_con_llm(mensaje_usuario, imagenes)
        if imagenes:
            return (
                "### No puedo mirar imagenes ahora mismo\n\n"
                "El analisis de imagenes necesita el modelo; sin clave de "
                "Gemini solo responde el motor de reglas, que no las ve.\n\n"
                "---\n\n" + self._responder_con_simulador(mensaje_usuario)
            )
        return self._responder_con_simulador(mensaje_usuario)

    # --------------------------------------------------------------------------


    # --------------------------------------------------------------------------

    def _responder_con_llm(self, mensaje: str, imagenes=None) -> str:
        """
        Envia el mensaje al modelo con el prompt y las herramientas del sistema.

        El prompt de sistema se CONSTRUYE en cada llamada leyendo el estado real
        del proyecto, para que el modelo hable del area de estudio que hay
        cargada ahora y no de una que estaba escrita en el codigo.

        Args:
            mensaje: Texto del usuario.
            imagenes: Imagenes que acompanan a la pregunta, si las hay.

        Returns:
            La respuesta del modelo. Si la llamada falla, devuelve el motivo y
            a continuacion la respuesta del motor de reglas, para no dejar al
            usuario sin nada.
        """
        try:
            from google.genai import types
            from core.dynamic_filter import resumen_area_generada

            contexto = resumen_area_generada()
            if self.cliente is not None:
                try:
                    from agent.tools import _conector_gdb
                    contexto["capas_gdb"] = _conector_gdb().nombres_capas()
                except Exception:
                    contexto["capas_gdb"] = []

            # La conversacion entera, no solo la ultima pregunta. Sin esto, a
            # "y donde vive esa especie?" el agente contestaba pidiendo que se
            # le dijera de que especie se hablaba, aunque acabara de nombrarla.
            self._recordar("user", mensaje, imagenes)

            # Recuerdos episodicos a largo plazo desde Qdrant
            try:
                from core import memoria
                recuerdos = memoria.buscar_recuerdos(mensaje, limite=3)
            except Exception:
                recuerdos = []

            config = types.GenerateContentConfig(
                system_instruction=construir_system_prompt(contexto, recuerdos=recuerdos),
                tools=HERRAMIENTAS,
            )
            respuesta = self._generar_con_reintentos(self._historial, config)
            texto = (respuesta.text or "").strip()

            # `.text` viene VACIO mas a menudo de lo que parece. Si el modelo
            # dice haber terminado bien (STOP) y aun asi no hay texto, es un
            # hipo del servicio: se midio que repetir la MISMA pregunta
            # devuelve la respuesta completa. Antes se degradaba al motor de
            # reglas a la primera, y el usuario recibia un analisis por
            # palabras clave creyendo que era del modelo.
            if not texto and self._motivo_fin(respuesta) == "STOP":
                respuesta = self._generar_con_reintentos(self._historial, config)
                texto = (respuesta.text or "").strip()

            if texto:
                # El apartado de Bases se comprueba contra lo que de verdad
                # devolvieron las herramientas, no contra lo que el modelo
                # dice haber consultado.
                fuentes, usadas = _fuentes_del_turno(respuesta)
                texto = _asegurar_bases(texto, fuentes, usadas)
                texto = _asegurar_notas(texto, mensaje)
                self._recordar("model", texto)
                self._guardar_recuerdo_de_fondo(mensaje)
                return texto

            motivo = self._motivo_fin(respuesta) or "el modelo no devolvio texto"
            return (
                f"*(Respuesta vacia: {motivo}. Se responde con el motor de "
                f"reglas local.)*\n\n"
                + self._responder_con_simulador(mensaje)
            )

        except Exception as exc:
            return self._aviso_degradado(mensaje, str(exc))

    #: Esperas entre reintentos, en segundos. Los fallos 503 y 429 de Gemini
    #: son pasajeros: el servicio se satura o se agota la cuota del minuto, y
    #: a los pocos segundos vuelve. Sin estos reintentos, un hipo de la red
    #: tumbaba la respuesta al motor de reglas.
    ESPERAS_REINTENTO = [2, 6, 15]

    #: Codigos que merecen reintento. Una clave invalida o una peticion mal
    #: formada no se arreglan esperando, asi que esos fallan de inmediato.
    FALLOS_PASAJEROS = ("503", "502", "500", "429", "unavailable",
                        "resource_exhausted", "quota",
                        # Cortes de red del lado de Google. Faltaban: se
                        # anadieron al indexador y no aqui, y una tanda de
                        # pruebas se cayo entera con "Server disconnected"
                        # degradando al motor de reglas a la primera.
                        "disconnected", "connection", "reset by peer", "eof",
                        "deadline", "timeout",
                        # "[Errno 32] Broken pipe": el otro extremo cerro el
                        # socket mientras se escribia. Es un corte de red como
                        # los de arriba, pero no contiene ninguna de sus
                        # palabras, asi que no se reintentaba: a la primera
                        # caia al motor de reglas y la respuesta salia del
                        # buscador de palabras clave en vez de la
                        # bibliografia. Pasa sobre todo con peticiones
                        # grandes, como una pregunta que lleva una imagen.
                        "broken pipe", "errno 32", "errno 104", "epipe")

    def _generar_con_reintentos(self, conversacion, config):
        """
        Llama al modelo reintentando los fallos pasajeros.

        Args:
            conversacion: Lista de turnos (`types.Content`) a enviar.
            config: Configuracion de generacion ya construida.

        Returns:
            La respuesta del servicio.

        Raises:
            Exception: El ultimo error, si ni los reintentos lo resolvieron, o
                de inmediato si el fallo no es de los que se arreglan solos.
        """
        ultimo = None
        for espera in self.ESPERAS_REINTENTO + [None]:
            try:
                return self.cliente.models.generate_content(
                    model=self.modelo, contents=conversacion, config=config)
            except Exception as exc:
                ultimo = exc
                texto = str(exc).lower()

                # Una cuota DIARIA agotada no se arregla esperando segundos.
                # El servicio dice cuanto falta, y cuando son horas, reintentar
                # solo hace esperar al usuario para fallar igual: se midio una
                # pregunta tardando 176 segundos en dar un error seguro.
                if self._espera_larga(exc):
                    raise
                if not any(c in texto for c in self.FALLOS_PASAJEROS):
                    raise
                if espera is None:
                    raise
                time.sleep(espera)
        raise ultimo

    #: A partir de aqui, esperar no compensa: se avisa y se deja de insistir.
    SEGUNDOS_INUTIL_REINTENTAR = 120

    @classmethod
    def _espera_larga(cls, exc) -> bool:
        """
        Dice si el servicio pide esperar mas de lo razonable.

        Gemini devuelve `retryDelay` en el error de cuota. Con la cuota del
        minuto son segundos y conviene reintentar; con la del dia son horas y
        no.

        Args:
            exc: Excepcion del servicio.

        Returns:
            True si no tiene sentido reintentar ahora.
        """
        hallado = re.search(r"'retryDelay':\s*'(\d+)s'", str(exc))
        if hallado:
            return int(hallado.group(1)) > cls.SEGUNDOS_INUTIL_REINTENTAR
        # Sin dato explicito, la cuota por dia se reconoce por su nombre.
        return "perdayper" in str(exc).lower().replace("_", "")

    def _aviso_degradado(self, mensaje: str, motivo: str) -> str:
        """
        Avisa de que la respuesta NO viene del modelo, y luego la da.

        POR QUE TAN GRANDE
        ------------------
        Antes esto era una linea en cursiva, y debajo aparecia el motor de
        reglas con su formato de siempre: parecia una respuesta normal. Con el
        modelo local ya habia pasado que, a una pregunta sobre San Carlos, el
        motor filtrara por la palabra "carlos" y devolviera registros del
        Amazonas con toda la apariencia de un analisis correcto.

        Ahora que Gemini es el proveedor por defecto, este es EL modo de fallo
        de produccion, asi que el aviso va primero y bien visible.

        Args:
            mensaje: Pregunta original.
            motivo: Descripcion tecnica del fallo.

        Returns:
            El aviso seguido de la respuesta del motor de reglas.
        """
        # Un volcado de 600 caracteres de JSON no le dice nada a nadie. Si es
        # cuota agotada, que es el caso frecuente con la capa gratuita, se
        # traduce a una frase y se dice cuanto falta.
        espera = re.search(r"'retryDelay':\s*'(\d+)s'", motivo)
        por_dia = "perdayper" in motivo.lower().replace("_", "")
        if "429" in motivo or "resource_exhausted" in motivo.lower():
            if espera:
                minutos = int(espera.group(1)) // 60
                cuando = (f"{minutos // 60} h {minutos % 60} min"
                          if minutos >= 60 else f"{minutos} min")
            else:
                cuando = "un rato"
            return (
                "### Se agoto la cuota del modelo\n\n"
                f"La capa gratuita de Gemini llego a su limite"
                f"{' diario' if por_dia else ' por minuto'}. "
                f"Vuelve a intentarlo en **{cuando}**.\n\n"
                "Mientras tanto puedes usar otra clave, o activar la "
                "facturacion de la API para quitar el limite.\n\n"
                "**Lo que sigue NO es una respuesta a tu pregunta.** Lo genero "
                "el motor de reglas, que busca palabras clave y **no consulta "
                "la bibliografia**.\n\n"
                "---\n\n"
                + self._responder_con_simulador(mensaje)
            )

        return (
            "### El modelo en la nube no respondio\n\n"
            f"`{motivo[:300]}`\n\n"
            "**Lo que sigue NO es una respuesta a tu pregunta.** Lo genero el "
            "motor de reglas, que busca palabras clave en los datos del area "
            "de estudio y **no consulta la bibliografia**. Puede parecer un "
            "analisis correcto sin serlo.\n\n"
            "Vuelve a preguntar en un momento. Si el fallo persiste, revisa la "
            "conexion a internet y que `GEMINI_API_KEY` siga siendo valida.\n\n"
            "---\n\n"
            + self._responder_con_simulador(mensaje)
        )

    # --------------------------------------------------------------------------

    def _responder_con_simulador(self, mensaje: str) -> str:
        """
        Motor de reglas deterministico, usado cuando no hay LLM disponible.

        Reconoce cinco intenciones por palabras clave y delega en la misma
        herramienta que usaria el modelo. No inventa contenido: todo lo que
        afirma sale de una herramienta.

        Args:
            mensaje: Texto del usuario.

        Returns:
            Respuesta en Markdown.
        """
        texto = (mensaje or "").lower().strip()
        encabezado = f"**[Agente en modo {self.modo}]**\n\n"

        numeros = re.findall(r"[-+]?\d*\.\d+|\d+", texto)

        # --- Intencion 1: consultar una coordenada ----------------------------
        if any(p in texto for p in ("coordenad", "lat", "lon", "linea base",
                                    "línea base", "gps")):
            decimales = [float(n) for n in numeros if "." in n]
            if len(decimales) >= 2:
                lat, lon = decimales[0], decimales[1]
                # En Colombia la longitud siempre es negativa y la latitud
                # positiva: si llegan al reves, se corrige en vez de fallar.
                if lat < 0 and lon > 0:
                    lat, lon = lon, lat
                return (
                    encabezado
                    + f"Consultando la linea base para latitud {lat}, "
                      f"longitud {lon}:\n\n"
                    + consultar_linea_base(lat, lon)
                )
            return (
                encabezado
                + "Necesito dos numeros decimales para ubicar el punto. "
                  "Ejemplo: *¿que hay en la coordenada 6.1872 y -74.9922?*"
            )

        # --- Intencion 2: calcular un impacto ---------------------------------
        if any(p in texto for p in ("calcula", "conesa", "formula", "fórmula",
                                    "importancia", "impacto")):
            enteros = [int(n) for n in numeros if "." not in n and int(n) <= 12]
            if len(enteros) >= 10:
                signo = "-" if ("-" in mensaje or "negativ" in texto) else "+"
                return encabezado + calcular_impacto_conesa(
                    signo=signo,
                    i=enteros[0], EX=enteros[1], MO=enteros[2], PE=enteros[3],
                    RV=enteros[4], SI=enteros[5], AC=enteros[6], EF=enteros[7],
                    PR=enteros[8], MC=enteros[9],
                )
            return (
                encabezado
                + "Para calcular un impacto necesito los 10 parametros "
                  "numericos en orden (i, EX, MO, PE, RV, SI, AC, EF, PR, MC) "
                  "y el signo. Ejemplo:\n\n"
                  "`Calcular impacto negativo: 4 2 4 4 2 2 1 4 4 4`"
            )

        # --- Intencion 3: procesar un archivo ---------------------------------
        if any(p in texto for p in ("procesar", "lote", "matriz", "archivo",
                                    "excel")):
            ruta = re.search(r"[a-zA-Z]:\\[^\s\"']+|/[^\s\"']+\.(?:csv|xlsx)",
                             mensaje)
            if ruta:
                return (
                    encabezado
                    + f"Procesando `{ruta.group(0)}`:\n\n"
                    + procesar_lote_excel_csv(ruta.group(0))
                )
            return (
                encabezado
                + "Indicame la ruta completa del archivo, o usa la pestana "
                  "**Calculo Masivo (Excel/CSV)** para subirlo directamente."
            )

        # --- Intencion 4: estado del area de estudio --------------------------
        if any(p in texto for p in ("departamento", "area de estudio",
                                    "área de estudio", "que datos", "qué datos",
                                    "registros", "de donde", "de dónde",
                                    "que tienes", "qué tienes")):
            return encabezado + describir_area_de_estudio()

        # --- Intencion 5: biodiversidad ---------------------------------------
        if any(p in texto for p in ("biodiversidad", "especie", "buscar",
                                    "fauna", "flora", "ave", "mariposa",
                                    "planta", "insecto")):
            # Se toma como termino de busqueda la ultima palabra significativa
            # del mensaje, sin traducirla con ninguna tabla fija: la busqueda
            # de la herramienta ya recorre todas las columnas del archivo.
            palabras = [p for p in re.findall(r"[a-záéíóúñ]{4,}", texto)
                        if p not in ("buscar", "busca", "sobre", "como",
                                     "cuales", "cuáles", "hay", "para",
                                     "registros", "datos", "especies",
                                     "biodiversidad")]
            termino = palabras[-1] if palabras else ""
            return encabezado + consultar_biodiversidad_local(termino)

        # --- Respuesta por defecto: menu de ayuda -----------------------------
        return (
            encabezado
            + "Soy el copiloto ambiental del proyecto. Puedo ayudarte con:\n\n"
              "1. **Linea base de una coordenada** — *¿que hay en la "
              "coordenada 6.1872 y -74.9922?*\n"
              "2. **Calculo de Conesa** — *calcular impacto negativo: "
              "4 2 4 4 2 2 1 4 4 4*\n"
              "3. **Procesar una matriz** — *procesar archivo "
              "C:\\ruta\\matriz.xlsx*\n"
              "4. **Estado del area de estudio** — *¿que datos tienes "
              "cargados?*\n"
              "5. **Consulta de biodiversidad** — *buscar Lepidoptera*\n\n"
            + f"_{self.detalle_estado}_"
        )


if __name__ == "__main__":
    # Prueba manual: python -m agent.chatbot
    agente = ConesaAgent()
    print(f"Modo activo: {agente.modo}")
    print(f"Estado: {agente.detalle_estado}\n")
    print(agente.responder("¿que datos tienes cargados?"))


# ==============================================================================
# ESTADO DEL PROVEEDOR
# ==============================================================================
# Vivia en `agent/proveedores.py`, que se retiro al quitar el modelo local.
# La interfaz lo consulta para decir si el agente esta conectado.

def estado() -> dict:
    """
    Describe si el agente puede responder ahora mismo.

    Returns:
        Diccionario con `proveedor`, `listo`, `motivo` y `modelo`.
    """
    tiene_clave = bool(settings.api_key_gemini())
    return {
        "proveedor": "gemini",
        "listo": tiene_clave,
        "modelo": settings.modelo_llm(),
        "motivo": ("Clave de Gemini configurada." if tiene_clave else
                   "Falta GEMINI_API_KEY en el .env. Consiguela en "
                   "aistudio.google.com; sin ella el agente responde con el "
                   "motor de reglas, que no consulta la bibliografia."),
    }


# ==============================================================================
# MEMORIA DE LA CONVERSACION
# ==============================================================================
# Se anade como metodos de ConesaAgent mas abajo, en tiempo de importacion, para
# no tocar la definicion de la clase. Ver `_recordar` y `_motivo_fin`.

#: Cuantos turnos se le recuerdan al modelo.
#:
#: POR QUE NO TODOS
#: ----------------
#: Cada turno viaja entero en cada peticion: la conversacion se vuelve a
#: enviar completa cada vez. Sin tope, una charla larga acaba costando mas en
#: contexto que la propia pregunta y empuja fuera las fichas del corpus, que
#: son lo que de verdad hace falta para citar. Doce turnos cubren de sobra
#: "y esa especie?" sin que el historial le quite sitio a la bibliografia.
TURNOS_RECORDADOS = settings.turnos_recordados()

#: Cuantos turnos CON IMAGEN se siguen mandando al modelo.
#:
#: Dos, no treinta. Permite preguntar "y en la otra foto?" justo despues de
#: pegar dos, que es el caso real, sin arrastrar en cada mensaje todas las
#: capturas de la conversacion.
TURNOS_CON_IMAGEN_RECORDADOS = 2


#: Tipos de imagen que el modelo sabe mirar, por extension.
TIPOS_IMAGEN = {
    ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png",
    ".webp": "image/webp", ".heic": "image/heic", ".heif": "image/heif",
}


def _partes_de_imagen(imagenes) -> list:
    """
    Convierte lo que llegue en partes que el modelo pueda mirar.

    Acepta rutas de archivo y tuplas `(bytes, tipo)`, porque el origen cambia
    segun quien llame: la interfaz sube un archivo en memoria, una herramienta
    lo tiene en disco y Drive lo descarga.

    Args:
        imagenes: Lista de rutas o de tuplas.

    Returns:
        Lista de partes listas para enviar. Lo que no se pueda leer se omite
        en silencio: una foto ilegible no debe impedir responder al texto.
    """
    import os
    from google.genai import types

    partes = []
    for imagen in imagenes or []:
        try:
            if isinstance(imagen, (tuple, list)) and len(imagen) == 2:
                datos, tipo = imagen
            else:
                ruta = str(imagen)
                extension = os.path.splitext(ruta)[1].lower()
                tipo = TIPOS_IMAGEN.get(extension)
                if not tipo:
                    continue
                with open(ruta, "rb") as f:
                    datos = f.read()
            partes.append(types.Part.from_bytes(data=datos, mime_type=tipo))
        except Exception:
            continue
    return partes


# ==============================================================================
# LAS BASES DE UNA RESPUESTA
# ==============================================================================
# El prompt pide tres apartados: Respuesta, Bases y Notas. El prompt es una
# peticion, no una garantia: un modelo cumple un formato las primeras veces y
# lo va soltando segun se alarga la conversacion.
#
# Esto es el respaldo. No reescribe lo que el modelo decidio decir; solo se
# asegura de que el apartado de Bases EXISTA, y cuando falta lo construye con
# las fuentes que las herramientas devolvieron DE VERDAD en ese turno. Nunca
# con otras: de eso se trata precisamente.

#: Como empieza la linea que las herramientas ponen debajo de cada fragmento.
MARCA_FUENTE = "Fuente:"

#: Titulo del apartado, tal como lo pide el prompt.
TITULO_BASES = "### Bases"

#: Cuantas fuentes distintas se listan como mucho.
MAXIMO_FUENTES = 8


def _fuentes_del_turno(respuesta) -> tuple:
    """
    Saca de la respuesta que documentos devolvieron las herramientas.

    POR QUE DE AQUI Y NO DEL TEXTO DEL MODELO
    -----------------------------------------
    Porque el texto del modelo es justo lo que hay que poder comprobar. El
    historial de llamadas automaticas trae lo que cada herramienta contesto,
    palabra por palabra: esa es la unica lista de fuentes que no puede estar
    inventada.

    Args:
        respuesta: Lo que devolvio `generate_content`.

    Returns:
        Tupla `(fuentes, herramientas)`. `fuentes` son las citas encontradas,
        sin repetir y en el orden en que aparecieron; `herramientas`, los
        nombres de las que se llamaron.
    """
    historial = getattr(respuesta, "automatic_function_calling_history", None)
    fuentes, herramientas = [], []
    for turno in historial or []:
        for parte in (getattr(turno, "parts", None) or []):
            llamada = getattr(parte, "function_call", None)
            if llamada is not None and llamada.name not in herramientas:
                herramientas.append(llamada.name)

            devuelto = getattr(parte, "function_response", None)
            if devuelto is None:
                continue
            texto = str(getattr(devuelto, "response", "") or "")
            # El contenido viaja como un diccionario convertido a texto, con
            # los saltos de linea escapados. Se deshace el escape para poder
            # separar por lineas, que es como vienen las citas.
            texto = texto.replace("\\n", "\n")
            for linea in texto.split("\n"):
                limpia = linea.strip()
                if not limpia.startswith(MARCA_FUENTE):
                    continue
                # El contenido viene dentro de un diccionario convertido a
                # texto, asi que la ultima cita arrastra el cierre: comillas
                # y llave. Se quitan por el final, nunca el corchete, que es
                # parte de la cita: "Guia.pdf, p. 60 [3_HOSPEDERAS]".
                cita = limpia[len(MARCA_FUENTE):].strip()
                cita = cita.strip("'\" ").rstrip("}'\" ,")
                if cita and cita not in fuentes:
                    fuentes.append(cita)
    return fuentes, herramientas


def _sin_encabezado_suelto(texto: str) -> str:
    """
    Quita el titulo de "Respuesta" cuando va solo, sin los demas apartados.

    Args:
        texto: La respuesta del modelo.

    Returns:
        El mismo texto, en prosa, si el encabezado era el unico.
    """
    marca = "### Respuesta"
    sin_espacios = texto.lstrip()
    if not sin_espacios.startswith(marca):
        return texto
    return sin_espacios[len(marca):].lstrip(chr(10)).lstrip()


def _asegurar_bases(texto: str, fuentes: list, herramientas: list) -> str:
    """
    Anade el apartado de Bases cuando el modelo se lo dejo.

    No toca el que ya exista. Si el modelo escribio sus bases, son las suyas:
    corregirselas desde aqui seria mezclar dos voces en la misma respuesta y
    dejar al usuario sin saber cual leer.

    Args:
        texto: La respuesta del modelo.
        fuentes: Citas que devolvieron las herramientas en este turno.
        herramientas: Nombres de las herramientas llamadas.

    Returns:
        La respuesta, con su apartado de Bases.
    """
    if not texto:
        return texto

    # Si ya tiene Bases, asegurar que fuentes devueltas por herramientas para especies similares
    # no queden por fuera de la seccion
    if TITULO_BASES.lower() in texto.lower():
        if fuentes:
            fuentes_faltantes = []
            for f in fuentes:
                doc_nom = f.split(",")[0].strip() if "," in f else f
                if doc_nom.lower() not in texto.lower():
                    fuentes_faltantes.append(f)
            if fuentes_faltantes:
                import re
                lineas_extra = ["  - **Fuentes de Drive para especies similares / candidatas**:"]
                for f in fuentes_faltantes[:5]:
                    lineas_extra.append(f"    * {f}")
                bloque_extra = "\n".join(lineas_extra)
                if "### notas" in texto.lower():
                    partes = re.split(r"(###\s*notas)", texto, flags=re.IGNORECASE)
                    texto = partes[0].rstrip() + "\n" + bloque_extra + "\n\n" + "".join(partes[1:])
                else:
                    texto = texto.rstrip() + "\n" + bloque_extra
        return texto

    # SIN HERRAMIENTAS NO SE INVENTA UN APARTADO DE FUENTES
    # -----------------------------------------------------
    # Si el modelo no consulto nada, no hay nada que listar, y lo que salia
    # era un aviso de relleno. Se vio en un "hola": debajo del saludo
    # aparecia "conviene comprobarlo antes de usarlo en el informe", que no
    # significa nada ahi y ensena a saltarse el apartado.
    #
    # Para las preguntas que SI afirman algo no hay riesgo de perderselo: la
    # regla 8 del prompt obliga a consultar antes de responder, asi que esas
    # llegan aqui con herramientas detras.
    #
    # De paso se quita el encabezado suelto. "### Respuesta" como unico
    # titulo de una conversacion normal es media estructura: o estan los
    # apartados o no esta ninguno.
    if not herramientas:
        return _sin_encabezado_suelto(texto)

    lineas = ["", TITULO_BASES]
    if fuentes:
        for cita in fuentes[:MAXIMO_FUENTES]:
            lineas.append(f"- **{cita}**")
        if len(fuentes) > MAXIMO_FUENTES:
            lineas.append(f"- …y {len(fuentes) - MAXIMO_FUENTES} fuente(s) "
                          f"mas del mismo grupo.")
    elif herramientas:
        # Sin citas pero con herramientas: la respuesta sale de mirar el
        # sistema, no de leer documentos. Es una base valida y hay que
        # decirla asi, no dejar el hueco.
        lineas.append(
            "- No sale de la bibliografia: sale de consultar el propio "
            "sistema (" + ", ".join(herramientas[:6]) + ").")
    else:
        lineas.append(
            "- No sale de ningun documento ni de ninguna herramienta: es "
            "razonamiento sobre lo que se dijo en esta conversacion. "
            "Conviene comprobarlo antes de usarlo en el informe.")
    return texto.rstrip() + "\n" + "\n".join(lineas)


def _asegurar_notas(texto: str, mensaje: str = "") -> str:
    """
    Asegura que el apartado de Notas / Advertencias este presente en respuestas
    relacionadas con identificacion de mariposas, fotografias o especies similares.
    """
    if not texto:
        return texto

    texto_l = texto.lower()
    if "### notas" in texto_l or "### advertencias" in texto_l or "## notas" in texto_l or "## advertencias" in texto_l:
        return texto

    palabras_clave = (
        "mariposa", "especie", "lepidoptera", "foto", "imagen", "observacion",
        "similitud", "similares", "candidata", "candidatas", "oruga", "pupa",
        "crisalida", "huevo", "ala", "alas", "genero", "familia"
    )
    es_biologico = any(p in texto_l or p in mensaje.lower() for p in palabras_clave)
    if not es_biologico:
        return texto

    bloque_notas = (
        "\n\n### Notas\n"
        "- **Advertencias sobre similitudes y caracteres diagnosticos:** La identificacion basada en fotografias o "
        "observaciones de campo es de caracter probable/tentativo. Diversas especies de Lepidoptera presentan "
        "patrones mimeticos convergentes (mimetismo mulleriano/batesiano) o variabilidad individual. Para diferenciar "
        "con certeza absoluta la especie observada de sus candidatas similares es necesario cotejar la cara ventral "
        "y dorsal de las alas, los patrones de venacion alar y, en especies cripticas, el examen microscopico "
        "de la genitalia.\n"
        "- **Alcance territorial y conservacion:** Los registros citados corresponden al municipio de San Carlos "
        "(Antioquia) y a los catalogos y normativas indexados en el repositorio de Drive del proyecto."
    )
    return texto.rstrip() + bloque_notas


def _recordar(self, papel: str, texto: str, imagenes=None) -> None:
    """
    Anade un turno al historial que se le envia al modelo.

    Args:
        papel: "user" o "model", que son los que entiende la API.
        texto: Contenido del turno.
        imagenes: Imagenes que acompanan al turno, si las hay.
    """
    from google.genai import types

    if not hasattr(self, "_historial") or self._historial is None:
        self._historial = []
    partes = [types.Part(text=texto)] + _partes_de_imagen(imagenes)
    self._historial.append(types.Content(role=papel, parts=partes))

    # LAS IMAGENES VIEJAS SE SUELTAN; EL TEXTO SE QUEDA
    # -------------------------------------------------
    # El historial guarda treinta turnos, y las imagenes se quedaban dentro
    # de todos ellos. Cada pregunta nueva volvia a subir TODAS las capturas
    # pegadas en la conversacion: con cuatro capturas de pantalla de 4 MB,
    # el quinto mensaje mandaba 16 MB al modelo para preguntar "y esto?".
    #
    # Eso no es solo lento. Pasado cierto tamano la llamada falla o el
    # modelo responde cualquier cosa, y desde fuera parece que el agente
    # "dejo de poder con las imagenes" justo cuando mas se usaban.
    #
    # Lo que la imagen decia ya esta en la respuesta que el agente dio
    # sobre ella, que SI se conserva. Se mantienen las de los ultimos
    # turnos para que un "y en la otra foto?" siga funcionando.
    con_imagen = [i for i, t in enumerate(self._historial)
                  if len(t.parts) > 1]
    for i in con_imagen[:-TURNOS_CON_IMAGEN_RECORDADOS]:
        turno = self._historial[i]
        self._historial[i] = types.Content(
            role=turno.role,
            parts=[p for p in turno.parts if getattr(p, "text", None)])

    # Se recorta por el principio, nunca por el final: lo reciente es lo que
    # da sentido a un "y eso?".
    if len(self._historial) > TURNOS_RECORDADOS * 2:
        self._historial = self._historial[-TURNOS_RECORDADOS * 2:]


def _motivo_fin(self, respuesta) -> str:
    """
    Extrae por que termino el modelo, sin reventar si falta el dato.

    Args:
        respuesta: Respuesta del servicio.

    Returns:
        El nombre del motivo ("STOP", "SAFETY", "MAX_TOKENS"...) o cadena
        vacia si no se pudo leer.
    """
    try:
        candidato = (respuesta.candidates or [None])[0]
        if candidato is not None and candidato.finish_reason is not None:
            return str(candidato.finish_reason).rsplit(".", 1)[-1]
    except Exception:
        pass
    return ""


def olvidar(self) -> None:
    """Vacia la memoria de la conversacion, sin tocar el corpus."""
    self._historial = []


ConesaAgent._recordar = _recordar
ConesaAgent._motivo_fin = _motivo_fin
ConesaAgent.olvidar = olvidar


def _guardar_recuerdo_de_fondo(self, mensaje: str) -> None:
    """
    Archiva el recuerdo episodico sin hacer esperar a quien pregunto.

    POR QUE EN OTRO HILO
    --------------------
    Guardar un recuerdo embebe el texto y lo escribe en Qdrant: se midio en
    2,80 segundos. Hacerlo antes de devolver la respuesta se los sumaba a cada
    pregunta, y el usuario los esperaba mirando "Pensando..." cuando la
    respuesta ya estaba lista.

    Si falla, no se avisa: es memoria auxiliar, y perder un recuerdo no debe
    estropear una respuesta que ya es correcta.

    Args:
        mensaje: Pregunta original, que es lo que se archiva.
    """
    def _trabajo():
        """Guarda el recuerdo sin hacer esperar a quien pregunto."""
        try:
            from core import memoria
            sesion = getattr(self, "_sesion_id", "default")
            memoria.guardar_recuerdo(
                f"En {sesion}, el usuario consulto o indico: {mensaje}",
                sesion_id=sesion)
        except Exception:
            pass

    try:
        threading.Thread(target=_trabajo, daemon=True).start()
    except Exception:
        pass


ConesaAgent._guardar_recuerdo_de_fondo = _guardar_recuerdo_de_fondo
