param(
    [string]$Config = ".\phase0\config.example.json",
    [string]$Profile = "",
    [ValidateSet("serve", "show", "pull", "bench", "run")]
    [string]$Action = "serve",
    [string]$Prompt = "Translate this sentence into natural Korean and output only the translation: The moonlight fell softly over the old town.",
    [switch]$PrintOnly
)

$ErrorActionPreference = "Stop"

function Resolve-WorkspacePath {
    param(
        [Parameter(Mandatory = $true)]
        [string]$PathValue,
        [Parameter(Mandatory = $true)]
        [string]$ConfigDir
    )

    if ([System.IO.Path]::IsPathRooted($PathValue)) {
        return [System.IO.Path]::GetFullPath($PathValue)
    }

    $fromCwd = Join-Path (Get-Location) $PathValue
    if (Test-Path -LiteralPath $fromCwd) {
        return (Resolve-Path -LiteralPath $fromCwd).Path
    }

    $fromConfig = Join-Path $ConfigDir $PathValue
    if (Test-Path -LiteralPath $fromConfig) {
        return (Resolve-Path -LiteralPath $fromConfig).Path
    }

    return [System.IO.Path]::GetFullPath($fromCwd)
}

$configPath = (Resolve-Path -LiteralPath $Config).Path
$configDir = Split-Path -Parent $configPath
$configJson = Get-Content -Raw -Encoding UTF8 $configPath | ConvertFrom-Json

$profilesPathValue = "phase0/model-profiles.json"
if ($configJson.model_profiles_path) {
    $profilesPathValue = [string]$configJson.model_profiles_path
}
$profilesPath = Resolve-WorkspacePath -PathValue $profilesPathValue -ConfigDir $configDir
$profilesJson = Get-Content -Raw -Encoding UTF8 $profilesPath | ConvertFrom-Json

$profileName = $Profile
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

$runtimeModel = [string]$profileJson.runtime_model
if ([string]::IsNullOrWhiteSpace($runtimeModel)) {
    throw "Profile '$profileName' does not define runtime_model."
}

$npurunPathValue = ".tools/npurun/npurun.exe"
if ($configJson.backend.npurun_path) {
    $npurunPathValue = [string]$configJson.backend.npurun_path
}
$npurunPath = Resolve-WorkspacePath -PathValue $npurunPathValue -ConfigDir $configDir

$qnnDir = ""
if ($configJson.backend.qnn_runtime_dir) {
    $qnnDir = Resolve-WorkspacePath -PathValue ([string]$configJson.backend.qnn_runtime_dir) -ConfigDir $configDir
}
if (-not [string]::IsNullOrWhiteSpace($qnnDir) -and (Test-Path -LiteralPath $qnnDir)) {
    $env:PATH = "$qnnDir;$env:PATH"
    $env:QNN_SDK_ROOT = $qnnDir
}

$modelsDirValue = ".models/npurun"
if ($configJson.backend.models_dir) {
    $modelsDirValue = [string]$configJson.backend.models_dir
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

Write-Host "profile: $profileName"
Write-Host "model:   $runtimeModel"
Write-Host "backend: $profileBackend"
Write-Host "status:  $($profileJson.status)"
Write-Host "npurun:  $npurunPath"
Write-Host "models:  $modelsDir"
if (-not [string]::IsNullOrWhiteSpace($qnnDir)) {
    Write-Host "qnn:     $qnnDir"
}
Write-Host "action:  $Action"

if ($PrintOnly) {
    Write-Host "command: $npurunPath $($arguments -join ' ')"
    exit 0
}

& $npurunPath @arguments
exit $LASTEXITCODE
