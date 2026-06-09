param(
    [string]$OutputDir = ".\phase0\runs\latest",
    [int]$SampleSeconds = 5
)

$ErrorActionPreference = "Stop"

New-Item -ItemType Directory -Force -Path $OutputDir | Out-Null

function ConvertTo-PlainObject {
    param([object]$InputObject)

    $InputObject | Select-Object *
}

function Try-Collect {
    param(
        [scriptblock]$Collector
    )

    try {
        & $Collector
    } catch {
        [ordered]@{
            collectionError = $_.Exception.Message
        }
    }
}

$environment = [ordered]@{
    collectedAt = (Get-Date).ToString("o")
    computerName = $env:COMPUTERNAME
    userName = $env:USERNAME
    os = Try-Collect { ConvertTo-PlainObject (Get-CimInstance Win32_OperatingSystem) }
    processor = Try-Collect { @(Get-CimInstance Win32_Processor | ForEach-Object { ConvertTo-PlainObject $_ }) }
    memory = Try-Collect { @(Get-CimInstance Win32_PhysicalMemory | ForEach-Object { ConvertTo-PlainObject $_ }) }
    videoControllers = Try-Collect { @(Get-CimInstance Win32_VideoController | ForEach-Object { ConvertTo-PlainObject $_ }) }
    qualcommDevices = Try-Collect {
        @(Get-CimInstance Win32_PnPEntity | Where-Object {
            ($_.Name -match "Qualcomm|Hexagon|NPU|Neural|AI|HTP") -or
            ($_.Manufacturer -match "Qualcomm")
        } | ForEach-Object { ConvertTo-PlainObject $_ })
    }
}

$environmentPath = Join-Path $OutputDir "environment.json"
$environment | ConvertTo-Json -Depth 8 | Set-Content -Encoding UTF8 -Path $environmentPath

$counterCandidates = @(Try-Collect {
    @(Get-Counter -ListSet * -ErrorAction SilentlyContinue | Where-Object {
        $_.CounterSetName -match "NPU|Neural|AI|HTP|GPU|Compute|Qualcomm"
    } | ForEach-Object {
        [ordered]@{
            counterSetName = $_.CounterSetName
            description = $_.Description
            paths = @($_.Paths)
        }
    })
})

$counterCandidatesPath = Join-Path $OutputDir "performance-counter-candidates.json"
$counterCandidates | ConvertTo-Json -Depth 8 | Set-Content -Encoding UTF8 -Path $counterCandidatesPath

$sample = [ordered]@{
    collectedAt = (Get-Date).ToString("o")
    sampleSeconds = $SampleSeconds
    samples = @()
    errors = @()
}

$pathsToSample = @()
if ($counterCandidates -is [array]) {
    foreach ($candidate in $counterCandidates) {
        foreach ($path in $candidate.paths) {
            if ($path -match "%|Utilization|Usage|Engine|Compute|NPU|Neural") {
                $pathsToSample += $path
            }
        }
    }
}

$pathsToSample = $pathsToSample | Select-Object -Unique -First 20

foreach ($path in $pathsToSample) {
    try {
        $counter = Get-Counter -Counter $path -SampleInterval 1 -MaxSamples $SampleSeconds -ErrorAction Stop
        $sample.samples += [ordered]@{
            path = $path
            values = @($counter.CounterSamples | ForEach-Object {
                [ordered]@{
                    timestamp = $_.Timestamp.ToString("o")
                    cookedValue = $_.CookedValue
                    instanceName = $_.InstanceName
                }
            })
        }
    } catch {
        $sample.errors += [ordered]@{
            path = $path
            error = $_.Exception.Message
        }
    }
}

$samplePath = Join-Path $OutputDir "counter-sample.json"
$sample | ConvertTo-Json -Depth 8 | Set-Content -Encoding UTF8 -Path $samplePath

Write-Host "Wrote $environmentPath"
Write-Host "Wrote $counterCandidatesPath"
Write-Host "Wrote $samplePath"
