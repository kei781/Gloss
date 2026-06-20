param(
    [string]$Config = ".\phase0\config.example.json",
    [string]$EnvFile = ".\phase0\.env",
    [string]$Profile = "",
    [ValidateSet("serve", "show", "pull")]
    [string]$Action = "serve",
    [switch]$PrintOnly
)

# Intel Core Ultra 358H (Intel AI Boost NPU) 용 OpenVINO Model Server(OVMS) 드라이버.
# OVMS는 device=NPU로 OpenAI 호환 엔드포인트(/v3/chat/completions)를 제공한다.
# Snapdragon/Hexagon(npurun) 시절 드라이버는 run_model_profile.npurun.ps1(DEPRECATED) 참조.
# 단발 추론/벤치 측정은 scripts/phase0/measure_openai_backend.py로 수행한다.

$ErrorActionPreference = "Stop"

. "$PSScriptRoot\common.ps1"

$workspaceDir = (Get-Location).Path
$envFilePath = Resolve-WorkspacePath -PathValue $EnvFile -ConfigDir $workspaceDir
$loadedEnv = Load-EnvFile -Path $envFilePath
if ($loadedEnv.Count -gt 0) {
    log "loaded env file: $envFilePath"
}

if (-not $PSBoundParameters.ContainsKey("Config")) {
    $configFromEnv = Get-EnvValue -Names @("GLOSS_PHASE0_CONFIG")
    if (-not [string]::IsNullOrWhiteSpace($configFromEnv)) {
        $Config = $configFromEnv
    }
}

$configPath = (Resolve-Path -LiteralPath $Config).Path
$configDir = Split-Path -Parent $configPath
$configJson = Get-Content -Raw -Encoding UTF8 $configPath | ConvertFrom-Json

$profilesPathValue = Get-EnvValue -Names @("GLOSS_PHASE0_MODEL_PROFILES_PATH")
if ([string]::IsNullOrWhiteSpace($profilesPathValue) -and $configJson.model_profiles_path) {
    $profilesPathValue = [string]$configJson.model_profiles_path
}
if ([string]::IsNullOrWhiteSpace($profilesPathValue)) {
    $profilesPathValue = "phase0/model-profiles.json"
}
$profilesPath = Resolve-WorkspacePath -PathValue $profilesPathValue -ConfigDir $configDir
$profilesJson = Get-Content -Raw -Encoding UTF8 $profilesPath | ConvertFrom-Json

$profileName = $Profile
if ([string]::IsNullOrWhiteSpace($profileName)) {
    $profileName = Get-EnvValue -Names @("GLOSS_PHASE0_ACTIVE_MODEL_PROFILE", "GLOSS_ACTIVE_MODEL_PROFILE")
}
if ([string]::IsNullOrWhiteSpace($profileName)) {
    $profileName = [string]$configJson.active_model_profile
}
if ([string]::IsNullOrWhiteSpace($profileName)) {
    $profileName = [string]$profilesJson.default_profile
}

$profileProperty = $profilesJson.profiles.PSObject.Properties[$profileName]
if ($null -eq $profileProperty) {
    $available = ($profilesJson.profiles.PSObject.Properties.Name | Sort-Object) -join ", "
    throw "Unknown model profile '$profileName'. Available profiles: $available"
}

$profileJson = $profileProperty.Value
$profileBackend = [string]$profileJson.backend
if ($profileBackend -ne "ovms") {
    if ($profileBackend -eq "npurun") {
        throw "Profile '$profileName' uses deprecated backend 'npurun' (Snapdragon/Hexagon). Intel NPU에서는 ovms profile을 쓰세요. Hexagon 재현이 필요하면 run_model_profile.npurun.ps1을 사용하세요."
    }
    throw "Profile '$profileName' uses backend '$profileBackend'. run_model_profile.ps1 only supports ovms (OpenVINO Model Server, Intel NPU) profiles."
}

$runtimeModel = Get-EnvValue -Names @("GLOSS_PHASE0_MODEL", "GLOSS_MODEL")
if ([string]::IsNullOrWhiteSpace($runtimeModel)) {
    $runtimeModel = [string]$profileJson.runtime_model
}
if ([string]::IsNullOrWhiteSpace($runtimeModel)) {
    throw "Profile '$profileName' does not define runtime_model."
}

