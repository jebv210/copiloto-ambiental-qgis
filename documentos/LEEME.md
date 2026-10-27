# Documentos de la práctica

Aquí viven los **entregables** del proyecto: informes, el libro de diccionario de
datos y los planes técnicos. **No son código**: ningún programa del sistema los
lee ni depende de ellos, por eso están fuera de la raíz.

| Archivo | Qué es |
|---|---|
| `Informe_Avance_Practica_II.docx` | Informe de avance, agosto–septiembre de 2026 |
| `Practica_II_Datos_y_Entregables.xlsx` | Libro de diccionario de datos (12 hojas, **boceto**) |
| `Boceto_Agente_IA_Lepidopteros.docx` | Diseño del agente de conocimiento |
| `Plan_v3_Agente_Local.docx` | Plan técnico vigente, con las mediciones del equipo |
| `informe_impacto_ambiental.md` | Informe de impacto ambiental del área de estudio |
| `informe_avance_y_cronograma.md` | Avance y cronograma en texto |

## Las tres carpetas que no son código, y en qué se diferencian

| Carpeta | Qué contiene | ¿El sistema la lee? |
|---|---|---|
| `documentos/` | Entregables de la práctica: informes y planes | **No** |
| `corpus/` | La bibliografía que el agente estudia | **Sí**, es su memoria |
| `_archivo/` | Material retirado que ya no se usa | **No** |

Si dudas dónde va algo nuevo: si el agente tiene que **aprenderlo**, va a
`corpus/`. Si es algo que **se entrega o se lee**, va aquí. Si **ya no sirve**
pero no quieres borrarlo, va a `_archivo/`.
