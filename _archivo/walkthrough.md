# Walkthrough Final: Ecosistema Conesa-QGIS Completado
**Fecha:** 9 de agosto de 2026

Hemos completado el desarrollo del 100% de la arquitectura híbrida propuesta para tu entregable de **Práctica II - Ingeniería de Software**.

---

## 1. Estructura de Entregables del Proyecto

El código fuente final en `C:\UNVIERSIDADES\MANUELA\PRACTICAS II\proyecto` está organizado de la siguiente manera:

```
proyecto/
│
├── api/
│   ├── __init__.py
│   └── server.py                        # Servidor API FastAPI
│
├── config/
│   ├── __init__.py
│   └── settings.py                      # Rutas de datos (GDB/Excel)
│
├── core/
│   ├── __init__.py
│   ├── evaluator.py                     # Motor matemático de Conesa
│   └── validator.py                     # Validaciones de rangos y parámetros
│
├── database/
│   ├── __init__.py
│   ├── excel_connector.py               # Procesador de Excel/CSV en lote
│   └── gdb_connector.py                 # Conector y motor espacial para la GDB
│
├── agent/
│   ├── __init__.py
│   ├── prompts.py                       # Prompt del consultor ambiental
│   ├── tools.py                         # Enlace de funciones a herramientas
│   └── chatbot.py                       # Orquestador del Agente de IA
│
├── .streamlit/
│   └── config.toml                      # Configuraciones visuales de la interfaz web
│
├── app.py                               # Interfaz de Usuario (Dashboard Web Streamlit)
├── requirements.txt                     # Paquetes requeridos (fastapi, streamlit, etc.)
├── setup_project.ps1                    # Script automatizado de instalación de dependencias
│
├── iniciar_servidor.bat                 # Lanzador del Servidor API (Puerto 8000)
└── iniciar_interfaz_web.bat             # Lanzador de la Interfaz Web (Puerto 8501)
```

---

## 2. Validación de Servicios en Ejecución

Todo el ecosistema de software está activo y verificado en la máquina local de desarrollo:

### A. API Backend (Uvicorn / FastAPI) - **Puerto 8000**
* Corre en segundo plano desde el batch de arranque.
* **Salud del Servidor:** `/` responde `online`.
* **Cálculo de Conesa:** `/calculate` responde con el cálculo de importancia y severidad correctas.
* **Intersección GIS:** `/baseline` intersecta coordenadas en la GDB en milisegundos.
* **Documentación:** `/docs` interactivo y listo.

### B. Interfaz Gráfica (Streamlit) - **Puerto 8501**
* Iniciado con éxito en segundo plano y sirviendo en `http://localhost:8501`.
* **Funciones Integradas en Pantalla:**
  1. **Chat con el Copiloto:** Panel lateral de chat en tiempo real con el Agente de IA (con soporte para modo simulado offline y modo API Gemini en la nube).
  2. **Subidor de Excel:** Botón de arrastrar y soltar que lee archivos de valoraciones masivas, procesa la matriz Conesa con pandas, muestra una vista previa interactiva y habilita la descarga inmediata del reporte calculado en un solo clic.
  3. **Visualizador de Coordenadas:** Formulario numérico para consultar qué vereda, municipio, cuenca u ecosistema protegido colisiona con el punto GPS ingresado.

---

## 3. Guía de Ejecución Rápida para Sustentaciones

Cuando presentes el proyecto a tus jurados o asesores de práctica, solo debes:
1. Asegurarte de que QGIS está instalado.
2. Hacer doble clic en **`iniciar_servidor.bat`** (arranca el cerebro matemático y GIS).
3. Hacer doble clic en **`iniciar_interfaz_web.bat`** (abre tu navegador web automáticamente con el panel interactivo listo para usar).
4. *(Opcional)* Abrir QGIS con el proyecto `proyecto.qgz` para ver las capas geográficas sincronizadas en la pantalla de mapas.
