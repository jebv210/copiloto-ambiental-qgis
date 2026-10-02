@echo off
REM ==============================================================================
REM  Localiza el interprete de Python que trae QGIS Desktop.
REM ==============================================================================
REM  Este archivo lo invocan (con CALL) iniciar_servidor.bat e
REM  iniciar_interfaz_web.bat. Se separo aqui para no mantener la misma busqueda
REM  duplicada en dos archivos.
REM
REM  QUE SE DESQUEMO:
REM    Antes cada .bat comprobaba tres rutas literales:
REM        C:\Program Files\QGIS 3.40.4\bin\python-qgis-ltr.bat
REM        C:\Program Files\QGIS 3.34.8\bin\python-qgis-ltr.bat
REM        C:\Program Files\QGIS 3.34.8\bin\python-qgis.bat
REM    Con QGIS instalado en otra version o en otra unidad, no arrancaba.
REM    Ahora se recorren las carpetas que empiecen por "QGIS" dentro de las
REM    ubicaciones habituales, en cualquier version.
REM
REM  ORDEN DE PRIORIDAD:
REM    1. La variable de entorno QGIS_PYTHON, si el usuario la definio.
REM    2. Cualquier QGIS instalado en Archivos de programa u OSGeo4W.
REM    3. El Python que este en el PATH del sistema.
REM
REM  Devuelve la ruta en la variable PY.
REM ==============================================================================

set "PY="

REM --- 1. Preferencia explicita del usuario -------------------------------------
if defined QGIS_PYTHON if exist "%QGIS_PYTHON%" set "PY=%QGIS_PYTHON%"

REM --- 2. Busqueda en las ubicaciones habituales ---------------------------------
if not defined PY call :explorar "%ProgramFiles%"
if not defined PY call :explorar "%ProgramW6432%"
if not defined PY call :explorar "C:\Program Files"
if not defined PY call :explorar "C:\OSGeo4W"
if not defined PY call :explorar "C:\OSGeo4W64"

REM --- 3. Respaldo: el Python del sistema ----------------------------------------
if not defined PY (
    where python >nul 2>nul
    if not errorlevel 1 (
        set "PY=python"
        echo [AVISO] No se encontro QGIS. Se usara el Python del sistema.
        echo         Las funciones geograficas necesitan geopandas y pyogrio
        echo         instalados en ese entorno.
    )
)

goto :eof

REM ------------------------------------------------------------------------------
REM  :explorar  ^<carpeta^>
REM  Busca un lanzador python-qgis dentro de esa carpeta y de sus subcarpetas
REM  QGIS*. Deja el resultado en PY si encuentra alguno.
REM ------------------------------------------------------------------------------
:explorar
if "%~1"=="" goto :eof
if not exist "%~1" goto :eof

REM Instalaciones tipo "C:\Program Files\QGIS 3.40.4\bin\..."
for /d %%D in ("%~1\QGIS*") do (
    if not defined PY if exist "%%D\bin\python-qgis-ltr.bat" set "PY=%%D\bin\python-qgis-ltr.bat"
    if not defined PY if exist "%%D\bin\python-qgis.bat"     set "PY=%%D\bin\python-qgis.bat"
)

REM Instalaciones tipo OSGeo4W, donde bin\ cuelga directamente de la raiz.
if not defined PY if exist "%~1\bin\python-qgis-ltr.bat" set "PY=%~1\bin\python-qgis-ltr.bat"
if not defined PY if exist "%~1\bin\python-qgis.bat"     set "PY=%~1\bin\python-qgis.bat"

goto :eof
