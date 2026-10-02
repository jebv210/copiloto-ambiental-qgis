# 🌱 Copiloto Ambiental Inteligente (Conesa + QGIS)

### Práctica Profesional II — Ingeniería de Software (UMB)

Sistema híbrido que automatiza el análisis cartográfico, la consulta de bases de
datos de biodiversidad y la evaluación de impacto ambiental por la metodología
de **Conesa**. Integra un backend en **FastAPI**, un frontend en **Streamlit**,
un agente de IA sobre **Gemini** y scripts de automatización para **QGIS
Desktop**.

---

## 1. Principio de diseño: nada está escrito a mano

El sistema **no trae datos precargados**. No sabe de antemano en qué
departamento trabajas ni qué especies te interesan: lo descubre leyendo el
archivo que tú cargues.

| Qué | De dónde sale |
| :--- | :--- |
| Lista de departamentos | Se leen del archivo cargado y de la cartografía oficial |
| Lista de grupos de especies | Se leen del archivo cargado |
| Ruta del dataset | La indica el usuario, o se descubre en disco |
| Ruta de la Geodatabase | Se busca automáticamente, o se indica en `.env` |
| Capas de la Geodatabase | Se reconocen por su nombre, sin listas fijas |
| Cantidad de registros | Se cuenta del archivo real, nunca se afirma de memoria |
| Rangos y umbrales de Conesa | `core/validator.py` y `core/evaluator.py` (fuente única) |

Existe un auditor que verifica que esto se mantenga:

```bash
python buscar_rutas.py
```

---

## 2. Estructura del proyecto

```
proyecto/
│
├── config/
│   └── settings.py             Única fuente de rutas y configuración
│
├── core/                       Lógica de negocio (sin Streamlit ni FastAPI)
│   ├── validator.py            Rangos válidos de Conesa (fuente de verdad)
│   ├── evaluator.py            Fórmula de Conesa y umbrales de severidad
│   ├── textos.py               Normalización de nombres para comparar
│   ├── catalogo.py             Descubre columnas y valores del archivo cargado
│   └── dynamic_filter.py       Genera el área de estudio (capas para QGIS)
│
├── database/
│   ├── gdb_connector.py        Consultas espaciales (municipio, RUNAP, páramo)
│   └── excel_connector.py      Cálculo por lote de matrices Excel/CSV
│
├── agent/
│   ├── prompts.py              Construye el prompt con el estado REAL
│   ├── tools.py                Herramientas que ejecuta el modelo
│   └── chatbot.py              Orquestador del agente
│
├── api/
│   └── server.py               Servidor FastAPI
│
├── app.py                      Interfaz web (Streamlit)
├── run_server.py               Lanzador del servidor (lee host y puerto)
├── cargar_capas_qgis.py        Script que se ejecuta DENTRO de QGIS
│
├── Arranque ─────────────────────────────────────────────────────────────
│   ├── setup_project.ps1       Instalación (ejecutar una sola vez)
│   ├── iniciar_servidor.bat    Levanta la API
│   ├── iniciar_interfaz_web.bat Levanta el dashboard
│   ├── buscar_qgis.bat         Localiza QGIS; lo usan los dos .bat
│   └── requirements.txt        Dependencias que QGIS no trae
│
├── Configuración ────────────────────────────────────────────────────────
│   ├── .env                    Tus claves y rutas (NO se versiona)
│   ├── .env.example            Plantilla con todas las opciones
│   ├── .gitignore              Excluye secretos, datos pesados y generados
│   └── .streamlit/config.toml  Límite de subida y telemetría de Streamlit
│
├── Datos incluidos ──────────────────────────────────────────────────────
│   ├── colombia_departamentos.geojson  Departamentos (fondo + nombres oficiales)
│   └── matriz_impactos.csv     Matriz de ejemplo para el cálculo masivo
│
└── Herramientas de línea de comandos ────────────────────────────────────
    ├── filtrar_biodiversidad.py    Explorar, filtrar y generar capas
    ├── buscar_rutas.py             Auditor de rutas y datos quemados
    ├── auditar_documentacion.py    Auditor de documentación
    └── test_gemini.py              Prueba de conexión con la IA
```

