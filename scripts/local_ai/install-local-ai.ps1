param(
    [string]$Root = 'C:\LayoutLingo-LocalAI',
    [switch]$SkipFastTranslator
)

$ErrorActionPreference = 'Stop'
$Release = 'b10058'
$RuntimeArchive = "llama-$Release-bin-win-vulkan-x64.zip"
$RuntimeUrl = "https://github.com/ggml-org/llama.cpp/releases/download/$Release/$RuntimeArchive"
$RuntimeSha256 = '9699fc9cc0f2409948d1e725509813917695ebe26f148fb3cde756bf33c22fa3'
$ModelName = 'aya-expanse-8b-Q4_K_M.gguf'
$ModelUrl = "https://huggingface.co/bartowski/aya-expanse-8b-GGUF/resolve/main/$ModelName?download=true"
$ModelSha256 = '9592bad943fe56cf93200286a0a4b00a158cd84a408f227b9978ec5879002fb8'

foreach ($Directory in @(
    $Root,
    "$Root\bin",
    "$Root\models",
    "$Root\cache\huggingface\hub",
    "$Root\cache\transformers",
    "$Root\cache\cuda",
    "$Root\temp",
    "$Root\logs",
    "$Root\packages",
    "$Root\config"
)) {
    New-Item -ItemType Directory -Force -Path $Directory | Out-Null
}

function Get-VerifiedFile([string]$Url, [string]$Destination, [string]$Sha256) {
    if (-not (Test-Path -LiteralPath $Destination) -or
        (Get-FileHash -LiteralPath $Destination -Algorithm SHA256).Hash.ToLowerInvariant() -ne $Sha256) {
        & curl.exe -L --fail --retry 5 --retry-delay 3 -C - -o $Destination $Url
    }
    $Actual = (Get-FileHash -LiteralPath $Destination -Algorithm SHA256).Hash.ToLowerInvariant()
    if ($Actual -ne $Sha256) {
        throw "Checksum mismatch for $Destination. Expected $Sha256, got $Actual."
    }
}

$ArchivePath = "$Root\packages\$RuntimeArchive"
Get-VerifiedFile $RuntimeUrl $ArchivePath $RuntimeSha256
if (-not (Test-Path -LiteralPath "$Root\bin\llama.cpp\llama-server.exe")) {
    New-Item -ItemType Directory -Force -Path "$Root\bin\llama.cpp" | Out-Null
    Expand-Archive -LiteralPath $ArchivePath -DestinationPath "$Root\bin\llama.cpp" -Force
}
Get-VerifiedFile $ModelUrl "$Root\models\$ModelName" $ModelSha256

$Environment = @{
    LOCAL_LLM_ROOT = $Root
    LOCAL_LLM_ENDPOINT = 'http://127.0.0.1:8080/v1/chat/completions'
    LOCAL_LLM_MODEL = 'aya-expanse-8b-local'
    LOCAL_LLM_API_KEY = 'local-private-key'
    HF_HOME = "$Root\cache\huggingface"
    HUGGINGFACE_HUB_CACHE = "$Root\cache\huggingface\hub"
    TRANSFORMERS_CACHE = "$Root\cache\transformers"
    CUDA_CACHE_PATH = "$Root\cache\cuda"
}
foreach ($Item in $Environment.GetEnumerator()) {
    [Environment]::SetEnvironmentVariable($Item.Key, $Item.Value, 'User')
}

$StartScript = @'
$ErrorActionPreference = 'Stop'
$Root = Split-Path -Parent $PSCommandPath
$Server = Join-Path $Root 'bin\llama.cpp\llama-server.exe'
$Model = Join-Path $Root 'models\aya-expanse-8b-Q4_K_M.gguf'
$PidFile = Join-Path $Root 'config\llama-server.pid'
if (-not (Test-Path -LiteralPath $Server) -or -not (Test-Path -LiteralPath $Model)) {
    throw 'Local AI is incomplete. Run install-local-ai.ps1 first.'
}
if (Test-Path -LiteralPath $PidFile) {
    $Existing = Get-Content -LiteralPath $PidFile -ErrorAction SilentlyContinue
    if ($Existing -and (Get-Process -Id $Existing -ErrorAction SilentlyContinue)) { exit 0 }
}
$process = Start-Process -FilePath $Server -ArgumentList @('-m', $Model, '--host', '127.0.0.1', '--port', '8080', '-c', '8192', '-ngl', '0') -PassThru -WindowStyle Hidden
$process.Id | Set-Content -LiteralPath $PidFile -Encoding ascii
'@
$StopScript = @'
$ErrorActionPreference = 'Stop'
$Root = Split-Path -Parent $PSCommandPath
$PidFile = Join-Path $Root 'config\llama-server.pid'
if (Test-Path -LiteralPath $PidFile) {
    $ProcessId = Get-Content -LiteralPath $PidFile -ErrorAction SilentlyContinue
    if ($ProcessId) { Stop-Process -Id $ProcessId -Force -ErrorAction SilentlyContinue }
    Remove-Item -LiteralPath $PidFile -Force -ErrorAction SilentlyContinue
}
'@
$VerifyScript = @'
$ErrorActionPreference = 'Stop'
try {
    $Health = Invoke-RestMethod -Uri 'http://127.0.0.1:8080/health' -TimeoutSec 5
    Write-Host 'Aya quality reviewer is ready.'
} catch { throw 'Aya quality reviewer is not running. Use start-local-ai.ps1.' }
'@
Set-Content -LiteralPath "$Root\start-local-ai.ps1" -Value $StartScript -Encoding utf8
Set-Content -LiteralPath "$Root\stop-local-ai.ps1" -Value $StopScript -Encoding utf8
Set-Content -LiteralPath "$Root\verify-local-ai.ps1" -Value $VerifyScript -Encoding utf8

if (-not $SkipFastTranslator) {
    $ProjectRoot = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
    $Python = Join-Path $ProjectRoot '.venv\Scripts\python.exe'
    if (-not (Test-Path -LiteralPath $Python)) { $Python = 'python' }
    & $Python (Join-Path $PSScriptRoot 'install_fast_model.py') --root $Root
    if ($LASTEXITCODE -ne 0) { throw 'NLLB fast translator installation failed.' }
}

Write-Host "Local AI runtime is ready under $Root. Runtime files and model caches stay in the selected folder."
