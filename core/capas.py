"""
Recoge la geometria que llega en los archivos y la deja lista para QGIS.

EL HUECO QUE TAPA
-----------------
El proyecto tenia dos caminos que no se cruzaban. Por uno entraban los
documentos, se troceaban y se indexaban: el agente podia buscar en ellos.
Por el otro salia el mapa, y el mapa solo conocia tres capas, las del
archivo del area de estudio. Un .zip con 1.882 areas protegidas de Colombia
recorria el primer camino entero y no tocaba el segundo. Se leia, se
resumia, se indexaba... y el mapa salia exactamente igual que el dia
anterior.

Este modulo es el cruce. Cuando por la fuente entra algo con geometria de
verdad -- un shapefile, una geodatabase --, la recorta al area de estudio y
la escribe en `capas_extra/`, que es de donde el script de QGIS recoge las
capas propias. El resultado es que subir un archivo a Drive cambia el mapa,
que es lo que cualquiera esperaria que pasara.

POR QUE SE RECORTA
------------------
Porque el Atlas Geologico trae 7.067 fallas de Colombia entera y la
pregunta nunca es esa. La pregunta es cuales cruzan el area de estudio.
Se guarda lo que cae cerca -- con holgura, para ver el contexto -- y cada
entidad lleva escrito si TOCA el area o solo pasa cerca, que es la
distincion con la que se trabaja.

LO QUE NO SE GUARDA
-------------------
Nada que no se sepa situar. Si no se pudo averiguar el sistema de
referencia de una capa, o la proyeccion no se sabe invertir, no se escribe
el archivo y se dice por que. Una capa mal colocada no se nota mirando el
mapa, y se arrastra hasta el informe final.
"""

import json
import os
import unicodedata

#: Carpeta de donde QGIS recoge las capas que no vienen del area de estudio.
CARPETA = "capas_extra"

#: Prefijo de los archivos que escribe este modulo.
#:
#: Sirve para distinguirlos de las capas de campo (`campo_`) y de las que
#: deje el usuario a mano: asi una nueva sincronizacion puede rehacer las
#: suyas sin tocar las de nadie.
PREFIJO = "fuente_"

#: Cuantas entidades se guardan como mucho en una capa.
MAXIMO_POR_CAPA = 20000


def carpeta() -> str:
    """
    Ruta de la carpeta de capas, creada si no existe.

    Returns:
        Ruta absoluta.
    """
    base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    destino = os.path.join(base, CARPETA)
    os.makedirs(destino, exist_ok=True)
    return destino


def _apodo(texto: str) -> str:
    """
    Convierte un nombre cualquiera en un nombre de archivo manejable.

    Args:
        texto: Nombre de origen.

    Returns:
        Minusculas, sin acentos ni espacios.
    """
    plano = unicodedata.normalize("NFKD", str(texto or ""))
    plano = "".join(c for c in plano if not unicodedata.combining(c))
    limpio = "".join(c if c.isalnum() else "_" for c in plano.lower())
    while "__" in limpio:
        limpio = limpio.replace("__", "_")
    return limpio.strip("_")[:60] or "capa"


def area_de_estudio() -> dict:
    """
    Carga el area de estudio para recortar contra ella.

    Returns:
        Diccionario con `anillos` y `caja`, o vacio si todavia no hay area
        fijada. Sin area no se recorta nada: se guarda la capa entera.
    """
    from core import geometria

    base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    ruta = os.path.join(base, "data", "municipios_filtrados.geojson")
    if not os.path.exists(ruta):
        return {}
    try:
        with open(ruta, encoding="utf-8") as fh:
            capa = json.load(fh)
    except (OSError, ValueError):
        return {}

    anillos = geometria.anillos_de(capa)
    if not anillos:
        return {}
    xs = [p[0] for a in anillos for p in a]
    ys = [p[1] for a in anillos for p in a]
    return {"anillos": anillos,
            "caja": (min(xs), min(ys), max(xs), max(ys))}