Además hay dos archivos que **genera el sistema** y que no se versionan:
`biodiversidad_filtrada.csv` y `municipios_filtrados.geojson` (ver sección 7).

### La carpeta `_archivo/`

Contiene material retirado del proyecto porque **ningún módulo lo usa**: copias
duplicadas, capas huérfanas y bitácoras de desarrollo con rutas absolutas
antiguas. Se conserva en lugar de borrarse porque el proyecto no está bajo
control de versiones. Su `LEEME.md` explica de dónde salió cada archivo.

**Puedes borrar `_archivo/` entera** cuando confirmes que no necesitas nada de
lo que hay dentro.

El único documento heredado que se quedó en la raíz es
`informe_impacto_ambiental.md`, por ser un entregable de la práctica. Ojo: sus
cifras de biodiversidad son de una corrida anterior y no coinciden con los
datos actuales.

---

## 3. Instalación

La aplicación usa el intérprete de Python de **QGIS Desktop**, que ya trae
GeoPandas, Shapely y Pyogrio compilados y funcionando en Windows.

1. Instala QGIS Desktop (cualquier versión reciente; ya no hay versiones fijadas
   en el código).
2. Clic derecho sobre `setup_project.ps1` → **Ejecutar con PowerShell**.

El script localiza QGIS solo, instala las dependencias de `requirements.txt`,
crea el entorno virtual `.venv` y crea un `.env` a partir de `.env.example`.

> Si QGIS está en una ubicación inusual, define la variable de entorno
> `QGIS_PYTHON` apuntando a su archivo `python-qgis-ltr.bat`.

---

## 4. Guía de uso

### Paso 1 — Arrancar

| Doble clic en | Qué levanta |
| :--- | :--- |
| `iniciar_servidor.bat` | Servidor API (por defecto `http://127.0.0.1:8000`) |
| `iniciar_interfaz_web.bat` | Dashboard web (por defecto `http://localhost:8501`) |

Puertos y host se cambian en `.env` (`API_HOST`, `API_PORT`, `STREAMLIT_PORT`),
sin tocar código.

### Paso 2 — Conectar la IA

En la barra lateral, pega tu **API Key de Gemini** y pulsa **🔌 Validar y
conectar**. El sistema hace una llamada real al servicio:

- 🟢 **verde** → la clave funciona; el agente razona con el modelo.
- 🔴 **rojo** → la clave no sirve; el mensaje dice exactamente por qué
  (incorrecta, sin permisos, sin cuota, sin internet).
- ⚪ **sin verificar** → el agente responde en **modo simulador**: un motor de
  reglas local que ejecuta las mismas herramientas sin necesidad de internet.

La clave también puede ir en `.env` (`GEMINI_API_KEY`), pero la que escribas en
la interfaz tiene prioridad.

### Paso 3 — Definir el área de estudio

Pestaña **⚙️ Área de Estudio**, en tres pasos:

1. **Indica tu archivo** de biodiversidad (`.csv` / `.tsv` / `.txt`). El archivo
   *no se sube*: solo entregas la ruta y el backend lo lee del disco, lo que
   permite trabajar con archivos de decenas de gigabytes.
2. **🔎 Analizar y explorar archivo** → el sistema muestra tamaño, columnas,
   registros estimados y separador, y luego recorre una muestra para descubrir
   qué departamentos y qué grupos de especies contiene **realmente**.
3. **Elige departamento y grupo**, y pulsa **🚀 Generar área de estudio**.

