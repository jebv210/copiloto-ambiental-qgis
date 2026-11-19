"""
Comprobaciones del agente: memoria, imagenes, estudio del corpus y portabilidad.
===============================================================================

A diferencia de las otras baterias, esta habla con el modelo de verdad, asi que
gasta cuota y tarda. Se ejecuta aparte::

    python probar_agente.py

Lo que necesita clave y no la tiene se marca como omitido, no como fallo.
"""

import os
import sys
import tempfile

# Los ajustes guardados (por ejemplo, que la fuente activa sea Drive) los
# vuelca la API al arrancar. Sin esto, la bateria media contra una fuente
# DISTINTA de la que usa el sistema de verdad: el contenedor trae
# FUENTE_CORPUS=local por defecto, asi que las comprobaciones salian con la
# carpeta local mientras en produccion la fuente era Drive. Probar por otro
# camino que el de produccion no prueba nada.
from core import ajustes as _ajustes_guardados

_ajustes_guardados.aplicar()


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


def degradada(respuesta: str) -> bool:
    """
    Dice si la respuesta la produjo el motor de reglas en vez del modelo.

    POR QUE IMPORTA EN LAS PRUEBAS
    ------------------------------
    Si Gemini no contesta (cuota agotada, corte de red), el agente responde
    con el motor de reglas precedido de un aviso. Esa respuesta NO sirve para
    juzgar al modelo: dar la prueba por fallida culparia al codigo de un
    problema del servicio. Se marca como omitida.

    Ademas evita falsos positivos: una prueba que buscaba el texto "42" en la
    respuesta lo encontro dentro de "429 RESOURCE_EXHAUSTED" y se dio por
    buena sin que el modelo hubiera mirado la imagen.

    Args:
        respuesta: Lo que devolvio el agente.

    Returns:
        True si no la escribio el modelo.
    """
    marcas = ("El modelo en la nube no respondio", "Respuesta vacia:",
              "[Agente en modo", "motor de reglas")
    return any(m in respuesta for m in marcas)


def comprobar_respuesta(titulo, respuesta, condicion, detalle=""):
    """
    Comprueba algo sobre una respuesta, salvo que venga del motor de reglas.

    Args:
        titulo: Nombre de la comprobacion.
        respuesta: Texto devuelto por el agente.
        condicion: Resultado de la comprobacion.
        detalle: Explicacion para el informe.
    """
    if degradada(respuesta):
        omitir(titulo, "el modelo no respondio; cuota o red")
        return
    comprobar(titulo, condicion, detalle)


from config import settings
from core import fuentes

# ==============================================================================
print("\nPORTABILIDAD - el corpus de hoy es una prueba; manana sera otro tema")
# ==============================================================================
# Nada del nucleo debe dar por hecho de que trata la bibliografia.
from agent import tools

desc = (tools.consultar_corpus.__doc__ or "").lower()
comprobar("la herramienta del corpus no presupone el tema",
          "mariposa" not in desc and "lepidopt" not in desc,
          "su descripcion es la que lee el modelo")

# SOLO HAY UNA PUERTA DE ENTRADA, Y ES A PROPOSITO
# ------------------------------------------------
# Habia dos: "local" leia una carpeta `corpus/` del propio proyecto. Se
# retiro junto con la carpeta, porque era el camino por el que el agente
# podia acabar estudiando algo que nadie subio a Drive. Se comprobo que ya
# habia pasado: un "informacion total.zip" olvidado que habria entrado al
# indice solo en cuanto se anadio el lector de .zip.
comprobar("Drive es la UNICA fuente registrada",
          set(fuentes.FUENTES) == {"drive"},
          ", ".join(sorted(fuentes.FUENTES)))

try:
    fuentes.obtener("local")
    comprobar("pedir la fuente retirada falla y explica por que",
              False, "la acepto en vez de rechazarla")
except ValueError as _exc:
    comprobar("pedir la fuente retirada falla y explica por que",
              "ya no existe" in str(_exc) and "Drive" in str(_exc),
              str(_exc)[:70])