def guardar(nombre: str, entidades: list) -> dict:
    """
    Escribe una capa en la carpeta de capas propias.

    Args:
        nombre: Nombre de la capa, sin extension.
        entidades: Entidades GeoJSON.

    Returns:
        Diccionario con `archivo`, `entidades` y `kb`, o con `error`.
    """
    if not entidades:
        return {"error": "no hay ninguna entidad que guardar"}
    archivo = f"{PREFIJO}{_apodo(nombre)}.geojson"
    ruta = os.path.join(carpeta(), archivo)
    try:
        with open(ruta, "w", encoding="utf-8") as fh:
            json.dump({"type": "FeatureCollection",
                       "features": entidades[:MAXIMO_POR_CAPA]},
                      fh, ensure_ascii=False)
    except OSError as exc:
        return {"error": f"no se pudo escribir la capa: {str(exc)[:90]}"}
    return {"archivo": archivo, "ruta": ruta,
            "entidades": min(len(entidades), MAXIMO_POR_CAPA),
            "kb": round(os.path.getsize(ruta) / 1024)}


def _cosechar(lectura: dict, nombre: str, area: dict) -> dict:
    """
    Marca el contacto con el area y guarda una capa ya leida.

    Args:
        lectura: Lo que devuelven `geometria.leer_shp` o
            `geodatabase.leer_capa`.
        nombre: Nombre para la capa.
        area: Lo que devuelve `area_de_estudio`.

    Returns:
        Diccionario con `nombre`, `guardado`, `tocan`, `cerca`, `total`,
        `crs` y `aviso`, o con `error`.
    """
    from core import geometria

    if lectura.get("error"):
        return {"nombre": nombre, "error": lectura["error"]}
    entidades = lectura.get("entidades") or []
    if not entidades:
        return {"nombre": nombre,
                "error": "no quedo ninguna entidad cerca del area de estudio",
                "total": lectura.get("total", 0)}
    if not lectura.get("situable"):
        return {"nombre": nombre, "total": lectura.get("total", 0),
                "error": lectura.get("aviso") or
                         "no se sabe donde situar esta capa, asi que no se "
                         "guarda: dibujarla seria colocarla a ojo"}

    tocan = (geometria.marcar_contacto(entidades, area["anillos"])
             if area.get("anillos") else None)
    # Si hay area de estudio activa, guardar EXCLUSIVAMENTE las entidades
    # que caen en o tocan el area (San Carlos). Descartar el resto de Antioquia.
    if area.get("anillos"):
        entidades_guardar = [e for e in entidades
                             if e.get("properties", {}).get("en_el_area") == "si"]
    else:
        entidades_guardar = entidades

    if not entidades_guardar:
        # NO ES UN ERROR, Y TAMPOCO ES NADA
        # ---------------------------------
        # Que ninguna falla geologica toque San Carlos es un RESULTADO: contesta
        # "no hay pliegues en el area" con un numero detras. Por eso se devuelve
        # como aviso y no como error, y por eso se arrastran igual el recuento y
        # la tabla entera: sin ellos, el indice se quedaria sin poder responder
        # cuantas habia ni de que tipo.
        return {"nombre": nombre,
                "aviso": "ninguna entidad toca el area de estudio",
                "total": lectura.get("total", 0), "tocan": 0,
                "cerca": len(entidades),
                "crs": lectura.get("crs", ""), "tipo": lectura.get("tipo", ""),
                "entidades": [], "todas": lectura.get("todas") or []}

    guardado = guardar(nombre, entidades_guardar)
    return {"nombre": nombre, "guardado": guardado, "tocan": tocan,
            "cerca": len(entidades_guardar), "total": lectura.get("total", 0),
            "crs": lectura.get("crs", ""), "tipo": lectura.get("tipo", ""),
            "entidades": entidades_guardar,
            "todas": lectura.get("todas") or []}


