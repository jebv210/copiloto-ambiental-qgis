"""
Memoria persistente y episodica del Copiloto Ambiental.
===============================================================================

Este modulo resuelve los dos niveles de memoria a largo plazo:

1. PERSISTENCIA DE CONVERSACIONES (SQLite):
   Guarda los mensajes de chat en disco (`data/conversaciones.sqlite`).
   Permite recuperar charlas de ayer, la semana pasada o meses atras,
   cambiar de sesion y no perder nada al reiniciar el navegador o el contenedor.

2. MEMORIA SEMANTICA EPISODICA (Qdrant):
   Almacena en una coleccion dedicada de Qdrant (`memoria_usuario`) los hechos,
   nombres de proyectos, coordenadas, especies de interes y acuerdos
   mencionados por el usuario.
   Cuando el usuario pregunta manana: "¿recuerdas que proyecto te mencione ayer?",
   el agente busca en esta coleccion vectorial y recupera el contexto exacto.
"""

import datetime
import json
import os
import sqlite3
import uuid
from typing import List, Dict, Optional

from config import settings


def _ruta_sqlite() -> str:
    """
    Ruta a la base de datos de conversaciones persistentes.

    Returns:
        Ruta absoluta de `data/conversaciones.sqlite`. La carpeta se crea si
        no existe.
    """
    carpeta_data = settings.ruta_proyecto("data")
    os.makedirs(carpeta_data, exist_ok=True)
    return os.path.join(carpeta_data, "conversaciones.sqlite")


