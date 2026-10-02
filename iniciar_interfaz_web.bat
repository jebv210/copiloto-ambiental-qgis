@echo off
REM ==============================================================================
REM  Arranca el dashboard web (Streamlit) usando el entorno de QGIS.
REM ==============================================================================
REM  El puerto se puede cambiar con la variable de entorno STREAMLIT_PORT sin
REM  tocar este archivo. Por defecto Streamlit usa el 8501.
REM ==============================================================================
title Copiloto Ambiental - Dashboard Web

cd /d "%~dp0"

if not defined STREAMLIT_PORT set "STREAMLIT_PORT=8501"

echo ==============================================================================
echo   INTERFAZ WEB - AGENTE DE IMPACTO AMBIENTAL (STREAMLIT + QGIS)
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
echo La aplicacion se abrira en: http://localhost:%STREAMLIT_PORT%
echo Presiona Ctrl+C en esta ventana para cerrarla.
echo.

cmd.exe /c ""%PY%" -m streamlit run "%~dp0app.py" --server.port %STREAMLIT_PORT% --browser.gatherUsageStats false"

echo.
echo La interfaz se detuvo.
pause