def cosechar_shapefile(ruta: str, nombre: str = "") -> dict:
    """
    Lee un shapefile y guarda su geometria como capa propia.

    Args:
        ruta: Ruta del .shp.
        nombre: Nombre para la capa. Por defecto, el del archivo.

    Returns:
        Lo que devuelve `_cosechar`.
    """
    from core import geometria

    area = area_de_estudio()
    lectura = geometria.leer_shp(ruta, recuadro=area.get("caja"),
                                 maximo=MAXIMO_POR_CAPA)
    etiqueta = nombre or os.path.splitext(os.path.basename(ruta))[0]
    cosechada = _cosechar(lectura, etiqueta, area)
    cosechada["origen"] = ruta
    return cosechada


def cosechar_geodatabase(ruta: str, nombre: str = "") -> list:
    """
    Lee todas las capas de una geodatabase y las guarda.

    Args:
        ruta: Ruta de la carpeta .gdb.
        nombre: Prefijo para las capas. Por defecto, el de la carpeta.

    Returns:
        Lista con un resultado por capa.
    """
    from core import geodatabase

    abierta = geodatabase.abrir(ruta)
    if abierta.get("error"):
        return [{"nombre": nombre or os.path.basename(ruta),
                 "error": abierta["error"]}]

    area = area_de_estudio()
    base = nombre or os.path.splitext(os.path.basename(ruta))[0]
    salida = []
    for tabla in abierta["tablas"]:
        lectura = geodatabase.leer_capa(tabla["archivo"],
                                        recuadro=area.get("caja"),
                                        maximo=MAXIMO_POR_CAPA)
        cosechada = _cosechar(lectura, f"{base}_{tabla['nombre']}", area)
        cosechada["origen"] = ruta
        salida.append(cosechada)
    return salida


def contar(resultado) -> str:
    """
    Resume en una linea lo que dejo una cosecha.

    Se usa tanto en el informe de la sincronizacion como en las fichas que
    se indexan, porque la frase que le sirve a una persona mirando el
    informe es la misma que le sirve al agente buscando en el indice.

    Args:
        resultado: Un resultado de cosecha o una lista de ellos.

    Returns:
        Texto de una o varias lineas.
    """
    if isinstance(resultado, dict):
        resultado = [resultado]
    lineas = []
    for uno in resultado:
        nombre = uno.get("nombre", "capa")
        if uno.get("error"):
            lineas.append(f"  {nombre}: {uno['error']}")
            continue
        guardado = uno.get("guardado") or {}
        if guardado.get("error"):
            lineas.append(f"  {nombre}: {guardado['error']}")
            continue
        # UNA CAPA SIN NADA QUE GUARDAR TAMBIEN SE CUENTA
        # -----------------------------------------------
        # Esta rama faltaba. Cuando el recorte dejaba la capa vacia -- los
        # 1.164 pliegues del Atlas, ninguno en San Carlos -- el resultado
        # venia sin `guardado`, y la linea de abajo lo leia a pelo: KeyError
        # que se tragaba el lector de .zip y dejaba el archivo entero en
        # cero fichas. Un caso normal no puede tumbar la lectura.
        if not guardado.get("archivo"):
            lineas.append(
                f"  {nombre}: {uno.get('total', 0)} entidades, "
                f"{uno.get('aviso') or 'nada que guardar'}.")
            continue
        trozos = [f"{uno.get('cerca', 0)} de {uno.get('total', 0)} entidades "
                  f"guardadas del area de estudio"]
        if uno.get("tocan") is not None:
            trozos.append(f"{uno['tocan']} la tocan")
        trozos.append(f"guardada como {guardado['archivo']} "
                      f"({guardado['kb']} KB)")
        lineas.append(f"  {nombre}: " + ", ".join(trozos) + ".")
    return "\n".join(lineas)


#: Cuantos valores distintos se nombran al resumir una columna.
MAXIMO_VALORES = 15

#: Cuantas entidades del area se describen una por una.
MAXIMO_DETALLE = 600


