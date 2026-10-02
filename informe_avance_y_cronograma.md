# Informe de avance y cronograma

**Proyecto:** Copiloto Ambiental Inteligente (Conesa + QGIS)
**Práctica Profesional II** — Ingeniería de Software, Universidad Manuela Beltrán
**Entidad receptora:** RED ABC
**Fecha del informe:** 26 de septiembre de 2026

---

## 1. Resumen ejecutivo

El sistema está **construido y funcionando**: arquitectura modular, interfaz web,
servidor API, agente de IA e integración con QGIS Desktop. Sobre esa base, el
trabajo de los últimos meses se concentró en dos frentes:

1. **Eliminar los datos escritos a mano en el código**, para que el sistema
   sirva para cualquier departamento de Colombia y no solo para el caso con el
   que nació.
2. **Verificar la veracidad de la información**, que es lo que pidió el cliente
   en la reunión del diccionario de datos.

El segundo frente arrojó hallazgos que condicionan el cierre del proyecto: la
documentación entregada **no describe** los datos entregados, y los datos
disponibles **no cubren** el área de estudio actual. Están detallados en la
sección 4 y son el principal riesgo del cronograma.

---

## 2. Un cambio de alcance que explica casi todos los desajustes

La documentación inicial del proyecto es inequívoca sobre el área de estudio:

| Documento | Fecha | Área de estudio |
| :--- | :--- | :--- |
| `fuentes_informacion_enlaces.md` | agosto 2026 | **Maripí, Boyacá** — cuenca del Río Carare-Minero, jurisdicción de CORPOBOYACÁ |
| `plan_implementacion_agente.md` | 2 de agosto de 2026 | Ídem; la GDB entregada es `GDB_POMCAS_CARARE_INTERSECT` |
| `informe_impacto_ambiental.md` | agosto 2026 | **San Carlos, Antioquia** — mariposario |
| Proyecto QField `INFOMARIPOSAS` | septiembre 2026 | San Carlos, Antioquia |

El proyecto **se trasladó de Boyacá a Antioquia**, pero los insumos (la
Geodatabase y los 17 diccionarios de datos) siguen siendo los de la cuenca del
Carare-Minero, en Boyacá.

De ahí se derivan, de forma directa, los tres problemas de datos de la sección 4.
**Esta es la primera decisión que hay que tomar con el cliente**, porque de ella
depende buena parte del cronograma.

---

## 3. Lo realizado

### 3.1 Arquitectura: lo diseñado frente a lo construido

El diseño de `plan_implementacion_agente.md` se cumplió en su totalidad:

| Módulo previsto | Estado | Observación |
| :--- | :--- | :--- |
| `config/settings.py` | ✅ | Resuelve todas las rutas; nada absoluto en el código |
| `core/evaluator.py` + `validator.py` | ✅ | Fórmula de Conesa y rangos, fuente única de verdad |
| `database/gdb_connector.py` | ✅ | Consultas espaciales con `pyogrio` |
| `database/excel_connector.py` | ✅ | Cálculo por lote de matrices |
| `agent/prompts.py` + `tools.py` | ✅ | 5 herramientas; prompt construido con el estado real |
| `api/server.py` | ✅ | 9 endpoints REST en FastAPI |
| `tests/` | 🟡 | No hay carpeta `tests/` con `pytest`; la verificación se hace con scripts de comprobación y dos auditores automáticos |

Módulos añadidos sobre el diseño original: `core/catalogo.py` (descubrimiento de
datos), `core/textos.py` (normalización de nombres) y `core/dynamic_filter.py`
(generación del área de estudio).

### 3.2 Sistema dinámico: se eliminaron los datos escritos a mano

Era el punto más frágil del código heredado. Estado actual:

| Antes | Ahora |
| :--- | :--- |
| Ruta del dataset escrita en el código | La indica el usuario o se descubre en disco |
| Lista de 33 departamentos a mano | Se leen de la cartografía oficial |
| 7 grupos de especies a mano | Se descubren del archivo cargado |
| Separador de columnas fijo | Autodetectado |
| Total de filas fijo (161.369.380) | Estimado del archivo real |
| Versión de QGIS fija en los `.bat` | Búsqueda automática |
| Rangos de Conesa repetidos en 3 sitios | Una sola fuente de verdad |

