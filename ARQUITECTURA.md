# Cómo funciona, archivo por archivo

Qué hace cada pieza, con qué se conecta y en qué orden. Si vienes a tocar algo
y no sabes por dónde empezar, busca aquí el recorrido que te interesa.

---

## El mapa en una frase

El sistema hace **dos cosas** que comparten infraestructura pero no se mezclan:

1. **Convierte un archivo gigante de biodiversidad en un mapa fiable.**
   Entra una descarga del SiB Colombia, sale un área de estudio depurada que
   QGIS puede dibujar.
2. **Responde preguntas citando documentos.** Un agente estudia 134
   publicaciones y contesta sin inventarse nada, diciendo de qué documento y
   página sale cada afirmación.

```
                    ┌─────────────────────────────────┐
 Archivo del SiB ──►│  core/dynamic_filter.py         │──► área de estudio
 (decenas de GB)    │  filtra por bloques             │    (.csv + .geojson)
                    └─────────────────────────────────┘           │
                                                                  ▼
 134 documentos ───►┌─────────────────────────────────┐    cargar_capas_qgis.py
                    │  core/corpus.py   (palabras)    │           │
                    │  core/vectorial.py (significado)│           ▼
                    └─────────────────────────────────┘      QGIS Desktop
                                   │
                                   ▼
                    ┌─────────────────────────────────┐
     Tu pregunta ──►│  agent/chatbot.py  +  11 tools  │──► respuesta con citas
                    └─────────────────────────────────┘
```

---

## Las cuatro capas

### 1. Configuración — `config/settings.py`

**Todo** se decide aquí y se lee del `.env`. Ningún otro archivo tiene rutas ni
valores escritos a mano; ese fue uno de los objetivos del proyecto.

Lo que gobierna, por secciones:

| Qué | Variable del `.env` | Por defecto |
|---|---|---|
| Quién responde el chat | `LLM_PROVEEDOR` | `gemini` |
| Quién genera los vectores | `PROVEEDOR_EMBEDDINGS` | `gemini` |
| De dónde salen los documentos | `FUENTE_CORPUS` | `local` |
| Dónde vive el índice vectorial | `QDRANT_URL` | vacío = embebido |

> **Todo va por Gemini.** El proveedor local se retiró el 18/10/2026 y quedó
> guardado en `_archivo/proveedor_ollama/`. Ojo con la cuota de embeddings: la
> capa gratuita permite **1.000 al día** y el corpus necesita unas 14.989, así
> que construir el índice desde cero lleva varios días o una tarde con la API
> de pago. Las reindexaciones posteriores son baratas.

### 2. Núcleo — `core/`

Aquí está todo lo que calcula. No sabe que existe una interfaz web.

| Archivo | Qué hace |
|---|---|
| `validator.py` | Los rangos válidos de la metodología Conesa. **Única fuente de verdad** de esos números |
| `evaluator.py` | Aplica la fórmula de Conesa y clasifica la severidad |
| `catalogo.py` | Mira un archivo desconocido y deduce su separador y qué columna es cuál |
| `textos.py` | Resuelve nombres de lugar contra el catálogo oficial |
| `dynamic_filter.py` | Recorre el archivo por bloques y produce el área de estudio |
| `corpus.py` | Trocea los documentos e indexa por palabras (BM25) |
| `vectorial.py` | Los mismos fragmentos como vectores, en Qdrant |
| `fuentes.py` | De dónde salen los documentos: carpeta local hoy, Drive mañana |
| `importar.py` | Mete fuentes nuevas al corpus sellando su procedencia. Tiene consola propia (recorrido 3) |
| `informes.py` | Plantillas de informe que solo repiten lo que dice el corpus |
| `sanear.py` | Limpia el corpus tras una exportación de cuaderno |

### 3. Agente — `agent/`

| Archivo | Qué hace |
|---|---|
| `chatbot.py` | Habla con Gemini, **recuerda la conversación**, acepta **imágenes**, reintenta los fallos pasajeros y avisa cuando degrada |
| `prompts.py` | Construye la instrucción leyendo el estado real del proyecto |
| `tools.py` | Las once herramientas que el modelo puede llamar |

### 4. Presentación

