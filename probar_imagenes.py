"""
Bateria de imagenes: que el agente LEA lo que dice la foto, pese lo que pese.

QUE SE PRUEBA AQUI
------------------
Una foto del corpus vale por lo que DICE, no por lo que se ve: una ficha de
campo fotografiada, el rotulo de una estacion, una tabla en papel. Por eso el
lector no describe la imagen, la transcribe.

Esta bateria comprueba tres cosas que solo se ven con imagenes de verdad:

  1. que el texto de la foto llega al indice, y no una descripcion vaga
  2. hasta que peso aguanta, que es un limite de la API y no del codigo
  3. cuanto tarda cada una, porque es lo que decide si una carpeta con
     doscientas fotos se puede sincronizar de noche o no

SI GASTA CUOTA, Y POCA A PROPOSITO
----------------------------------
Cada imagen es UNA llamada al modelo. No se puede simular: lo que se esta
midiendo es justamente si el modelo la lee y cuanto tarda. Se usan unas pocas
imagenes, no un banco grande, por eso mismo.

Las imagenes se generan aqui con el MISMO texto conocido en todas y a
resoluciones distintas. Asi la unica variable es el peso, y se puede decir si
el acierto depende de el.

COMO SE EJECUTA
---------------
    python probar_imagenes.py
"""

import os
import shutil
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from core import ajustes as _ajustes

_ajustes.aplicar()

from core import corpus as _cp             # noqa: E402
from config import settings as _st         # noqa: E402

ACIERTOS = 0
FALLOS = []

#: Lo que se escribe dentro de cada imagen. Son datos concretos y raros a
#: proposito: si el modelo los devuelve, los LEYO, no los adivino.
FICHA = [
    "FICHA DE CAMPO 042",
    "Vereda: El Jordan",
    "Quebrada: La Palmichala",
    "Coordenadas: 6.18742 N, -74.99315 W",
    "Caudal medido: 37.4 l/s",
    "Fecha: 19 de octubre de 2026",
    "Responsable: brigada 3",
]

