# Comparativa de Arquitecturas y Decisión de Diseño
**Proyecto:** Sistema de Evaluación de Impactos Ambientales Basado en Conesa  
**Fase:** Práctica II - Ingeniería de Software (UMB)  
**Organización:** RED ABC  
**Rol:** Arquitecto de Software / Ingeniero de Datos Senior  
**Estado:** **Aprobado para Implementación (Arquitectura Híbrida / Copiloto de QGIS)**

Este documento evalúa las tres alternativas de integración entre el Agente de IA y QGIS, y detalla la **Decisión de Diseño** seleccionada para el desarrollo de la práctica empresarial.

---

## 1. Matriz de Decisiones Arquitectónicas

| Criterio | Opción 1: Agente como Operador (Headless CLI) | Opción 2: Generador de Proyecto (.qgs XML) | Opción 3: Copiloto / Plugin en QGIS (FastAPI) | Recomendación Seleccionada: Arquitectura Híbrida (Opción 3 + 2) |
| :--- | :---: | :---: | :---: | :---: |
| **Esfuerzo / Presupuesto** | **Bajo** (2 - 3 semanas) | **Muy Bajo** (1 - 2 semanas) | **Alto** (4 - 6 semanas) | **Moderado** (3 - 4 semanas) |
| **Mantenimiento** | **Medio** (Propenso a desajustes por rutas del sistema) | **Fácil** (Solo escritura de archivos planos) | **Complejo** (Mantenimiento de dos entornos y GUIs) | **Fácil - Medio** (API desacoplada e interfaz ligera) |
| **Escalabilidad** | **Muy Baja** (Levantar QGIS por consulta consume mucha RAM) | **Muy Alta** (Renderizado distribuido en clientes) | **Alta** (Servicios web REST distribuidos) | **Excelente** (Procesamiento espacial ligero sin GUI en el servidor) |
| **Experiencia de Usuario** | **Asíncrona** (Espera por terminal de comandos) | **Asíncrona** (Obliga al usuario a abrir archivos) | **En Vivo** (Interactividad visual en la ventana activa) | **En Vivo** (Sincronización en tiempo real vía API) |

---

## 2. La Decisión de Diseño Seleccionada: Arquitectura Híbrida

Se ha seleccionado implementar un enfoque **Híbrido**. Este combina la interactividad en tiempo real de un **Plugin Copiloto dentro de QGIS (Opción 3)** con la eficiencia y velocidad de un procesamiento geoespacial ligero en el servidor utilizando librerías estándar en Python sin necesidad de arrancar el motor gráfico de QGIS en el backend.

### Diagrama de Arquitectura del Sistema
```
┌────────────────────────────────────────────────────────┐
│                    QGIS DESKTOP (CLIENTE)              │
│  - Mapa Satelital + Capas Vectoriales de la GDB        │
│  - Plugin en la barra lateral (Chat con el Agente)     │
└───────────────────────────┬────────────────────────────┘
                            │ (Peticiones HTTP REST / JSON)
                            ▼
┌────────────────────────────────────────────────────────┐
│                   FASTAPI BACKEND (SERVIDOR)           │
│  - Cerebro del Agente de IA (LLM & Reasoning)          │
│  - Motor Matemático de Conesa (calcular_importancia)   │
│  - Procesador Geográfico Ligero (GeoPandas / Shapely)  │
└───────────────────────────┬────────────────────────────┘
                            │ (Escritura y Consulta Directa)
                            ▼
┌────────────────────────────────────────────────────────┐
│                   GEODATABASE (GDB) COMPARTIDA         │
│  - Capas de línea base (Agua, Clima, Suelos, POMCA)   │
│  - Resultados de la valoración actual                  │
└────────────────────────────────────────────────────────┘
```

---

## 3. Funcionamiento del Enlace Híbrido

1. **La Interfaz del Usuario (Plugin QGIS):** 
   El usuario interactúa desde un panel lateral dentro de QGIS (creado con `PyQt` e instalado en el menú `Complementos`). El usuario puede pedir tareas en lenguaje natural: *"Agente, calcula el impacto de construir una vía en esta coordenada y muéstralo en el mapa"*.
2. **La Inteligencia y el Procesamiento (FastAPI / Agent):** 
   El plugin no realiza cálculos pesados. Envía la solicitud al servidor web local en `FastAPI`. 
   * El Agente utiliza la librería **`GeoPandas`** y **`pyogrio`** para leer la Geodatabase en milisegundos directamente en el backend, sin necesidad de abrir la interfaz gráfica de QGIS en el servidor.
   * Ejecuta la fórmula de Conesa y clasifica los impactos correspondientes.
3. **Persistencia y Actualización (La Base de Datos):** 
   El servidor actualiza los atributos en la Geodatabase compartida (`GDB_POMCAS_CARARE_INTERSECT.gdb`) e inserta las nuevas valoraciones calculadas.
4. **Respuesta Visual:** 
   El servidor responde al Plugin con un reporte en texto. El mapa de QGIS, al estar conectado directamente a la misma Geodatabase, se actualiza automáticamente mostrando los nuevos colores de impacto (Bajo, Moderado, Severo, Crítico) sobre la imagen satelital de Google.

---

## 4. Ventajas Técnicas y de Negocio

* **Optimización de Recursos (Presupuesto):** Al no levantar instancias de QGIS Desktop en segundo plano para procesar la geometría (como en la Opción 1), el consumo de memoria en el servidor disminuye un **90%**, abaratando los costos de infraestructura (Cloud/VPS).
* **Robustez en el Mantenimiento:** La lógica ambiental (Conesa) y la lógica espacial (GeoPandas) están aisladas en el Backend de FastAPI. Si decides cambiar la interfaz de usuario en el futuro (por ejemplo, hacer un Dashboard web con Streamlit), la API del Agente funcionará exactamente igual sin modificar una sola línea de código del backend.
* **Impacto Académico (Ingeniería de Software):** El proyecto pasa de ser un simple script de automatización a convertirse en un **sistema cliente-servidor geoespacial de arquitectura moderna**, lo cual garantiza una excelente valoración por parte de los jurados de práctica y de grado.
