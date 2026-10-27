# Despliegue en producción (Azure)

Guía para poner el **Copiloto Ambiental Inteligente (Conesa + QGIS)** a
funcionar en la nube de Azure de la entidad.

Todo lo que dice este documento está medido sobre el sistema real, no estimado:
los tamaños, los consumos y los tiempos salen de ejecuciones hechas en el
equipo de desarrollo, y se indica de dónde sale cada cifra.

---

## 1. Qué se despliega

El sistema son **cuatro contenedores** que ya funcionan juntos con
`docker-compose.yml`. No hay nada más que instalar.

| Servicio | Imagen | Puerto | Qué hace | ¿Público? |
|---|---|---|---|---|
| `app` | `copiloto-ambiental:latest` | 8501 | La interfaz web (Streamlit) | **Sí**, es lo único |
| `api` | `copiloto-ambiental:latest` | 8000 | FastAPI: Conesa, corpus, agente | No |
| `qdrant` | `qdrant/qdrant:v1.19.0` | 6333 | Índice vectorial | No |
| `n8n` | `n8nio/n8n:1.68.0` | 5678 | Automatización y cron diario | No |

`app` y `api` comparten **la misma imagen**: cambia el comando de arranque, no
el contenido. Eso está puesto a propósito y conviene no deshacerlo — con dos
construcciones separadas, `docker compose build api` dejaba la imagen de la
interfaz atrás y la web seguía mostrando textos y un modelo ya cambiados.

### Lo que NO se despliega

- **QGIS Desktop.** El script `cargar_capas_qgis.py` se ejecuta en el equipo de
  la persona, dentro de su QGIS. Meter el escritorio en la imagen la
  multiplicaría por diez para algo que el servidor no usa.
- **Los documentos de estudio.** Viven en Google Drive. El servidor los baja
  para leerlos y **los borra en cuanto termina**, incluso si la lectura falla a
  mitad. En el disco solo queda el índice: números, no copias.

### Lo que el servidor sí guarda

Tres archivos que genera la pestaña «Área de Estudio» y que QGIS lee. Van en
`data/`, que es carpeta montada:

| Archivo | Qué es |
|---|---|
| `municipios_filtrados.geojson` | El límite del área de estudio |
| `puntos_area_estudio.geojson` | Veredas, casco urbano, sitios del proyecto |
| `hidrografia_area_estudio.geojson` | Quebradas, ríos y rutas |

Salen todos del mismo archivo que delimita el área — un `.kmz` de Google Earth,
típicamente — y se extraen juntos al fijarla.

**Por qué están en `data/` y no en la raíz del proyecto.** La raíz no está
montada en los contenedores. Escrito ahí, el área quedaba encerrada dentro del
contenedor: ni la interfaz la veía, ni QGIS, que corre en el equipo de la
persona. Se midió — la interfaz decía «no hay área de estudio fijada» con el
archivo recién creado.

---

## 2. Dónde alojarlo en Azure

### Recomendación: una Máquina Virtual con Docker

Para este sistema —cuatro contenedores, un equipo de trabajo, dos volúmenes con
estado— **una VM Linux con Docker Compose es la opción correcta**. Es lo que ya
funciona, sin traducir nada, y se administra con los mismos comandos que en
desarrollo.

| | |
|---|---|
| Tamaño | **Standard B2s** (2 vCPU, 4 GB) como mínimo; **B2ms** (2 vCPU, 8 GB) cómodo |
| Sistema | Ubuntu Server 22.04 LTS |
| Disco | **64 GB** Premium SSD |
| Red | Solo 443 abierto a internet. SSH restringido por IP o por Azure Bastion |

**De dónde sale ese tamaño.** Con el sistema encendido y en reposo, los cuatro
contenedores consumen en total **entre 550 y 620 MB de RAM** (medido con
`docker stats` en dos momentos distintos; el reparto típico es app 114 MB,
api 176 MB, n8n 165 MB, qdrant 161 MB). Los 4 GB dan margen de
sobra para la indexación, que es el momento de más consumo. Con 2 GB se corre
riesgo: antes de existir el presupuesto de fichas, procesar un CSV de 434 MB
tumbó el contenedor de la API por falta de memoria.