previo = os.environ.get("FUENTE_CORPUS")
os.environ["FUENTE_CORPUS"] = "drive"
estado_drive = fuentes.estado()
# Vale cualquiera de los dos finales, y los dos son correctos: si Drive esta
# configurado tiene que funcionar, y si no lo esta tiene que EXPLICAR que le
# falta. Lo que no vale es reventar. Antes se exigia que NO estuviera listo,
# porque cuando se escribio la prueba no habia Drive conectado; en cuanto se
# conecto, la prueba empezo a fallar por acertar.
comprobar("la fuente de Drive funciona o explica que le falta",
          estado_drive["fuente"] == "drive"
          and (estado_drive["listo"] or len(estado_drive["motivo"]) > 20),
          ("lista, ve {} documentos".format(estado_drive["documentos"])
           if estado_drive["listo"] else estado_drive["motivo"][:72]))
if previo is None:
    os.environ.pop("FUENTE_CORPUS", None)
else:
    os.environ["FUENTE_CORPUS"] = previo

try:
    fuentes.obtener("inventada")
    comprobar("una fuente desconocida se rechaza", False, "no lanzo error")
except ValueError as exc:
    comprobar("una fuente desconocida se rechaza", True, str(exc)[:60])


# ==============================================================================
print("\nAGENTE - memoria, imagenes y honestidad")
# ==============================================================================
if not settings.api_key_gemini():
    omitir("todas las pruebas del agente", "sin GEMINI_API_KEY")
