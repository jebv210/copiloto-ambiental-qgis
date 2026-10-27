# Imagen del backend y de la interfaz del Copiloto Ambiental.
# ==============================================================================
#
# QUE ENTRA Y QUE NO
# ------------------
# Entra lo que calcula: FastAPI, el motor de Conesa, el filtrado por bloques,
# el corpus y la busqueda. Entran tambien GeoPandas y pyogrio, que son los que
# intersectan coordenadas contra las capas.
#
# NO entra QGIS Desktop. `cargar_capas_qgis.py` es un script que se ejecuta
# DENTRO del QGIS de la persona, en su equipo; meter el escritorio entero en la
# imagen la multiplicaria por diez para algo que el contenedor no usa.
#
# NO entra el archivo de 99,5 GB de GBIF. En despliegue se monta un volumen o
# se trabaja con el area ya filtrada, que son unos pocos megas.

FROM python:3.12-slim

# Bibliotecas de sistema que GeoPandas y pyogrio necesitan para leer GeoJSON y
# geodatabases. Sin ellas la instalacion compila pero falla al abrir una capa.
RUN apt-get update && apt-get install -y --no-install-recommends \
        gdal-bin \
        libgdal-dev \
        libgeos-dev \
        libproj-dev \
        curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Las dependencias van antes que el codigo: asi un cambio en un .py no obliga a
# reinstalar todo el arbol en cada construccion.
COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir -r requirements.txt

COPY . .

# El codigo no debe escribir en la imagen: lo que persiste va en volumenes.
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

EXPOSE 8000 8501

# El servicio concreto lo elige docker-compose; por si alguien arranca la
# imagen suelta, el valor por defecto es la API.
CMD ["python", "-m", "uvicorn", "api.server:app", "--host", "0.0.0.0", "--port", "8000"]
