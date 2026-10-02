"""
Construccion del prompt de sistema del Agente de IA.
===============================================================================

QUE CAMBIO Y POR QUE
--------------------
Antes este archivo era una constante de texto con datos escritos a mano:

    - "136.997 registros de especies observadas en Antioquia"
    - "Capa Municipal: `antioquia_municipios.geojson` (125 municipios)"
    - "Coordenadas de Referencia: Latitud 6.1872, Longitud -74.9922"

Los tres eran falsos o fragiles:
  * el archivo real tenia ~2.000 registros, no 136.997;
  * `antioquia_municipios.geojson` ni siquiera existe (se llama
    `municipios_filtrados.geojson`);
  * las coordenadas fijaban el proyecto a un municipio concreto aunque el
    usuario hubiera generado el area de estudio de otro departamento.

Un modelo de lenguaje repite con total seguridad lo que le pongas en el prompt.
Meterle cifras quemadas es garantizar que le mienta al usuario.

AHORA el prompt se ARMA en el momento de cada conversacion, leyendo el estado
real del disco: que departamento hay cargado, cuantos registros tiene el CSV
filtrado, que capas ofrece la Geodatabase. Si no hay area de estudio generada,
el prompt lo dice explicitamente y le prohibe al modelo inventarse cifras.

La parte metodologica (la formula de Conesa, los rangos y los umbrales) SI es
fija, porque es la definicion de la metodologia. Pero ni siquiera esos numeros
se escriben aqui: se leen de `core.validator` y `core.evaluator`, que son la
unica fuente de verdad del sistema.
"""

from core.validator import ConesaValidator
from core.evaluator import ConesaEvaluator


# ==============================================================================
# BLOQUES FIJOS  (definicion de la metodologia, no datos de un proyecto)
# ==============================================================================

IDENTIDAD = """
Eres un Agente de IA experto en Evaluacion de Impacto Ambiental (EIA),
especializado en la metodologia de Conesa Fernandez-Vitora.

Tu trabajo es ayudar a evaluar el impacto de un proyecto sobre el medio, a
analizar la linea base geografica y biotica, y a diagnosticar susceptibilidades
del terreno.
""".strip()

COMPORTAMIENTO = """
REGLAS DE COMPORTAMIENTO
------------------------
1. Comunicate en espanol, de forma profesional, clara y didactica.
2. NUNCA inventes datos. Si necesitas una cifra geografica o un calculo,
   ejecuta la herramienta correspondiente y usa su resultado.
3. Si una herramienta te dice que un dato "no fue evaluado" o "no esta
   disponible", di exactamente eso. No lo interpretes como "no hay riesgo":
   no haber mirado no es lo mismo que haber mirado y no encontrar nada.
4. Si el usuario pregunta por informacion del area de estudio (departamento,
   especies, cantidad de registros), consulta la herramienta de biodiversidad
   en vez de responder de memoria.
5. Cuando el usuario describa una accion del proyecto sobre un factor
   ambiental, puedes proponer los 11 parametros de Conesa argumentandolos
   tecnicamente, pero el calculo final siempre lo hace la herramienta.
""".strip()


def _bloque_metodologia() -> str:
    """
    Genera la explicacion de la metodologia leyendo los rangos y umbrales
    reales del codigo, para que prompt y motor de calculo nunca se desalineen.

    Returns:
        Texto con la formula, los rangos validos por parametro y los umbrales
        de severidad, tal como estan definidos en `core/`.
    """
    lineas = [
        "METODOLOGIA CONESA",
        "------------------",
        "Formula de la Importancia:",
        "    I = signo * [3*i + 2*EX + MO + PE + RV + SI + AC + EF + PR + MC]",
        "",
        "Parametros y valores admitidos (definidos en core/validator.py):",
    ]

    for sigla, valores in ConesaValidator.RANGOS.items():
        descripcion = ConesaValidator.DESCRIPCIONES.get(sigla, "")
        admitidos = ", ".join(str(v) for v in valores)
        lineas.append(f"    {sigla:<3} {descripcion:<28} valores: {admitidos}")

    lineas += [
        "",
        "El signo es '+' (impacto beneficioso) o '-' (impacto perjudicial).",
        "",
        "Clasificacion de la severidad sobre |I| "
        "(definida en core/evaluator.py):",
    ]

    for etiqueta, limite in ConesaEvaluator.UMBRALES:
        if limite is None:
            lineas.append(f"    |I| mayor que el anterior -> {etiqueta.upper()}")
        else:
            lineas.append(f"    |I| <= {limite:<3} -> {etiqueta.upper()}")

    return "\n".join(lineas)