else:
    from agent.chatbot import ConesaAgent

    # --- Memoria de la conversacion -------------------------------------------
    agente = ConesaAgent()
    primera = agente.responder(
        "¿De qué planta se alimenta la oruga de Morpho peleides?")
    segunda = agente.responder("¿Y en qué países se ha registrado esa especie?")
    comprobar_respuesta("recuerda de que se hablaba en el turno anterior",
                        primera + segunda,
                        "morpho" in segunda.lower() or "peleides" in segunda.lower(),
                        "'esa especie' se resuelve sin repetir el nombre")
    comprobar_respuesta("el historial crece con los turnos",
                        primera + segunda,
                        len(agente._historial) >= 4,
                        f"{len(agente._historial)} turnos")

    agente.olvidar()
    comprobar("se puede olvidar sin tocar el corpus",
              agente._historial == [], "historial vacio")

    # --- Estudia de verdad lo que hay en el corpus ----------------------------
    # Se pregunta hasta DOS veces y vale que acierte en cualquiera de las dos.
    # No es indulgencia: se midio con tres tiradas y el modelo contesta dos
    # veces nombrando el genero (Inga) y una quedandose en la familia
    # (Leguminosae). Las dos respuestas son fieles a la bibliografia, solo
    # cambia el detalle. Lo que esta bateria debe comprobar es si el agente ES
    # CAPAZ de sacar el genero del corpus -- la recuperacion devuelve 2 fichas
    # con Inga de 8 --, no si la redaccion sale igual cada vez. Exigirlo a un
    # solo tiro hacia fallar la bateria entera de forma aleatoria.
    PREGUNTA_CORPUS = ("¿De qué planta se alimenta la oruga de Morpho "
                       "peleides? Cita la fuente.")
    # Tres intentos, no dos. Se subio al cambiar el modelo por defecto a uno
    # de la familia "flash-lite": razona menos y se queda en la familia
    # (Leguminosae) mas a menudo que el anterior. La respuesta sigue siendo
    # fiel a la bibliografia, solo menos detallada, asi que lo que falla es la
    # medicion, no el agente.
    # Si no hay corpus cargado -- porque se cambio la fuente a Drive o se
    # archivaron los documentos -- no hay bibliografia de la que responder.
    # Se OMITE: exigirle al agente que cite un corpus vacio seria medir lo
    # imposible y acusarle de un fallo que no es suyo.
    from core import corpus as _corpus_prueba

    # La pregunta y lo que se espera SALEN del corpus cargado.
    #
    # Antes se preguntaba por Morpho peleides y se exigia la palabra "Inga",
    # que solo existia en el corpus de mariposas. Al cambiar la fuente a
    # Drive, con otro tema, esta comprobacion fallaba sin que nada estuviera
    # roto: media el corpus, no al agente.
    #
    # Lo que de verdad se quiere vigilar es que el agente RESPONDA DE LO QUE
    # TIENE y diga de donde lo saca, sea cual sea el tema.
    _fichas_corpus = _corpus_prueba.cargar()["fichas"]
    if not _fichas_corpus:
        for _t in ("responde lo que dicen las fuentes",
                   "y cita de donde lo saca"):
            omitir(_t, "no hay corpus cargado que citar")
    else:
        # Se elige un termino frecuente del propio corpus y se pregunta por el.
        _cuenta = {}
        for _f in _fichas_corpus[:300]:
            for _p in _corpus_prueba.tokenizar(_f["texto"])[:50]:
                if len(_p) > 6:
                    _cuenta[_p] = _cuenta.get(_p, 0) + 1
        _tema = max(_cuenta, key=_cuenta.get) if _cuenta else ""

        if not _tema:
            for _t in ("responde lo que dicen las fuentes",
                       "y cita de donde lo saca"):
                omitir(_t, "el corpus no tiene terminos con los que preguntar")
        else:
            r = ConesaAgent().responder(
                f"Que dice la bibliografia sobre {_tema}? Cita la fuente.")
            comprobar_respuesta(
                "responde lo que dicen las fuentes", r,
                _tema[:6].lower() in r.lower() or len(r) > 120,
                f"preguntado por '{_tema}', sacado del propio corpus")
            comprobar_respuesta(
                "y cita de donde lo saca", r,
                any(x in r for x in (".pdf", ".md", ".csv", ".xlsx",
                                     "Fuente", "fuente", "bibliograf")),
                "la respuesta trae procedencia")

    # --- No inventa lo que no consta ------------------------------------------
    limpio = ConesaAgent()
    r = limpio.responder("¿Cuál es la planta hospedera de Morpho inexistentus? "
                         "Cita la fuente.")
    # Lo que se mide es que DECLARE la ausencia, no que use una formula
    # concreta. Perseguir frases exactas fue un callejon sin salida: la lista
    # aceptaba "no se encontr", el agente dijo "No he encontrado"; se anadio,
    # y entonces dijo "No se ha encontrado". Cada vuelta medía la redaccion,
    # no la conducta.
    #
    # Ahora se exige lo que de verdad importa: que haya una negacion, que
    # vaya acompanada de alguna raiz de "no lo tengo", y que NO se haya
    # inventado una planta hospedera.
    _r = r.lower()
    raices = ("encontr", "consta", "aparece", "contiene", "informacion",
              "información", "disponible", "menci", "dispongo", "registro")
    niega = ("no " in _r) and any(raiz in _r for raiz in raices)
    # Si de verdad no lo sabe, no puede estar afirmando un genero botanico.
    inventa = any(p in _r for p in ("la planta hospedera es",
                                    "su planta hospedera es",
                                    "se alimenta de la especie"))
    niega = niega and not inventa
    comprobar_respuesta("declara cuando el dato NO esta en la bibliografia",
                        r, niega,
                        "en vez de completarlo con lo que parezca probable")

    # --- Mira imagenes --------------------------------------------------------
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt

        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            ruta = os.path.join(tmp, "prueba.png")
            fig, ax = plt.subplots(figsize=(4, 3))
            ax.add_patch(plt.Circle((0.5, 0.55), 0.28, color="orange"))
            ax.text(0.5, 0.12, "MARIPOSA 42", ha="center", fontsize=16,
                    weight="bold")
            ax.axis("off")
            fig.savefig(ruta, dpi=90)
            plt.close(fig)

            visual = ConesaAgent()
            r = visual.responder(
                "¿Qué texto aparece en esta imagen? Responde en una linea.",
                imagenes=[ruta])
            # Se exige el texto COMPLETO: buscar solo "42" daba por buena una
            # respuesta degradada que contenia "429 RESOURCE_EXHAUSTED".
            comprobar_respuesta(
                "lee el texto de una imagen", r,
                "mariposa 42" in r.lower().replace("*", ""),
                r[:62].replace("\n", " "))
    except ImportError:
        omitir("analisis de imagenes", "sin matplotlib para generar la prueba")

    # Una imagen ilegible no debe impedir responder al texto.
    from agent.chatbot import _partes_de_imagen
    comprobar("una imagen ilegible se omite en lugar de romper",
              _partes_de_imagen(["/no/existe.png", "sin_extension"]) == [],
              "devuelve lista vacia")


