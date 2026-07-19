param(
    [int]$Port = 5000
)

$ErrorActionPreference = 'Stop'
$ProjectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$Python = Join-Path $ProjectRoot '.venv\Scripts\python.exe'

if (-not (Test-Path -LiteralPath $Python)) {
    throw 'The project virtual environment is missing. Create it and install requirements first.'
}

$env:PORT = $Port
& $Python (Join-Path $ProjectRoot 'run.py')
