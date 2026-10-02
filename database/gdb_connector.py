"""
Conector espacial: consulta la linea base geografica de una coordenada.
===============================================================================

EL ERROR QUE ESTE ARCHIVO ARRASTRABA
------------------------------------
La version anterior tenia este bloque:

    if os.path.exists(ruta_ant):
        ...
        diagnostico["vereda"] = f"Area rural/urbana de {mpio_nombre}"
        diagnostico["cuenca"] = f"Cuenca local de {mpio_nombre} (Jurisdiccion
                                 Ambiental Antioquia)"
        diagnostico["areas_protegidas"] = []
        diagnostico["paramo"] = False
        return diagnostico          # <-- SALIDA ANTICIPADA

Consecuencias, todas graves para una herramienta de evaluacion ambiental:

  1. La vereda y la cuenca eran cadenas INVENTADAS con f-strings. No salian de
     ningun dato: se fabricaban a partir del nombre del municipio.
  2. La jurisdiccion decia "Antioquia" siempre, aunque el usuario hubiera
     generado el area de estudio de otro departamento.
  3. El `return` impedia que se consultaran RUNAP y paramos. Como
     `areas_protegidas` quedaba en `[]` y `paramo` en `False`, la interfaz
     mostraba en verde "esta fuera de zonas de reserva" para CUALQUIER punto.
     Es decir: daba via libre sin haber mirado.

COMO SE CORRIGIO
----------------
  * Se elimino la salida anticipada: la capa municipal aporta el municipio y el
    proceso CONTINUA consultando la Geodatabase.
  * Nada se inventa. Un dato que no se pudo obtener vale None y se reporta como
    "no disponible", no como un valor plausible.
  * Se distingue explicitamente entre "evaluado, sin hallazgos" (lista vacia /
    False) y "NO evaluado" (None). La interfaz pinta el primero en verde y el
    segundo en amarillo. No haber mirado no es lo mismo que no encontrar nada.
  * Los nombres de capa y de campo ya no estan escritos literalmente: se
    descubren en la Geodatabase por palabras clave (ver `ROLES_CAPA`).
"""

import os

from config import settings
from core.textos import normalizar
from core import catalogo