Se creó el auditor `buscar_rutas.py`, que verifica que esto se mantenga.
Resultado actual: **cero hallazgos de severidad alta**.

### 3.3 Integración con QGIS

- Script `cargar_capas_qgis.py` reescrito: carga el mapa de Colombia con los
  departamentos, los municipios del área de estudio y los registros de
  biodiversidad, y encuadra la vista automáticamente.
- Se corrigieron tres defectos: el mapa base nunca cargaba (se creaba como capa
  vectorial en lugar de ráster), el script no arrancaba en QGIS 3.34 por una
  incompatibilidad de sintaxis, y los nombres de las columnas de coordenadas
  estaban fijos. Verificado en QGIS real y compatible con Python 3.9 a 3.12.
- Se añadió la carpeta `capas_extra/`, que permite cargar capas propias
  (polígono de monitoreo, formularios de QField, ortofotos) sin tocar código.

### 3.4 Diccionarios de datos y estándar Darwin Core

Entregable: **`Practica_II_Datos_y_Entregables.xlsx`**, 12 hojas, 114 fórmulas,
sin errores. Contiene:

- Inventario completo de la carpeta de entregables (578 archivos, 2.205 MB).
- Los 17 diccionarios del POMCA, con sus 166 capas y 1.515 campos.
- Cruce entre lo documentado y lo que existe de verdad en las Geodatabases.
- Plantilla de registros biológicos en **Darwin Core**, con los términos
  verificados contra la versión vigente del estándar (2026-05-26).
- Diccionario de estaciones hidrometeorológicas del **IDEAM** (19 campos
  oficiales) y cotejo con la capa que existe en la Geodatabase.

### 3.5 Verificación

Todo lo anterior está respaldado por comprobaciones automáticas:

| Comprobación | Resultado |
| :--- | :--- |
| Sintaxis de los archivos Python | 0 errores |
| Pruebas de humo del sistema | 15 / 15 |
| Filtros (país, departamento, especie) | 19 / 19 |
| Resolución de nombres de departamento | 37 / 37 |
| Cobertura de documentación del código | 100 % |
| Auditoría de rutas y datos quemados | 0 hallazgos altos |

---

## 4. Hallazgos sobre la veracidad de la información

Responden al punto 11 de la reunión: *"diccionario de datos que se necesita:
resumen de la información y **veracidad** de la misma"*.

### 4.1 La documentación no describe los datos entregados

| | |
| :--- | ---: |
| Capas reales en las dos Geodatabases | 78 |
| Capas descritas en los 17 diccionarios | 166 |
| Capas que coinciden por nombre | **4** |
| Capas documentadas que no existen en ninguna GDB | 109 |

Las cuatro que coinciden son `Pendiente`, `Suelo`, `PuntoMuestreoAguaSuper` y
`PuntoMuestreoSuelo`. Las capas que el sistema sí utiliza —`RUNAP`,
`AIA_EcoEstrat_Paramo`, `Municipio`, `Vereda`— **no tienen diccionario**.

### 4.2 Los datos no cubren el área de estudio

Sobre un área de estudio de Boyacá (123 municipios):

| Capa | Alcanza | Cobertura |
| :--- | ---: | ---: |
| RUNAP (áreas protegidas) | 2 municipios | **2 %** |
| Páramos | 11 municipios | **9 %** |

En el 98 % restante el sistema respondía *"verificado, sin colisiones"* sin
haber consultado nada. Se corrigió para que distinga **"no evaluado"** de
**"sin hallazgos"**, pero el vacío de datos persiste.

### 4.3 Calidad del dato en la estación hidrometeorológica

De 13 campos cotejados contra el Catálogo Nacional de Estaciones del IDEAM:

- **Código corrupto:** el archivo dice `2312701`; el real es `0023127010`. Se
  guardó como número y perdió los ceros. Con ese valor la estación no existe en
  el catálogo oficial.
