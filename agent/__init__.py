"""
Paquete del Agente de IA.
===============================================================================

Contiene tres piezas:

    prompts.py  -> construye el prompt de sistema a partir del estado real
                   del proyecto (no de datos escritos en el codigo).
    tools.py    -> las funciones que el modelo puede ejecutar.
    chatbot.py  -> el orquestador `ConesaAgent`.

NOTA SOBRE LAS IMPORTACIONES
----------------------------
Este archivo esta deliberadamente vacio de importaciones. La version anterior
hacia aqui `from agent.chatbot import ConesaAgent`, lo que provocaba que un
simple `import agent.prompts` arrastrara el chatbot, las herramientas, pandas,
geopandas y un recorrido de disco buscando la Geodatabase.

Importe siempre lo que necesite de forma explicita::

    from agent.chatbot import ConesaAgent
"""
