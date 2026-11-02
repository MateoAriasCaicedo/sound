param(
    [switch]$Installer
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

$version = (Select-String -Path "app\__init__.py" -Pattern '__version__ = "([^"]+)"').Matches[0].Groups[1].Value
Write-Host "Sound $version"

python -m pip install --upgrade pip
if (Test-Path "constraints.txt") {
    python -m pip install -e ".[dev]" -c constraints.txt
} else {
    python -m pip install -e ".[dev]"
}
python packaging\make_icon.py

# Node.js: YouTube lo exige para resolver los enlaces. Se incluye para que el usuario no lo
# tenga que instalar. Si la descarga falla, se compila igual y la app avisará al arrancar.
$nodeDir = Join-Path $root "packaging\node"
$nodeExe = Join-Path $nodeDir "node.exe"
if (-not (Test-Path $nodeExe)) {
    New-Item -ItemType Directory -Force -Path $nodeDir | Out-Null
    try {
        Write-Host "Descargando Node.js..."
        Invoke-WebRequest -Uri "https://nodejs.org/dist/latest-v22.x/win-x64/node.exe" `
            -OutFile $nodeExe -UseBasicParsing
    } catch {
        Write-Warning "No se pudo descargar Node.js: $($_.Exception.Message)"
    }
}

python -m PyInstaller --noconfirm --clean sound.spec

if ($Installer) {
    $iscc = Get-Command iscc -ErrorAction SilentlyContinue
    if (-not $iscc) {
        $candidates = @(
            "$env:ProgramFiles\Inno Setup 6\ISCC.exe",
            "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe"
        )
        $iscc = $candidates | Where-Object { Test-Path $_ } | Select-Object -First 1
    }
    if (-not $iscc) {
        throw "Inno Setup 6 no está instalado. Instálalo o compila solo la carpeta dist\Sound."
    }
    & $iscc "/DMyAppVersion=$version" "installer.iss"
    Write-Host "Instalador en dist\Sound-$version-setup.exe"
}