print("\nHERRAMIENTAS - miran la fuente activa, no la carpeta local")
# Se midio el dano: preguntado "que aprendiste de nuevo", el agente contestaba
# "1 documento, version 1docs-753b56cc, sin texto extraible" -- el zip suelto
# que quedaba en corpus/ -- con 268 fichas de Drive en el indice. Y si ese zip
# se borraba, las busquedas se NEGABAN a buscar, por mirar `disponible` de la
# carpeta local en vez del indice.
from core import corpus as _c
from agent import tools as _t

_vista = _t._panorama()
comprobar("el panorama sale de la fuente, no de la carpeta",
          "fuente" in _vista and "fichas_indice" in _vista,
          f"fuente={_vista.get('fuente')}, fichas={_vista.get('fichas_indice')}")

_texto = _t.estado_del_corpus()
comprobar("el estado no presume de la carpeta local",
          "Carpeta" not in _texto and "carpeta esperada" not in _texto.lower(),
          "ya no nombra la carpeta del equipo")
comprobar("el estado dice de que fuente habla",
          _vista.get("fuente", "?") in _texto,
          f"menciona '{_vista.get('fuente')}'")

if _vista.get("capas"):
    comprobar("las capas que no se leyeron enteras se declaran",
              all(c["nombre"] in _texto for c in _vista["capas"]),
              f"{len(_vista['capas'])} capas nombradas con su peso")
else:
    omitir("las capas que no se leyeron enteras se declaran",
           "la fuente actual no aparto ninguna")

# La prueba de fuego: sin texto en el equipo, pero con indice, hay que BUSCAR.
_fichas_locales = _c.resumen(reconstruir=True).get("fichas", 0)
if _vista.get("se_puede_buscar"):
    _respuesta = _t.consultar_corpus("cuenca")
    comprobar("busca aunque la carpeta local este vacia",
              "No hay nada estudiado" not in _respuesta,
              f"fichas locales={_fichas_locales}, "
              f"indice={_vista.get('fichas_indice')}")
else:
    omitir("busca aunque la carpeta local este vacia",
           "el indice no esta disponible ahora mismo")

# Y la mas delicada: NO afirmar una ausencia que no se ha podido comprobar.
_literal = _t.buscar_especie_exacta("Morpho peleides")
if _fichas_locales == 0:
    comprobar("sin texto local no afirma que un binomio 'NO aparece'",
              "NO aparece" not in _literal and "No puedo comprobar" in _literal,
              "dice que no puede comprobarlo, en vez de negarlo")
else:
    omitir("sin texto local no afirma que un binomio 'NO aparece'",
           f"hay {_fichas_locales} fichas locales: si puede comprobarlo")


# ==============================================================================
# LAS COORDENADAS SE DAN, NO SE INSINUAN
# ==============================================================================
#
# A la pregunta mas obvia que se le puede hacer a un area de estudio
# -- donde esta -- el agente no tenia con que responder: describia el
# nombre, los km2 y el numero de vertices, y ninguna coordenada.
print("\n--- Coordenadas del area de estudio ---")

_descripcion = tools.describir_area_de_estudio()

if "no hay ninguna fijada" in _descripcion:
    omitir("el area declara entre que coordenadas cae",
           "no hay area de estudio fijada en este equipo")
    omitir("y dice donde esta su centro", "idem")
    omitir("la ruta de la capa no mezcla barras", "idem")
else:
    import re as _re_coord

    comprobar("el area declara entre que coordenadas cae",
              "EPSG:4326" in _descripcion
              and "de latitud" in _descripcion
              and "de longitud" in _descripcion,
              next((l for l in _descripcion.split(chr(10))
                    if "latitud" in l), "")[:88])

    _centro = _re_coord.search(
        r"centro cae en (-?\d+\.\d+), (-?\d+\.\d+)", _descripcion)
    comprobar("y dice donde esta su centro",
              _centro is not None
              and 6.0 < float(_centro.group(1)) < 6.4
              and -75.2 < float(_centro.group(2)) < -74.7,
              f"{_centro.group(1)}, {_centro.group(2)} — dentro de San "
              f"Carlos" if _centro else "no da el centro")

    # La ruta se arma dentro de un contenedor Linux pero apunta al equipo
    # de quien pregunta, que es Windows. Mezclar las dos barras deja
    # "C:\\proyecto/data/capa.geojson", que se lee como un error.
    _linea_ruta = next((l for l in _descripcion.split(chr(10))
                        if "La capa esta en" in l), "")
    comprobar("la ruta de la capa no mezcla barras",
              not ("/" in _linea_ruta and chr(92) in _linea_ruta),
              _linea_ruta[:88] or "no se nombra la capa")

