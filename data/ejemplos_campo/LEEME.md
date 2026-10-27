# Ejemplos de salidas de campo

Archivos reales usados por `probar_campo.py`. No son documentacion: son los
datos contra los que se comprueba que la revision funciona.

## `qfield_fuera_del_area.zip`

Exportacion real de QField. Trae dos GeoPackage (notas y recorridos), el
proyecto de QGIS y una miniatura.

Se conserva porque reune, en un solo archivo, cuatro de los problemas que la
revision tiene que detectar:

  - sus 4 notas y su recorrido caen a 166-168 km del area de estudio;
  - dos notas estan en coordenadas identicas, 38 segundos aparte;
  - trae un adjunto con la foto vacia;
  - el recorrido no tiene titulo.

Ademas es el que destapo que los recorridos se estaban leyendo como si fueran
puntos: una linea decodificada como punto daba "a 8.318 km del area", mas que
el ancho de Colombia.