**De dónde salen los 64 GB.** El volumen de Qdrant ocupa hoy **1,69 GB** y la
carpeta `data/` **505 MB**, de los cuales 86 MB son la caché de *embeddings*.
El resto es holgura para que el índice crezca sin sustos.

### Alternativa: Azure Container Apps

Si la entidad prefiere no administrar una VM, Container Apps funciona, pero
hay que resolver dos cosas que en la VM vienen dadas:

- **El estado.** Qdrant y n8n guardan datos. Hacen falta montajes de *Azure
  Files* para `/qdrant/storage` y `/home/node/.n8n`, y Qdrant debe quedar en una
  **única réplica**: dos instancias escribiendo el mismo almacenamiento se
  corrompen entre sí.
- **El cron.** n8n dispara la carga diaria desde dentro. Si el contenedor se
  escala a cero por inactividad, el cron no existe a esa hora. Hay que fijar
  `minReplicas: 1` para n8n.

**No se recomienda AKS.** Kubernetes para cuatro contenedores y un grupo de
usuarios añade una capa de administración que nadie va a necesitar.

---

## 3. Antes de desplegar: cinco cosas que hay que cambiar

El sistema funciona en desarrollo con valores de desarrollo. Estas cinco cosas
**fallarán en producción** si no se tocan. Están ordenadas de más a menos grave.

### 3.1. La dirección pública: una variable para dos destinos (bloqueante)

**Qué pasa.** La variable `API_URL_NAVEGADOR` —la dirección de la API *tal como
la ve el navegador*— se usa hoy para **dos cosas que en producción dejan de
coincidir**:

| Uso | Adónde tiene que apuntar | Cómo se construye |
|---|---|---|
| Pegar imágenes con Ctrl+V | A la **API** | `CFG.api + "/chat/imagen"` |
| Volver de Google al conectar Drive | A la **interfaz** | `api_url_navegador().replace(":8000", ":8501")` |

En desarrollo las dos direcciones solo se diferencian en el puerto, así que
cambiar `:8000` por `:8501` acierta. Detrás de un proxy HTTPS con un único
dominio, ese reemplazo ya no sirve para separarlas.

**Medido en el contenedor**, con la variable sin definir —que es el estado por
defecto:

```
api_url_navegador()  ->  http://127.0.0.1:8000
redirección          ->  http://127.0.0.1:8501     <-- Google no vuelve nunca
```

**Hay dos salidas, y conviene elegir a conciencia.**

*Opción A — publicar dos puertos (sin tocar código).*
Se expone la API en `https://dominio:8000` y la interfaz en
`https://dominio:8501`, y se define:

```
API_URL_NAVEGADOR=https://copiloto.entidad.gov.co:8000
```

Entonces la redirección sale `https://copiloto.entidad.gov.co:8501`, que es
correcta. Funciona hoy mismo, pero **obliga a dejar la API abierta a internet**,
justo lo que desaconseja el punto 3.3. Aceptable para un piloto; no para un
servicio de la entidad.

*Opción B — separar las dos direcciones (recomendada).*
Añadir una variable propia para la redirección, en vez de deducirla del puerto
de la API. Es un cambio pequeño: dos líneas en `app.py` y una entrada en el
`.env`. Permite dejar solo el 443 abierto y que el proxy encamine la API por una
ruta interna.

> **Este trabajo no está hecho.** Es la primera tarea del despliegue, y conviene
> decidir entre A y B antes de abrir el puerto 443.

**En cualquiera de los dos casos**, hay que dar de alta la URI en
**Google Cloud Console → Credenciales → ID de cliente OAuth 2.0 → URI de
redirección autorizados**:

```
https://copiloto.entidad.gov.co/
```

La barra final cuenta: Google compara la cadena completa, carácter a carácter.

### 3.2. El código va montado desde el disco

En `docker-compose.yml`, los servicios `api` y `app` montan el código fuente:

```yaml
volumes:
  - ./api:/app/api
  - ./core:/app/core
  - ./app.py:/app/app.py
  # ...
```