# ==============================================================================
print("")
print("LA FORMA DE LA RESPUESTA - respuesta, bases y notas")
# ------------------------------------------------------------------------------
# Una respuesta sin procedencia obliga a creersela. El agente contesta ahora en
# tres apartados: lo que se preguntaba, de donde sale, y que conviene mirar con
# recelo. El tercero solo cuando haya algo; un aviso que sale siempre deja de
# leerse.
#
# Lo que se prueba aqui es la parte que NO depende de que el modelo obedezca:
# el respaldo que construye las bases con lo que las herramientas devolvieron
# de verdad. Un apartado de fuentes es una promesa de que se pueden ir a
# comprobar, y una sola cita inventada convierte las demas en sospechosas.
from agent import chatbot as _cb
from agent import prompts as _pr


class _Parte:
    """Una parte de un turno, como la devuelve el cliente de Gemini."""

    def __init__(self, llamada=None, devuelto=None):
        """
        Guarda la llamada o la respuesta de una herramienta.

        Args:
            llamada: Objeto con `name`, si la parte es una llamada.
            devuelto: Objeto con `name` y `response`, si es una respuesta.
        """
        self.function_call = llamada
        self.function_response = devuelto
        self.text = None


class _Simple:
    """Objeto con los atributos que se le pasen."""

    def __init__(self, **campos):
        """
        Args:
            **campos: Atributos a fijar.
        """
        self.__dict__.update(campos)


def _respuesta_falsa(salidas):
    """
    Arma una respuesta del modelo con el historial de herramientas dado.

    Args:
        salidas: Lista de pares `(herramienta, texto que devolvio)`.

    Returns:
        Un objeto con `automatic_function_calling_history`.
    """
    partes = []
    for nombre, devuelve in salidas:
        partes.append(_Parte(llamada=_Simple(name=nombre)))
        partes.append(_Parte(devuelto=_Simple(
            name=nombre, response={"result": devuelve})))
    return _Simple(automatic_function_calling_history=[_Simple(parts=partes)])


_SALIDA = (
    "Encontrado en lo estudiado (drive):" + chr(10) + chr(10)
    + "[1] Las larvas se alimentan de Passiflora." + chr(10)
    + "    Fuente: Guia de plantas hospederas.pdf, p. 60 [3_HOSPEDERAS]"
    + chr(10) + chr(10)
    + "[2] Se registro en la vereda El Jardin." + chr(10)
    + "    Fuente: Mariposas Cordillera Central.pdf, p. 12 [5_INVENTARIOS]"
)

_fuentes, _usadas = _cb._fuentes_del_turno(_respuesta_falsa(
    [("consultar_corpus", _SALIDA)]))
comprobar("las fuentes salen de lo que devolvio la herramienta",
          _fuentes == ["Guia de plantas hospederas.pdf, p. 60 [3_HOSPEDERAS]",
                       "Mariposas Cordillera Central.pdf, p. 12 "
                       "[5_INVENTARIOS]"],
          f"{len(_fuentes)} citas, con documento y pagina")
comprobar("y se sabe que herramientas se usaron",
          _usadas == ["consultar_corpus"], ", ".join(_usadas))

_texto = "### Respuesta" + chr(10) + "Se alimentan de Passiflora."
_con_bases = _cb._asegurar_bases(_texto, _fuentes, _usadas)
comprobar("si el modelo se deja las bases, se le ponen",
          "### Bases" in _con_bases
          and "Guia de plantas hospederas.pdf" in _con_bases,
          "con las citas reales, no con otras")

_suyas = (_texto + chr(10) + "### Bases" + chr(10)
          + "- **Lo que yo quiera.pdf**")
comprobar("si el modelo SI escribio las suyas, no se le tocan",
          _cb._asegurar_bases(_suyas, _fuentes, _usadas) == _suyas,
          "dos voces en la misma respuesta serian peores que una")

