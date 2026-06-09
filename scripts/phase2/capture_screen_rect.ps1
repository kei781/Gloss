param(
  [Parameter(Mandatory=$true)][int]$X,
  [Parameter(Mandatory=$true)][int]$Y,
  [Parameter(Mandatory=$true)][int]$Width,
  [Parameter(Mandatory=$true)][int]$Height,
  [Parameter(Mandatory=$true)][string]$Output
)

function log {
  param(
    [Parameter(Mandatory=$true)][string]$Message,
    [string]$Level = "INFO"
  )
  [Console]::Error.WriteLine("[$($Level.ToUpperInvariant())] $Message")
}

try {
  if ($Width -le 0 -or $Height -le 0) {
    throw "Width and Height must be greater than zero."
  }

  Add-Type -AssemblyName System.Windows.Forms
  Add-Type -AssemblyName System.Drawing

  $resolvedOutput = [System.IO.Path]::GetFullPath($Output)
  $parent = [System.IO.Path]::GetDirectoryName($resolvedOutput)
  if ($parent) {
    New-Item -ItemType Directory -Force -Path $parent | Out-Null
  }

  $bitmap = New-Object System.Drawing.Bitmap $Width, $Height
  $graphics = [System.Drawing.Graphics]::FromImage($bitmap)
  $graphics.CopyFromScreen($X, $Y, 0, 0, $bitmap.Size)
  $bitmap.Save($resolvedOutput, [System.Drawing.Imaging.ImageFormat]::Png)

  $graphics.Dispose()
  $bitmap.Dispose()

  [pscustomobject]@{
    backend = "gdi-copy-from-screen"
    output = $resolvedOutput
    x = $X
    y = $Y
    width = $Width
    height = $Height
  } | ConvertTo-Json -Compress
} catch {
  log -Level "ERROR" -Message "Screen capture failed: $($_.Exception.Message)"
  exit 1
}