Eso es comodidad de desarrollo: se edita un archivo y el contenedor lo ve sin
reconstruir. **En producción sobra y es peligroso**: la imagen deja de ser la
fuente de verdad, y lo que corre depende de qué haya en el disco del servidor.

Para producción hay que usar un `docker-compose.prod.yml` **sin esos montajes**.
Solo se conservan los que guardan datos:

```yaml
volumes:
  - ./data:/app/data            # ajustes, conversaciones, caché, pendientes
  - ./.streamlit:/app/.streamlit  # tema de la interfaz
```

### 3.3. Solo un puerto mirando a internet

Hoy el compose publica cuatro puertos (8501, 8000, 6333, 5678). En producción,
**solo la interfaz debe ser accesible**:

- **8501** → detrás del proxy, con HTTPS. Es lo único público.
- **8000** (API), **6333** (Qdrant), **5678** (n8n) → se quitan del `ports:`.
  Los contenedores se siguen hablando por la red interna de Docker, que es como
  ya funcionan (`http://api:8000`, `http://qdrant:6333`).

**n8n expuesto es el riesgo más serio** de los tres: su interfaz permite
ejecutar código y ver credenciales. Si hace falta administrarlo, se llega por
un túnel SSH, no abriendo el puerto:

```bash
ssh -L 5678:localhost:5678 usuario@servidor
```

### 3.4. La interfaz no pide contraseña

Streamlit no tiene autenticación. Quien llegue a la URL, entra. Hay dos formas
de resolverlo en Azure:

- **Application Gateway o App Service con Easy Auth** delante, integrado con el
  Entra ID (Azure AD) de la entidad. Es la opción buena: la gente entra con su
  cuenta institucional y no hay una contraseña más que repartir.
- **nginx con autenticación básica** en la misma VM. Más simple, suficiente
  para un piloto, pero la contraseña se comparte y no se audita quién entró.

### 3.5. Los orígenes permitidos (CORS)

La API solo acepta peticiones del navegador desde los orígenes que se le digan.
Por defecto son `localhost`. En producción:

```
ORIGENES_PERMITIDOS=https://copiloto.entidad.gov.co
```

Si no se pone, **pegar una imagen con Ctrl+V deja de funcionar** y el fallo no
se ve en los registros del servidor: aparece solo en la consola del navegador
como "Failed to fetch". Es exactamente el síntoma que costó encontrar la
primera vez.

### 3.6. QGIS no corre en el servidor

Esto cambia de raíz al pasar a producción, y conviene entenderlo antes de
prometerle a nadie que «el mapa se abre solo».

**En desarrollo** todo ocurre en la misma máquina: Docker, la interfaz y QGIS.
El script le pregunta a la API dónde está el proyecto, la API deduce la ruta
leyendo sus propios montajes de Docker, y QGIS encuentra las capas.

**En producción no.** El servidor estará en Azure y QGIS en el portátil de
quien hace el estudio. Esa pregunta sigue funcionando —la API responde— pero
la respuesta es **la ruta del servidor**, que en el portátil no existe. El
script no se rompe: esa es su última pista, y antes mira lo que tiene al lado.

Así que en producción hay que darle las capas a quien use QGIS. Dos formas:

- **Carpeta compartida.** Un recurso de Azure Files montado en la VM sobre
  `data/`, y montado también en el equipo de la persona. El script encuentra
  las capas sin que nadie copie nada.
- **Descarga manual.** La persona baja los tres GeoJSON y los deja junto al
  script, o en una carpeta `data/` al lado. Funciona, pero hay que repetirlo
  cada vez que cambie el área.

> **Esto no está resuelto todavía.** La interfaz no ofrece botón para
> descargar las capas. Es una tarea pequeña y conviene hacerla antes de
> entregar, o la primera persona que use QGIS fuera del servidor se quedará
> con el mapa vacío.

---

## 4. Secretos

El sistema necesita dos secretos de verdad:

