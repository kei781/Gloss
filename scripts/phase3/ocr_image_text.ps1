param(
  [string]$Image = "",
  [string]$Language = "",
  [switch]$ListLanguages
)

$helper = Join-Path $PSScriptRoot "..\..\src\gloss\visual\resources\ocr_image_text.ps1"
if (-not (Test-Path -LiteralPath $helper)) {
  [Console]::Error.WriteLine("[ERROR] Packaged OCR helper not found: $helper")
  exit 1
}

& $helper @PSBoundParameters
exit $LASTEXITCODE
