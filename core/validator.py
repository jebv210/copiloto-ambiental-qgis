"""
Validacion de los parametros de la metodologia Conesa.
===============================================================================

Este modulo es la UNICA fuente de verdad sobre que valores son admisibles en
una valoracion de Conesa. Antes esa informacion estaba repetida en tres sitios:

    core/validator.py   -> el diccionario RANGOS
    api/server.py       -> las restricciones ge=/le= de los modelos Pydantic
    agent/prompts.py    -> el texto del prompt del agente

Tres copias significan tres oportunidades de que se desincronicen. Ahora las
otras dos LEEN de aqui.
"""


class ConesaValidator:
    """
    Valida que los parametros cuantitativos de Conesa esten en rango.

    Uso tipico::

        ConesaValidator.validar_parametros({
            "signo": "-", "i": 4, "EX": 2, "MO": 4, "PE": 4, "RV": 2,
            "SI": 2, "AC": 1, "EF": 4, "PR": 4, "MC": 4,
        })
    """

    #: Valores oficiales de la metodologia para cada parametro.
    #: La clave es la sigla usada en las matrices de impacto; el valor es la
    #: lista de calificaciones que contempla el metodo.
    RANGOS = {
        "i":  [1, 2, 4, 8, 12],   # Intensidad
        "EX": [1, 2, 4, 8, 12],   # Extension
        "MO": [1, 2, 4],          # Momento
        "PE": [1, 2, 4],          # Persistencia
        "RV": [1, 2, 4],          # Reversibilidad
        "SI": [1, 2, 4],          # Sinergia
        "AC": [1, 4],             # Acumulacion
        "EF": [1, 4],             # Efecto
        "PR": [1, 2, 4],          # Periodicidad
        "MC": [1, 2, 4, 8],       # Recuperabilidad
    }

    #: Nombre legible de cada sigla. Lo consume el prompt del agente y la
    #: documentacion de la API, para no volver a escribirlos a mano alla.
    DESCRIPCIONES = {
        "i":  "Intensidad",
        "EX": "Extension",
        "MO": "Momento",
        "PE": "Persistencia",
        "RV": "Reversibilidad",
        "SI": "Sinergia",
        "AC": "Acumulacion",
        "EF": "Efecto",
        "PR": "Periodicidad",
        "MC": "Recuperabilidad",
    }

    #: Signos admitidos: '+' impacto beneficioso, '-' impacto perjudicial.
    SIGNOS = ["+", "-"]

    @classmethod
    def limites(cls, sigla: str):
        """
        Devuelve el valor minimo y maximo admisible de un parametro.

        Lo usan los modelos Pydantic de la API para declarar sus restricciones
        sin repetir numeros.

        Args:
            sigla: Sigla del parametro (por ejemplo "EX").

        Returns:
            Tupla `(minimo, maximo)`.

        Raises:
            KeyError: Si la sigla no pertenece a la metodologia.
        """
        valores = cls.RANGOS[sigla]
        return min(valores), max(valores)

    @classmethod
    def validar_parametros(cls, params: dict, estricto: bool = False) -> bool:
        """
        Valida un diccionario completo de parametros de Conesa.

        Hay dos niveles de exigencia, y la diferencia es deliberada:

        * `estricto=False` (por defecto): acepta cualquier entero dentro del
          intervalo [minimo, maximo] del parametro, aunque no sea uno de los
          valores canonicos. Es decir, `AC=2` pasa aunque la metodologia solo
          contemple 1 y 4. Se mantiene asi porque las matrices reales que
          entregan los evaluadores ambientales a veces usan calificaciones
          intermedias, y rechazar el archivo completo por eso seria peor que
          procesarlo.

        * `estricto=True`: exige que el valor sea EXACTAMENTE uno de los
          valores canonicos de la metodologia. Recomendado cuando el resultado
          va a un informe formal.

        Args:
            params: Diccionario con las claves `signo` y las 10 siglas.
            estricto: Nivel de exigencia, ver arriba.

        Returns:
            True si todo es valido.

        Raises:
            ValueError: Con un mensaje que indica QUE parametro fallo, con QUE
                valor y cual era el rango esperado.
        """
        signo = params.get("signo")
        if signo not in cls.SIGNOS:
            raise ValueError(
                f"Signo invalido: '{signo}'. Debe ser "
                f"{' o '.join(repr(s) for s in cls.SIGNOS)}."
            )

        for sigla, valores_validos in cls.RANGOS.items():
            valor = params.get(sigla)
            nombre = cls.DESCRIPCIONES.get(sigla, sigla)

            if valor is None:
                raise ValueError(
                    f"Falta el parametro obligatorio '{sigla}' ({nombre})."
                )

            try:
                entero = int(valor)
            except (ValueError, TypeError):
                raise ValueError(
                    f"El parametro '{sigla}' ({nombre}) debe ser un numero "
                    f"entero. Se recibio: {valor!r}"
                )

            if entero in valores_validos:
                continue

            if estricto:
                raise ValueError(
                    f"El parametro '{sigla}' ({nombre}) con valor {entero} no "
                    f"es un valor de la metodologia. Admitidos: "
                    f"{valores_validos}."
                )

            minimo, maximo = min(valores_validos), max(valores_validos)
            if not (minimo <= entero <= maximo):
                raise ValueError(
                    f"El parametro '{sigla}' ({nombre}) con valor {entero} "
                    f"esta fuera del rango absoluto [{minimo}, {maximo}]."
                )

        return True

    @classmethod
    def descripcion_rangos(cls) -> str:
        """
        Devuelve los rangos en texto legible, para mensajes de ayuda y plantillas.

        Returns:
            Cadena multilinea con una linea por parametro.
        """
        return "\n".join(
            f"{sigla:<3} {cls.DESCRIPCIONES.get(sigla, ''):<18} -> "
            f"{', '.join(str(v) for v in valores)}"
            for sigla, valores in cls.RANGOS.items()
        )


if __name__ == "__main__":
    print("Parametros de la metodologia Conesa:")
    print(ConesaValidator.descripcion_rangos())