| Secreto | Para qué | Formato |
|---|---|---|
| `GEMINI_API_KEY` | Generar *embeddings* y responder en el chat | 53 caracteres, empieza por `AQ.` |
| Secreto de cliente OAuth | Conectar Google Drive | Se introduce desde la interfaz |

**No deben viajar en la imagen ni en un `.env` dentro del repositorio.** En
Azure van en **Key Vault**, y se inyectan como variables de entorno al arrancar
los contenedores.

Dos avisos que salieron de problemas reales en este proyecto:

- **Cuidado con los espacios.** Una clave escrita como `GEMINI_API_KEY= AQ...`
  —con un espacio tras el `=`— deja la clave con 54 caracteres en vez de 53.
  El sistema arranca sin quejarse y **toda búsqueda devuelve cero resultados**,
  como si el corpus estuviera vacío. El estado llegó a decir "10 fichas
  indexadas, listo" mientras ninguna consulta encontraba nada.
- **`env_file` se lee al CREAR el contenedor, no al reiniciarlo.** Después de
  cambiar un secreto, `docker compose restart` no sirve:

  ```bash
  docker compose up -d --force-recreate api app
  ```

El sistema trae una comprobación que revisa las credenciales sin imprimirlas
nunca (`core/seguridad.py`). Conviene pasarla después de desplegar.

---

## 5. Pasos del despliegue

### 5.1. Preparar la VM

```bash
sudo apt-get update && sudo apt-get install -y docker.io docker-compose-plugin
sudo usermod -aG docker $USER   # cerrar sesión y volver a entrar
```

### 5.2. Llevar el código y construir

```bash
git clone <repositorio> copiloto && cd copiloto
cp .env.example .env            # y rellenar los valores de producción
docker compose build
```

### 5.3. Comprobar la zona horaria

**El cron depende de esto.** La carga diaria está programada a las 10:30, y esa
hora se interpreta en la zona horaria del contenedor de n8n. Si el servidor
está en UTC y se espera hora de Colombia, la carga ocurrirá cinco horas
después de lo previsto.

```bash
docker compose exec n8n date   # debe coincidir con la hora esperada
```

Si no coincide, se fija en el compose:

```yaml
n8n:
  environment:
    GENERIC_TIMEZONE: America/Bogota
    TZ: America/Bogota
```

### 5.4. Arrancar y comprobar

```bash
docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d
```

Y entonces, **antes de darlo por bueno**, pasar las baterías de pruebas. No son
decorativas: cada una existe porque algo falló de esa manera.

```bash
docker compose exec api python probar_campo.py       # 26 comprobaciones
docker compose exec api python probar_vectorial.py   # 54
docker compose exec api python probar_corpus.py      # 32
docker compose exec api python probar_drive.py       # 22
docker compose exec api python probar_agente.py      # 15
python probar_despliegue.py                          # 128, desde FUERA
```

Y una séptima, que necesita QGIS instalado y se ejecuta con **su** intérprete,
no con el del sistema:

```bash
"/c/Program Files/QGIS 3.40.4/apps/Python312/python.exe" probar_qgis.py   # 17
```

En total **294 comprobaciones**. Ninguna es decorativa: cada una existe porque
algo falló de esa manera.

`probar_despliegue.py` se ejecuta **desde el servidor, no dentro del
contenedor**: comprueba los contenedores desde afuera, por HTTP y por Docker.
Correrla por dentro no prueba lo mismo.

Necesita algunas bibliotecas en el Python del servidor, las que arrastra
`qdrant-client`. Sin ellas la comprobación de Qdrant falla por una razón que
no tiene nada que ver con el sistema, y ensucia la única señal que dice si el
despliegue está bien:

```bash
pip install -r requirements-equipo.txt
```

Son cuatro paquetes —`idna`, `urllib3`, `certifi` y `charset-normalizer`— que
se descubrieron de uno en uno, cada uno al resolver el anterior. Están
escritos en ese archivo para que nadie repita la cadena.

### 5.5. Fijar el área de estudio

Sin esto, el sistema funciona pero **no puede validar nada de campo**: no tiene
con qué comparar una coordenada. Y QGIS abre un mapa vacío.