def _conectar_sqlite() -> sqlite3.Connection:
    """
    Abre la conexion SQLite y crea las tablas si no existen.

    Las tablas se crean en cada apertura con `IF NOT EXISTS`: sale mas barato
    que mantener un sistema de migraciones para dos tablas, y evita que una
    instalacion nueva falle por una base sin estrenar.

    Returns:
        Conexion abierta, con `row_factory` puesto para poder leer las
        columnas por nombre.
    """
    ruta = _ruta_sqlite()
    conn = sqlite3.connect(ruta, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    with conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS sesiones (
                id TEXT PRIMARY KEY,
                titulo TEXT NOT NULL,
                creada_en TEXT NOT NULL,
                actualizada_en TEXT NOT NULL
            );
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS mensajes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                sesion_id TEXT NOT NULL,
                rol TEXT NOT NULL,
                contenido TEXT NOT NULL,
                creado_en TEXT NOT NULL,
                FOREIGN KEY (sesion_id) REFERENCES sesiones(id) ON DELETE CASCADE
            );
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_mensajes_sesion ON mensajes(sesion_id);
        """)
    return conn


# ==============================================================================
# 1. PERSISTENCIA DE CHATS (SESIONES Y MENSAJES)
# ==============================================================================

def registrar_sesion(sesion_id: str, titulo: Optional[str] = None) -> dict:
    """
    Crea o actualiza los metadatos de una conversacion.

    Args:
        sesion_id: Identificador de la conversacion.
        titulo: Titulo a poner. Si no se indica y la sesion es nueva, se
            genera uno con la fecha.

    Returns:
        Los datos de la sesion, ya creada o actualizada.
    """
    ahora = datetime.datetime.now().isoformat()
    conn = _conectar_sqlite()
    try:
        with conn:
            fila = conn.execute("SELECT * FROM sesiones WHERE id = ?", (sesion_id,)).fetchone()
            if not fila:
                nombre = titulo or f"Conversación {datetime.datetime.now().strftime('%d/%m %H:%M')}"
                conn.execute(
                    "INSERT INTO sesiones (id, titulo, creada_en, actualizada_en) VALUES (?, ?, ?, ?)",
                    (sesion_id, nombre, ahora, ahora)
                )
            else:
                if titulo:
                    conn.execute(
                        "UPDATE sesiones SET titulo = ?, actualizada_en = ? WHERE id = ?",
                        (titulo, ahora, sesion_id)
                    )
                else:
                    conn.execute(
                        "UPDATE sesiones SET actualizada_en = ? WHERE id = ?",
                        (ahora, sesion_id)
                    )
        return obtener_sesion(sesion_id)
    finally:
        conn.close()


def obtener_sesion(sesion_id: str) -> Optional[dict]:
    """
    Busca una conversacion por su identificador.

    Args:
        sesion_id: Identificador de la conversacion.

    Returns:
        Diccionario con `id`, `titulo`, `creada_en` y `actualizada_en`, o
        None si no existe.
    """
    conn = _conectar_sqlite()
    try:
        fila = conn.execute("SELECT * FROM sesiones WHERE id = ?", (sesion_id,)).fetchone()
        if fila:
            return dict(fila)
        return None
    finally:
        conn.close()


def listar_sesiones(limite: int = 30) -> List[dict]:
    """
    Lista las conversaciones mas recientes.

    Se ordenan por ultima actividad y no por fecha de creacion: lo que
    interesa al abrir la lista es donde se estaba trabajando, no que se creo
    primero.

    Args:
        limite: Cuantas devolver como maximo.

    Returns:
        Lista de diccionarios de sesion, de la mas reciente a la mas antigua.
    """
    conn = _conectar_sqlite()
    try:
        filas = conn.execute(
            "SELECT * FROM sesiones ORDER BY actualizada_en DESC LIMIT ?", (limite,)
        ).fetchall()
        return [dict(f) for f in filas]
    finally:
        conn.close()


def guardar_mensaje(sesion_id: str, rol: str, contenido: str) -> None:
    """
    Guarda un mensaje y refresca la fecha de actividad de su conversacion.

    Args:
        sesion_id: Conversacion a la que pertenece.
        rol: Quien habla: "user" o "assistant".
        contenido: Texto del mensaje. Si viene vacio no se guarda nada.
    """
    if not contenido:
        return
    ahora = datetime.datetime.now().isoformat()
    conn = _conectar_sqlite()
    try:
        registrar_sesion(sesion_id)
        with conn:
            conn.execute(
                "INSERT INTO mensajes (sesion_id, rol, contenido, creado_en) VALUES (?, ?, ?, ?)",
                (sesion_id, rol, contenido, ahora)
            )
            # Auto-titular si es el primer mensaje de usuario
            if rol == "user":
                conteo = conn.execute("SELECT COUNT(*) FROM mensajes WHERE sesion_id = ?", (sesion_id,)).fetchone()[0]
                if conteo <= 2:
                    primer_titulo = contenido[:45].strip() + ("..." if len(contenido) > 45 else "")
                    conn.execute("UPDATE sesiones SET titulo = ? WHERE id = ?", (primer_titulo, sesion_id))
    finally:
        conn.close()


def cargar_historial(sesion_id: str) -> List[dict]:
    """
    Recupera el historial completo de una conversacion.

    Args:
        sesion_id: Conversacion a cargar.

    Returns:
        Lista de diccionarios con `role` y `content`, en orden cronologico y
        con los nombres que espera la interfaz de chat.
    """
    conn = _conectar_sqlite()
    try:
        filas = conn.execute(
            "SELECT rol, contenido, creado_en FROM mensajes WHERE sesion_id = ? ORDER BY id ASC",
            (sesion_id,)
        ).fetchall()
        return [{"role": f["rol"], "content": f["contenido"], "creado_en": f["creado_en"]} for f in filas]
    finally:
        conn.close()


# ==============================================================================
# 2. MEMORIA SEMANTICA EN QDRANT (HECHOS A LARGO PLAZO)
# ==============================================================================

COLECCION_MEMORIA = "memoria_usuario"


def asegurar_coleccion_memoria(cliente=None):
    """
    Crea la coleccion de memoria semantica en Qdrant si no existe.

    Args:
        cliente: Cliente de Qdrant ya abierto. Si no se pasa, se abre uno.
    """
    from core import vectorial
    from qdrant_client import models

    cliente = cliente or vectorial._cliente_qdrant()
    if not cliente.collection_exists(COLECCION_MEMORIA):
        cliente.create_collection(
            collection_name=COLECCION_MEMORIA,
            vectors_config={
                vectorial.NOMBRE_VECTOR: models.VectorParams(
                    size=vectorial.dimensiones_efectivas(),
                    distance=models.Distance.COSINE,
                )
            },
        )
        # `sesion_id` y `tipo` se buscan por valor exacto: KEYWORD.
        for campo in ("sesion_id", "tipo"):
            try:
                cliente.create_payload_index(
                    collection_name=COLECCION_MEMORIA,
                    field_name=campo,
                    field_schema=models.PayloadSchemaType.KEYWORD
                )
            except Exception:
                pass

        # `fecha` va como DATETIME, no como KEYWORD.
        #
        # POR QUE IMPORTA LA DIFERENCIA
        # -----------------------------
        # Un indice KEYWORD solo sirve para "dame los que valgan exactamente
        # esto". Para ORDENAR hace falta un indice de rango, y Qdrant rechaza
        # `order_by` sobre un campo KEYWORD con "No range index for order_by
        # key". Se midio: la consulta de ordenar por fecha fallaba, y escrita
        # con un espacio ("order by") Qdrant la IGNORA en silencio y devuelve
        # los puntos en orden arbitrario, con `status: ok`. El resultado es
        # que parecia que no entraba nada nuevo cuando si entraba.
        try:
            cliente.create_payload_index(
                collection_name=COLECCION_MEMORIA,
                field_name="fecha",
                field_schema=models.PayloadSchemaType.DATETIME
            )
        except Exception:
            pass


# ==============================================================================
# RECUERDOS QUE TODAVIA NO SE PUDIERON VECTORIZAR
# ==============================================================================
# Un recuerdo se guarda convirtiendolo en vector con Gemini, y esa llamada sale
# a internet. Se midio: uno de cada diez intentos fallaba con un corte SSL. Con
# el codigo anterior ese recuerdo se perdia, y la interfaz decia "No se guardo
# en la memoria: el texto es demasiado corto o no se pudo vectorizar" -- una
# frase que ADIVINABA el motivo, porque el error real se lo habia tragado un
# `except` sin guardarlo en ninguna parte.
#
# Ahora no se pierde: si no se puede vectorizar, el texto se escribe en disco y
# se reintenta a la primera ocasion. Guardar no puede depender de que la red
# acierte a la primera.

#: Donde esperan los recuerdos que aun no tienen vector. Un JSON por linea,
#: para poder anadir sin releer el archivo entero y para que un corte a mitad
#: solo estropee la ultima linea.
NOMBRE_PENDIENTES = "memoria_pendiente.jsonl"

#: Tope de recuerdos en espera. Si la red lleva mucho tiempo caida, mas vale
#: soltar los mas viejos que dejar crecer un archivo sin limite.
MAXIMO_PENDIENTES = 500


def ruta_pendientes() -> str:
    """
    Devuelve el archivo donde esperan los recuerdos sin vector.

    Returns:
        Ruta dentro de `data/`, que es carpeta montada y sobrevive al
        contenedor.
    """
    carpeta = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")
    os.makedirs(carpeta, exist_ok=True)
    return os.path.join(carpeta, NOMBRE_PENDIENTES)


def _apuntar_pendiente(texto: str, sesion_id: str, tipo: str,
                       motivo: str) -> bool:
    """
    Deja un recuerdo en espera de poder vectorizarse.

    Args:
        texto: El recuerdo.
        sesion_id: Conversacion de la que salio.
        tipo: Categoria del recuerdo.
        motivo: Por que no se pudo guardar ahora.

    Returns:
        True si quedo anotado en disco.
    """
    fila = {
        "texto": texto,
        "sesion_id": sesion_id,
        "tipo": tipo,
        "motivo": motivo[:200],
        "cuando": datetime.datetime.now(
            datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    try:
        ruta = ruta_pendientes()
        with open(ruta, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(fila, ensure_ascii=False) + "\n")

        # Se recorta solo si hace falta, conservando los MAS RECIENTES.
        with open(ruta, "r", encoding="utf-8") as fh:
            lineas = fh.readlines()
        if len(lineas) > MAXIMO_PENDIENTES:
            with open(ruta, "w", encoding="utf-8") as fh:
                fh.writelines(lineas[-MAXIMO_PENDIENTES:])
        return True
    except OSError:
        return False


def pendientes() -> list:
    """
    Lee los recuerdos que esperan vector.

    Returns:
        Lista de diccionarios. Vacia si no hay archivo o no se puede leer.
    """
    ruta = ruta_pendientes()
    if not os.path.exists(ruta):
        return []
    filas = []
    try:
        with open(ruta, "r", encoding="utf-8") as fh:
            for linea in fh:
                linea = linea.strip()
                if not linea:
                    continue
                try:
                    filas.append(json.loads(linea))
                except ValueError:
                    continue  # linea rota por un corte: se salta
    except OSError:
        return []
    return filas


def _escribir_recuerdo(texto: str, sesion_id: str, tipo: str) -> tuple:
    """
    Intenta vectorizar un recuerdo y escribirlo en Qdrant.

    Es la parte que puede fallar por red. Se separa para que el guardado
    normal y el reintento de los pendientes usen EXACTAMENTE el mismo camino:
    reintentar por una via distinta de la de produccion no demuestra nada.

    Args:
        texto: El recuerdo.
        sesion_id: Conversacion de procedencia.
        tipo: Categoria.

    Returns:
        Tupla `(se_guardo, motivo)`. El motivo viene vacio si se guardo.
    """
    try:
        from core import vectorial
        from qdrant_client import models

        cliente = vectorial._cliente_qdrant()
        asegurar_coleccion_memoria(cliente)

        # Con reintentos: un corte de red es pasajero, y volver a intentarlo
        # es justo lo que lo arregla. Antes iba con `reintentar=False`.
        vectores = vectorial.embeber([texto], "RETRIEVAL_DOCUMENT",
                                     reintentar=True)
        if not vectores or vectores[0] is None:
            return False, "la API de embeddings no devolvio vector"

        ahora = datetime.datetime.now(
            datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        cliente.upsert(
            collection_name=COLECCION_MEMORIA,
            points=[models.PointStruct(
                id=str(uuid.uuid4()),
                vector={vectorial.NOMBRE_VECTOR: vectores[0]},
                payload={"texto": texto, "sesion_id": sesion_id,
                         "tipo": tipo, "fecha": ahora})])
        return True, ""
    except Exception as exc:
        # El motivo se DEVUELVE en vez de tragarse. Tragarlo es lo que obligaba
        # a la interfaz a inventar "el texto es demasiado corto o no se pudo
        # vectorizar" sin saber cual de las dos cosas era.
        return False, f"{type(exc).__name__}: {str(exc)[:180]}"


def reintentar_pendientes(limite: int = 25) -> dict:
    """
    Vuelve a intentar guardar los recuerdos que se quedaron sin vector.

    Se llama al arrancar la API y despues de cada guardado con exito: si la
    red vuelve, lo pendiente entra sin que nadie tenga que hacer nada.

    Args:
        limite: Cuantos intentar en esta pasada, para no bloquear una
            peticion si se acumularon muchos.

    Returns:
        Diccionario con `intentados`, `guardados` y `siguen_esperando`.
    """
    filas = pendientes()
    if not filas:
        return {"intentados": 0, "guardados": 0, "siguen_esperando": 0}

    quedan, guardados = [], 0
    for numero, fila in enumerate(filas):
        if numero >= limite:
            quedan.append(fila)
            continue
        logrado, _motivo = _escribir_recuerdo(
            fila.get("texto", ""), fila.get("sesion_id", "default"),
            fila.get("tipo", "hecho_usuario"))
        if logrado:
            guardados += 1
        else:
            quedan.append(fila)

    try:
        ruta = ruta_pendientes()
        if quedan:
            with open(ruta, "w", encoding="utf-8") as fh:
                for fila in quedan:
                    fh.write(json.dumps(fila, ensure_ascii=False) + "\n")
        elif os.path.exists(ruta):
            os.remove(ruta)
    except OSError:
        pass

    return {"intentados": min(len(filas), limite), "guardados": guardados,
            "siguen_esperando": len(quedan)}


def guardar_recuerdo_detallado(texto: str, sesion_id: str = "default",
                               tipo: str = "hecho_usuario") -> dict:
    """
    Guarda un recuerdo y cuenta exactamente como acabo.

    QUE PASA SI LA RED FALLA
    ------------------------
    Nada se pierde. Si no se puede convertir en vector ahora mismo, el texto
    queda anotado en disco y se reintenta en el siguiente guardado o al
    arrancar la API. Devolver False y olvidarlo era lo que hacia aparecer
    "No se guardo en la memoria" en mitad de una conversacion, con el texto
    ya perdido.

    Args:
        texto: Texto del recuerdo.
        sesion_id: Sesion de procedencia.
        tipo: Categoria de memoria.

    Returns:
        Diccionario con `a_salvo` (el recuerdo no se ha perdido), `en_indice`
        (ya se puede buscar), `en_espera` (cuantos aguardan vector) y
        `motivo` si algo fallo.
    """
    if not texto or len(texto.strip()) < 8:
        return {"a_salvo": False, "en_indice": False, "en_espera": 0,
                "motivo": "el texto es demasiado corto para ser un recuerdo"}

    logrado, motivo = _escribir_recuerdo(texto, sesion_id, tipo)
    if logrado:
        # Aprovechando que la red responde, se intenta colar lo que estaba
        # esperando: es el momento con mas probabilidades de exito.
        try:
            reintentar_pendientes(limite=10)
        except Exception:
            pass
        return {"a_salvo": True, "en_indice": True,
                "en_espera": len(pendientes()), "motivo": ""}

    # No se pudo vectorizar: a la cola, con el motivo de verdad apuntado.
    anotado = _apuntar_pendiente(texto, sesion_id, tipo, motivo)
    return {
        "a_salvo": bool(anotado),
        "en_indice": False,
        "en_espera": len(pendientes()),
        "motivo": motivo if anotado else (
            f"no se pudo guardar ni anotar para reintentarlo: {motivo}"),
    }


def guardar_recuerdo(texto: str, sesion_id: str = "default",
                     tipo: str = "hecho_usuario") -> bool:
    """
    Guarda un recuerdo. Version corta, para quien solo quiere saber si vale.

    Args:
        texto: Texto del recuerdo.
        sesion_id: Sesion de procedencia.
        tipo: Categoria de memoria.

    Returns:
        True si el recuerdo quedo a salvo, ya sea en el indice o en la cola.
    """
    return guardar_recuerdo_detallado(texto, sesion_id, tipo)["a_salvo"]


def buscar_recuerdos(consulta: str, limite: int = 3, umbral: float = 0.45) -> List[dict]:
    """
    Busca recuerdos del usuario en Qdrant que tengan relacion semantica con la pregunta.
    
    Args:
        consulta: Lo que el usuario esta preguntando hoy.
        limite: Maximo de recuerdos a traer.
        umbral: Similitud minima del coseno para considerarlo relevante.
        
    Returns:
        Lista de diccionarios con `texto`, `fecha`, `puntaje` y `sesion_id`.
    """
    if not consulta or len(consulta.strip()) < 5:
        return []
    try:
        from core import vectorial

        cliente = vectorial._cliente_qdrant()
        if not cliente.collection_exists(COLECCION_MEMORIA):
            return []

        vectores = vectorial.embeber([consulta], "RETRIEVAL_QUERY", reintentar=False)
        if not vectores:
            return []

        respuesta_qdrant = cliente.query_points(
            collection_name=COLECCION_MEMORIA,
            query=vectores[0],
            using=vectorial.NOMBRE_VECTOR,
            limit=limite,
            score_threshold=umbral,
            with_payload=True,
        )
        resultados = respuesta_qdrant.points

        recuerdos = []
        for r in resultados:
            recuerdos.append({
                "texto": r.payload.get("texto", ""),
                "fecha": r.payload.get("fecha", ""),
                "sesion_id": r.payload.get("sesion_id", ""),
                "puntaje": round(float(r.score), 3),
            })
        return recuerdos
    except Exception:
        return []


def reparar_fechas() -> dict:
    """
    Deja todas las fechas de la memoria en el mismo formato y ordenables.

    QUE ARREGLA
    -----------
    Dos cosas que impedian ordenar los recuerdos:

      1. Convivian dos formatos. Los recuerdos antiguos se guardaron como
         "2026-10-19 01:55" y los nuevos como "2026-10-19T01:55:00Z". Mezclados
         no se pueden comparar ni ordenar.
      2. El campo `fecha` estaba indexado como KEYWORD, que solo sirve para
         buscar por valor exacto. Qdrant rechaza `order_by` sobre un campo asi.

    POR QUE LA CONVERSION ES EXACTA Y NO UNA SUPOSICION
    ---------------------------------------------------
    Las fechas viejas las escribio `datetime.now()` dentro del contenedor de la
    API, y ese contenedor corre en UTC (no tiene TZ definida). Asi que ya eran
    UTC: solo cambia como estaban escritas, no el momento que representan. No
    se desplaza ninguna hora.

    No cuesta cuota: se reescribe el payload, no los vectores.

    Returns:
        Diccionario con `convertidas`, `ya_estaban`, `indice` y `total`.
    """
    from core import vectorial
    from qdrant_client import models

    cliente = vectorial._cliente_qdrant()
    if not cliente.collection_exists(COLECCION_MEMORIA):
        return {"convertidas": 0, "ya_estaban": 0, "indice": "sin coleccion",
                "total": 0}

    convertidas, ya_estaban, total = 0, 0, 0
    siguiente = None
    while True:
        lote, siguiente = cliente.scroll(
            collection_name=COLECCION_MEMORIA, limit=256, offset=siguiente,
            with_payload=["fecha"], with_vectors=False)
        if not lote:
            break
        for punto in lote:
            total += 1
            fecha = ((punto.payload or {}).get("fecha") or "").strip()
            if not fecha:
                continue
            if "T" in fecha and fecha.endswith("Z"):
                ya_estaban += 1
                continue
            # "2026-10-19 01:55" -> "2026-10-19T01:55:00Z"
            try:
                partes = fecha.split(" ")
                dia = partes[0]
                hora = partes[1] if len(partes) > 1 else "00:00"
                if hora.count(":") == 1:
                    hora += ":00"
                nueva = f"{dia}T{hora}Z"
                # Se valida antes de escribir: una fecha mal formada en un
                # indice DATETIME hace fallar toda la coleccion.
                datetime.datetime.strptime(nueva, "%Y-%m-%dT%H:%M:%SZ")
            except Exception:
                continue
            cliente.set_payload(collection_name=COLECCION_MEMORIA,
                                payload={"fecha": nueva}, points=[punto.id])
            convertidas += 1
        if siguiente is None:
            break

    # El indice viejo (KEYWORD) se tira y se crea el de fecha. Hay que hacerlo
    # DESPUES de normalizar: si quedara una fecha con el formato antiguo, el
    # indice DATETIME no se podria construir.
    estado_indice = "creado"
    try:
        cliente.delete_payload_index(collection_name=COLECCION_MEMORIA,
                                     field_name="fecha")
    except Exception:
        pass
    try:
        cliente.create_payload_index(
            collection_name=COLECCION_MEMORIA, field_name="fecha",
            field_schema=models.PayloadSchemaType.DATETIME)
    except Exception as exc:
        estado_indice = f"no se pudo crear: {str(exc)[:120]}"

    return {"convertidas": convertidas, "ya_estaban": ya_estaban,
            "indice": estado_indice, "total": total}


def renombrar_sesion(sesion_id: str, titulo: str) -> bool:
    """
    Cambia el titulo de una conversacion.

    El titulo lo ponia automaticamente la primera pregunta, asi que la lista
    acababa llena de entradas como "hola" o "recuerdas la primera pregunta
    que te", imposibles de distinguir. Poder renombrarlas es lo que convierte
    esa lista en algo utilizable.

    Args:
        sesion_id: Conversacion a renombrar.
        titulo: Titulo nuevo. Se recorta a 80 caracteres y se le quitan los
            espacios de los extremos.

    Returns:
        True si se cambio algo.
    """
    titulo = (titulo or "").strip()[:80]
    if not sesion_id or not titulo:
        return False
    conn = _conectar_sqlite()
    try:
        with conn:
            cur = conn.execute(
                "UPDATE sesiones SET titulo = ?, actualizada_en = ? WHERE id = ?",
                (titulo, datetime.datetime.now().isoformat(timespec="seconds"),
                 sesion_id))
        return cur.rowcount > 0
    finally:
        conn.close()


def borrar_sesion(sesion_id: str) -> dict:
    """
    Elimina una conversacion de los DOS sitios donde vive.

    POR QUE HAY QUE BORRAR EN DOS SITIOS
    ------------------------------------
    Una conversacion deja rastro en dos almacenes distintos:

      - SQLite (`sesiones` y `mensajes`): el historial literal, lo que se ve
        al reabrir la charla.
      - Qdrant (`memoria_usuario`): los recuerdos vectorizados, que el agente
        consulta para acordarse de lo hablado.

    Borrar solo de SQLite dejaria la conversacion invisible pero viva: el
    agente seguiria recordando cosas de una charla que el usuario cree
    eliminada. Eso no es un descuido menor, es lo contrario de lo que se pidio
    al pulsar "eliminar".

    Args:
        sesion_id: Conversacion a eliminar.

    Returns:
        Diccionario con `mensajes`, `recuerdos` y `sesion`, que dice cuantas
        filas y cuantos puntos se quitaron de cada sitio.
    """
    resultado = {"mensajes": 0, "recuerdos": 0, "sesion": False}
    if not sesion_id:
        return resultado

    # --- 1. El historial literal -----------------------------------------
    conn = _conectar_sqlite()
    try:
        with conn:
            cur = conn.execute("DELETE FROM mensajes WHERE sesion_id = ?",
                               (sesion_id,))
            resultado["mensajes"] = cur.rowcount
            cur = conn.execute("DELETE FROM sesiones WHERE id = ?",
                               (sesion_id,))
            resultado["sesion"] = cur.rowcount > 0
    finally:
        conn.close()

    # --- 2. Los recuerdos vectorizados ------------------------------------
    try:
        from core import vectorial
        from qdrant_client import models

        cliente = vectorial._cliente_qdrant()
        if cliente.collection_exists(COLECCION_MEMORIA):
            filtro = models.Filter(must=[models.FieldCondition(
                key="sesion_id",
                match=models.MatchValue(value=sesion_id))])
            # Se cuentan antes de borrar: despues ya no hay nada que contar.
            resultado["recuerdos"] = cliente.count(
                COLECCION_MEMORIA, count_filter=filtro, exact=True).count
            cliente.delete(collection_name=COLECCION_MEMORIA,
                           points_selector=models.FilterSelector(filter=filtro))
    except Exception:
        # Si Qdrant no responde, el historial ya se borro. Se informa con un
        # -1 para que quien llama pueda avisar en vez de dar por hecho que
        # quedo todo limpio.
        resultado["recuerdos"] = -1

    return resultado
