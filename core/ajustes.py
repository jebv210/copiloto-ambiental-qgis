# -*- coding: utf-8 -*-
"""
Ajustes que se cambian desde la interfaz y sobreviven al reinicio.
===============================================================================

EL PROBLEMA
-----------
Hasta ahora, conectar Google Drive obligaba a: abrir el `.env` con un editor,
escribir tres variables, dejar el JSON de credenciales en la carpeta correcta
y recrear los contenedores. Cuatro pasos manuales, tres de ellos fuera de la
aplicacion, y uno (`--force-recreate`, no `restart`) que no es evidente.

Ademas el `.env` se lee UNA vez, al crear el contenedor. Cambiarlo con la
aplicacion en marcha no sirve de nada hasta recrearla, lo que hace que el
cambio parezca que no funciona.

LA SOLUCION
-----------
Un archivo JSON pequeno que la aplicacion escribe y relee en caliente. Lo que
haya aqui MANDA sobre el `.env`; lo que no este aqui, se sigue leyendo de alli.

Asi:
  - El `.env` sigue siendo el sitio para la configuracion de despliegue.
  - La interfaz puede cambiar lo que es decision del usuario (de donde estudia
    el agente) sin tocar archivos ni reiniciar nada.

QUE NO SE GUARDA AQUI
---------------------
Ninguna credencial. La API Key sigue en el `.env` y el JSON de la cuenta de
servicio en su propio archivo, con el ignorado de git que le corresponde. Aqui
solo van decisiones: que fuente usar y donde esta la carpeta.

===============================================================================
"""

import json
import os
import threading

#: Ajustes que la interfaz puede cambiar. Cualquier otra clave se ignora al
#: leer: una lista explicita evita que un archivo manipulado inyecte
#: configuracion que nadie penso en validar.
AJUSTES_PERMITIDOS = {
    "FUENTE_CORPUS",
    "DRIVE_CARPETA_ID",
    "GOOGLE_CREDENCIALES",
}

_CERROJO = threading.Lock()


def ruta() -> str:
    """
    Ruta del archivo de ajustes.

    Returns:
        Ruta absoluta de `data/ajustes.json`. La carpeta se crea si no existe.
    """
    from config import settings

    carpeta = settings.ruta_proyecto("data")
    os.makedirs(carpeta, exist_ok=True)
    return os.path.join(carpeta, "ajustes.json")


def leer() -> dict:
    """
    Lee los ajustes guardados.

    Returns:
        Diccionario con los ajustes reconocidos. Vacio si el archivo no existe
        o no se puede leer: un archivo roto no debe impedir arrancar, solo
        hace que valga lo que diga el `.env`.
    """
    try:
        with open(ruta(), encoding="utf-8") as f:
            datos = json.load(f)
    except Exception:
        return {}
    if not isinstance(datos, dict):
        return {}
    return {k: str(v) for k, v in datos.items()
            if k in AJUSTES_PERMITIDOS and str(v).strip()}


def guardar(cambios: dict) -> dict:
    """
    Guarda o actualiza ajustes, y los aplica en el acto.

    Se escriben tambien en `os.environ` para que surtan efecto sin reiniciar:
    el resto del sistema lee la configuracion del entorno, asi que ponerlos
    alli es lo que hace que el cambio se note de inmediato.

    Args:
        cambios: Ajustes a fijar. Un valor vacio o None BORRA ese ajuste, con
            lo que vuelve a mandar el `.env`.

    Returns:
        Los ajustes resultantes, ya aplicados.
    """
    with _CERROJO:
        actuales = leer()
        for clave, valor in (cambios or {}).items():
            if clave not in AJUSTES_PERMITIDOS:
                continue
            if valor is None or not str(valor).strip():
                actuales.pop(clave, None)
                os.environ.pop(clave, None)
            else:
                actuales[clave] = str(valor).strip()
                os.environ[clave] = str(valor).strip()

        destino = ruta()
        # Se escribe en un temporal y se renombra: si el proceso muere a mitad,
        # no queda un JSON cortado que impida arrancar la proxima vez.
        temporal = destino + ".parcial"
        with open(temporal, "w", encoding="utf-8") as f:
            json.dump(actuales, f, ensure_ascii=False, indent=2)
        os.replace(temporal, destino)
        return dict(actuales)


def aplicar() -> dict:
    """
    Vuelca los ajustes guardados sobre el entorno del proceso.

    Se llama al arrancar, antes de leer cualquier configuracion. Lo guardado
    MANDA sobre el `.env`: es lo que el usuario eligio en la interfaz, mas
    reciente que lo que se dejo escrito en el archivo de despliegue.

    Returns:
        Los ajustes que se aplicaron.
    """
    guardados = leer()
    for clave, valor in guardados.items():
        os.environ[clave] = valor
    return guardados


def olvidar(clave: str) -> dict:
    """
    Borra un ajuste guardado y devuelve el mando al `.env`.

    Args:
        clave: Ajuste a olvidar.

    Returns:
        Los ajustes que quedan.
    """
    return guardar({clave: None})