En la pestaña **Área de Estudio**, sección 1, se sube el archivo que delimita
el área —un `.kmz` de Google Earth, un `.kml` o un `.geojson`— y se elige cuál
de sus polígonos es el área. Del mismo archivo se extraen, de paso, las veredas
y la red hídrica.

Esa elección **cambia qué datos de campo se aceptan**, así que la toma quien
conoce el proyecto, no el sistema. En el caso de San Carlos el archivo trae
cuatro polígonos que significan cosas distintas: el límite municipal
(720,77 km²), una unidad hidrográfica del POMCA (908,75 km²), el área inscrita
del proyecto (127,70 km²) y el embalse (36,74 km²).

### 5.6. Conectar Drive

Desde la interfaz, pestaña **Drive y Estudio**: se introducen el ID de cliente
y el secreto, y se autoriza en Google. No hace falta ningún archivo JSON.

Si la redirección del punto 3.1 no se ha arreglado, **este paso fallará**.

---

## 6. Qué hay que respaldar

| Qué | Dónde | Por qué |
|---|---|---|
| `qdrant_datos` | Volumen Docker (1,69 GB hoy) | El índice. Reconstruirlo gasta cuota del modelo |
| `data/conversaciones.sqlite` | Carpeta `data/` | El historial de conversaciones |
| `data/vectorial_cache.sqlite` | Carpeta `data/` (86 MB) | Caché de *embeddings*. **Ahorra cuota de verdad**: reindexar con la caché caliente no gasta nada |
| `n8n_datos` | Volumen Docker | Los flujos y la hora del cron |
| `data/*.geojson` | Carpeta `data/` | El área de estudio y sus capas. Pequeñas, pero rehacerlas obliga a volver a subir el archivo de origen |
| `.env` | Fuera del servidor | Los secretos |

**Ojo con `data/` en el repositorio.** Ahí conviven el área de estudio (1 MB,
debe viajar con el código) y cosas que no deben salir de la máquina: la caché
de vectores (86 MB), las conversaciones, las credenciales pendientes y las
capas nacionales descargadas, una de ellas de **417 MB**. El `.gitignore`
ignora la carpeta entera y rescata solo lo que sí sube. Sin esa regla, el
primer `git push` habría intentado subir medio giga y GitHub lo habría
rechazado: no admite archivos de más de 100 MB.

**La caché de *embeddings* merece atención especial.** Perderla no rompe nada,
pero obliga a volver a pagar cada vector. Con ella, una reindexación completa de
los cuatro documentos tarda **72 segundos y cuesta cero**.

Lo que **no** hay que respaldar: `corpus/`. Con Drive conectado esa carpeta está
vacía por diseño.

Respaldo diario, con la aplicación parada un momento para que Qdrant no esté
escribiendo:

```bash
docker compose stop qdrant
docker run --rm -v proyecto_qdrant_datos:/datos -v $(pwd)/respaldos:/salida \
  alpine tar czf /salida/qdrant-$(date +%F).tar.gz -C /datos .
docker compose start qdrant
```

En Azure, lo más simple es activar **Azure Backup** sobre el disco de la VM y
olvidarse del script.

---

## 7. Operación diaria

### El estudio programado

Todos los días a las **10:30** (hora del servidor), n8n le pide a la API que
revise Drive y actualice el índice. Solo procesa lo que cambió: si no hay nada
nuevo, termina en segundos.

Medido en una ejecución real partiendo de un índice vacío: **4 documentos, 264
fichas, 72,5 segundos**, y los archivos descargados borrados al terminar.

La hora se cambia **únicamente en el nodo del flujo de n8n**. No está escrita en
ningún otro sitio, y eso es deliberado: cuando la hora aparecía también en el
nombre del nodo y en los textos de la interfaz, se cambiaba en un sitio y los
otros dos seguían anunciando la hora vieja.

### Qué mirar cuando algo va mal

```bash
docker compose ps                    # ¿están los cuatro en pie?
docker compose logs --tail 50 api
curl http://localhost:8000/corpus/estado | jq .
```