Nada de esto se configura: qué columna mirar, cuántas filas muestrear y con
qué nombre de la cartografía emparejar el departamento se resuelve solo. Si tu
archivo no sigue el estándar de GBIF y la detección falla, todo eso está en
**⚙️ Opciones avanzadas**, plegado.

> **Solo departamentos de Colombia.** Una descarga de GBIF es mundial: su
> columna `stateProvince` trae millares de regiones del planeta (California,
> Texas, Ontario…). El menú cruza los valores de tu archivo contra los nombres
> oficiales de `colombia_departamentos.geojson` y muestra **solo los
> departamentos colombianos que existen en tus datos**, con su número real de
> registros. Si tu proyecto es de otro país, actívalo en Opciones avanzadas.
>
> La correspondencia es tolerante: reconoce *Narino* como **NARIÑO** aunque
> GBIF lo exporte sin eñe, y *San Andres y Providencia* como **ARCHIPIÉLAGO DE
> SAN ANDRÉS, PROVIDENCIA Y SANTA CATALINA**, sin confundir *Cauca* con *Valle
> del Cauca* ni *Santander* con *Norte de Santander*.

> **Grupos de especies de varios niveles.** El menú mezcla reinos, clases y
> órdenes en una sola lista, indicando el nivel de cada uno. Por eso puedes
> elegir *Aves* (una clase) o *Lepidoptera* (un orden) sin tener que saber en
> qué columna vive cada uno. El sistema filtra por la columna correcta.

> **Tildes y mayúsculas.** Si tu archivo escribe el mismo departamento de
> varias formas ("Antioquia", "ANTIOQUIA", "Antioquía"), el sistema las agrupa
> en una sola opción y las incluye todas en el filtro. No se pierden registros
> por una tilde.

### Paso 4 — Ver los mapas en QGIS

1. Barra lateral → **📥 Descargar Script PyQGIS**.
2. Abre QGIS → **Complementos ▸ Consola Python** (`Ctrl + Alt + P`).
3. Botón de carpeta ("Abrir archivo de script") → elige el script descargado.
4. Botón verde **Play**.

El script dibuja **el mapa de Colombia** con tres capas, de abajo hacia arriba,
y encuadra la vista sobre el país automáticamente:

| Capa | Aspecto |
| :--- | :--- |
| Departamentos de Colombia | gris muy claro — es el fondo del mapa |
| Municipios del área de estudio | verde |
| Registros de biodiversidad | puntos naranjas |

> **Sin mapa base mundial.** No se añade OpenStreetMap de fondo: es una capa del
> planeta entero y convertía el resultado en un mapamundi con Colombia diminuta
> en el centro. El fondo lo pone la propia capa de departamentos, que es
> justamente el alcance del proyecto. Si algún día quieres ver calles y relieve
> detrás de los datos, pon `MOSTRAR_MAPA_BASE = True` en el script.

El script no necesita configuración: fija el sistema de coordenadas en
EPSG:4326, localiza la carpeta del proyecto solo (preguntándole al servidor API,
o por su propia ubicación, o por el proyecto `.qgz` abierto), lee los nombres de
las columnas de coordenadas de la cabecera del CSV y avisa por consola de cada
capa que carga o que falla.

> Si alguna capa no aparece, mira la consola de Python de QGIS: cada línea dice
> `[OK]`, `[--]` (falta el archivo) o `[!!]` (el archivo existe pero no se pudo
> interpretar), con el motivo.

### Añadir tus propias capas

Además de las tres del sistema, el script carga automáticamente las capas que
tú quieras: el polígono de tu área de trabajo, los puntos de un formulario de
campo (QField), una ortofoto o imagen satelital, lo que sea.

Dos formas, y puedes combinarlas:

1. **Copia los archivos** dentro de la carpeta `capas_extra/` del proyecto.
2. **O apunta a ellos** en `capas_extra/rutas.txt`, una ruta completa por línea.
   Así no duplicas nada — útil cuando la capa es una imagen de cientos de MB.

Formatos admitidos:

| Tipo | Extensiones |
| :--- | :--- |
| Vectorial | `.shp` `.geojson` `.gpkg` `.kml` `.kmz` `.gml` `.gpx` `.tab` `.dxf` |
| Imágenes | `.tif` `.tiff` `.img` `.jp2` `.ecw` `.vrt` `.png` `.jpg` |

El script las coloca donde corresponde: **las imágenes al fondo** (para que no
tapen nada) y **tus polígonos y puntos encima de todo**. Y encuadra el mapa
sobre tu zona de trabajo en vez de sobre Colombia entera, saltándose las capas
vacías (un formulario de campo recién creado no tiene puntos todavía).

> **Conservar tus colores.** Por defecto tus polígonos se dibujan sin relleno y
> con borde naranja, para dejar ver la imagen de fondo. Si quieres los estilos
> que ya definiste en tu propio proyecto de QGIS, exporta el estilo de la capa
> (clic derecho sobre ella → *Exportar* → *Guardar estilo*) junto al archivo y
> con el mismo nombre, terminado en `.qml`. QGIS lo aplica solo y el script no
> lo toca.

### Paso 5 — Diagnóstico de coordenadas

Pestaña **📍 Diagnóstico de Coordenadas**. Cruza un punto GPS con la capa
municipal y con la Geodatabase.

**Los resultados tienen tres estados, y la diferencia importa:**

| Color | Significado |
| :--- | :--- |
| 🟢 Verde | Se verificó y **no** hay colisión |
| 🔴 Rojo | Se verificó y **sí** hay colisión |
| 🟡 Amarillo | **No se pudo verificar** — no equivale a "sin riesgo" |

### Paso 6 — Cálculo masivo

Pestaña **📊 Cálculo Masivo**. Sube tu matriz (hay una plantilla descargable, y
`matriz_impactos.csv` sirve de ejemplo). Las filas con errores se reportan una a
una en lugar de tumbar el archivo completo.

---

## 5. Uso desde la línea de comandos

Todo lo que hace la interfaz web se puede hacer también desde una terminal.
Es lo práctico cuando el filtrado de un archivo de decenas de GB va a tardar y
prefieres dejarlo corriendo.

```bash
python -m config.settings                      # ver qué encontró el sistema
python -m database.gdb_connector               # qué capa usa para cada consulta
python test_gemini.py                          # probar la conexión con la IA
python buscar_rutas.py                         # auditar rutas quemadas
python auditar_documentacion.py                # auditar la documentación
```

### `filtrar_biodiversidad.py`

Reúne las cinco operaciones sobre los datos:

```bash
python filtrar_biodiversidad.py --describir              # ficha técnica del archivo
python filtrar_biodiversidad.py --explorar               # qué departamentos y taxones tiene
python filtrar_biodiversidad.py --listar-departamentos   # nombres oficiales de la cartografía
python filtrar_biodiversidad.py --solo-cartografia --departamento Antioquia
python filtrar_biodiversidad.py --departamento Antioquia --taxon Lepidoptera --columna-taxon order
```

Sin `--archivo`, el dataset se busca solo. Ejecuta `--explorar` antes de filtrar:
así el filtro conoce todas las grafías del valor que pides y no pierde registros
escritos de otra forma.

### Los dos auditores

El proyecto trae dos comprobaciones automáticas que puedes correr después de
cualquier cambio. Ambas devuelven código de salida `1` si encuentran algo, así
que sirven para encadenarlas en un script de entrega.

| Comando | Qué verifica |
| :--- | :--- |
| `python buscar_rutas.py` | Que no se haya colado ninguna ruta absoluta ni ningún dato atado a un computador |
| `python auditar_documentacion.py` | Que cada módulo, clase y función tenga docstring **y** que documente sus `Args`, `Returns` y `Raises` |

Añade `--detalle` al segundo para ver el recuento archivo por archivo.