class GDBConnector:
    """
    Consulta espacial sobre la Geodatabase del proyecto y la capa municipal.

    A diferencia de la version anterior, el constructor NUNCA lanza excepcion
    por falta de Geodatabase: se construye igual y expone `disponible = False`.
    Asi la aplicacion arranca sin GDB y puede seguir usando la capa municipal,
    avisando de lo que no puede evaluar.
    """

    # --------------------------------------------------------------------------
    # HEURISTICAS DE DESCUBRIMIENTO
    # --------------------------------------------------------------------------
    # Estas NO son rutas ni datos quemados: son palabras clave para reconocer
    # que papel cumple cada capa dentro de una Geodatabase cualquiera. Antes el
    # codigo pedia capas por su nombre exacto ("SubCuencaHidrografica",
    # "AIA_EcoEstrat_Paramo"), asi que con una GDB distinta no encontraba nada.
    # La comparacion se hace sobre el nombre normalizado (sin tildes, en
    # mayusculas), buscando la palabra clave como subcadena.

    #: Papel logico -> palabras clave, EN ORDEN DE PREFERENCIA. La primera
    #: palabra que encuentre una capa gana, de modo que "SUBCUENCA" se prefiere
    #: sobre "CUENCA" (la subcuenca es la unidad de analisis en un POMCA) y
    #: "RUNAP" sobre otras figuras de proteccion.
    ROLES_CAPA = {
        "municipio":        ["MUNICIPIO", "MPIO"],
        "vereda":           ["VEREDA"],
        "cuenca":           ["SUBCUENCA", "MICROCUENCA", "CUENCA", "HIDROGRAFICA"],
        "areas_protegidas": ["RUNAP", "AREA PROTEGIDA", "PROTEGIDA", "SINAP",
                             "RESERVA", "DISTRITO MANEJO"],
        "paramo":           ["PARAMO"],
    }

    #: Papeles para los que interesa consultar TODAS las capas que coincidan,
    #: no solo la mejor. Una figura de proteccion puede estar repartida entre
    #: varias capas (RUNAP, SINAP, distritos de manejo) y quedarse con una sola
    #: produciria falsos negativos, que es exactamente el error que este
    #: modulo venia arrastrando.
    ROLES_MULTIPLES = {"areas_protegidas"}

    #: Papel logico -> palabras clave que puede contener el nombre del campo
    #: del que se extrae la etiqueta a mostrar.
    ROLES_CAMPO = {
        "municipio":        ["MPIO CNMBR", "NOMBRE ENT", "NOMBRE MPIO",
                             "MUNICIPIO", "NOMBRE"],
        "vereda":           ["NOMBRE VER", "VEREDA", "NOMBRE"],
        "cuenca":           ["NOM SUBCUE", "NOMBRE CUENCA", "CUENCA", "NOMBRE"],
        "areas_protegidas": ["NOMBRE", "NOM AREA", "CATEGORIA"],
        "paramo":           ["NOMBRE", "COMPLEJO"],
    }

    def __init__(self, ruta_gdb: str = None):
        """
        Prepara el conector eligiendo la Geodatabase mas util disponible.

        Args:
            ruta_gdb: Ruta explicita. Si es None, se evaluan todas las
                Geodatabases que encuentre `config.settings.gdb_candidatas()`
                y se elige la que mas capas de interes contenga.
        """
        self.gdb_path = ruta_gdb or self._elegir_mejor_gdb()
        self.disponible = bool(self.gdb_path and os.path.exists(self.gdb_path))

        #: Cache de nombres de capa de la GDB, para no releer el catalogo en
        #: cada consulta.
        self._capas = None

    @classmethod
    def _capas_de(cls, ruta: str):
        """
        Lista los nombres de capa de una Geodatabase cualquiera.

        Args:
            ruta: Ruta de la Geodatabase.

        Returns:
            Lista de nombres. Vacia si no se pudo leer.
        """
        try:
            import pyogrio
            return [nombre for nombre, _ in pyogrio.list_layers(ruta)]
        except Exception:
            return []

    @classmethod
    def _puntuar_gdb(cls, capas) -> int:
        """
        Cuenta cuantos papeles de interes cubre un conjunto de capas.

        POR QUE HACE FALTA:
        una carpeta `.gdb` suele contener varias Geodatabases anidadas. En este
        proyecto conviven `GDB_OFICIAL_IGAC_INTERSECT.gdb` (cartografia base:
        vias, curvas de nivel, construcciones) y
        `GDB_POMCAS_CARARE_INTERSECT.gdb` (la del POMCA: municipios, veredas,
        cuencas, RUNAP y paramos). Elegir "la primera por orden alfabetico"
        seleccionaba la primera, que no tiene ninguna de las capas que este
        modulo necesita, y todas las consultas quedaban sin evaluar.

        Args:
            capas: Nombres de capa de la Geodatabase.

        Returns:
            Numero de papeles (`ROLES_CAPA`) que esa Geodatabase puede cubrir.
        """
        normalizadas = [normalizar(c) for c in capas]
        puntos = 0
        for claves in cls.ROLES_CAPA.values():
            if any(clave in capa for clave in claves for capa in normalizadas):
                puntos += 1
        return puntos

    @classmethod
    def _elegir_mejor_gdb(cls):
        """
        Selecciona, entre las Geodatabases encontradas, la mas completa.

        Si la variable de entorno `GDB_PATH` esta definida, `gdb_candidatas`
        devuelve solo esa y se respeta sin discusion.

        Returns:
            Ruta de la Geodatabase elegida, o None si no hay ninguna.
        """
        candidatas = settings.gdb_candidatas()
        if not candidatas:
            return None
        if len(candidatas) == 1:
            return candidatas[0]

        puntuadas = [
            (cls._puntuar_gdb(cls._capas_de(ruta)), -len(ruta), ruta)
            for ruta in candidatas
        ]
        puntuadas.sort(reverse=True)

        mejor_puntaje, _, mejor_ruta = puntuadas[0]
        # Si ninguna cubre ningun papel, se devuelve la primera igualmente:
        # al menos permite listar sus capas desde la API.
        return mejor_ruta if mejor_puntaje else candidatas[0]

    # --------------------------------------------------------------------------
    # INVENTARIO DE LA GEODATABASE
    # --------------------------------------------------------------------------

    def listar_capas(self) -> list:
        """
        Lista las capas vectoriales de la Geodatabase con su tipo de geometria.

        Returns:
            Lista de diccionarios `{"capa": nombre, "geometria": tipo}`.
            Lista vacia si no hay Geodatabase disponible.
        """
        if not self.disponible:
            return []

        import pyogrio
        try:
            return [
                {"capa": nombre, "geometria": geometria}
                for nombre, geometria in pyogrio.list_layers(self.gdb_path)
            ]
        except Exception:
            return []

    def nombres_capas(self) -> list:
        """
        Devuelve solo los nombres de las capas, cacheados en memoria.

        Returns:
            Lista de nombres de capa. Vacia si no hay Geodatabase.
        """
        if self._capas is None:
            self._capas = [c["capa"] for c in self.listar_capas()]
        return self._capas

    def buscar_capas(self, rol: str):
        """
        Encuentra TODAS las capas que cumplen un papel, por orden de relevancia.

        Prioridad de la busqueda:
          1. La variable de entorno `CAPA_<ROL>` (ej. `CAPA_PARAMO`), si el
             usuario quiere forzar una capa concreta.
          2. Coincidencia exacta del nombre normalizado con una palabra clave.
          3. Coincidencia parcial, respetando el ORDEN de las palabras clave y,
             a igualdad, prefiriendo el nombre de capa mas corto (el mas
             especifico: "Municipio" antes que "MunicipioAnno").

        Args:
            rol: Clave de `ROLES_CAPA`.

        Returns:
            Lista de nombres de capa, la mejor primero. Vacia si ninguna encaja.
        """
        disponibles = self.nombres_capas()

        forzada = settings.env(f"CAPA_{rol.upper()}")
        if forzada and forzada in disponibles:
            return [forzada]

        claves = self.ROLES_CAPA.get(rol, [])
        encontradas = []

        for clave in claves:
            # Primero las coincidencias exactas de esta palabra clave.
            exactas = [c for c in disponibles if normalizar(c) == clave]
            # Luego las parciales, de nombre mas corto a mas largo.
            parciales = sorted(
                (c for c in disponibles
                 if clave in normalizar(c) and normalizar(c) != clave),
                key=lambda c: len(c),
            )
            for capa in exactas + parciales:
                if capa not in encontradas:
                    encontradas.append(capa)

        return encontradas

    def buscar_capa(self, rol: str):
        """
        Devuelve la capa mas adecuada para un papel, o None.

        Args:
            rol: Clave de `ROLES_CAPA` ("municipio", "vereda", "cuenca",
                "areas_protegidas" o "paramo").

        Returns:
            Nombre real de la capa, o None si ninguna encaja.
        """
        capas = self.buscar_capas(rol)
        return capas[0] if capas else None

    def obtener_resumen_capa(self, nombre_capa: str) -> dict:
        """
        Devuelve metadatos de una capa sin cargar su geometria en memoria.

        Args:
            nombre_capa: Nombre exacto de la capa.

        Returns:
            Diccionario con `nombre`, `cantidad_objetos`, `geometria` y
            `columnas`.

        Raises:
            RuntimeError: Si no hay Geodatabase disponible.
        """
        if not self.disponible:
            raise RuntimeError("No hay Geodatabase configurada.")

        import pyogrio
        info = pyogrio.read_info(self.gdb_path, layer=nombre_capa)
        return {
            "nombre": nombre_capa,
            "cantidad_objetos": info["features"],
            "geometria": info["geometry_type"],
            "columnas": list(info["fields"]),
        }

    # --------------------------------------------------------------------------
    # CONSULTA ESPACIAL BASICA
    # --------------------------------------------------------------------------

    def consultar_interseccion_punto(self, lat: float, lon: float,
                                     nombre_capa: str) -> list:
        """
        Devuelve los objetos de una capa que contienen una coordenada GPS.

        El punto se crea en EPSG:4326 (coordenadas GPS) y se REPROYECTA al
        sistema de la capa antes de intersectar. Sin esa reproyeccion, un punto
        en grados nunca coincidiria con poligonos en metros (las capas del IGAC
        suelen venir en EPSG:9377, Origen Nacional).

        Args:
            lat: Latitud en grados decimales (WGS84).
            lon: Longitud en grados decimales (WGS84).
            nombre_capa: Capa de la Geodatabase a consultar.

        Returns:
            Lista de diccionarios con los atributos de cada objeto que contiene
            el punto (sin la columna de geometria, para poder serializar).

        Raises:
            RuntimeError: Si no hay Geodatabase disponible.
        """
        if not self.disponible:
            raise RuntimeError("No hay Geodatabase configurada.")

        import geopandas as gpd
        from shapely.geometry import Point

        punto_gps = gpd.GeoDataFrame(geometry=[Point(lon, lat)], crs="EPSG:4326")

        # pyogrio es el motor de lectura: hasta 10 veces mas rapido que fiona.
        gdf_capa = gpd.read_file(self.gdb_path, layer=nombre_capa, engine="pyogrio")

        punto_proyectado = punto_gps.to_crs(gdf_capa.crs)
        geometria_punto = punto_proyectado.geometry.iloc[0]

        intersectan = gdf_capa[gdf_capa.intersects(geometria_punto)]
        if intersectan.empty:
            return []

        sin_geometria = intersectan.drop(columns="geometry", errors="ignore")
        return [fila.to_dict() for _, fila in sin_geometria.iterrows()]

    @staticmethod
    def _extraer_etiqueta(atributos: dict, claves_probables) -> str:
        """
        Saca de un registro el campo que sirve como nombre legible.

        Antes el campo estaba escrito literalmente (`"NOMBRE_ENT"`,
        `"NOM_SUBCUE"`), de modo que con una Geodatabase de otra corporacion
        el resultado quedaba en blanco. Ahora se busca por palabras clave y,
        si nada coincide, se toma el primer campo de texto no vacio.

        Args:
            atributos: Diccionario de atributos del objeto.
            claves_probables: Palabras clave a buscar en los nombres de campo.

        Returns:
            El valor encontrado, o None si el registro no tiene ningun texto.
        """
        if not atributos:
            return None

        indice = {normalizar(k): k for k in atributos}

        for clave in claves_probables:
            clave_norm = normalizar(clave)
            for campo_norm, campo_real in indice.items():
                if clave_norm in campo_norm:
                    valor = atributos.get(campo_real)
                    if valor is not None and str(valor).strip():
                        return str(valor).strip()

        for valor in atributos.values():
            if isinstance(valor, str) and valor.strip():
                return valor.strip()
        return None

    # --------------------------------------------------------------------------
    # CAPA MUNICIPAL GENERADA POR EL AREA DE ESTUDIO
    # --------------------------------------------------------------------------

    def _municipio_desde_capa_local(self, lat: float, lon: float):
        """
        Busca el municipio en `municipios_filtrados.geojson`, si existe.

        Esa capa la genera la pestana "Area de Estudio" y esta siempre en
        EPSG:4326, asi que la consulta es directa y muy rapida. A diferencia de
        la version anterior, aqui SOLO se extrae el municipio (y el
        departamento, si la capa lo trae). Nada mas se deduce ni se inventa.

        Args:
            lat: Latitud en grados decimales.
            lon: Longitud en grados decimales.

        Returns:
            Diccionario `{"municipio": str|None, "departamento": str|None}`,
            o None si la capa no existe o el punto cae fuera de ella.
        """
        ruta = settings.ruta_municipios_salida()
        if not os.path.exists(ruta):
            return None

        try:
            import geopandas as gpd
            from shapely.geometry import Point

            gdf = gpd.read_file(ruta).to_crs("EPSG:4326")
            punto = Point(lon, lat)
            coincidencias = gdf[gdf.intersects(punto)]
            if coincidencias.empty:
                return None

            fila = coincidencias.iloc[0]
            columnas = list(gdf.columns)

            campo_mpio = catalogo.sugerir_columna(
                columnas, ["MPIO_CNMBR", "NOMBRE_MPIO", "municipio", "NAME_2"]
            )
            campo_dpto = catalogo.sugerir_columna(
                columnas, ["DPTO_CNMBR", "NOMBRE_DPT", "departamento", "NAME_1"]
            )

            return {
                "municipio": str(fila[campo_mpio]).strip().title()
                if campo_mpio else None,
                "departamento": str(fila[campo_dpto]).strip().title()
                if campo_dpto else None,
            }
        except Exception:
            # Una capa corrupta no debe tumbar la consulta: simplemente no
            # aporta el municipio y el resto del diagnostico sigue su curso.
            return None

    # --------------------------------------------------------------------------
    # DIAGNOSTICO INTEGRAL
    # --------------------------------------------------------------------------

    def consultar_linea_base_coordenada(self, lat: float, lon: float) -> dict:
        """
        Diagnostico completo de linea base para una coordenada GPS.

        Consulta, en este orden y SIN saltarse ninguno:
          1. Municipio, desde la capa municipal del area de estudio.
          2. Municipio / vereda / cuenca, desde la Geodatabase.
          3. Areas protegidas (RUNAP).
          4. Ecosistemas de paramo.

        SEMANTICA DE LOS RESULTADOS (importante para no repetir el error
        anterior de dar falsos "todo bien"):

            areas_protegidas = []      -> se consulto y NO hay colision.
            areas_protegidas = [...]   -> se consulto y SI hay colision.
            areas_protegidas = None    -> NO se pudo consultar. Desconocido.

            paramo = False  -> consultado, fuera de paramo.
            paramo = True   -> consultado, dentro de paramo.
            paramo = None   -> no evaluado. Desconocido.

        Args:
            lat: Latitud en grados decimales (WGS84).
            lon: Longitud en grados decimales (WGS84).

        Returns:
            Diccionario con:
                coordenadas_consulta : {"latitud", "longitud"}
                municipio / vereda / cuenca : str o None
                departamento         : str o None
                areas_protegidas     : lista, o None si no evaluado
                paramo               : bool, o None si no evaluado
                gdb_disponible       : bool
                fuentes              : de donde salio cada dato
                advertencias         : mensajes para mostrarle al usuario
        """
        diagnostico = {
            "coordenadas_consulta": {"latitud": lat, "longitud": lon},
            "municipio": None,
            "departamento": None,
            "vereda": None,
            "cuenca": None,
            "areas_protegidas": None,
            "paramo": None,
            "gdb_disponible": self.disponible,
            "fuentes": {},
            "advertencias": [],
        }

        # --- 1. Municipio desde la capa del area de estudio ---------------------
        local = self._municipio_desde_capa_local(lat, lon)
        if local:
            diagnostico["municipio"] = local["municipio"]
            diagnostico["departamento"] = local["departamento"]
            diagnostico["fuentes"]["municipio"] = os.path.basename(
                settings.ruta_municipios_salida()
            )

        # --- 2. Geodatabase ----------------------------------------------------
        if not self.disponible:
            diagnostico["advertencias"].append(
                "No hay Geodatabase configurada, por lo que NO se evaluaron "
                "areas protegidas (RUNAP) ni ecosistemas de paramo. Estos "
                "resultados figuran como 'no evaluado', que no equivale a "
                "'sin riesgo'. Indique la ruta en la variable GDB_PATH del "
                "archivo .env."
            )
            return diagnostico

        # Municipio y vereda: solo se consultan si aun faltan o para completar.
        for rol, clave_destino in (("municipio", "municipio"),
                                   ("vereda", "vereda"),
                                   ("cuenca", "cuenca")):
            if diagnostico[clave_destino] is not None:
                continue

            capa = self.buscar_capa(rol)
            if not capa:
                diagnostico["advertencias"].append(
                    f"La Geodatabase no tiene una capa reconocible de "
                    f"'{rol}'; ese dato queda sin determinar."
                )
                continue

            try:
                resultados = self.consultar_interseccion_punto(lat, lon, capa)
                if resultados:
                    diagnostico[clave_destino] = self._extraer_etiqueta(
                        resultados[0], self.ROLES_CAMPO.get(rol, ["NOMBRE"])
                    )
                    diagnostico["fuentes"][clave_destino] = capa
            except Exception as exc:
                diagnostico["advertencias"].append(
                    f"Error al consultar la capa '{capa}' ({rol}): {exc}"
                )

        # --- 3. Areas protegidas (RUNAP, SINAP, distritos de manejo) -----------
        # Se consultan TODAS las capas de figuras de proteccion, no solo una:
        # quedarse con la primera produciria falsos negativos si la restriccion
        # esta registrada en otra capa.
        capas_protegidas = self.buscar_capas("areas_protegidas")
        if not capas_protegidas:
            diagnostico["advertencias"].append(
                "La Geodatabase no contiene ninguna capa de areas protegidas "
                "(RUNAP, SINAP o similares). Esa verificacion NO se realizo."
            )
        else:
            hallazgos = []
            consultadas = []
            for capa in capas_protegidas:
                try:
                    resultados = self.consultar_interseccion_punto(lat, lon, capa)
                    consultadas.append(capa)
                    for registro in resultados:
                        etiqueta = self._extraer_etiqueta(
                            registro, self.ROLES_CAMPO["areas_protegidas"]
                        ) or "Area protegida sin nombre"
                        hallazgos.append(f"{etiqueta} (capa {capa})")
                except Exception as exc:
                    diagnostico["advertencias"].append(
                        f"No se pudo consultar la capa de areas protegidas "
                        f"'{capa}': {exc}."
                    )

            # Solo se considera "evaluado" si al menos una capa respondio.
            if consultadas:
                diagnostico["areas_protegidas"] = hallazgos
                diagnostico["fuentes"]["areas_protegidas"] = consultadas

        # --- 4. Paramos --------------------------------------------------------
        capa_paramo = self.buscar_capa("paramo")
        if not capa_paramo:
            diagnostico["advertencias"].append(
                "La Geodatabase no contiene una capa de paramos. Esa "
                "verificacion NO se realizo."
            )
        else:
            try:
                resultados = self.consultar_interseccion_punto(lat, lon, capa_paramo)
                diagnostico["paramo"] = bool(resultados)
                diagnostico["fuentes"]["paramo"] = capa_paramo
            except Exception as exc:
                diagnostico["advertencias"].append(
                    f"No se pudo consultar la capa de paramos "
                    f"'{capa_paramo}': {exc}. La verificacion queda pendiente."
                )

        return diagnostico


if __name__ == "__main__":
    # Diagnostico manual: python -m database.gdb_connector
    conector = GDBConnector()
    print(f"Geodatabase: {conector.gdb_path or 'NO ENCONTRADA'}")
    print(f"Disponible : {conector.disponible}")

    if conector.disponible:
        capas = conector.listar_capas()
        print(f"Capas detectadas: {len(capas)}")
        print("\nCapas asignadas a cada papel:")
        for rol in GDBConnector.ROLES_CAPA:
            print(f"  {rol:<18} -> {conector.buscar_capa(rol)}")
