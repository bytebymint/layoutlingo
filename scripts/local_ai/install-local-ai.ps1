param(
    [string]$Root = 'D:\DocIntel-LocalAI'
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

Write-Host "Verified llama.cpp $Release and $ModelName under $Root."
