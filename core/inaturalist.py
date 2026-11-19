# -*- coding: utf-8 -*-
"""
Consulta en vivo a la API publica de iNaturalist (Naturalista Colombia).
===============================================================================

POR QUE EXISTE ESTE MODULO
--------------------------
Permite que el Copiloto Ambiental consulte observaciones reales de campo,
taxonomia validada, nombres comunes en espanol y fotografias de especies
(principalmente mariposas y fauna/flora) registradas en el municipio de San Carlos,
Antioquia (y su zona de influencia), utilizando la API abierta de iNaturalist
(https://api.inaturalist.org/v1/).

NO REQUIERE API KEY
-------------------
La API de iNaturalist es publica y gratuita para lectura. Se utiliza un User-Agent
identificado segun las buenas practicas del servicio.
"""

import json
import logging
import urllib.parse
import urllib.request

logger = logging.getLogger(__name__)

USER_AGENT = "CopilotoAmbiental/1.0 (evaluacion.ambiental.sancarlos@proyecto.local)"
TIMEOUT_SECS = 8

# Coordenadas envolventes (bounding box) oficiales del Municipio de San Carlos, Antioquia
SAN_CARLOS_BOUNDS = {
    "nelat": 6.3387,
    "nelng": -74.7132,
    "swlat": 6.0429,
    "swlng": -75.1223,
}

# Taxon ID de Lepidoptera (Mariposas y polillas) en iNaturalist
TAXON_ID_LEPIDOPTERA = 47157
# Place ID de Antioquia en iNaturalist (para busqueda regional si se requiere)
PLACE_ID_ANTIOQUIA = 9495


def _hacer_peticion(url: str) -> dict:
    """Ejecuta una peticion HTTP GET a la API de iNaturalist."""
    try:
        req = urllib.request.Request(
            url,
            headers={
                "User-Agent": USER_AGENT,
                "Accept": "application/json",
            },
        )
        with urllib.request.urlopen(req, timeout=TIMEOUT_SECS) as resp:
            if resp.status == 200:
                return json.loads(resp.read().decode("utf-8", errors="replace"))
    except Exception as exc:
        logger.warning("[inaturalist] Fallo consulta a %s: %s", url, exc)
    return {}


def buscar_taxones(query: str, limite: int = 5) -> list:
    """
    Busca taxones (especies, generos, familias) por nombre comun o cientifico.
    """
    if not query or not query.strip():
        return []
    q_enc = urllib.parse.quote(query.strip())
    url = f"https://api.inaturalist.org/v1/taxa?q={q_enc}&locale=es&per_page={limite}"
    data = _hacer_peticion(url)
    resultados = []
    for t in data.get("results", []):
        foto = t.get("default_photo") or {}
        resultados.append({
            "id": t.get("id"),
            "nombre_cientifico": t.get("name"),
            "nombre_comun": t.get("preferred_common_name") or "(sin nombre comun registrado)",
            "rango": t.get("rank"),
            "icono": foto.get("square_url") or foto.get("url"),
            "foto_mediana": foto.get("medium_url"),
            "estado_conservacion": (t.get("conservation_status") or {}).get("status_name"),
            "wikipedia_url": t.get("wikipedia_url"),
        })
    return resultados


def buscar_observaciones_san_carlos(
    termino: str = "",
    solo_lepidopteros: bool = True,
    limite: int = 6,
) -> tuple:
    """
    Recupera observaciones reales georreferenciadas dentro de San Carlos, Antioquia.

    Returns:
        Tupla (total_hallados, lista_de_observaciones)
    """
    params = [
        f"nelat={SAN_CARLOS_BOUNDS['nelat']}",
        f"nelng={SAN_CARLOS_BOUNDS['nelng']}",
        f"swlat={SAN_CARLOS_BOUNDS['swlat']}",
        f"swlng={SAN_CARLOS_BOUNDS['swlng']}",
        f"per_page={limite}",
        "locale=es",
        "order=desc",
        "order_by=votes",
    ]
    if solo_lepidopteros:
        params.append(f"taxon_id={TAXON_ID_LEPIDOPTERA}")
    if termino and termino.strip():
        params.append(f"q={urllib.parse.quote(termino.strip())}")

    url = f"https://api.inaturalist.org/v1/observations?{'&'.join(params)}"
    data = _hacer_peticion(url)
    total = data.get("total_results", 0)
    obs = []
    for o in data.get("results", []):
        t = o.get("taxon") or {}
        fotos = o.get("photos") or []
        foto_url = fotos[0].get("url") if fotos else None
        if foto_url and "square" in foto_url:
            foto_url = foto_url.replace("square", "medium")

        obs.append({
            "id": o.get("id"),
            "nombre_cientifico": t.get("name") or "Indet.",
            "nombre_comun": t.get("preferred_common_name") or "",
            "lugar": o.get("place_guess") or "San Carlos, Antioquia",
            "coordenadas": o.get("location"),
            "fecha": o.get("observed_on_details", {}).get("date") or o.get("observed_on"),
            "calidad": "Grado de Investigacion (confirmado por expertos)" if o.get("quality_grade") == "research" else "Casual / En verificacion",
            "foto_url": foto_url,
            "enlace": f"https://www.inaturalist.org/observations/{o.get('id')}",
        })
    return total, obs


