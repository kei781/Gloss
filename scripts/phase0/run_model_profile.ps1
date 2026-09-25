param(
    [string]$Config = ".\phase0\config.example.json",
    [string]$EnvFile = ".\phase0\.env",
    [string]$Profile = "",
    [ValidateSet("serve", "show", "pull")]
    [string]$Action = "serve",
    [switch]$PrintOnly
)

# Intel Core Ultra X7 358H (Intel AI Boost NPU) 용 OpenVINO Model Server(OVMS) 드라이버.
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

$pipelineType = [string]$profileJson.serve.pipeline_type
if ([string]::IsNullOrWhiteSpace($pipelineType)) {
    $pipelineType = if ($profileJson.capabilities -contains "vision") { "VLM" } else { "LM" }
}
if ($pipelineType -notin @("LM", "VLM")) {
    throw "Profile '$profileName' needs an NPU-compatible pipeline_type: LM or VLM."
}

$runtimeModel = Get-EnvValue -Names @("GLOSS_PHASE0_MODEL", "GLOSS_MODEL")
if ([string]::IsNullOrWhiteSpace($runtimeModel)) {
    $runtimeModel = [string]$profileJson.runtime_model
}
if ([string]::IsNullOrWhiteSpace($runtimeModel)) {
    throw "Profile '$profileName' does not define runtime_model."
}

$sourceModel = ""
if ($profileJson.artifact -and $profileJson.artifact.hf_id) {
    $sourceModel = [string]$profileJson.artifact.hf_id
}
if (
    [string]::IsNullOrWhiteSpace($sourceModel) -and
    $profileJson.artifact -and
    $profileJson.artifact.source -and
    ([string]$profileJson.artifact.source) -match "^[^/\s]+/[^/\s]+$"
) {
    $sourceModel = [string]$profileJson.artifact.source
}
if ([string]::IsNullOrWhiteSpace($sourceModel) -and $runtimeModel -match "^[^/\s]+/[^/\s]+$") {
    $sourceModel = $runtimeModel
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

$cacheDirValue = Get-EnvValue -Names @("GLOSS_PHASE0_CACHE_DIR")
if ([string]::IsNullOrWhiteSpace($cacheDirValue)) {
    $cacheDirValue = Join-Path (Split-Path -Parent $modelsDir) "ovms-cache"
}
$cacheDir = Resolve-WorkspacePath -PathValue $cacheDirValue -ConfigDir $configDir
New-Item -ItemType Directory -Force -Path $cacheDir | Out-Null

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
        # OVMS pull stores the generated graph under <repository>/<source_model>.
        # Serving that exact path also avoids an implicit second HF download.
        $modelDirectory = if ($sourceModel) { $sourceModel } else { $runtimeModel }
        $arguments = @(
            "--rest_port", $restPort,
            "--model_path", (Join-Path $modelsDir ($modelDirectory -replace '/', '\')),
            "--cache_dir", $cacheDir,
            "--model_name", $runtimeModel,
            "--target_device", $targetDevice,
            "--pipeline_type", $pipelineType,
            "--task", "text_generation"
        )
    }
    "pull" {
        if ([string]::IsNullOrWhiteSpace($sourceModel)) {
            throw "Profile '$profileName' does not define an HF repo id for OVMS pull. Add artifact.hf_id (for example, Qwen/Qwen3-4B-Instruct-2507) or pass a profile whose runtime_model is already an owner/repo id."
        }
        $arguments = @(
            "--pull",
            "--source_model", $sourceModel,
            "--model_repository_path", $modelsDir,
            "--cache_dir", $cacheDir,
            "--model_name", $runtimeModel,
            "--target_device", $targetDevice,
            "--pipeline_type", $pipelineType,
            "--task", "text_generation"
        )
        if (-not ($profileJson.artifact -and $profileJson.artifact.preconverted)) {
            $arguments += @("--weight-format", "int4")
            if ($profileJson.artifact.pull_extra_quantization_params) {
                $arguments += @("--extra_quantization_params", [string]$profileJson.artifact.pull_extra_quantization_params)
            }
        }
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
if ($Action -eq "pull") {
    log "source:   $sourceModel"
    if (-not ($profileJson.artifact -and $profileJson.artifact.preconverted)) {
        log "pull requires an OVMS build with Python/Optimum export support for raw Hugging Face models" -level "WARN"
    }
}

if ($Action -in @("serve", "pull") -and $profileJson.serve.max_prompt_len) {
    $arguments += @("--max_prompt_len", [string]$profileJson.serve.max_prompt_len)
}
log "device:   $targetDevice"
log "pipeline: $pipelineType"
log "models:   $modelsDir"
log "cache:    $cacheDir"
log "rest_port:$restPort"
log "action:   $Action"

if ($PrintOnly) {
    $displayArguments = foreach ($argument in $arguments) {
        $value = [string]$argument
        if ($value -match '\s') {
            "'" + ($value -replace "'", "''") + "'"
        } else {
            $value
        }
    }
    log "command: $ovmsPath $($displayArguments -join ' ')"
    exit 0
}

& $ovmsPath @arguments
exit $LASTEXITCODE
