# ==============================================================================
#  Configuracion del entorno del proyecto
# ==============================================================================
#  Instala las dependencias web usando el pip del entorno de QGIS y crea un
#  entorno virtual (.venv) que hereda las librerias geoespaciales pesadas
#  (geopandas, shapely, pyogrio) en lugar de recompilarlas.
#
#  Uso: clic derecho sobre este archivo -> "Ejecutar con PowerShell"
#
#  QUE SE DESQUEMO:
#    Antes este script probaba cuatro rutas literales con las versiones
#    "QGIS 3.40.4" y "QGIS 3.34.8". Ahora enumera cualquier carpeta que empiece
#    por "QGIS" en las ubicaciones habituales, sin importar la version, y
#    respeta la variable de entorno QGIS_PYTHON si el usuario la definio.
# ==============================================================================

$ErrorActionPreference = "Stop"

$projectDir      = $PSScriptRoot
$venvDir         = Join-Path $projectDir ".venv"
$requirementsPath = Join-Path $projectDir "requirements.txt"

# ------------------------------------------------------------------------------
#  Busca el lanzador python-qgis*.bat
# ------------------------------------------------------------------------------
function Find-QgisPython {
    <#
    .SYNOPSIS
        Localiza el interprete de Python de QGIS sin rutas escritas a mano.
    .DESCRIPTION
        Orden de busqueda:
          1. Variable de entorno QGIS_PYTHON.
          2. Carpetas QGIS* dentro de Archivos de programa y OSGeo4W.
          3. Busqueda recursiva como ultimo recurso.
    .OUTPUTS
        La ruta completa al .bat, o cadena vacia si no encuentra nada.
    #>

    if ($env:QGIS_PYTHON -and (Test-Path $env:QGIS_PYTHON)) {
        return $env:QGIS_PYTHON
    }

    $raices = @(
        $env:ProgramFiles,
        $env:ProgramW6432,
        "C:\Program Files",
        "C:\OSGeo4W",
        "C:\OSGeo4W64"
    ) | Where-Object { $_ -and (Test-Path $_) } | Select-Object -Unique

    foreach ($raiz in $raices) {
        # Instalaciones tipo "C:\Program Files\QGIS 3.40.4\bin\..."
        $carpetas = Get-ChildItem -Path $raiz -Directory -Filter "QGIS*" -ErrorAction SilentlyContinue
        foreach ($carpeta in $carpetas) {
            foreach ($nombre in @("python-qgis-ltr.bat", "python-qgis.bat")) {
                $candidato = Join-Path $carpeta.FullName "bin\$nombre"
                if (Test-Path $candidato) { return $candidato }
            }
        }
        # Instalaciones tipo OSGeo4W, con bin\ colgando de la raiz.
        foreach ($nombre in @("python-qgis-ltr.bat", "python-qgis.bat")) {
            $candidato = Join-Path $raiz "bin\$nombre"
            if (Test-Path $candidato) { return $candidato }
        }
    }

    # Ultimo recurso: busqueda recursiva (lenta, por eso va al final).
    foreach ($raiz in $raices) {
        $encontrado = Get-ChildItem -Path $raiz -Filter "python-qgis*.bat" -Recurse -ErrorAction SilentlyContinue |
                      Select-Object -First 1
        if ($encontrado) { return $encontrado.FullName }
    }

    return ""
}

Write-Host "=== 1. LOCALIZANDO EL ENTORNO DE QGIS ==="
$qgisPythonBat = Find-QgisPython

if ($qgisPythonBat -eq "") {
    Write-Error @"
No se encontro el interprete de Python de QGIS.

Opciones:
  - Instale QGIS Desktop desde https://qgis.org/es/site/forusers/download.html
  - O defina la variable de entorno QGIS_PYTHON apuntando al archivo
    python-qgis-ltr.bat de su instalacion y vuelva a ejecutar este script.
"@
    exit 1
}

Write-Host "Interprete detectado: $qgisPythonBat"
Write-Host "Directorio del proyecto: $projectDir"

# ------------------------------------------------------------------------------
Write-Host "`n=== 2. INSTALANDO DEPENDENCIAS ==="
Write-Host "Se instalan en el perfil del usuario (--user) para no requerir"
Write-Host "permisos de administrador sobre la carpeta de QGIS."

$pipCommand = "`"$qgisPythonBat`" -m pip install --user -r `"$requirementsPath`""
cmd.exe /c $pipCommand

# ------------------------------------------------------------------------------
Write-Host "`n=== 3. CREANDO EL ENTORNO VIRTUAL (.venv) ==="
Write-Host "Se crea con --system-site-packages para heredar geopandas, shapely"
Write-Host "y pyogrio de QGIS, y con --without-pip porque ensurepip no funciona"
Write-Host "dentro del entorno de QGIS."

if (Test-Path $venvDir) {
    Remove-Item -Path $venvDir -Recurse -Force -ErrorAction SilentlyContinue
}

$venvCommand = "`"$qgisPythonBat`" -m venv `"$venvDir`" --without-pip --system-site-packages"
cmd.exe /c $venvCommand

if (-not (Test-Path $venvDir)) {
    Write-Error "No se pudo crear el entorno virtual en $venvDir"
    exit 1
}
Write-Host "Entorno virtual creado en: $venvDir"

# ------------------------------------------------------------------------------
Write-Host "`n=== 4. VERIFICANDO LAS IMPORTACIONES ==="

$venvPython = Join-Path $venvDir "Scripts\python.exe"

$verifyScript = @"
import importlib
paquetes = [
    ('fastapi',    'servidor API'),
    ('uvicorn',    'servidor API'),
    ('streamlit',  'interfaz web'),
    ('requests',   'cliente HTTP'),
    ('dotenv',     'lectura del .env'),
    ('pandas',     'procesamiento tabular'),
    ('geopandas',  'geoprocesamiento'),
    ('shapely',    'geometrias'),
    ('pyogrio',    'lectura de Geodatabases'),
]
for modulo, para_que in paquetes:
    try:
        m = importlib.import_module(modulo)
        version = getattr(m, '__version__', 'sin version')
        print(f'  [OK]    {modulo:<12} {version:<12} ({para_que})')
    except Exception as e:
        print(f'  [FALLA] {modulo:<12} {\"\":<12} ({para_que}) -> {e}')

try:
    from google import genai
    print('  [OK]    google-genai              (agente de IA)')
except Exception as e:
    print(f'  [FALLA] google-genai              (agente de IA) -> {e}')
"@

& $venvPython -c $verifyScript

# ------------------------------------------------------------------------------
Write-Host "`n=== 5. ARCHIVO DE CONFIGURACION ==="

$envPath        = Join-Path $projectDir ".env"
$envExamplePath = Join-Path $projectDir ".env.example"

if (-not (Test-Path $envPath) -and (Test-Path $envExamplePath)) {
    Copy-Item $envExamplePath $envPath
    Write-Host "Se creo un archivo .env a partir de la plantilla."
    Write-Host "Abralo y pegue su GEMINI_API_KEY (o peguela en la barra lateral"
    Write-Host "de la interfaz web, que tambien la valida)."
} else {
    Write-Host "Ya existe un archivo .env; no se toco."
}

Write-Host "`n=== CONFIGURACION COMPLETADA ==="
Write-Host "Siguiente paso: ejecute iniciar_servidor.bat y luego iniciar_interfaz_web.bat"
