param(
    [string]$Config = ".\phase0\config.example.json",
    [string]$EnvFile = ".\phase0\.env",
    [string]$Profile = "",
    [ValidateSet("serve", "show", "pull", "bench", "run")]
    [string]$Action = "serve",
    [string]$Prompt = "Translate this sentence into natural Korean and output only the translation: The moonlight fell softly over the old town.",
    [switch]$PrintOnly
)

$ErrorActionPreference = "Stop"

. "$PSScriptRoot\common.ps1"

$workspaceDir = (Get-Location).Path
$envFilePath = Resolve-WorkspacePath -PathValue $EnvFile -ConfigDir $workspaceDir
$loadedEnv = Load-EnvFile -Path $envFilePath
if ($loadedEnv.Count -gt 0) {
    log "loaded env file: $envFilePath"
}

if ($Config -eq ".\phase0\config.example.json") {
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
if ($profileBackend -ne "npurun") {
    throw "Profile '$profileName' uses backend '$profileBackend'. run_model_profile.ps1 only supports npurun profiles."
}

$runtimeModel = Get-EnvValue -Names @("GLOSS_PHASE0_MODEL", "GLOSS_MODEL")
if ([string]::IsNullOrWhiteSpace($runtimeModel)) {
    $runtimeModel = [string]$profileJson.runtime_model
}
if ([string]::IsNullOrWhiteSpace($runtimeModel)) {
    throw "Profile '$profileName' does not define runtime_model."
}

$npurunPathValue = Get-EnvValue -Names @("GLOSS_PHASE0_NPURUN_PATH", "NPURUN_PATH")
if ([string]::IsNullOrWhiteSpace($npurunPathValue) -and $configJson.backend.npurun_path) {
    $npurunPathValue = [string]$configJson.backend.npurun_path
}
if ([string]::IsNullOrWhiteSpace($npurunPathValue)) {
    $npurunPathValue = ".tools/npurun/npurun.exe"
}
$npurunPath = Resolve-WorkspacePath -PathValue $npurunPathValue -ConfigDir $configDir

$qnnDir = Get-EnvValue -Names @("GLOSS_PHASE0_QNN_RUNTIME_DIR", "QNN_SDK_ROOT")
if ([string]::IsNullOrWhiteSpace($qnnDir) -and $configJson.backend.qnn_runtime_dir) {
    $qnnDir = Resolve-WorkspacePath -PathValue ([string]$configJson.backend.qnn_runtime_dir) -ConfigDir $configDir
} elseif (-not [string]::IsNullOrWhiteSpace($qnnDir)) {
    $qnnDir = Resolve-WorkspacePath -PathValue $qnnDir -ConfigDir $configDir
}
if (-not [string]::IsNullOrWhiteSpace($qnnDir) -and (Test-Path -LiteralPath $qnnDir)) {
    $env:PATH = "$qnnDir;$env:PATH"
    $env:QNN_SDK_ROOT = $qnnDir
}

$modelsDirValue = Get-EnvValue -Names @("GLOSS_PHASE0_MODELS_DIR", "NPURUN_MODELS_DIR")
if ([string]::IsNullOrWhiteSpace($modelsDirValue) -and $configJson.backend.models_dir) {
    $modelsDirValue = [string]$configJson.backend.models_dir
}
if ([string]::IsNullOrWhiteSpace($modelsDirValue)) {
    $modelsDirValue = ".models/npurun"
}
$modelsDir = Resolve-WorkspacePath -PathValue $modelsDirValue -ConfigDir $configDir
New-Item -ItemType Directory -Force -Path $modelsDir | Out-Null
$env:NPURUN_MODELS_DIR = $modelsDir

$arguments = @()
switch ($Action) {
    "serve" { $arguments = @("serve", "--model", $runtimeModel) }
    "show" { $arguments = @("show", $runtimeModel) }
    "pull" { $arguments = @("pull", $runtimeModel) }
    "bench" { $arguments = @("bench", $runtimeModel) }
    "run" { $arguments = @("run", $runtimeModel, $Prompt) }
}

log "profile: $profileName"
log "model:   $runtimeModel"
log "backend: $profileBackend"
log "status:  $($profileJson.status)"
log "npurun:  $npurunPath"
log "models:  $modelsDir"
if (-not [string]::IsNullOrWhiteSpace($qnnDir)) {
    log "qnn:     $qnnDir"
}
log "action:  $Action"

if ($PrintOnly) {
    log "command: $npurunPath $($arguments -join ' ')"
    exit 0
}

& $npurunPath @arguments
exit $LASTEXITCODE
