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
        if api_key is None:
            api_key = settings.api_key_gemini() or ""
        self.api_key = str(api_key).strip()
        self.modelo = modelo or settings.modelo_llm()
        self.cliente = None
        self.modo = "Simulador Offline (Reglas)"
        self.detalle_estado = "Sin API Key: el agente responde con reglas locales."

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

    def responder(self, mensaje_usuario: str) -> str:
        """
        Punto de entrada unico del agente.

        Args:
            mensaje_usuario: Texto escrito por la persona.

        Returns:
            Respuesta del agente en formato Markdown.
        """
        if self.modo.startswith("LLM") and self.cliente is not None:
            return self._responder_con_llm(mensaje_usuario)
        return self._responder_con_simulador(mensaje_usuario)

    # --------------------------------------------------------------------------

    def _responder_con_llm(self, mensaje: str) -> str:
        """
        Envia el mensaje al modelo con el prompt y las herramientas del sistema.

        El prompt de sistema se CONSTRUYE en cada llamada leyendo el estado real
        del proyecto, para que el modelo hable del area de estudio que hay
        cargada ahora y no de una que estaba escrita en el codigo.

        Args:
            mensaje: Texto del usuario.

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

            respuesta = self.cliente.models.generate_content(
                model=self.modelo,
                contents=mensaje,
                config=types.GenerateContentConfig(
                    system_instruction=construir_system_prompt(contexto),
                    tools=HERRAMIENTAS,
                ),
            )
            return respuesta.text

        except Exception as exc:
            return (
                f"*(Fallo la consulta al modelo: {exc}. Se responde con el "
                f"motor de reglas local.)*\n\n"
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