> `cargar_capas_qgis.py` está excluido de la auditoría de documentación a
> propósito, por ser zona intocable. Para incluirlo, sácalo de
> `ARCHIVOS_EXCLUIDOS` en `auditar_documentacion.py`.

---

## 6. Configuración (`.env`)

Todas las claves son **opcionales**. Copia `.env.example` como `.env` y rellena
solo lo que necesites; lo que dejes vacío se descubre automáticamente.

| Variable | Para qué |
| :--- | :--- |
| `GEMINI_API_KEY` | Clave del agente de IA |
| `MODELO_LLM` | Modelo a usar (por defecto `gemini-2.5-flash`) |
| `DATASET_BIODIVERSIDAD` | Ruta del archivo crudo de GBIF |
| `GDB_PATH` | Ruta de la Geodatabase |
| `RUTA_MUNICIPIOS_NACIONAL` | Cartografía municipal local (modo sin internet) |
| `CAPA_PARAMO`, `CAPA_RUNAP`… | Forzar una capa concreta de la Geodatabase |
| `API_HOST`, `API_PORT` | Dirección del servidor |
| `QGIS_PYTHON` | Ruta del intérprete de QGIS |

> ⚠️ El archivo `.env` contiene tu clave y está en `.gitignore`. **No lo subas a
> ningún repositorio.** Si alguna vez lo hiciste, revoca la clave en
> <https://aistudio.google.com/> y genera otra.

---

## 7. Archivos que genera el sistema

Ninguno de estos se versiona; se regeneran cuando haga falta.

| Archivo | Qué es |
| :--- | :--- |
| `biodiversidad_filtrada.csv` | Registros del área de estudio (lo lee QGIS) |
| `municipios_filtrados.geojson` | Municipios del área de estudio (lo lee QGIS) |
| `cache_catalogo.json` | Valores descubiertos, para no re-escanear |
| `cache_municipios_nacional.geojson` | Copia local de la cartografía nacional |

> Los dos primeros nombres son un **contrato** con `cargar_capas_qgis.py`, que
> los busca por nombre. Están definidos en `config/settings.py`; si se
> renombran, hay que renombrarlos también en el script de QGIS.

---

## 8. Solución de problemas

Estos son los fallos que aparecen de verdad, con su causa y su arreglo.

**`No module named 'encodings'` al ejecutar Python**
Estás llamando a `python.exe` de QGIS directamente. Ese intérprete necesita las
variables de entorno que prepara su lanzador. Usa `python-qgis-ltr.bat`, o los
`.bat` del proyecto, que ya lo resuelven.

**`Valid PROJ data directory not found` / `Could not detect GDAL data files`**
Mismo origen: el entorno geoespacial no está inicializado. Arranca siempre desde
`iniciar_servidor.bat` o `iniciar_interfaz_web.bat`. El sistema aguanta este
fallo en las partes que puede (la lista de departamentos se lee como JSON plano,
sin geopandas), pero las intersecciones espaciales sí lo necesitan.

**El `.venv` no arranca**
Su `pyvenv.cfg` apunta a la ruta de QGIS que existía cuando se creó. Si
actualizaste o reinstalaste QGIS, esa ruta ya no existe. Vuelve a ejecutar
`setup_project.ps1`, que lo recrea.

**`503 UNAVAILABLE` al chatear con el agente**
Es el modelo de Google saturado, no un fallo tuyo. El agente lo detecta, avisa y
responde con el motor de reglas local. Reintenta en unos minutos.

**La API aparece APAGADA en la barra lateral**
O el servidor no está arrancado, o está en otro puerto. Comprueba con
`python -m config.settings` qué URL espera la interfaz y arranca
`iniciar_servidor.bat`.

**`Error al filtrar municipios` / no descarga la cartografía**
No hay internet. Descarga una vez el GeoJSON municipal nacional y apunta a él
con `RUTA_MUNICIPIOS_NACIONAL` en el `.env`. Tras la primera descarga con éxito,
el sistema guarda una copia local y ya no vuelve a necesitar red.