| Archivo | Qué hace |
|---|---|
| `app.py` | Interfaz Streamlit, cuatro pestañas |
| `api/server.py` | Los mismos servicios por HTTP, y los endpoints que consume n8n |
| `cargar_capas_qgis.py` | Se ejecuta **dentro de QGIS**: carga las capas y encuadra la vista |

---

## Recorrido 1: de un archivo de 99 GB a un mapa

**Paso 1 — Cargar.** Indicas la ruta en la pestaña «Área de Estudio».
`core/catalogo.py` lee solo las primeras líneas y deduce el separador, qué
columna lleva el departamento, cuál el taxón y cuál el código de país. Nada de
eso está escrito en el código: si tu archivo usa otros nombres, lo descubre.

**Paso 2 — Elegir.** Te ofrece los valores que de verdad hay en *tu* archivo.

**Paso 3 — Filtrar.** `core/dynamic_filter.py` recorre el archivo **por
bloques**: nunca se carga entero, así que la memoria depende del tamaño del
bloque y no del archivo. Sobre cada bloque aplica tres filtros **en un orden
deliberado**:

1. **Geográfico** — cada valor distinto de `stateProvince` se traduce a un
   nombre oficial del DANE con `core/textos.py`. Compara **palabras
   completas**, y si dos nombres encajan igual de bien **descarta el registro**
   en vez de adivinar.
2. **Taxonómico** — por orden o clase.
3. **País** — y va el **último a propósito**.

> **Por qué el país va al final.** Si fuera el primero, los registros
> extranjeros desaparecerían antes de contarlos. Yendo el último se puede medir
> cuántos cumplían departamento y taxón **y aun así eran de otro país**. De ahí
> salió la cifra de 7.019 descartados sobre 7.980: solo el 12 % era del país
> analizado. Ese contador es lo que convirtió un problema invisible en un
> número.

**Paso 4 — Dibujar.** Salen dos archivos con nombre fijo, porque
`cargar_capas_qgis.py` los busca por ese nombre:

```
biodiversidad_filtrada.csv     registros depurados, con coordenadas
municipios_filtrados.geojson   límites del departamento elegido
```

> **Ahora mismo no hay área generada**, y es correcto que así sea. Los archivos
> que había eran de una prueba sobre el **Amazonas**, no sobre San Carlos.
> Están guardados en `_archivo/area_estudio_amazonas_pruebas/` porque contienen
> los 961 registros del caso del 12 %. Para trabajar sobre San Carlos hay que
> generar el área desde la interfaz.

---

## Recorrido 2: de una pregunta a una respuesta con fuentes

**Paso 1 — Llega la pregunta.** `app.py` la recoge y lanza el agente **en un
hilo aparte**, porque Streamlit vuelve a ejecutar el script entero en cada
interacción y si no la pantalla se congelaría. Mientras, el hilo principal
pinta «Pensando… N s».

**Paso 2 — Se prepara la llamada.** `agent/chatbot.py` construye el cliente de
Gemini con la clave del `.env`, y le envía **la conversación entera**, no solo
la última pregunta. Sin eso, a *«¿y dónde vive esa especie?»* el agente
contestaba pidiendo que se le dijera de qué especie se hablaba, aunque acabara
de nombrarla. Se recuerdan 12 turnos: más empezaría a quitarle sitio a las
fichas del corpus, que son las que permiten citar.

Si la pregunta trae **imágenes**, viajan en el mismo turno. El modelo las mira
junto al texto; una foto ilegible se omite en lugar de impedir la respuesta.

Si no hay clave o el servicio falla, responde el motor de reglas **avisando en
grande** de que eso no es una respuesta.

**Paso 3 — El modelo decide qué herramienta usar.** Recibe las once de
`agent/tools.py`. Las importantes para el corpus:

| Herramienta | Cuándo |
|---|---|
| `consultar_corpus` | Pregunta general. Búsqueda **híbrida** |
| `buscar_especie_exacta` | Un nombre científico. **Literal, nunca vectores** |
| `reporte_taxonomico_especie` | Informe completo de una especie |
| `consultar_linea_base` | Una coordenada contra las capas del POMCA |