- **Municipio equivocado:** el archivo dice San Pablo de Borbur; el IDEAM
  registra **Pauna**.
- **7 campos ausentes**, entre ellos categoría (es **limnigráfica**: mide nivel
  de agua, no lluvia), estado (**en mantenimiento**) y subzona hidrográfica.
- Solo **3 de 13** campos coinciden plenamente.

### 4.4 Mezcla de países en los registros biológicos

Un área de estudio de Amazonas arrojó 7.980 registros, de los cuales **solo 961
(12 %) eran de Colombia**: 5.901 eran de Brasil, 1.097 de Perú y 21 de
Venezuela. "Amazonas" es un departamento colombiano, pero también un estado de
Brasil, uno de Venezuela y un departamento de Perú.

Corregido: el motor ahora filtra por país y resuelve los nombres de departamento
contra la cartografía oficial. Verificado contra los 3.317 nombres de región del
catálogo real de GBIF: reconoce los 33 departamentos y no admite ninguno
extranjero.

### 4.5 El informe de impacto no coincide con la matriz

Ninguno de los 7 valores de importancia de `informe_impacto_ambiental.md`
coincide con lo que calcula el motor a partir de `matriz_impactos.csv`. Además
el informe tiene 7 filas y la matriz 8, y usa una categoría ("POSITIVO") que no
pertenece a la metodología. Este desfase ya se había advertido en
`plan_implementacion_agente.md` en agosto.

---

## 5. Requisitos de la reunión del diccionario de datos

Estado de los 16 puntos registrados en la reunión:

| # | Requisito | Estado |
| :--- | :--- | :--- |
| 1, 3 | Bases de datos distintas y sin relación entre sí | ✅ Medido y documentado |
| 2 | Entrega de una de las bases | — Contexto |
| 4 | Depurar no es responsabilidad del analista | 🟡 El sistema reporta, no limpia en silencio |
| 5 | Qué sistemas de información usan para su trabajo | ❌ Levantamiento pendiente |
| 6 | SIG para las mariposas | ✅ Script de QGIS operativo |
| 7 | Deben suministrar la información **y sus fuentes** | ❌ Sin registro de procedencia |
| 8 | Hablan del uso pero no traen la información | — Contexto; justifica el diccionario |
| 9 | Agente con preguntas definidas **y memoria** | 🟡 Intenciones definidas; **sin memoria** |
| 10 | Parámetros definidos (variables que necesitan) | 🟡 Existen los filtros; faltan las variables por componente |
| 11 | Diccionario único del cliente: resumen y veracidad | 🟡 Insumos listos; falta el consolidado |
| 12 | Verificación de ítems y sus mediciones | 🟡 Hecho para el IDEAM; sin sistematizar |
| 13 | La semántica difiere entre entidades | ✅ Documentado con casos concretos |
| 14 | Información desproporcionada | ✅ Cuantificado (secciones 4.1 y 4.4) |
| — | La búsqueda y el filtro deben entregarse en **una sola base**, por el solicitante | ❌ Depende del cliente |
| 15 | La base de datos la entrega el solicitante, con sus referencias | ❌ Depende del cliente |
| 16 | Tramo 56:48 – 1:00:00 del video | ⏳ Pendiente de revisar |

**Nota sobre responsabilidades**, que conviene no mezclar en las actas:
*depurar* la información no es del analista (punto 4), pero *recopilarla* sí es
de RED ABC (minuto 36).

---

## 6. Pendientes y cronograma

Las estimaciones son en días de trabajo efectivo. El calendario propuesto
arranca el lunes 28 de septiembre de 2026 y debe ajustarse a la fecha real de
entrega de la práctica.

### Semana 1 (28 sep – 2 oct) — Trazabilidad y decisiones

