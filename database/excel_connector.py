"""
Procesamiento masivo de matrices de impacto en Excel o CSV.
===============================================================================

Lee un archivo con una fila por valoracion, calcula la formula de Conesa en
cada fila y escribe el mismo archivo con dos columnas nuevas:
`importancia_calculada` y `severidad_calculada`.

DECISIONES DE DISENO
--------------------
* Una fila con un error NO aborta el archivo completo. Se marca esa fila y se
  sigue. El resumen final dice cuantas fallaron y por que, porque en una matriz
  de doscientas valoraciones tener que repetir todo por una celda mal escrita
  seria inaceptable.
* Los nombres de columna se buscan ignorando mayusculas y espacios. Antes se
  exigia `fila["signo"]` exacto, asi que un archivo con la cabecera `Signo`
  fallaba en todas las filas sin explicar por que.
* El formato de salida lo decide la extension del archivo de salida, no la del
  de entrada. Antes se miraba la del de entrada, de modo que pedir un `.xlsx`
  a partir de un `.csv` producia un archivo `.xlsx` con contenido CSV dentro.
"""

import os

from core.evaluator import ConesaEvaluator
from core.validator import ConesaValidator


class ExcelConnector:
    """
    Ingesta y calculo por lote de matrices de valoracion de Conesa.

    Uso tipico::

        conector = ExcelConnector()
        resumen = conector.procesar_archivo_evaluaciones("matriz.csv")
    """

    #: Extensiones que el conector sabe leer y escribir.
    EXTENSIONES_CSV = (".csv", ".tsv", ".txt")
    EXTENSIONES_EXCEL = (".xlsx", ".xls")

    def __init__(self):
        """El conector no mantiene estado; se instancia sin argumentos."""
        pass

    # --------------------------------------------------------------------------

    @staticmethod
    def _mapa_columnas(columnas):
        """
        Construye un indice de columnas insensible a mayusculas y espacios.

        Args:
            columnas: Nombres de columna reales del archivo.

        Returns:
            Diccionario `{nombre_normalizado: nombre_real}`.
        """
        return {str(c).strip().lower(): c for c in columnas}

    @classmethod
    def _valor(cls, fila, mapa, sigla):
        """
        Extrae de una fila el valor de un parametro, tolerando la grafia.

        Args:
            fila: Fila del DataFrame (Series).
            mapa: Indice devuelto por `_mapa_columnas`.
            sigla: Sigla buscada, por ejemplo "EX".

        Returns:
            El valor de la celda.

        Raises:
            KeyError: Si la columna no existe en el archivo.
        """
        real = mapa.get(sigla.lower())
        if real is None:
            raise KeyError(
                f"El archivo no tiene la columna '{sigla}'. Columnas "
                f"requeridas: signo, "
                + ", ".join(ConesaValidator.RANGOS.keys())
            )
        return fila[real]

    # --------------------------------------------------------------------------

    def procesar_archivo_evaluaciones(self, input_path: str,
                                      output_path: str = None,
                                      estricto: bool = False) -> dict:
        """
        Calcula la matriz de Conesa completa de un archivo.

        Columnas obligatorias (en cualquier combinacion de mayusculas):
        `signo`, `i`, `EX`, `MO`, `PE`, `RV`, `SI`, `AC`, `EF`, `PR`, `MC`.
        Cualquier otra columna del archivo (identificadores, nombres de accion
        o de factor, comentarios) se conserva intacta en la salida.

        Args:
            input_path: Ruta del archivo de entrada (.csv/.tsv/.txt/.xlsx/.xls).
            output_path: Ruta del archivo de salida. Si es None, se genera
                junto al de entrada con el sufijo `_resultado`.
            estricto: Si True, exige valores canonicos de la metodologia. Ver
                `ConesaValidator.validar_parametros`.

        Returns:
            Diccionario con:
                registros_procesados : filas leidas.
                registros_exitosos   : filas calculadas sin error.
                registros_con_error  : filas rechazadas.
                errores              : lista de `{fila, motivo}` (maximo 50).
                archivo_salida       : ruta absoluta del resultado.
                conteo_severidad     : cuantas filas por categoria.

        Raises:
            FileNotFoundError: Si el archivo de entrada no existe.
            ValueError: Si la extension no es compatible.
        """
        import pandas as pd

        if not os.path.exists(input_path):
            raise FileNotFoundError(
                f"No se encontro el archivo de entrada: {input_path}"
            )

        extension_entrada = os.path.splitext(input_path)[1].lower()

        # --- 1. Lectura -------------------------------------------------------
        if extension_entrada in self.EXTENSIONES_CSV:
            # `sep=None` + engine="python" deja que pandas deduzca si el archivo
            # usa comas, punto y coma o tabuladores. Los archivos que exporta
            # Excel en espanol suelen venir con punto y coma.
            df = pd.read_csv(input_path, sep=None, engine="python",
                             encoding="utf-8", encoding_errors="replace")
        elif extension_entrada in self.EXTENSIONES_EXCEL:
            df = pd.read_excel(input_path)
        else:
            raise ValueError(
                f"Formato no soportado: '{extension_entrada}'. Use "
                f"{', '.join(self.EXTENSIONES_CSV + self.EXTENSIONES_EXCEL)}."
            )

        mapa = self._mapa_columnas(df.columns)

        # --- 2. Calculo fila por fila -----------------------------------------
        importancias = []
        categorias = []
        errores = []

        for indice, fila in df.iterrows():
            try:
                parametros = {"signo": str(self._valor(fila, mapa, "signo")).strip()}
                for sigla in ConesaValidator.RANGOS:
                    parametros[sigla] = self._valor(fila, mapa, sigla)

                resultado = ConesaEvaluator.calcular_importancia(
                    parametros, estricto=estricto
                )
                importancias.append(resultado["importancia"])
                categorias.append(resultado["categoria"])

            except Exception as exc:
                importancias.append(None)
                categorias.append("ERROR")
                # +2 convierte el indice de pandas (base 0, sin cabecera) al
                # numero de fila que la persona ve en Excel.
                errores.append({"fila": int(indice) + 2, "motivo": str(exc)})

        df["importancia_calculada"] = importancias
        df["severidad_calculada"] = categorias

        # --- 3. Escritura -----------------------------------------------------
        if not output_path:
            base, ext = os.path.splitext(input_path)
            output_path = f"{base}_resultado{ext}"

        extension_salida = os.path.splitext(output_path)[1].lower()
        if extension_salida in self.EXTENSIONES_EXCEL:
            df.to_excel(output_path, index=False)
        else:
            df.to_csv(output_path, index=False, encoding="utf-8")

        # --- 4. Resumen -------------------------------------------------------
        return {
            "registros_procesados": len(df),
            "registros_exitosos": len(df) - len(errores),
            "registros_con_error": len(errores),
            "errores": errores[:50],
            "archivo_salida": os.path.abspath(output_path),
            "conteo_severidad": df["severidad_calculada"].value_counts().to_dict(),
        }


if __name__ == "__main__":
    # Prueba manual: python -m database.excel_connector
    import pandas as pd

    archivo_prueba = "evaluaciones_prueba.csv"
    pd.DataFrame({
        "id_valoracion": [1, 2, 3],
        "signo": ["+", "-", "-"],
        "i": [2, 1, 15],          # la tercera fila tiene un error intencional
        "EX": [2, 1, 1], "MO": [4, 4, 1], "PE": [4, 1, 1],
        "RV": [1, 1, 1], "SI": [2, 1, 1], "AC": [1, 1, 1],
        "EF": [4, 4, 1], "PR": [4, 1, 1], "MC": [1, 1, 1],
    }).to_csv(archivo_prueba, index=False)

    resumen = ExcelConnector().procesar_archivo_evaluaciones(archivo_prueba)
    for clave, valor in resumen.items():
        print(f"{clave}: {valor}")

    for temporal in (archivo_prueba,
                     archivo_prueba.replace(".csv", "_resultado.csv")):
        if os.path.exists(temporal):
            os.remove(temporal)
