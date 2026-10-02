# Plan de Implementación Aprobado: Desarrollo del Sistema Híbrido Conesa
**Ruta del Proyecto:** `C:\UNVIERSIDADES\MANUELA\PRACTICAS II\proyecto`  
**Estado:** **Aprobado - Iniciando Desarrollo**

Este plan de implementación detalla la arquitectura seleccionada y la hoja de ruta para construir el sistema modular (backend en FastAPI, conectores GIS con GeoPandas, motor de cálculo Conesa y especificación del Agente de IA).

---

## 1. Estructura del Proyecto

El código fuente se organizará en la siguiente estructura de directorios y archivos:

```
proyecto/
│
├── config/
│   └── settings.py                      # Configuración de rutas y variables globales
│
├── core/
│   ├── __init__.py
│   ├── evaluator.py                     # Motor de cálculo matemático de Conesa
│   └── validator.py                     # Validador de rangos y parámetros (1-12)
│
├── database/
│   ├── __init__.py
│   ├── gdb_connector.py                 # Conector y procesador geográfico (GeoPandas)
│   └── excel_connector.py               # Lector de diccionarios Excel y CSVs de línea base
│
├── agent/
│   ├── __init__.py
│   ├── prompts.py                       # Plantillas y prompts del Agente de IA
│   └── tools.py                         # Definición de herramientas (Tools) del Agente
│
├── api/
│   ├── __init__.py
│   └── server.py                        # Servidor API FastAPI
│
├── requirements.txt                     # Dependencias del proyecto Python
└── setup_project.ps1                    # Script de automatización de entorno virtual
```

---

## 2. Hoja de Ruta del Desarrollo

### Fase 1: Creación del Entorno Virtual (Completado)
* **Objetivo:** Configurar un entorno virtual `.venv` que herede las librerías geoespaciales globales de QGIS (`geopandas`, `pyogrio`, `shapely`, `pandas`) e instalar los paquetes de desarrollo web (`fastapi`, `uvicorn`, `requests`).
* **Script:** `setup_project.ps1`.

### Fase 2: Configuración y Core Matemático (`config/` y `core/`)
* **`config/settings.py`**: Definirá las rutas absolutas para los datos (`GDB` y archivos Excel) para evitar errores de archivo no encontrado.
* **`core/validator.py`**: Validará que cada parámetro de Conesa (IN, EX, MO, etc.) sea un entero válido y que esté dentro de los límites del estándar (ej. Intensidad de 1 a 12).
* **`core/evaluator.py`**: Implementará la clase `ConesaEvaluator` para calcular la importancia numérica y asignar la categoría cualitativa ("Bajo", "Moderado", "Severo", "Crítico").

### Fase 3: Módulo de Datos y GIS (`database/`)
* **`database/gdb_connector.py`**: Métodos para listar capas, consultar el número de registros y realizar intersecciones espaciales automáticas.
* **`database/excel_connector.py`**: Métodos para leer archivos CSV climáticos o biológicos que alimenten el análisis.

### Fase 4: Servidor Web API (`api/`)
* **`api/server.py`**: Creará la aplicación FastAPI con los endpoints REST para la interacción del usuario o agente.

---

## 3. Guía de Ejecución y Despliegue para Evaluadores

Cuando el proyecto esté completo, los evaluadores podrán ejecutarlo mediante scripts automáticos:
1. **`iniciar_servidor.bat`**: Creará el entorno virtual si no existe y arrancará la API del agente.
2. **`abrir_mapa.bat`**: Iniciará QGIS cargando directamente el archivo del proyecto con el satélite y la GDB enlazados.
3. **`abrir_dashboard.bat`**: (Opcional) Lanzará una interfaz web interactiva en Streamlit.