| Tarea | Días | Responsable |
| :--- | ---: | :--- |
| **Registro de procedencia**: que cada área generada guarde su origen, filtros, fecha y conteos (puntos 7 y 15; el campo `fuente` ya está en el modelo de datos del documento de diseño) | 2 | Desarrollo |
| **Decidir el área de estudio**: Boyacá o Antioquia, y conseguir la Geodatabase y el RUNAP que la cubran | — | **Cliente / RED ABC** |
| Rotar la clave de API expuesta en el archivo `.env` | 0,1 | Desarrollo |

### Semana 2 (5 – 9 oct) — Diccionario único

| Tarea | Días | Responsable |
| :--- | ---: | :--- |
| **Diccionario de datos consolidado** (punto 11): una sola hoja con las capas reales, sus campos y una columna de veracidad (coincide / no coincide / sin verificar) | 3 | Desarrollo |
| Revisar el tramo 56:48 – 1:00:00 del video e incorporar lo que falte | 0,5 | Analista |

### Semana 3 (12 – 16 oct) — Agente y filtros

| Tarea | Días | Responsable |
| :--- | ---: | :--- |
| **Memoria del agente** (punto 9): enviar el historial de la conversación al modelo | 1 | Desarrollo |
| Restaurar en la interfaz los filtros dinámicos de departamento y especie (el motor ya los resuelve; la pantalla sigue con listas fijas) | 1 | Desarrollo |
| Definir con el cliente las **variables por componente** (punto 10) y los **sistemas de información que usan** (punto 5) | — | **Cliente / RED ABC** |

### Semana 4 (19 – 23 oct) — Consistencia documental

| Tarea | Días | Responsable |
| :--- | ---: | :--- |
| Conciliar `informe_impacto_ambiental.md` con la matriz de impactos recalculada | 1 | Desarrollo |
| Decidir el futuro del Diagnóstico de Coordenadas: completarlo con datos que cubran el área, o retirarlo | 1 | Conjunto |
| Sistematizar la verificación de ítems y mediciones (punto 12) | 2 | Desarrollo |

### Semana 5 (26 – 30 oct) — Cierre

| Tarea | Días | Responsable |
| :--- | ---: | :--- |
| Pruebas automatizadas con `pytest` en una carpeta `tests/`, como preveía el diseño | 2 | Desarrollo |
| Documento final y preparación de la sustentación | 2 | Conjunto |

**Total de desarrollo pendiente: 15,6 días efectivos**, más las decisiones y
entregas que dependen del cliente.

---

## 7. Riesgos

| Riesgo | Impacto | Mitigación |
| :--- | :--- | :--- |
| **No se define el área de estudio** (Boyacá o Antioquia) | Alto — bloquea el 40 % del cronograma | Decidirlo en la Semana 1 |
| **El cliente no entrega la base unificada ni sus referencias** | Alto — puntos 7, 10 y 15 no se pueden cerrar | Acta con compromiso y fecha |
| Los datos disponibles no cubren el área (RUNAP al 2 %) | Medio | Descargar el RUNAP nacional de Parques Nacionales |
| El dataset crudo de GBIF (93 GB) ya no está en disco | Medio | Descomprimir de nuevo el `.zip` de 25 GB antes de regenerar |
| Generar un área sobrescribe la anterior | Bajo | Incluido en la tarea de procedencia, Semana 1 |

---

## 8. Dónde está la evidencia

| Afirmación | Fuente verificable |
| :--- | :--- |
| Inventario de entregables, diccionarios y cruce con la GDB | `Practica_II_Datos_y_Entregables.xlsx`, hojas *Inventario*, *Diccionarios*, *Capas en la GDB* |
| Estructura del IDEAM y cotejo de la estación | Mismo libro, hojas *Dicc. Estaciones IDEAM* y *Cotejo IDEAM* |
| Términos Darwin Core vigentes | Mismo libro, hojas *Mapeo DwC* y *Cambios DwC*; estándar en `rs.tdwg.org/dwc/doc/list/2026-05-26` |
| Ausencia de rutas y datos escritos a mano | `python buscar_rutas.py` |
| Cobertura de documentación del código | `python auditar_documentacion.py` |
| Configuración resuelta del sistema | `python -m config.settings` |
| Capas que usa cada consulta espacial | `python -m database.gdb_connector` |