> **Por qué los nombres científicos no pasan por vectores.** Un vector no
> distingue *Polites otho* de *Polites vibex*: los coloca casi en el mismo
> punto. Ya pasó una vez en este proyecto, y el agente citó una fuente que
> nunca había nombrado esa especie. Por eso `buscar_especie_exacta` exige que
> las palabras aparezcan **juntas y en ese orden**.

**Paso 4 — Se busca.** `core/vectorial.py` mezcla dos búsquedas:
BM25 (palabras) y Qdrant (significado). Se combinan por **posición**, no por
puntuación, porque los puntajes de una y otra no son comparables. Si Qdrant no
está disponible, **cae a BM25 y el sistema sigue funcionando**.

**Paso 5 — Se responde citando.** Cada fragmento lleva su documento y página.
Si el dato no consta, el sistema **lo dice** en vez de completarlo.

> **Si la respuesta empieza con «El modelo en la nube no respondió»**, lo que
> sigue **no es una respuesta**: lo generó el motor de reglas, que busca
> palabras clave y **no consulta la bibliografía**. El aviso es grande a
> propósito.

---

## Recorrido 3: cómo se le da información nueva al agente

Esto es lo que hay que hacer cuando llegue otro tema y otras fuentes.

### Hay dos formas, y las dos valen

**1. Soltar el archivo en la carpeta.** El corpus se organiza por subcarpetas,
y el nombre de la subcarpeta **es la categoría** con la que se cita:

```
corpus/
├── Guías de campo/
│   └── guia_regional.pdf
└── Catálogos/
    └── catalogo_2026.docx
```

Se admiten `.pdf`, `.docx`, `.md`, `.txt` y `.csv`. En cuanto el archivo está
ahí, el corpus lo ve: el inventario sube, la versión cambia y la búsqueda ya lo
encuentra.

**2. Por consola, que además comprueba antes de meterlo:**

```bash
python -m core.importar                                  # qué hay ahora
python -m core.importar guia.pdf -c "Guías de campo"     # un archivo
python -m core.importar carpeta/ -c "Catálogos"          # una carpeta entera
```

La diferencia con copiar a mano es que **avisa de lo que no va a servir** antes
de que el agente lo cite vacío:

```
  [OK ] guia_regional.pdf
  [NO ] portada.md
         Solo se extrajeron 10 caracteres; el minimo es 400.
  [NO ] hoja.xyz
         La extension .xyz no se sabe leer.
```

Un PDF escaneado sin capa de texto se rechaza aquí, que es donde conviene
enterarse. Necesitaría OCR antes.

### Después, reconstruir el índice

Añadir el archivo no basta para la búsqueda por significado: hay que generar
sus vectores.

```bash
curl -X POST http://localhost:8000/corpus/sincronizar \
     -H "Content-Type: application/json" -d "{}"
```

O esperar al flujo de n8n de las 02:00, que hace lo mismo solo. **Solo se
procesa lo que cambió**: los documentos que ya estaban ni se leen.

### Si la información va a estar en Drive

Cambiar de origen es una línea:

```bash
FUENTE_CORPUS=drive
DRIVE_CARPETA_ID=<el id que sale de la dirección de la carpeta>
GOOGLE_CREDENCIALES=C:\ruta\credenciales.json
```

Hace falta una **cuenta de servicio de Google Cloud con permiso de solo
lectura**, y compartirle la carpeta por su correo. A partir de ahí el sistema
recorre las subcarpetas igual, usa el nombre de la subcarpeta como categoría y
**solo descarga lo que cambió**, comparando el `md5Checksum` que da Drive.

El resto del sistema no se entera de dónde vinieron los documentos.

---

## Recorrido 4: cómo se mantiene el corpus al día

Aquí entra n8n, y el reparto de responsabilidades importa:

**n8n decide CUÁNDO. La API hace QUÉ.**

```
  n8n: cron 02:00
        │
        ├─► POST /corpus/sincronizar      arranca y devuelve al instante
        │
        └─► GET  /corpus/estado  (en bucle hasta que termina)
```

El endpoint devuelve enseguida porque reindexar son **minutos**: una petición
que esperase moriría por tiempo de espera.

Dentro, `core/vectorial.py`:

