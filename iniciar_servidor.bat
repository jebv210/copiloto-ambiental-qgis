@echo off
REM ==============================================================================
REM  Arranca el servidor API (FastAPI) usando el entorno de QGIS.
REM ==============================================================================
REM  El host y el puerto NO se escriben aqui: los lee run_server.py de
REM  config/settings.py (o del archivo .env). Antes estaban repetidos en el
REM  mensaje de pantalla, en la linea de uvicorn y en app.py.
REM ==============================================================================
title Servidor API Conesa - Agente IA + QGIS

cd /d "%~dp0"

echo ==============================================================================
echo   SERVIDOR DE API - EVALUACION DE IMPACTO AMBIENTAL (CONESA + QGIS)
echo ==============================================================================
echo.
echo Buscando el interprete de Python de QGIS...

call "%~dp0buscar_qgis.bat"

if not defined PY (
    echo.
    echo ERROR: No se encontro ningun interprete de Python utilizable.
    echo.
    echo   - Instale QGIS Desktop, o
    echo   - defina la variable de entorno QGIS_PYTHON apuntando al archivo
    echo     python-qgis-ltr.bat de su instalacion.
    echo.
    pause
    exit /b 1
)

echo Interprete detectado: %PY%
echo.

cmd.exe /c ""%PY%" "%~dp0run_server.py""

echo.
echo El servidor se detuvo.
pause
