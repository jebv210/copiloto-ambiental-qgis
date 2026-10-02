"""
Prueba de conexion con el agente de IA.
===============================================================================

Verifica, sin abrir la interfaz web, que la API Key funciona y que el modelo
puede ejecutar las herramientas del proyecto.

Uso::

    python test_gemini.py
    python test_gemini.py --clave AIza...           # probar otra clave
    python test_gemini.py --pregunta "que datos tienes cargados?"

QUE SE CORRIGIO
---------------
1. La version anterior imprimia fragmentos de la clave en pantalla::

       print(f"API Key: {api_key[:10]}...{api_key[-10:]}")

   Eso deja el secreto en el historial de la terminal y en cualquier captura de
   pantalla de la sustentacion. Ahora solo se informa si hay clave o no.

2. Mantenia su propia copia de la lista de herramientas, que podia quedar
   desfasada respecto a la del agente. Ahora usa `agent.tools.HERRAMIENTAS`.

3. Tenia el nombre del modelo escrito a mano; ahora sale de la configuracion.
"""

import argparse

from config import settings
from agent.chatbot import ConesaAgent, validar_api_key


def main():
    """Ejecuta la bateria de comprobaciones y muestra el resultado."""
    parser = argparse.ArgumentParser(
        description="Comprueba la conexion con el agente de IA."
    )
    parser.add_argument("--clave", help="API Key a probar. Por defecto, la "
                                        "del archivo .env.")
    parser.add_argument(
        "--pregunta",
        default="¿Que datos tienes cargados en el area de estudio?",
        help="Pregunta a enviarle al agente.",
    )
    argumentos = parser.parse_args()

    clave = argumentos.clave or settings.api_key_gemini()

    print("=" * 70)
    print("PRUEBA DE CONEXION DEL AGENTE DE IA")
    print("=" * 70)
    print(f"Modelo configurado : {settings.modelo_llm()}")
    # Nunca se imprime la clave, ni siquiera parcialmente.
    print(f"API Key            : {'presente' if clave else 'NO CONFIGURADA'}")
    print()

    # --- Paso 1: validar la clave ---------------------------------------------
    print("[1/2] Validando la API Key contra el servicio...")
    valida, mensaje = validar_api_key(clave)
    print(f"      {'OK' if valida else 'FALLO'}: {mensaje}")
    print()

    # --- Paso 2: conversacion real --------------------------------------------
    print("[2/2] Enviando una pregunta al agente...")
    agente = ConesaAgent(api_key=clave)
    print(f"      Modo activo: {agente.modo}")
    print(f"      Estado     : {agente.detalle_estado}")
    print()
    print("-" * 70)
    print(f"PREGUNTA: {argumentos.pregunta}")
    print("-" * 70)
    print(agente.responder(argumentos.pregunta))
    print("-" * 70)

    if not valida:
        print("\nEl agente respondio en modo simulador. Para activar el modo "
              "IA, corrija la clave en el archivo .env o peguela en la barra "
              "lateral de la interfaz web.")


if __name__ == "__main__":
    main()
