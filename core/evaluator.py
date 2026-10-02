"""
Motor de calculo de la metodologia Conesa.
===============================================================================

Calcula el Indice de Importancia (I) de un impacto ambiental y lo clasifica
cualitativamente.

Este modulo es la UNICA fuente de verdad de los umbrales de severidad. El
prompt del agente de IA los lee de aqui (`ConesaEvaluator.UMBRALES`) en lugar
de repetirlos en su texto, que era lo que ocurria antes: el prompt decia
"25 / 50 / 75" y el codigo tambien, y nada garantizaba que siguieran iguales.
"""

from core.validator import ConesaValidator


class ConesaEvaluator:
    """
    Aplica la ecuacion de Conesa y clasifica el resultado.

    Uso tipico::

        ConesaEvaluator.calcular_importancia({
            "signo": "-", "i": 4, "EX": 2, "MO": 4, "PE": 4, "RV": 2,
            "SI": 2, "AC": 1, "EF": 4, "PR": 4, "MC": 4,
        })
        # -> {'importancia': -43, 'categoria': 'Moderado', ...}
    """

    #: Coeficientes de la ecuacion. La Intensidad pesa el triple y la Extension
    #: el doble; el resto de parametros suman con peso 1. Se dejan explicitos
    #: para que quien lea el codigo vea la formula sin descifrar una expresion.
    PESOS = {
        "i": 3, "EX": 2, "MO": 1, "PE": 1, "RV": 1,
        "SI": 1, "AC": 1, "EF": 1, "PR": 1, "MC": 1,
    }

    #: Umbrales de severidad sobre el valor ABSOLUTO de la importancia.
    #: Se evaluan en orden; el ultimo tiene limite None y actua como "todo lo
    #: que quede por encima".
    UMBRALES = [
        ("Bajo",     25),   # poco significativo, medidas preventivas basicas
        ("Moderado", 50),   # requiere plan de manejo y mitigacion estandar
        ("Severo",   75),   # requiere mitigacion y monitoreo estrictos
        ("Critico",  None),  # altera irreversiblemente, exige rediseno
    ]

    #: Recomendacion asociada a cada categoria. Se usa en los informes y en las
    #: respuestas del agente para no redactarla a mano cada vez.
    RECOMENDACIONES = {
        "Bajo": "Poco significativo. Bastan medidas preventivas basicas.",
        "Moderado": "Requiere plan de manejo ambiental y medidas de "
                    "mitigacion estandar.",
        "Severo": "Impacto significativo. Exige medidas de mitigacion y un "
                  "programa de monitoreo estricto.",
        "Critico": "Alerta maxima. Altera el medio de forma irreversible; "
                   "obliga a replantear el diseno del proyecto.",
    }

    @classmethod
    def clasificar(cls, importancia) -> str:
        """
        Traduce un valor de importancia a su categoria cualitativa.

        La clasificacion se hace sobre el valor absoluto: un impacto positivo
        de +80 es tan significativo como uno negativo de -80; el signo indica
        si beneficia o perjudica, no la magnitud.

        Args:
            importancia: Valor numerico de la importancia (con signo).

        Returns:
            "Bajo", "Moderado", "Severo" o "Critico".
        """
        absoluto = abs(importancia)
        for etiqueta, limite in cls.UMBRALES:
            if limite is None or absoluto <= limite:
                return etiqueta
        return cls.UMBRALES[-1][0]

    @classmethod
    def calcular_importancia(cls, params: dict, estricto: bool = False) -> dict:
        """
        Calcula la importancia de un impacto y su severidad.

        Formula::

            I = signo * (3*i + 2*EX + MO + PE + RV + SI + AC + EF + PR + MC)

        Args:
            params: Diccionario con `signo` y las 10 siglas de la metodologia.
            estricto: Se pasa tal cual al validador. Ver
                `ConesaValidator.validar_parametros`.

        Returns:
            Diccionario con:
                importancia   : valor numerico con signo.
                importancia_abs: valor absoluto.
                categoria     : severidad cualitativa.
                recomendacion : accion sugerida para esa severidad.
                signo         : el signo empleado.

        Raises:
            ValueError: Si algun parametro es invalido (lo lanza el validador).
        """
        ConesaValidator.validar_parametros(params, estricto=estricto)

        # La suma ponderada se arma recorriendo PESOS, de modo que anadir o
        # cambiar un coeficiente sea un cambio de una linea en un diccionario y
        # no una edicion de la expresion aritmetica.
        base = sum(
            peso * int(params[sigla]) for sigla, peso in cls.PESOS.items()
        )

        signo = params["signo"]
        importancia = -base if signo == "-" else base
        categoria = cls.clasificar(importancia)

        return {
            "importancia": importancia,
            "importancia_abs": abs(importancia),
            "categoria": categoria,
            "recomendacion": cls.RECOMENDACIONES.get(categoria, ""),
            "signo": signo,
        }

    @classmethod
    def descripcion_umbrales(cls) -> str:
        """
        Devuelve los umbrales en texto legible, para prompts e informes.

        Returns:
            Cadena multilinea, una linea por categoria.
        """
        lineas, anterior = [], 0
        for etiqueta, limite in cls.UMBRALES:
            if limite is None:
                lineas.append(f"|I| > {anterior:<3} -> {etiqueta}")
            else:
                lineas.append(f"{anterior:>3} < |I| <= {limite:<3} -> {etiqueta}")
                anterior = limite
        return "\n".join(lineas)


if __name__ == "__main__":
    # Pruebas manuales rapidas: python -m core.evaluator
    print("Umbrales de severidad:")
    print(ConesaEvaluator.descripcion_umbrales())
    print()

    caso_positivo = {
        "signo": "+", "i": 2, "EX": 2, "MO": 4, "PE": 4,
        "RV": 1, "SI": 2, "AC": 1, "EF": 4, "PR": 4, "MC": 1,
    }
    print("Caso positivo (esperado 31, Moderado):",
          ConesaEvaluator.calcular_importancia(caso_positivo))

    caso_negativo = {
        "signo": "-", "i": 1, "EX": 1, "MO": 4, "PE": 1,
        "RV": 1, "SI": 1, "AC": 1, "EF": 4, "PR": 1, "MC": 1,
    }
    print("Caso negativo (esperado -19, Bajo):",
          ConesaEvaluator.calcular_importancia(caso_negativo))
