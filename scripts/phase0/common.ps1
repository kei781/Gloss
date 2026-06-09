function log {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Message,
        [ValidateSet("INFO", "WARN", "ERROR", "DEBUG")]
        [string]$Level = "INFO"
    )

    $record = [ordered]@{
        ts = (Get-Date).ToString("o")
        level = $Level
        message = $Message
    }

    if ($env:GLOSS_LOG_FORMAT -eq "json") {
        Write-Host ($record | ConvertTo-Json -Compress)
    } else {
        Write-Host "[$Level] $Message"
    }
}

function Load-EnvFile {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Path
    )

    $loaded = [ordered]@{}
    if (-not (Test-Path -LiteralPath $Path)) {
        return $loaded
    }

    foreach ($rawLine in Get-Content -Encoding UTF8 -LiteralPath $Path) {
        $line = $rawLine.Trim()
        if ([string]::IsNullOrWhiteSpace($line) -or $line.StartsWith("#")) {
            continue
        }
        if ($line.StartsWith("export ")) {
            $line = $line.Substring(7).Trim()
        }
        $equalsIndex = $line.IndexOf("=")
        if ($equalsIndex -lt 1) {
            continue
        }

        $key = $line.Substring(0, $equalsIndex).Trim()
        $value = $line.Substring($equalsIndex + 1).Trim()
        if ($value.Length -ge 2 -and (($value.StartsWith('"') -and $value.EndsWith('"')) -or ($value.StartsWith("'") -and $value.EndsWith("'")))) {
            $value = $value.Substring(1, $value.Length - 2)
        }

        if ([string]::IsNullOrWhiteSpace([Environment]::GetEnvironmentVariable($key, "Process"))) {
            [Environment]::SetEnvironmentVariable($key, $value, "Process")
        }
        $loaded[$key] = $value
    }

    return $loaded
}

function Get-EnvValue {
    param(
        [Parameter(Mandatory = $true)]
        [string[]]$Names,
        [string]$Default = ""
    )

    foreach ($name in $Names) {
        $value = [Environment]::GetEnvironmentVariable($name, "Process")
        if (-not [string]::IsNullOrWhiteSpace($value)) {
            return $value
        }
    }

    return $Default
}

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