**"No se pudo actualizar `municipios_filtrados.geojson` porque otro programa lo
tiene abierto"**
Tienes QGIS Desktop abierto con esa capa cargada, y Windows no deja
sobrescribir un archivo en uso. Cierra QGIS —o quita esa capa del panel de
capas— y vuelve a generar. **No pierdes nada**: el resultado queda guardado
junto al original con el sufijo `.nuevo`, listo para renombrar si prefieres
hacerlo a mano.

**El campo del archivo aparece vacío al abrir la pestaña**
No se encontró ningún dataset en el disco. El sistema solo propone un archivo
si su cabecera tiene columnas de biodiversidad reconocibles, así que prefiere
no proponer nada antes que sugerirte un archivo que no sirve. Indícale la ruta
o pulsa **📂 Buscar...**.

**Aparecen puntos fuera de Colombia (en Brasil, Perú o Venezuela)**
El nombre del departamento se repite en otros países: "Amazonas" es también un
estado de Brasil, un departamento de Perú y un estado de Venezuela; lo mismo
ocurre con Bolívar (Venezuela, Ecuador), Sucre (Venezuela) o Córdoba
(Argentina, España). El motor descarta los registros de otros países usando la
columna `countryCode`, así que basta con volver a generar el área de estudio.
El país se configura con `PAIS_ESTUDIO` en el `.env` (por defecto `CO`).
Después, en QGIS: clic derecho sobre la capa de puntos → *Volver a cargar*.

**Un departamento con tilde devuelve cero registros (Chocó, Córdoba, Vaupés…)**
Ya no debería pasar. El motor no compara el departamento letra por letra: traduce
tanto lo que pides como cada valor del archivo a su nombre oficial en
`colombia_departamentos.geojson`, comparando **palabras completas**. Así
`CHOCO` encuentra `Chocó`, `BOGOTA` encuentra `Distrito Capital` (como lo
escribe GBIF) y `SAN ANDRES` encuentra `San Andrés y Providencia`, sin confundir
`Cauca` con `Valle del Cauca` ni `Coahuila` (México) con `Huila`. Verificado
contra los 3.317 nombres de región del catálogo real de GBIF: cubre los 33
departamentos y no se cuela ninguno extranjero.

**El filtrado no encuentra ningún registro**
La combinación de departamento y taxón no existe en tus datos. Usa
`python filtrar_biodiversidad.py --explorar` para ver qué contiene realmente el
archivo, o amplía la muestra en el paso 3 de la interfaz: un valor poco
frecuente puede no aparecer en los primeros millones de filas.

**El diagnóstico de coordenadas devuelve casi todo "no determinado"**
Tu Geodatabase no cubre esa zona. Ejecuta `python -m config.settings` para ver
cuál está usando el sistema, y `python -m database.gdb_connector` para ver qué
capa asignó a cada papel. Puedes forzar una capa concreta con las variables
`CAPA_MUNICIPIO`, `CAPA_PARAMO`, etc.

---

## 9. Endpoints de la API

Documentación interactiva en `/docs`.

| Método | Ruta | Qué hace |
| :--- | :--- | :--- |
| `GET` | `/` | Estado del servidor |
| `GET` | `/config` | Toda la configuración resuelta (útil para depurar) |
| `GET` | `/parametros` | Rangos y umbrales de la metodología |
| `POST` | `/calculate` | Calcula un impacto individual |
| `POST` | `/calculate-batch` | Procesa una matriz completa |
| `GET` | `/layers` | Capas de la Geodatabase |
| `POST` | `/baseline` | Línea base de una coordenada |
| `GET` | `/area-estudio` | Qué área de estudio hay generada |
| `GET` | `/base-path` | Ruta raíz del proyecto (la usa QGIS) |
