#!/usr/bin/env sh
# Importa y activa los flujos del proyecto dentro del contenedor de n8n.
# ==============================================================================
#
#   sh n8n/importar_flujos.sh
#
# Es idempotente: volver a ejecutarlo actualiza los flujos con lo que haya en
# n8n/flujos/ sin duplicarlos, porque n8n los reconoce por su identificador.
#
# TRES COSAS QUE COSTARON DE AVERIGUAR
# ------------------------------------
#   1. `--input=archivo.json` falla con "workflows.map is not a function": la
#      orden espera una CARPETA con --separate, o un array. Se usa la carpeta.
#   2. Un flujo sin el campo `active` revienta la importacion con
#      "NOT NULL constraint failed: workflow_entity.active".
#   3. Dos flujos que comparten etiqueta fallan con
#      "UNIQUE constraint failed: tag_entity.name". Por eso los JSON van sin
#      etiquetas: se ponen desde la interfaz, que si sabe reutilizarlas.
#
# Y la activacion no surte efecto hasta reiniciar n8n, asi que se reinicia.

set -e

CONTENEDOR="${CONTENEDOR_N8N:-copiloto-n8n}"

echo "==> Importando flujos en $CONTENEDOR"
docker exec "$CONTENEDOR" n8n import:workflow --separate --input=/flujos

echo "==> Activando"
# Se activan por identificador, que es lo que devuelve la lista.
IDS=$(docker exec "$CONTENEDOR" n8n list:workflow 2>/dev/null \
      | grep "Copiloto" | cut -d"|" -f1)

for id in $IDS; do
    docker exec "$CONTENEDOR" n8n update:workflow --id="$id" --active=true \
        >/dev/null 2>&1 || true
    echo "    activado $id"
done

echo "==> Reiniciando n8n para que registre el webhook y el disparador horario"
docker restart "$CONTENEDOR" >/dev/null
sleep 25

echo "==> Flujos en marcha:"
docker exec "$CONTENEDOR" n8n list:workflow 2>/dev/null | grep "Copiloto" || true

echo ""
echo "Interfaz : http://localhost:5678"
echo "Webhook  : curl -X POST http://localhost:5678/webhook/corpus-consultar \\"
echo "             -H 'Content-Type: application/json' \\"
echo "             -d '{\"pregunta\":\"plantas hospederas de Morpho peleides\"}'"
