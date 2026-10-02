# Archivos retirados del proyecto

Se movieron aquí porque **ningún módulo del sistema los usa**. Se conservan en
lugar de borrarse porque el proyecto no está bajo control de versiones: si algo
de esto te hace falta, aquí sigue. Cuando confirmes que no lo necesitas, borra
esta carpeta entera.

| Archivo | Por qué salió |
| :--- | :--- |
| `municipios_filtrados(eliminiar).md` | Copia byte a byte de `municipios_filtrados.geojson`. El propio nombre indicaba que sobraba. |
| `sancarlos_municipio.geojson` | Capa de un solo municipio. Se verificó que ninguno de los cuatro proyectos `.qgz` de la carpeta superior la referencia. |
| `walkthrough.md` | Bitácora de desarrollo. Describía una estructura de carpetas que ya no existe y contenía rutas absolutas. |
| `plan_implementacion.md` | Plan inicial, ya ejecutado. Contenía rutas absolutas. |
| `comparacion arquitectura detallado.md` | Análisis previo de alternativas de arquitectura, anterior a la decisión ya tomada. |
| `comparativa_arquitecturas_gis_resultado.md` | Ídem. |
| `descargar_antioquia.py` | Su función se integró en `filtrar_biodiversidad.py` (`--listar-departamentos` y `--solo-cartografia`). El nombre además era engañoso: hacía tiempo que no tenía nada específico de Antioquia. |

## Equivalencias

Lo que hacía `descargar_antioquia.py` ahora se hace así:

```bash
python filtrar_biodiversidad.py --listar-departamentos
python filtrar_biodiversidad.py --solo-cartografia --departamento Antioquia
```