1. Pregunta a `core/fuentes.py` qué documentos hay y **con qué huella**
2. Compara con lo que ya está indexado y **se salta lo que no cambió**
3. De lo que cambió, descarta las fichas de índice (`...... 12 Alimentación`)
4. Pide los vectores, **sin repetir** lo que ya está en caché
5. Los guarda en Qdrant con documento, categoría, página y huella

> **La firma del modelo.** La colección guarda con qué modelo se creó
> (`gemini-embedding-001@768#v2`). Si cambia, se **rehace entera**: mezclar
> vectores de dos modelos no da un error, da resultados sin sentido, que es
> peor.

El segundo flujo es el **chat**: recibe la pregunta, se la pasa al agente,
guarda el intercambio en Qdrant y devuelve la respuesta con el acuse de ese
guardado.

```bash
curl -X POST http://localhost:5678/webhook/copiloto-chat \
  -H "Content-Type: application/json" \
  -d '{"pregunta":"plantas hospederas de Morpho peleides","session_id":"demo"}'
```

Hubo un tercer flujo que exponía la bibliografía a otros proyectos
(`corpus-consultar`). Se retiró el 23-10-2026: llevaba desde el 18 de octubre
sin que lo llamara nadie del proyecto, y sus 42 ejecuciones eran todas de la
batería de pruebas. Un flujo cuyo único cliente es su propia prueba no
demuestra nada, y en cambio hay que mantenerlo.

Lo que hacía sigue disponible en la API, que es donde siempre estuvo:

```bash
curl -X POST http://localhost:8000/corpus/consultar \
  -H "Content-Type: application/json" \
  -d '{"pregunta":"plantas hospederas de Morpho peleides","limite":5}'
```

Su JSON quedó en `_archivo/flujos_n8n_retirados/` por si vuelve a hacer falta.

---

## Qué se conecta con qué

```
app.py ──────────┬──► agent/chatbot.py ──────────────────────► Gemini
                 │           │
                 │           └──► agent/tools.py ──┬──► core/vectorial.py ──► Qdrant
                 │                                 ├──► core/corpus.py
                 │                                 ├──► core/evaluator.py
                 │                                 └──► database/gdb_connector.py
                 │
                 ├──► core/catalogo.py ──► core/dynamic_filter.py ──► core/textos.py
                 └──► config/settings.py   (todos leen de aquí)

core/importar.py ──► corpus/          (añadir documentos, con validación)
core/fuentes.py  ──► corpus/ o Drive  (de dónde salen; se elige en el .env)

api/server.py ───┴──► lo mismo, por HTTP  ◄── n8n
```

---

## Qué NO se usa, y por qué sigue estando

| Archivo | Para qué |
|---|---|
| `auditar_documentacion.py` | Comprueba que todas las funciones estén documentadas. Era un objetivo del proyecto |
| `buscar_rutas.py` | Busca rutas y valores escritos a mano. El mismo motivo |
| `test_gemini.py` | Prueba el agente sin abrir la web. Útil para aislar un fallo |
| `filtrar_biodiversidad.py` | Hace lo mismo que la pestaña, por consola |
| `capas_extra/` | Donde dejas tus propias capas para que QGIS las cargue |
| `matriz_impactos.csv` | Matriz de Conesa de ejemplo |

Se movió a `_archivo/` lo que de verdad sobraba: el área de estudio del
Amazonas, que no es la de este proyecto y que además hacía que el agente
afirmara que el área cargada era esa.

---

## Si vas a tocar algo

| Quieres cambiar | Toca |
|---|---|
| El modelo que responde | `MODELO_LLM` en el `.env` |
| Añadir documentos | `python -m core.importar <archivo> -c "Categoría"` |
| De dónde salen los documentos | `FUENTE_CORPUS`, y rellena `FuenteDrive` |
| Cómo se trocean | `TAMANO_FICHA` en `core/corpus.py`, y **reindexa** |
| El modelo de embeddings | `MODELO_GEMINI_EMBEDDINGS` en `core/vectorial.py`, y sube `ESQUEMA_EMBEDDINGS` |
| Los rangos de Conesa | `core/validator.py`, y solo ahí |

Y después, siempre:

```bash
python probar_todo.py
```

Lo que cubre cada batería está en [PRUEBAS.md](PRUEBAS.md).
