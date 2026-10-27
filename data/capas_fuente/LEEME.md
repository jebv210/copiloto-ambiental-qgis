# Capas de datos, NO corpus

Esto **no es bibliografía**: son tablas de atributos de capas cartográficas.
El agente no las lee. Se procesan con el filtrado por bloques
(`core/dynamic_filter.py`), igual que el archivo del SiB.

| Archivo | Filas | Qué es |
|---|---|---|
| `ecosistemas_colombia_100K_2024.csv` | 460.353 | Ecosistemas continentales, costeros y marinos 1:100K, nacional |
| `pomcas_nacional_2021.csv` | 400 | Registro de Planes de Ordenación y Manejo de Cuencas |

## Por qué no van al corpus

Medido: trocear el de ecosistemas da unas **204.000 fichas**. A 1.000
embeddings diarios de la capa gratuita son **más de 200 días** de indexación,
para que el agente rebusque entre 460.000 filas de todo el país lo que un
cruce espacial resuelve en milisegundos.

## Cómo se usan

El de ecosistemas **no trae columna de departamento ni municipio**: se
comprobó que no menciona «Antioquia» ni «San Carlos» en ninguna de sus 49
columnas. Para quedarse con San Carlos hay que cruzarlo **por geometría**
contra la cartografía del DANE.

Ese recorte —unas pocas decenas de filas— sí es material de corpus, y entonces
se añade con:

```bash
python -m core.importar <recorte.csv> -c "9. Ecosistemas San Carlos"
```

## Duplicados descartados

Llegaron cuatro archivos de ecosistemas y dos de POMCAS. Comprobado por md5:
eran dos copias de cada uno. La única diferencia entre los dos pares de
ecosistemas es una columna `OBJECTID`; se conservó la que la trae.