def resumen_de_filas(filas: list) -> list:
    """
    Resume una tabla diciendo que valores trae cada columna.

    POR QUE UN RESUMEN Y NO LA TABLA ENTERA
    ---------------------------------------
    Porque la tabla entera no cabe, y porque repetida no dice nada. El
    Atlas Geologico trae 7.067 fallas y entre todas usan once tipos: volcar
    las 7.067 lineas gasta el presupuesto de fichas del documento -- con lo
    que el archivo acaba con una ficha de "existe" y nada mas -- para
    repetir once palabras tres mil veces.

    Resumido ocupa veinte lineas y contesta la pregunta que de verdad se
    hace: que hay aqui dentro.

    Args:
        filas: Filas como diccionarios.

    Returns:
        Lista de lineas de texto.
    """
    from collections import Counter

    if not filas:
        return []
    columnas = []
    for fila in filas:
        for clave in fila:
            if clave not in columnas:
                columnas.append(clave)

    lineas = []
    for columna in columnas:
        valores = [str(f[columna]).strip() for f in filas
                   if f.get(columna) not in (None, "")]
        valores = [v for v in valores if v]
        if not valores:
            continue
        cuenta = Counter(valores)
        if len(cuenta) == 1:
            lineas.append(f"{columna}: siempre '{list(cuenta)[0]}'.")
            continue
        if len(cuenta) > len(valores) * 0.9 and len(cuenta) > 30:
            # Una columna con un valor distinto por fila es un
            # identificador o un nombre propio. Contar sus repeticiones no
            # dice nada; lo util es cuantos hay y unos cuantos ejemplos.
            muestra = ", ".join(list(cuenta)[:MAXIMO_VALORES])
            lineas.append(f"{columna}: {len(cuenta)} valores distintos, "
                          f"por ejemplo {muestra}.")
            continue
        partes = [f"{valor} ({veces})"
                  for valor, veces in cuenta.most_common(MAXIMO_VALORES)]
        cola = ("" if len(cuenta) <= MAXIMO_VALORES
                else f", y {len(cuenta) - MAXIMO_VALORES} mas")
        lineas.append(f"{columna}: " + ", ".join(partes) + cola + ".")
    return lineas


def paginas_de_cosecha(resultado: dict) -> list:
    """
    Escribe lo que hay que indexar de una capa ya cosechada.

    EL REPARTO, Y POR QUE ESTE
    --------------------------
    Un archivo nacional no se puede indexar entero y tampoco se puede
    dejar fuera. Se parte en dos:

      TODO EL ARCHIVO, resumido. Cuantas entidades, de que tipos, con que
      valores en cada columna. Responde "que trae este archivo" sin gastar
      el presupuesto.

      EL AREA DE ESTUDIO, entera. Las entidades que tocan el area o quedan
      cerca van una por una, con todos sus campos. Son las unicas sobre
      las que se va a escribir un informe, asi que son las unicas que
      merecen estar completas.

    El reparto no es un apano: es que las dos preguntas son distintas. A
    "cuantas areas protegidas hay en Colombia" le sirve el resumen; a
    "cuales afectan a San Carlos" solo le sirve el detalle.

    Args:
        resultado: Lo que devuelve una cosecha.

    Returns:
        Lista de textos, uno por bloque.
    """
    if resultado.get("error"):
        return []
    nombre = resultado.get("nombre", "capa")
    # Una capa de la que no se guardo nada SI tiene algo que decir: cuantas
    # entidades traia y que ninguna cae en el area. Es la respuesta a "hay
    # pliegues aqui?", y sin ella el agente no puede ni negarlo con un
    # numero delante.
    guardado = resultado.get("guardado") or {}
    entidades = resultado.get("entidades") or []
    todas = resultado.get("todas") or []

    cabeza = [f"[Capa {nombre}]",
              f"{resultado.get('total', 0)} entidades de tipo "
              f"{resultado.get('tipo') or 'desconocido'}, en el sistema de "
              f"referencia {resultado.get('crs') or 'sin declarar'}."]
    if resultado.get("tocan") is not None:
        cabeza.append(f"{resultado['tocan']} tocan el area de estudio y "
                      f"{resultado.get('cerca', 0) - resultado['tocan']} "
                      f"quedan cerca sin tocarla.")
    if guardado.get("archivo"):
        cabeza.append(f"Esta capa YA ESTA en el proyecto de QGIS, como "
                      f"{guardado['archivo']}: se puede dibujar.")
    elif resultado.get("aviso"):
        cabeza.append(f"No se dibuja ninguna: {resultado['aviso']}. Lo que "
                      f"sigue describe el archivo completo, no el area.")

    bloques = [chr(10).join(cabeza)]

    resumen = resumen_de_filas(todas or
                               [e.get("properties", {}) for e in entidades])
    if resumen:
        bloques.append(f"[Que trae {nombre}, en conjunto]" + chr(10)
                       + chr(10).join(resumen))

    dentro = [e for e in entidades
              if (e.get("properties") or {}).get("en_el_area") == "si"]
    fuera = [e for e in entidades if e not in dentro]
    for titulo, grupo in ((f"[Lo de {nombre} que toca el area de estudio]",
                           dentro),
                          (f"[Lo de {nombre} que queda cerca del area]",
                           fuera)):
        lineas = []
        for entidad in grupo[:MAXIMO_DETALLE]:
            texto = " | ".join(
                f"{k}: {v}" for k, v in (entidad.get("properties") or
                                         {}).items() if k != "en_el_area")
            if texto:
                lineas.append(texto)
        if not lineas:
            continue
        if len(grupo) > MAXIMO_DETALLE:
            lineas.append(f"(y {len(grupo) - MAXIMO_DETALLE} mas, que no "
                          f"caben)")
        for desde in range(0, len(lineas), 40):
            bloques.append(titulo + chr(10)
                           + chr(10).join(lineas[desde:desde + 40]))
    return bloques


