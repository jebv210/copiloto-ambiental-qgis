# Orquestación con n8n

Esta carpeta contiene los flujos de n8n del proyecto. La idea de fondo es un
reparto de responsabilidades que conviene no romper:

- **n8n decide CUÁNDO.** Disparadores horarios, reintentos, avisos y la entrada
  de otros proyectos.
- **La API decide QUÉ.** Leer documentos, trocearlos, generar embeddings,
  guardar en Qdrant, calcular Conesa, filtrar GBIF e intersectar geometrías.

Meter el geoprocesamiento o el filtrado del archivo pesado dentro de nodos de
n8n ahogaría el flujo. Por eso cada nodo que hace trabajo real es una llamada
HTTP a la API.

## Arrancar todo

```bash
docker compose up -d
```

| Servicio | Dirección |
|---|---|
| Interfaz (Streamlit) | http://localhost:8501 |
| API (documentación) | http://localhost:8000/docs |
| n8n | http://localhost:5678 |
| Qdrant (panel) | http://localhost:6333/dashboard |

## Importar los flujos

```bash
sh n8n/importar_flujos.sh
```

Importa, activa y reinicia n8n. Es **idempotente**: volver a ejecutarlo
actualiza los flujos con lo que haya en `n8n/flujos/` sin duplicarlos, porque
cada uno lleva un identificador fijo.

También se pueden importar a mano desde el panel (menú de los tres puntos →
**Import from File**), pero entonces hay que activarlos uno a uno.

Los flujos usan la variable `COPILOTO_API`, que `docker-compose.yml` ya define
como `http://api:8000`. Si se cambia el puerto o el nombre del servicio, se
cambia ahí y no en cada nodo.

### Si editas un flujo, cuidado con estos tres

Son errores que costaron de averiguar y que no dicen lo que pasa:

| Mensaje al importar | Causa |
|---|---|
| `workflows.map is not a function` | Se paso un archivo suelto; la orden quiere una carpeta con `--separate` |
| `NOT NULL constraint failed: workflow_entity.active` | Al flujo le falta el campo `active` |
| `UNIQUE constraint failed: tag_entity.name` | Dos flujos comparten etiqueta; por eso van sin ellas |

Y si quitas el campo `id`, cada importación crea un flujo nuevo en vez de
actualizar el existente.

## Los flujos

### 01 · Sincronizar corpus cada 24 h

Cada noche a las 02:00 dispara la indexación y espera a que termine.

```
Cron 02:00 → POST /corpus/sincronizar → esperar 30 s
                                            ↓
                              GET /corpus/estado
                                            ↓
                              ¿sigue? ─sí→ volver a esperar
                                            └no→ resumen
```

El bucle de espera existe porque reindexar son minutos, no segundos: una sola
comprobación encontraría el trabajo a medias. El endpoint devuelve al instante
y el trabajo sigue en segundo plano, para que n8n no muera por tiempo de espera.

El nodo final deja preparado el resumen (documentos nuevos, actualizados, con
error). Ahí es donde se engancha el aviso por correo, Slack o Discord; no se
incluye porque cada equipo usa uno distinto.

### 03 · Chat del Agente (con memoria en Qdrant)

El recorrido completo de una pregunta:

```
Pregunta del sistema → ¿Trae pregunta? → Preguntar al agente
                            → Guardar en Qdrant → Armar respuesta → Responder
```

```bash
curl -X POST http://localhost:5678/webhook/copiloto-chat \
  -H "Content-Type: application/json" \
  -d '{"pregunta": "plantas hospederas de Morpho peleides", "session_id": "demo"}'
```

El nodo **Guardar en Qdrant** escribe el intercambio en la colección
`memoria_usuario`, con su fecha en ISO 8601 (UTC). Es un nodo propio a
propósito: el agente también sabe guardar por su cuenta en un hilo de fondo,
pero así el paso **se ve** en el lienzo y queda en el historial de ejecuciones.
Por eso la llamada a `/agente/chat` va con `guardar_memoria: false`: si
guardara en los dos sitios, el mismo intercambio entraría dos veces.

La respuesta trae el acuse: `memoria_guardada`, `memoria_coleccion` y
`memoria_fecha`.

> **Las imágenes pegadas no viajan por n8n.** La API las recoge por
> `session_id`. Pasar una captura en base64 por interfaz → n8n → API serían
> megas en cada pregunta, y al modelo le llega igual.

---

### Flujo retirado: 02 · Consultar corpus

Exponía la bibliografía a otros proyectos por el webhook `corpus-consultar`.
**Se retiró el 23-10-2026.** Llevaba desde el 18 de octubre sin que lo llamara
nadie del proyecto: sus 42 ejecuciones eran todas de `probar_despliegue.py`.

Lo que hacía no se perdió: está en la API, que es donde siempre estuvo.

```bash
curl -X POST http://localhost:8000/corpus/consultar \
  -H "Content-Type: application/json" \
  -d '{"pregunta": "plantas hospederas de Morpho peleides", "limite": 5}'
```

Su JSON está en `_archivo/flujos_n8n_retirados/`. Para recuperarlo basta
devolverlo a `n8n/flujos/` y reimportar.

## Lo que hay que saber antes de la primera sincronización

**La primera construcción del índice tarda horas.** No es el código: es la
cuota de la capa gratuita de Gemini. Medido en este proyecto, 450 fragmentos
tardaron 247 segundos, y casi todo ese tiempo fue esperando a que se liberara
la cuota. El corpus completo son 17.032 fragmentos.

Las siguientes son rápidas, porque solo se reprocesa lo que cambió: cada
documento lleva una huella y los que siguen iguales ni se leen.

La recomendación práctica es lanzar la primera indexación de noche, que es
justamente para lo que sirve el flujo 01.

**Si no hay índice vectorial, el sistema sigue funcionando.** La búsqueda
híbrida cae a BM25 sin avisar y el agente responde igual. La base vectorial
añade búsqueda por significado; no es un requisito para operar.

## Antes de exponerlo a internet

`docker-compose.yml` arranca n8n **sin autenticación**, que está bien en local
y no está bien en ningún otro sitio. Antes de publicarlo hay que activar
`N8N_BASIC_AUTH_ACTIVE` con usuario y clave, y poner la API detrás del mismo
dominio o de una red privada.