$ovmsPathValue = Get-EnvValue -Names @("GLOSS_PHASE0_OVMS_PATH", "OVMS_PATH")
if ([string]::IsNullOrWhiteSpace($ovmsPathValue) -and $configJson.backend.ovms_path) {
    $ovmsPathValue = [string]$configJson.backend.ovms_path
}
if ([string]::IsNullOrWhiteSpace($ovmsPathValue)) {
    $ovmsPathValue = ".tools/ovms/ovms.exe"
}
$ovmsPath = Resolve-WorkspacePath -PathValue $ovmsPathValue -ConfigDir $configDir

$targetDevice = Get-EnvValue -Names @("GLOSS_PHASE0_TARGET_DEVICE")
if ([string]::IsNullOrWhiteSpace($targetDevice) -and $profileJson.target_device) {
    $targetDevice = [string]$profileJson.target_device
}
if ([string]::IsNullOrWhiteSpace($targetDevice) -and $configJson.backend.target_device) {
    $targetDevice = [string]$configJson.backend.target_device
}
if ([string]::IsNullOrWhiteSpace($targetDevice)) {
    $targetDevice = "NPU"
}

$modelsDirValue = Get-EnvValue -Names @("GLOSS_PHASE0_MODELS_DIR", "OVMS_MODELS_DIR")
if ([string]::IsNullOrWhiteSpace($modelsDirValue) -and $configJson.backend.models_dir) {
    $modelsDirValue = [string]$configJson.backend.models_dir
}
if ([string]::IsNullOrWhiteSpace($modelsDirValue)) {
    $modelsDirValue = ".models/ovms"
}
$modelsDir = Resolve-WorkspacePath -PathValue $modelsDirValue -ConfigDir $configDir
New-Item -ItemType Directory -Force -Path $modelsDir | Out-Null

# REST port는 profile/config의 base_url에서 추출한다. 기본 8000.
$baseUrl = Get-EnvValue -Names @("GLOSS_PHASE0_BASE_URL", "GLOSS_OPENAI_BASE_URL")
if ([string]::IsNullOrWhiteSpace($baseUrl) -and $profileJson.serve.base_url) {
    $baseUrl = [string]$profileJson.serve.base_url
}
if ([string]::IsNullOrWhiteSpace($baseUrl) -and $configJson.backend.base_url) {
    $baseUrl = [string]$configJson.backend.base_url
}
$restPort = "8000"
if (-not [string]::IsNullOrWhiteSpace($baseUrl)) {
    try {
        $parsedPort = ([System.Uri]$baseUrl).Port
        if ($parsedPort -gt 0) { $restPort = [string]$parsedPort }
    } catch {
        log "base_url에서 port 파싱 실패, 기본 8000 사용: $baseUrl" -level "WARN"
    }
}

$arguments = @()
switch ($Action) {
    "serve" {
        $arguments = @(
            "--rest_port", $restPort,
            "--model_repository_path", $modelsDir,
            "--model_name", $runtimeModel,
            "--target_device", $targetDevice,
            "--task", "text_generation"
        )
    }
    "pull" {
        # OVMS HuggingFace pull/export: source_model은 artifact.source의 HF id이거나 runtime_model.
        $arguments = @(
            "--pull",
            "--source_model", $runtimeModel,
            "--model_repository_path", $modelsDir,
            "--target_device", $targetDevice,
            "--task", "text_generation"
        )
    }
    "show" {
        $arguments = @("--version")
    }
}

log "profile:  $profileName"
log "model:    $runtimeModel"
log "backend:  $profileBackend"
log "status:   $($profileJson.status)"
log "ovms:     $ovmsPath"
log "device:   $targetDevice"
log "models:   $modelsDir"
log "rest_port:$restPort"
log "action:   $Action"

if ($PrintOnly) {
    log "command: $ovmsPath $($arguments -join ' ')"
    exit 0
}

& $ovmsPath @arguments
exit $LASTEXITCODE