# ==============================================================================
# BLOQUE DINAMICO  (estado real del proyecto en este momento)
# ==============================================================================

def _bloque_contexto(contexto: dict) -> str:
    """
    Describe el area de estudio realmente cargada en disco.

    Args:
        contexto: Diccionario producido por
            `core.dynamic_filter.resumen_area_generada()`, opcionalmente
            enriquecido con la clave `capas_gdb` (lista de nombres de capa).

    Returns:
        Texto con el estado real, o una advertencia explicita si no hay nada
        generado todavia.
    """
    if not contexto:
        return (
            "ESTADO DEL AREA DE ESTUDIO\n"
            "--------------------------\n"
            "No hay informacion del area de estudio disponible. No afirmes "
            "nada sobre departamentos, especies ni cantidades de registros: "
            "pidele al usuario que genere primero el area de estudio en la "
            "pestana 'Area de Estudio'."
        )

    lineas = ["ESTADO DEL AREA DE ESTUDIO", "--------------------------"]

    if contexto.get("biodiversidad_existe") and contexto.get("registros") is not None:
        lineas.append(
            f"Linea base biotica: el archivo "
            f"'{_solo_nombre(contexto['biodiversidad_archivo'])}' contiene "
            f"{contexto['registros']} registros de ocurrencias."
        )
    else:
        lineas.append(
            "Linea base biotica: NO hay archivo de biodiversidad generado. "
            "No cites ninguna cifra de registros."
        )

    departamentos = contexto.get("departamentos") or []
    if contexto.get("municipios_existe") and departamentos:
        lineas.append(
            f"Cobertura cartografica: "
            f"{contexto.get('municipios_total', '?')} municipios del "
            f"departamento de {', '.join(departamentos)}, en el archivo "
            f"'{_solo_nombre(contexto['municipios_archivo'])}' (EPSG:4326)."
        )
    else:
        lineas.append(
            "Cobertura cartografica: NO hay capa municipal generada. "
            "No afirmes en que departamento se encuentra el proyecto."
        )

    capas = contexto.get("capas_gdb")
    if capas:
        lineas.append(
            f"Geodatabase conectada con {len(capas)} capas disponibles: "
            f"{', '.join(capas[:12])}"
            + (" ..." if len(capas) > 12 else "")
        )
    else:
        lineas.append(
            "Geodatabase: NO conectada. Las consultas de areas protegidas "
            "(RUNAP) y paramos no se pueden evaluar; reportalo como "
            "'no evaluado', nunca como 'sin riesgo'."
        )

    return "\n".join(lineas)


def _solo_nombre(ruta) -> str:
    """
    Extrae el nombre de archivo de una ruta.

    Se usa para no filtrarle al modelo la estructura de carpetas del computador
    del usuario, que no le aporta nada y es informacion privada.

    Args:
        ruta: Ruta completa o None.

    Returns:
        El nombre del archivo, o "(desconocido)".
    """
    import os
    return os.path.basename(str(ruta)) if ruta else "(desconocido)"


# ==============================================================================
# API PUBLICA
# ==============================================================================

def construir_system_prompt(contexto: dict = None) -> str:
    """
    Arma el prompt de sistema completo para una conversacion.

    Se llama en cada peticion, no una sola vez al importar el modulo, para que
    el modelo siempre vea el estado actual del proyecto: si el usuario acaba de
    generar el area de estudio de Choco, el agente ya habla de Choco.

    Args:
        contexto: Estado del area de estudio, normalmente el resultado de
            `core.dynamic_filter.resumen_area_generada()`. Si es None, el
            prompt le indica al modelo que no tiene contexto y no debe
            inventarlo.

    Returns:
        Cadena con el prompt de sistema listo para enviar al modelo.
    """
    return "\n\n".join([
        IDENTIDAD,
        _bloque_metodologia(),
        _bloque_contexto(contexto),
        COMPORTAMIENTO,
    ])


if __name__ == "__main__":
    # Permite revisar el prompt exacto que se le enviara al modelo:
    #   python -m agent.prompts
    from core.dynamic_filter import resumen_area_generada
    print(construir_system_prompt(resumen_area_generada()))