def consultar_resumen(termino: str = "", solo_san_carlos: bool = True) -> str:
    """
    Devuelve un texto formateado con hallazgos de iNaturalist para que el Agente
    lo integre en su apartado de Bases y elabore la respuesta.
    """
    termino = (termino or "").strip()
    total_obs, observaciones = buscar_observaciones_san_carlos(
        termino=termino,
        solo_lepidopteros=True,
        limite=5,
    )
    taxones = buscar_taxones(termino, limite=3) if termino else []

    # Si no hubo observaciones con el termino especifico en San Carlos, buscar taxones y
    # observaciones generales de mariposas en San Carlos para contexto
    if not observaciones and termino:
        # Intentar buscar observaciones en Antioquia como contexto si en San Carlos no hay registro
        params_ant = [
            f"place_id={PLACE_ID_ANTIOQUIA}",
            f"taxon_id={TAXON_ID_LEPIDOPTERA}",
            f"q={urllib.parse.quote(termino)}",
            "locale=es",
            "per_page=3",
        ]
        data_ant = _hacer_peticion(f"https://api.inaturalist.org/v1/observations?{'&'.join(params_ant)}")
        total_ant = data_ant.get("total_results", 0)
    else:
        total_ant = 0

    lineas = [
        f"Fuente: iNaturalist API — Observaciones de Lepidopteros en San Carlos, Antioquia"
    ]

    if taxones:
        lineas.append("\n[Taxonomia y Nombres Registrados en iNaturalist]")
        for t in taxones:
            comun = f" ('{t['nombre_comun']}')" if t["nombre_comun"] and t["nombre_comun"] != "(sin nombre comun registrado)" else ""
            lineas.append(f"  * {t['rango'].capitalize()}: {t['nombre_cientifico']}{comun}")
            if t["estado_conservacion"]:
                lineas.append(f"    - Estado de conservacion: {t['estado_conservacion']}")
            if t["foto_mediana"]:
                lineas.append(f"    - Foto de referencia: {t['foto_mediana']}")

    lineas.append(f"\n[Observaciones Reales en San Carlos, Antioquia]")
    lineas.append(f"Total de registros de campo en San Carlos: {total_obs}")

    if observaciones:
        for o in observaciones:
            nom = o["nombre_cientifico"]
            if o["nombre_comun"]:
                nom += f" ('{o['nombre_comun']}')"
            lineas.append(f"  - Registro #{o['id']}: {nom}")
            lineas.append(f"    Ubicacion: {o['lugar']} ({o['coordenadas'] or 'coord. reservada'})")
            lineas.append(f"    Validacion: {o['calidad']} | Fecha: {o['fecha']}")
            if o["foto_url"]:
                lineas.append(f"    Foto de campo: {o['foto_url']}")
            lineas.append(f"    Ficha en linea: {o['enlace']}")
    elif total_ant > 0:
        lineas.append(
            f"  * NOTA: No hay avistamientos directos de '{termino}' dentro de las coordenadas de San Carlos, "
            f"pero se encontraron {total_ant} observaciones en otros municipios de Antioquia."
        )
    else:
        lineas.append(
            f"  * No se hallaron observaciones registradas con el termino '{termino}' en la zona de San Carlos."
        )

    return "\n".join(lineas)