`/corpus/estado` responde en **13 ms** (tiene una caché de 30 segundos) y dice
de dónde salen los documentos, cuántas fichas hay en el índice y si hay una
carga en marcha.

En la interfaz, mientras se carga aparece una banda arriba, visible desde
cualquier pestaña, que dice **quién pidió la carga** (el cron o una persona),
qué documento se está leyendo y cuánto lleva. Se actualiza sola.

### Qué NO hacer

- **No reiniciar n8n con una carga en marcha.** La ejecución queda marcada como
  `crashed` y hay que lanzarla de nuevo.
- **No borrar el volumen de Qdrant para "limpiar".** Si lo que se quiere es
  vaciar el índice, se hace desde la aplicación, que deja la colección
  preparada y con sus índices de búsqueda. Recrearla a mano pierde el índice de
  fechas, y entonces ordenar por fecha devuelve resultados arbitrarios **sin
  dar ningún error**.

---

## 8. El consumo del modelo

El recurso escaso no es el servidor: es la **cuota de la API de Gemini**. La
capa gratuita permite alrededor de **1.000 *embeddings* al día**.

El sistema se protege solo con un **presupuesto por documento**:

```
PRESUPUESTO_FICHAS=2000
```

Lo que pase de ahí **no desaparece ni se queda fuera**: en vez de indexarlo
fila a fila, que es imposible, se recorre entero y se indexa su **resumen
agrupado**. Las cifras que justifican ese límite, medidas sobre los archivos
reales de la entidad:

| | |
|---|---|
| Densidad de un CSV tabular | **1.485 fichas por MB** |
| Un archivo de 434 MB | **644.936 fichas** |
| Tiempo para indexarlo | **97 horas** seguidas |
| Con la cuota gratuita | **3,5 años** |

Por eso el límite no está en megas. Un informe de 26 MB con fotos cuesta **30
fichas** y entra entero; un CSV de 30 MB costaría 45.000 y entra resumido.

**Qué significa «resumido».** Se recorre la tabla completa —460.350 filas en
unos 30 segundos, sin gastar un solo embedding, porque agrupar y sumar es
trabajo local— y se indexan las cuentas: cuántas hectáreas de bosque, de
agroecosistema, por zona hidrográfica. Una fila suelta no significa nada para
una búsqueda por significado; el agregado sí. Cada capa de 434 MB aporta así
**28 fichas en lugar de 644.936**.

El resumen se guarda en disco, así que la sincronización diaria no vuelve a
bajar 1,7 GB si las capas no han cambiado.

Hay además un segundo límite, para lo que **no se ha podido medir**: a ciegas
solo se bajan 300 MB. No saber lo que algo cuesta no es razón para suponer que
es gratis.

Si la entidad contrata cuota de pago, se sube `PRESUPUESTO_FICHAS` en el `.env`
y se reinicia. No hay que tocar código.

---

## 9. Lista de verificación

Antes de dar el despliegue por terminado:

- [ ] Decidida la opción A o B del punto 3.1, y `API_URL_NAVEGADOR` definida
- [ ] Comprobado que al volver de Google se aterriza en la interfaz, no en `127.0.0.1`
- [ ] La URI está dada de alta en Google Cloud Console, con la barra final
- [ ] El compose de producción **no monta el código fuente**
- [ ] Solo el 443 está abierto a internet; 8000, 6333 y 5678 cerrados
- [ ] La interfaz pide autenticación
- [ ] `ORIGENES_PERMITIDOS` apunta al dominio de producción
- [ ] Los secretos vienen de Key Vault, no de un archivo en el repositorio
- [ ] `GEMINI_API_KEY` mide 53 caracteres, **sin espacios**
- [ ] `docker compose exec n8n date` da la hora esperada
- [ ] Las siete baterías de pruebas pasan (294 comprobaciones)
- [ ] El área de estudio está fijada, y la eligió quien conoce el proyecto
- [ ] Quien use QGIS puede llegar a las capas de `data/` (punto 3.6)
- [ ] Drive conecta desde la interfaz
- [ ] El respaldo está programado y **se ha probado una restauración**
- [ ] Alguien ha visto una carga completa de principio a fin