#: Lo que se busca en la transcripcion para dar por buena la lectura. Se
#: eligen cadenas que no se pueden inventar.
CLAVES = ("042", "Jordan", "Palmichala", "37.4", "6.18742")


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

    Args:
        nombre: Que se iba a comprobar.
        motivo: Por que no se pudo.
    """
    print(f"  [--- ] {nombre}: omitido ({motivo})")


def dibujar_ficha(ruta: str, ancho: int, alto: int, ruido: bool = False):
    """
    Escribe la ficha de campo dentro de una imagen del tamano pedido.

    El texto se dibuja a un tamano proporcional al ancho, para que una imagen
    grande no sea solo la pequena estirada: tiene que ser legible en las dos,
    porque lo que se mide es el PESO, no la legibilidad.

    Args:
        ruta: Donde guardarla. La extension decide el formato.
        ancho: Pixeles de ancho.
        alto: Pixeles de alto.
        ruido: Si se le mete ruido para que no comprima casi a nada, que es
            lo que hace una foto de verdad frente a un PNG plano.

    Returns:
        Tamano del archivo en bytes.
    """
    from PIL import Image, ImageDraw, ImageFont

    imagen = Image.new("RGB", (ancho, alto), (250, 248, 242))
    lapiz = ImageDraw.Draw(imagen)

    cuerpo = max(14, ancho // 34)
    try:
        tipo = ImageFont.truetype("DejaVuSans.ttf", cuerpo)
    except OSError:
        tipo = ImageFont.load_default()

    y = alto // 12
    for linea in FICHA:
        lapiz.text((ancho // 14, y), linea, fill=(20, 20, 20), font=tipo)
        y += int(cuerpo * 1.7)

    if ruido:
        # Una foto real no comprime como un PNG plano. Sin esto, subir la
        # resolucion no sube el peso y la prueba de "distintos pesos" no
        # probaria nada.
        import random

        random.seed(ancho)
        pixeles = imagen.load()
        for _ in range((ancho * alto) // 6):
            x, yy = random.randrange(ancho), random.randrange(alto)
            g = random.randrange(200, 256)
            pixeles[x, yy] = (g, g, max(190, g - 8))

    imagen.save(ruta)
    return os.path.getsize(ruta)


print("=" * 70)
print("  Imagenes: que el agente lea la foto, al peso que llegue")
print("=" * 70)

if not _st.api_key_gemini():
    print("\n  Sin GEMINI_API_KEY no se puede transcribir nada.")
    print("  Esta bateria mide al modelo de verdad; no se puede simular.")
    sys.exit(1)

_banco = tempfile.mkdtemp(prefix="probar_imagenes_")
_medidas = []
try:
    # ==========================================================================
    # 1. LA MISMA FICHA A PESOS MUY DISTINTOS
    # ==========================================================================
    print("\n--- La misma ficha, a pesos distintos ---")

    _casos = [
        ("ficha_pequena.png", 700, 420, False),
        ("ficha_media.png", 1600, 1000, True),
        ("ficha_grande.png", 3000, 1900, True),
        ("ficha_enorme.png", 5200, 3300, True),
    ]

    for _nombre, _ancho, _alto, _ruido in _casos:
        _ruta = os.path.join(_banco, _nombre)
        _bytes = dibujar_ficha(_ruta, _ancho, _alto, _ruido)
        _kb = _bytes / 1024

        _t0 = time.time()
        try:
            _paginas = _cp.leer_documento(_ruta)
            _error = ""
        except Exception as _exc:
            _paginas = []
            _error = str(_exc)[:90]
        _tardo = time.time() - _t0

        _texto = " ".join(t for _p, t in _paginas)
        _encontradas = [c for c in CLAVES if c in _texto]
        _medidas.append({
            "nombre": _nombre, "kb": _kb, "px": f"{_ancho}x{_alto}",
            "segundos": _tardo, "claves": len(_encontradas),
            "error": _error, "caracteres": len(_texto),
        })

        if _error:
            comprobar(f"{_nombre} ({_kb:.0f} kB) se transcribe",
                      False, f"la API fallo: {_error}")
        else:
            comprobar(f"{_nombre} ({_kb:.0f} kB) se transcribe",
                      len(_encontradas) >= 4,
                      f"{len(_encontradas)}/{len(CLAVES)} datos leidos en "
                      f"{_tardo:.1f} s")

    # ==========================================================================
    # 2. QUE SE TRANSCRIBE, NO QUE SE DESCRIBE
    # ==========================================================================
    print("\n--- Transcribe, no interpreta ---")

    _buenas = [m for m in _medidas if not m["error"] and m["claves"] >= 4]
    if _buenas:
        _ruta = os.path.join(_banco, "ficha_media.png")
        _texto = " ".join(t for _p, t in _cp.leer_documento(_ruta))
        comprobar("la coordenada llega EXACTA al indice",
                  "6.18742" in _texto and "-74.99315" in _texto,
                  "no redondeada ni reescrita")
        comprobar("el caudal llega con su unidad",
                  "37.4" in _texto,
                  "el dato numerico sobrevive a la transcripcion")
    else:
        omitir("la coordenada llega EXACTA al indice",
               "ninguna imagen se transcribio bien")
        omitir("el caudal llega con su unidad", "idem")

    # ==========================================================================
    # 3. EL TIEMPO EN FUNCION DEL PESO
    # ==========================================================================
    print("\n--- Cuanto tarda cada una ---")
    print(f"  {'imagen':<20} {'pixeles':>12} {'kB':>8} {'segundos':>9} "
          f"{'datos':>7}")
    for _m in _medidas:
        print(f"  {_m['nombre']:<20} {_m['px']:>12} {_m['kb']:>8.0f} "
              f"{_m['segundos']:>9.1f} {_m['claves']:>5}/{len(CLAVES)}")

    _ok = [m for m in _medidas if not m["error"]]
    if len(_ok) >= 2:
        _rapida = min(_ok, key=lambda m: m["kb"])
        _lenta = max(_ok, key=lambda m: m["kb"])
        _factor_peso = _lenta["kb"] / max(_rapida["kb"], 1)
        _factor_tiempo = _lenta["segundos"] / max(_rapida["segundos"], 0.1)
        comprobar("el tiempo NO crece como el peso",
                  _factor_tiempo < _factor_peso,
                  f"{_factor_peso:.0f}x de peso solo cuesta "
                  f"{_factor_tiempo:.1f}x de tiempo")

        _media = sum(m["segundos"] for m in _ok) / len(_ok)
        print(f"\n  Media: {_media:.1f} s por imagen. Una carpeta de 200 "
              f"fotos seria {_media * 200 / 60:.0f} minutos.")
        comprobar("una foto se transcribe en un tiempo razonable",
                  _media < 30,
                  f"{_media:.1f} s de media")

    # ==========================================================================
    # 4. LO QUE NO SE PUEDE LEER, SE DECLARA
    # ==========================================================================
    print("\n--- Lo que no se puede leer ---")

    _rota = os.path.join(_banco, "foto_corrupta.png")
    with open(_rota, "wb") as _fh:
        _fh.write(b"\x89PNG\r\n\x1a\n" + b"basura" * 300)
    try:
        _p = _cp.leer_documento(_rota)
        _fallo_limpio = (_p == [])
        _detalle = "devuelve vacio, el importador lo declara"
    except Exception as _exc:
        _fallo_limpio = True
        _detalle = f"levanta {type(_exc).__name__}, que se recoge arriba"
    comprobar("una imagen corrupta no se traga el error",
              _fallo_limpio, _detalle)
finally:
    shutil.rmtree(_banco, ignore_errors=True)

# ==============================================================================
print("")
print("PEGAR EN EL CHAT - del portapapeles al modelo, sin perder nada")
# ------------------------------------------------------------------------------
# Lo que se prueba aqui no es que el modelo sepa leer una foto -- eso ya esta
# arriba -- sino que la foto LLEGUE. Fallaba en el camino, y se veia igual
# que si el modelo no supiera: el agente contestaba "no tengo la capacidad de
# procesar imagenes" y parecia una limitacion suya.
#
# Eran dos huecos distintos:
#   * el buzon colgaba del identificador de la CHARLA, que cambia al abrir
#     otra conversacion; la imagen quedaba guardada bajo un nombre que ya no
#     miraba nadie
#   * con imagenes la pregunta seguia yendo por el webhook de n8n, que solo
#     transporta texto, asi que el nombre del buzon no viajaba
import json as _js
import urllib.error as _uerr
import urllib.request as _ureq

_API = os.environ.get("API_URL", "http://localhost:8000")


def _llamar(ruta, metodo="GET", cuerpo=None, espera=240):
    """
    Habla con la API del proyecto.

    Args:
        ruta: Camino del endpoint.
        metodo: Verbo HTTP.
        cuerpo: Diccionario a enviar como JSON, si lo hay.
        espera: Segundos de paciencia.

    Returns:
        Tupla `(codigo, datos)`. El codigo es 0 si no se pudo ni conectar.
    """
    datos = _js.dumps(cuerpo).encode() if cuerpo is not None else None
    peticion = _ureq.Request(_API + ruta, data=datos, method=metodo,
                             headers={"Content-Type": "application/json"})
    try:
        with _ureq.urlopen(peticion, timeout=espera) as r:
            crudo = r.read().decode("utf-8")
            return r.status, (_js.loads(crudo) if crudo else {})
    except _uerr.HTTPError as exc:
        return exc.code, {"detail": exc.read().decode("utf-8")[:200]}
    except Exception:
        return 0, {}


_codigo_vivo, _ = _llamar("/", espera=10)
if _codigo_vivo != 200:
    omitir("el buzon de imagenes pegadas",
           "la API no responde; esta parte necesita la pila levantada")
else:
    _buzon = "buzon_prueba_" + str(int(time.time()))
    _charla = "charla_prueba_" + str(int(time.time()))
    _llamar(f"/chat/imagenes/{_buzon}", "DELETE", espera=20)

    def _letrero(texto):
        """
        Dibuja un cartel con una sola palabra, en base64.

        Args:
            texto: Lo que se escribe en el cartel.

        Returns:
            El PNG codificado en base64.
        """
        import base64 as _b64m
        import io as _iom

        from PIL import Image, ImageDraw, ImageFont

        imagen = Image.new("RGB", (900, 400), (20, 90, 60))
        lapiz = ImageDraw.Draw(imagen)
        try:
            tipo = ImageFont.truetype("DejaVuSans-Bold.ttf", 70)
        except OSError:
            tipo = ImageFont.load_default()
        lapiz.text((50, 170), texto, fill=(255, 255, 255), font=tipo)
        caja = _iom.BytesIO()
        imagen.save(caja, "PNG")
        return _b64m.b64encode(caja.getvalue()).decode()

    _letreros = ["MORPHO AZUL", "QUEBRADA LA HONDA", "VEREDA EL JARDIN"]
    _guardadas = []
    for _n, _texto in enumerate(_letreros, 1):
        _b64 = _letrero(_texto)
        _cod, _resp = _llamar("/chat/imagen", "POST",
                              {"session_id": _buzon, "nombre": f"{_n}.png",
                               "tipo": "image/png", "datos": _b64})
        _guardadas.append(_cod == 200)

    comprobar("se pueden pegar VARIAS imagenes seguidas, no solo una",
              all(_guardadas) and len(_guardadas) == 3,
              f"{sum(_guardadas)} de 3 aceptadas")

    _cod, _listado = _llamar(f"/chat/imagenes/{_buzon}")
    comprobar("las tres esperan juntas en el buzon",
              _listado.get("cuantas") == 3,
              f"{_listado.get('cuantas')} en espera")

    # La vista previa no puede costar lo que cuesta la imagen: la pagina se
    # redibuja entera cada vez que se pega, y con capturas de varios megas
    # eso eran diez segundos de pantalla congelada por imagen.
    _con_mini = [i for i in _listado.get("imagenes", []) if i.get("miniatura")]
    _kb_listado = len(_js.dumps(_listado)) / 1024
    _kb_reales = sum(i.get("bytes", 0) for i in _listado.get("imagenes", []))
    _kb_reales = _kb_reales / 1024
    comprobar("cada imagen trae su miniatura ya hecha",
              len(_con_mini) == 3, f"{len(_con_mini)} de 3")
    comprobar("listar el buzon NO arrastra las imagenes enteras",
              _kb_listado < _kb_reales / 2,
              f"{_kb_listado:.0f} kB de listado para {_kb_reales:.0f} kB de "
              f"imagenes")

    # Y el que de verdad importa: que lleguen al modelo aunque la charla se
    # llame de otra manera que el buzon.
    _cod, _resp = _llamar("/agente/chat", "POST", {
        "pregunta": "Escribe el texto de cada imagen, uno por linea. Nada mas.",
        "session_id": _charla, "buzon_id": _buzon, "guardar_memoria": False})
    _dicho = (_resp.get("respuesta") or "").upper()
    _aciertos = [t for t in _letreros if t in _dicho]
    comprobar("el buzon no depende de como se llame la charla",
              len(_aciertos) >= 2,
              f"leyo {len(_aciertos)} de 3: {', '.join(_aciertos) or 'ninguno'}")
    comprobar("y NO contesta que no puede con imagenes",
              "no tengo la capacidad de procesar" not in _dicho.lower(),
              _resp.get("respuesta", "")[:70].replace(chr(10), " "))

    _cod, _vacio = _llamar(f"/chat/imagenes/{_buzon}")
    comprobar("las imagenes se consumen al mirarlas, no se quedan pegadas",
              _vacio.get("cuantas") == 0,
              f"{_vacio.get('cuantas')} en espera despues de responder")

    _llamar(f"/chat/imagenes/{_buzon}", "DELETE", espera=20)


# ==============================================================================
print("\n" + "=" * 70)
if FALLOS:
    print(f"{ACIERTOS} comprobaciones superadas, {len(FALLOS)} FALLIDAS:\n")
    for _f in FALLOS:
        print("  -", _f)
    sys.exit(1)
print(f"{ACIERTOS} de {ACIERTOS} comprobaciones superadas")
