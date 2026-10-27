# Cómo probar que el sistema funciona

Tres baterías, de lo más aislado a lo más integrado. La idea de todas es la
misma: **intentar tumbar** cada afirmación que se hace sobre el sistema, no
confirmarla.

## Lo único que hay que recordar

```bash
python probar_todo.py
```

Eso ejecuta las tres y resume. Si sale **«Todo en orden»**, el sistema está
sano.

Si Docker no está levantado, usa:

```bash
python probar_todo.py --rapido
```

que omite lo que necesita servicios.

> **Importante: con qué Python.** El proyecto usa el intérprete de QGIS, no el
> de Windows. Si `python` te da errores de módulos que faltan, llámalo por su
> ruta completa:
>
> ```bash
> "C:\Program Files\QGIS 3.40.4\apps\Python312\python.exe" probar_todo.py
> ```

---

## Las baterías

### 1. `probar_corpus.py` — 24 comprobaciones

No necesita nada levantado. Comprueba la memoria documental del agente:

- Que el troceado no se estanque ni invente fichas de más
- Que la cabecera de procedencia no se indexe como si fuera contenido
- Que la búsqueda por binomio sea **literal**: que *Polites otho* no devuelva
  fuentes que solo hablan de *Polites vibex*
- Que el importador cuente bien lo importado, lo rechazado y lo duplicado
- Que la versión del corpus cambie al mover un documento de categoría

### 2. `probar_vectorial.py` — 35 comprobaciones

Necesita la clave de Gemini en el `.env` (para los embeddings). Comprueba:

- Que la versión del corpus sea **portable**: que cambiar solo la fecha de un
  archivo no la altere, pero que una edición del mismo tamaño sí
- Que las fichas basura (índices con puntos suspensivos) no se embeban
- Qué fallos se reintentan (cuota, 503, cortes de red) y cuáles no (clave
  inválida)
- Que la caché evite repetir trabajo
- Que **el tamaño real del vector mande sobre el `.env`**
- Que cambiar de modelo de embeddings **rehaga la colección** en vez de mezclar
  vectores incompatibles
- Que sin Qdrant el sistema siga respondiendo por BM25

### 3. `probar_despliegue.py` — necesita la pila levantada

Esta batería se ejecuta **desde el equipo, no dentro del contenedor**:
comprueba los contenedores desde afuera, por HTTP y por Docker. Correrla por
dentro no prueba lo mismo.

```bash
docker compose up -d
pip install -r requirements-equipo.txt   # solo la primera vez
python probar_despliegue.py
```

Sin esas bibliotecas, la comprobación de Qdrant falla con «Qdrant no
respondió». El mensaje es correcto —no respondió— pero la causa no es Qdrant
ni el sistema: es que `qdrant-client` no se puede ni importar en esa máquina.
Un fallo ajeno al proyecto ensucia la única señal que dice si el despliegue
está bien.

Comprueba que los contenedores se hablen de verdad:

- Que respondan API, Qdrant, n8n y Streamlit
- Que `/corpus/estado` traiga sus cuatro secciones
- Que `/corpus/consultar` devuelva fichas **con su procedencia**
- Que una segunda sincronización simultánea devuelva **409**
- Que los flujos de n8n sean importables y **sin enlaces rotos entre nodos**

### 4. `probar_agente.py` — 12 comprobaciones

Necesita la pila levantada y **consume cuota de Gemini**. Si la cuota diaria se
agota, las comprobaciones que dependen del modelo se marcan **omitidas**, no
fallidas: una cuota agotada no es un defecto del código.

```bash
python probar_agente.py
```

Comprueba:

- Que **recuerda el turno anterior**: «esa especie» se resuelve sin repetir el
  nombre, y el historial crece
- Que se puede **olvidar** sin tocar el corpus
- Que **responde lo que dicen las fuentes** y **cita de dónde lo saca**
- Que **declara cuando el dato no está** en la bibliografía, en vez de
  completarlo con lo que parezca probable
- Que **lee el texto de una imagen**, y que una imagen ilegible se omite en
  lugar de romper
- Que el corpus es **portable**: la herramienta no presupone el tema, las dos
  puertas de entrada (local y Drive) están registradas, y una fuente
  desconocida se rechaza con un mensaje claro

### 5. `probar_qgis.py` — 13 comprobaciones

No necesita Docker, pero sí QGIS. Si QGIS no está instalado en la máquina, la
batería **se omite** en vez de fallar.

```bash
python probar_qgis.py
```

Ejecuta `cargar_capas_qgis.py` **completo y sin modificarlo**, en un QGIS sin
interfaz gráfica, sobre un juego de datos real de San Carlos (Antioquia) que se
arma al vuelo con la cartografía DANE del proyecto.

Comprueba:

- Que carga las **tres capas** del área de estudio y que todas son válidas
- Que dibuja **todos** los registros del CSV, y que los departamentos son 33
- Que reconoce las coordenadas en sus cuatro formas: Darwin Core de GBIF
  (`decimalLatitude`), castellano (`latitud`), abreviado (`lat`) y con punto y
  coma como separador
- Que **exige latitud y longitud**: con una sola columna se niega a dibujar
- Que un archivo inexistente o un CSV sin filas **avisan en vez de reventar**
- Que **rechaza la columna «Longitud Total (km)»** de los Excel de quebradas,
  que es el largo del cauce y no una coordenada. Esta es la importante: 46 de
  sus 127 valores caen en el rango de latitudes de Colombia, así que un
  detector descuidado sembraría el mapa de puntos falsos sin dar error
- Que el script **dibuja todo el CSV sin comprobar el polígono** del municipio
  (38 de 60 puntos caen dentro; el filtrado es de quien genera el CSV)

El detalle del análisis está en
[documentos/Analisis_Excel_y_Script_QGIS.md](documentos/Analisis_Excel_y_Script_QGIS.md).

---

## Qué mirar cuando algo falla

Cada fallo dice qué se esperaba y qué se obtuvo. Los más probables:

**«la API responde: HTTP 0»** — la pila no está levantada, o Docker Desktop no
terminó de arrancar. Compruébalo con `docker compose ps`: los cuatro deben
estar `Up`, y `copiloto-qdrant` además `(healthy)`.

**«Falta GEMINI_API_KEY»** — los embeddings y el chat salen de Gemini.
Comprueba que el `.env` tenga la clave y que no haya caducado.

**«La API de embeddings sigue sin cuota»** — la capa gratuita permite 1.000
embeddings al día. No es un fallo: es el límite. El trabajo se reanuda al día
siguiente sin repetir lo ya hecho, porque la caché lo conserva.

**«cambiar de modelo rehace la colección: 1 -> 1»** — el índice conserva
vectores del modelo anterior. Fuerza una reindexación:

```bash
curl -X POST http://localhost:8000/corpus/sincronizar -H "Content-Type: application/json" -d "{\"forzar\": true}"
```

**«DLL load failed while importing _sqlite3»** — estás usando el Python
equivocado, o lo lanzas como subproceso sin `QGIS\bin` en el PATH. Usa
`probar_todo.py`, que ya lo resuelve.

**«n8n responde: HTTP 0 — el puerto NO está publicado»** — pasó de verdad y
cuesta de ver. Si al crear el contenedor había **otro** n8n ocupando el 5678,
el nuestro se queda con el puerto expuesto pero sin publicar: arranca bien,
responde dentro del contenedor y es inalcanzable desde fuera.

```bash
docker ps -a --filter publish=5678      # ¿quién lo ocupa?
docker compose up -d --force-recreate n8n
```

Si el contenedor ajeno tiene `restart: always`, vuelve cada vez que arranca
Docker y repite el problema. Se le quita sin borrarlo:

```bash
docker update --restart=no <nombre-del-contenedor>
```

La batería de despliegue ya diagnostica este caso y te dice cuál es el
contenedor culpable.

---

## Comprobaciones a ojo

Hay cosas que ninguna batería puede verificar sola.

**El agente responde con fuentes.** Abre http://localhost:8501 y pregunta:

> ¿De qué planta se alimenta la oruga de Morpho peleides? Cita la fuente.

Debe responder **Inga cf. semialata** y nombrar el documento. Si responde otra
planta, o no cita, hay un problema de recuperación.

**El aviso de degradación se ve.** Si alguna vez la respuesta empieza con
**«El modelo en la nube no respondió»**, lo que sigue **no es una respuesta**:
lo generó el motor de reglas, que no consulta la bibliografía. Ese aviso está
puesto a propósito para que no se confunda con un análisis correcto.

**La indexación avanza.** Mientras corre:

```bash
curl http://localhost:8000/corpus/estado
```

El número de `fichas` debe subir. Son unas 6 por segundo, así que el corpus
entero lleva alrededor de 40 minutos la primera vez. Las siguientes son
rápidas: solo se reprocesa lo que cambió.

---

## Cuándo volver a pasarlas

- **Siempre** antes de dar por bueno un cambio en `core/` o en `agent/`
- Después de tocar el `.env`, porque casi todo se configura ahí
- Después de añadir o quitar documentos del corpus
- Después de `docker compose build`, para confirmar que la imagen nueva sirve

Y si cambias el **modelo de embeddings** o cómo se construye el texto que se
envía al modelo, sube `ESQUEMA_EMBEDDINGS` en `core/vectorial.py`. Ese número
es lo que obliga a reindexar; sin subirlo, convivirían vectores de dos esquemas
distintos sin que nada avisara.
