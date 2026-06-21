param(
  [Parameter(Mandatory=$true)][int]$X,
  [Parameter(Mandatory=$true)][int]$Y,
  [Parameter(Mandatory=$true)][int]$Width,
  [Parameter(Mandatory=$true)][int]$Height,
  [Parameter(Mandatory=$true)][string]$Output
)

[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$OutputEncoding = [System.Text.Encoding]::UTF8

function log {
  param(
    [Parameter(Mandatory=$true)][string]$Message,
    [string]$Level = "INFO"
  )
  [Console]::Error.WriteLine("[$($Level.ToUpperInvariant())] $Message")
}

function Enable-PerMonitorDpiAwareness {
  try {
    Add-Type -TypeDefinition @"
using System;
using System.Runtime.InteropServices;

public static class GlossDpi {
  [DllImport("user32.dll", SetLastError = true)]
  public static extern bool SetProcessDpiAwarenessContext(IntPtr dpiContext);

  [DllImport("user32.dll", SetLastError = true)]
  public static extern bool SetProcessDPIAware();
}
"@
    $perMonitorAwareV2 = [IntPtr]::new(-4)
    $enabled = [GlossDpi]::SetProcessDpiAwarenessContext($perMonitorAwareV2)
    if (-not $enabled) {
      [GlossDpi]::SetProcessDPIAware() | Out-Null
    }
  } catch {
    try {
      [GlossDpi]::SetProcessDPIAware() | Out-Null
    } catch {
      log -Level "WARN" -Message "Unable to enable DPI awareness: $($_.Exception.Message)"
    }
  }
}

try {
  if ($Width -le 0 -or $Height -le 0) {
    throw "Width and Height must be greater than zero."
  }

  Enable-PerMonitorDpiAwareness

  Add-Type -AssemblyName System.Windows.Forms
  Add-Type -AssemblyName System.Drawing

  $resolvedOutput = [System.IO.Path]::GetFullPath($Output)
  $parent = [System.IO.Path]::GetDirectoryName($resolvedOutput)
  if ($parent) {
    New-Item -ItemType Directory -Force -Path $parent | Out-Null
  }

  $bitmap = $null
  $graphics = $null
  try {
    $bitmap = New-Object System.Drawing.Bitmap $Width, $Height
    $graphics = [System.Drawing.Graphics]::FromImage($bitmap)
    $graphics.CopyFromScreen($X, $Y, 0, 0, $bitmap.Size)
    $bitmap.Save($resolvedOutput, [System.Drawing.Imaging.ImageFormat]::Png)
  } finally {
    if ($graphics) {
      $graphics.Dispose()
    }
    if ($bitmap) {
      $bitmap.Dispose()
    }
  }

  [pscustomobject]@{
    backend = "gdi-copy-from-screen"
    output = $resolvedOutput
    dpiAwareness = "per-monitor-v2-attempted"
    x = $X
    y = $Y
    width = $Width
    height = $Height
  } | ConvertTo-Json -Compress
} catch {
  log -Level "ERROR" -Message "Screen capture failed: $($_.Exception.Message)"
  exit 1
}