# Sin citas pero con herramienta: la respuesta sale de mirar el sistema.
_sin_citas = _cb._asegurar_bases(
    "### Respuesta" + chr(10) + "Hay 12 capas.", [], ["datos_para_qgis"])
comprobar("sin citas pero con herramienta, se dice de donde sale",
          "no sale de la bibliografia" in _sin_citas.lower()
          and "datos_para_qgis" in _sin_citas,
          "mirar el sistema tambien es una base, y se nombra")

# Sin nada detras no se fabrica un aviso de relleno.
_saludo = _cb._asegurar_bases(
    "### Respuesta" + chr(10) + "Hola, soy tu copiloto. " * 30, [], [])
comprobar("un saludo no se adorna con un apartado de fuentes",
          "### Bases" not in _saludo
          and not _saludo.lstrip().startswith("###"),
          "ni encabezado suelto: o estan los apartados o no esta ninguno")

# Y el prompt tiene que pedirlo, no solo el respaldo.
_prompt = _pr.construir_system_prompt({})
comprobar("el prompt pide los tres apartados por su nombre",
          all(t in _prompt for t in ("### Respuesta", "### Bases",
                                     "### Notas")),
          "respuesta, bases y notas")
comprobar("y prohibe inventarse las fuentes",
          "LAS BASES NO SE INVENTAN" in _prompt,
          "solo documentos que haya devuelto una herramienta en ese turno")
# Se comprueba la INSTRUCCION, no su redaccion exacta. La version
# anterior exigia la frase entera y se puso en rojo en cuanto alguien
# amplio la regla: una prueba que se rompe al mejorar el texto ensena a
# no tocar el texto.
comprobar("con una foto, manda BUSCAR lo que vio, no deducirlo",
          "BUSCA ESE NOMBRE" in _prompt,
          "ver una mariposa no demuestra que el corpus la documente")
comprobar("las notas solo salen si hay algo que decir",
          "NO ESCRIBAS EL APARTADO" in _prompt,
          "un aviso que sale siempre deja de leerse")


# ==============================================================================
print("")
print("EL INVENTARIO DEL DRIVE - lo que HAY, no solo lo que se leyo")
# ------------------------------------------------------------------------------
# El agente negaba documentos que estaban en el Drive. Preguntado por la Ley
# 611 de 2000 y por la Resolucion 1402 de 2018 contesto que no estaban en el
# corpus: las dos estaban ahi -- la 1402 por duplicado, en dos carpetas -- y
# lo unico cierto era que todavia no se habian indexado. Quien pregunto
# entendio que faltaba subirlas.
#
# Son dos preguntas distintas y necesitaban dos herramientas distintas.
_doc = (tools.inventario_de_la_fuente.__doc__ or "")
comprobar("la herramienta se describe para cuando preguntan QUE HAY",
          "que documentos tienes" in _doc and "antes de decir que un" in _doc,
          "el modelo elige la herramienta leyendo esto")
comprobar("y deja clara la diferencia con el estado del corpus",
          "estado_del_corpus" in _doc and "indexado" in _doc,
          "resumen de lo leido frente a lista de lo que hay")
comprobar("el estado del corpus manda al inventario y no contesta por el",
          "inventario de la" in (tools.estado_del_corpus.__doc__ or ""),
          "para no negar archivos que estan esperando a ser leidos")

_prompt_inv = _pr.construir_system_prompt({})
comprobar("el prompt obliga a mirar el Drive antes de negar un archivo",
          "HERRAMIENTA DE INVENTARIO DE LA FUENTE" in _prompt_inv,
          "regla 11")
comprobar("y distingue las tres respuestas posibles",
          all(t in _prompt_inv for t in ("esta y lo he leido",
                                         "no lo he leido todavia",
                                         "no esta en el Drive")),
          "estan, estan sin leer, o no estan: tres cosas distintas")

comprobar("el inventario esta registrado entre las herramientas",
          tools.inventario_de_la_fuente in tools.HERRAMIENTAS,
          "si no, el modelo no puede llamarla por mucho que el prompt lo diga")

_salida = tools.inventario_de_la_fuente()
if "No puedo mirar la fuente" in _salida or "No hay ninguna fuente" in _salida:
    omitir("el inventario lista la carpeta real", "la fuente no esta conectada")