def _juntar(etiqueta: str, base: str) -> str:
    """
    Pega el nombre del paquete y el de la capa sin repetirse.

    Un .zip llamado `Atlas_Geologico_2020.gdb.zip` trae dentro una carpeta
    `Atlas_Geologico_2020.gdb`, y pegandolos a lo bruto salia
    `atlas_geologico_2020_gdb_atlas_geologico_2020_fallas`. Un nombre que
    no cabe en la leyenda de un mapa es un nombre que habra que cambiar a
    mano.

    Args:
        etiqueta: Nombre del paquete del que salio.
        base: Nombre de la capa.

    Returns:
        El nombre combinado.
    """
    if not etiqueta:
        return base
    if _apodo(base) in _apodo(etiqueta):
        return etiqueta
    if _apodo(etiqueta) in _apodo(base):
        return base
    return f"{etiqueta}_{base}"


def buscar_y_cosechar(carpeta_raiz: str, etiqueta: str = "") -> list:
    """
    Recorre una carpeta buscando geometria y la cosecha toda.

    Es la puerta que usa el lector de .zip: dentro de un paquete puede
    venir un shapefile suelto, una geodatabase entera, o las dos cosas en
    subcarpetas distintas.

    Args:
        carpeta_raiz: Carpeta donde buscar.
        etiqueta: Prefijo para los nombres de capa, normalmente el nombre
            del archivo del que salieron.

    Returns:
        Lista de resultados de cosecha. Vacia si no habia geometria.
    """
    salida = []
    vistos = set()
    for raiz, carpetas, archivos in os.walk(carpeta_raiz):
        for una in list(carpetas):
            if not una.lower().endswith(".gdb"):
                continue
            carpetas.remove(una)
            completa = os.path.join(raiz, una)
            if completa in vistos:
                continue
            vistos.add(completa)
            base = os.path.splitext(una)[0]
            salida.extend(cosechar_geodatabase(completa,
                                               _juntar(etiqueta, base)))
        for uno in archivos:
            if not uno.lower().endswith(".shp"):
                continue
            completa = os.path.join(raiz, uno)
            base = os.path.splitext(uno)[0]
            salida.append(cosechar_shapefile(completa,
                                             _juntar(etiqueta, base)))
    return salida