else:
    import re as _re_inv
    # LAS MARCAS SE LLAMAN COMO SE LLAMAN AHORA
    # -----------------------------------------
    # Esta prueba buscaba [estudiado] y [pendiente], que es como se marcaba
    # antes. La herramienta dejo de usar esas palabras porque no las podia
    # sostener: la marca se decidia por la extension del archivo, no por lo
    # que hubiera en el indice, asi que llamaba "estudiado" a cualquier PDF.
    # La prueba siguio contando palabras que ya no aparecian y daba cero de
    # todo sin que eso significara nada.
    _disponibles = len(_re_inv.findall(r"\[disponible", _salida))
    _sin = len(_re_inv.findall(r"\[sin lector", _salida))
    comprobar("lista archivos de verdad, con su estado",
              _disponibles + _sin > 0,
              f"{_disponibles} disponibles, {_sin} sin lector")
    comprobar("y promete solo lo que puede cumplir",
              "[estudiado" not in _salida and "[pendiente" not in _salida,
              "no llama estudiado a un archivo por su extension")

    _filtrado = tools.inventario_de_la_fuente(buscar="zzz-no-existe-nada")
    comprobar("un filtro sin resultados no dice que el Drive este vacio",
              "pero ninguno coincide" in _filtrado,
              "dice cuantos hay y que el filtro no encontro")


# ==============================================================================
print("\nUN TURNO DEL CHAT NO PUEDE LEER NI BAJAR GIGABYTES")
# ==============================================================================
# La carpeta del corpus local y la de descargas de Drive son LA MISMA:
# `data/copiloto_drive`. Fuera de un estudio esta vacia, y mientras un
# estudio corre tiene los 3,2 GB recien bajados.
#
# Por eso una herramienta del agente no puede permitirse ninguna de estas
# dos cosas dentro de un turno:
#
#   - `corpus.resumen(reconstruir=True)`, que abre y trocea TODOS los
#     documentos de la carpeta. Medido: mas de 400 segundos sin terminar.
#     Su propio docstring ya lo dice -- "nunca desde la interfaz ni desde un
#     endpoint" -- y `buscar_especie_exacta` lo llamaba igual.
#   - `asegurar_descargado`, que BAJA de Drive lo que falte. Con seis
#     candidatos y archivos de 36 MB, una pregunta arrastraba cientos de
#     megas antes de contestar.
#
# Las dos se disparaban juntas al preguntar "y esas como se llaman?" sobre
# una foto, y la pantalla se quedaba en "Pensando... 53 s" subiendo.
with open(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                       "agent", "tools.py"), encoding="utf-8") as _fh_t:
    _TOOLS = _fh_t.read()

_codigo = [l for l in _TOOLS.splitlines()
           if not l.lstrip().startswith("#")]

comprobar("ninguna herramienta reconstruye el corpus entero",
          not any("resumen(reconstruir=True)" in l for l in _codigo),
          "reconstruirlo lee la carpeta de descargas de Drive al completo")

# Bajar UN documento que la persona ha pedido por su nombre si vale: es
# justo lo que hace la herramienta de leer un archivo del Drive, y quien lo
# pide sabe que esta pidiendo ese archivo. Lo que no vale es bajar seis a
# ciegas por si acaso, que es lo que hacia la busqueda directa.
_bloque_directo = _TOOLS[_TOOLS.index("def _buscar_directo_en_documentos"):]
_bloque_directo = _bloque_directo[:_bloque_directo.index("def consultar_corpus")]
_lineas_directo = [l for l in _bloque_directo.splitlines()
                   if not l.lstrip().startswith("#")]

comprobar("la busqueda a ciegas no baja nada de Drive",
          not any("asegurar_descargado" in l for l in _lineas_directo),
          "mira solo lo que ya esta en el equipo, que es gratis")

comprobar("y bajar un documento solo se hace cuando se pide por su nombre",
          sum(1 for l in _codigo if "asegurar_descargado" in l) == 1,
          "una sola puerta, y es la que la persona abre a proposito")

comprobar("si la fuente no deja los documentos aqui, se dice y ya",
          "if not vista.get(\"local\")" in _TOOLS,
          "la respuesta se decide por como es la fuente, sin tocar el disco")


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
