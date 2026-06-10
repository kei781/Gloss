param(
  [string]$Image = "",
  [string]$Language = "",
  [switch]$ListLanguages
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

try {
  $null = [Windows.Media.Ocr.OcrEngine, Windows.Foundation, ContentType = WindowsRuntime]
  $null = [Windows.Globalization.Language, Windows.Globalization, ContentType = WindowsRuntime]
  $null = [Windows.Storage.StorageFile, Windows.Storage, ContentType = WindowsRuntime]
  $null = [Windows.Storage.Streams.IRandomAccessStream, Windows.Storage.Streams, ContentType = WindowsRuntime]
  $null = [Windows.Graphics.Imaging.BitmapDecoder, Windows.Graphics, ContentType = WindowsRuntime]
  $null = [Windows.Graphics.Imaging.SoftwareBitmap, Windows.Graphics, ContentType = WindowsRuntime]
  Add-Type -AssemblyName System.Runtime.WindowsRuntime

  $asTaskGeneric = ([System.WindowsRuntimeSystemExtensions].GetMethods() | Where-Object {
    $_.Name -eq 'AsTask' -and $_.GetParameters().Count -eq 1 -and
    $_.GetParameters()[0].ParameterType.Name -eq 'IAsyncOperation`1'
  })[0]

  function Await {
    param($Operation, $ResultType)
    $task = $asTaskGeneric.MakeGenericMethod($ResultType).Invoke($null, @($Operation))
    # GetResult() unwraps AggregateException so the real WinRT failure
    # (sharing violation, decoder error, ...) reaches the catch below.
    return $task.GetAwaiter().GetResult()
  }

  $availableLanguages = @([Windows.Media.Ocr.OcrEngine]::AvailableRecognizerLanguages | ForEach-Object {
    [ordered]@{
      tag = $_.LanguageTag
      displayName = $_.DisplayName
    }
  })

  if ($ListLanguages) {
    [pscustomobject]@{
      backend = "windows-media-ocr"
      availableLanguages = $availableLanguages
    } | ConvertTo-Json -Depth 5 -Compress
    exit 0
  }

  if ([string]::IsNullOrWhiteSpace($Image)) {
    throw "Provide -Image <path> or -ListLanguages."
  }

  # Resolve against the PowerShell location (Set-Location aware), not the
  # process CWD; GetFileFromPathAsync requires an absolute path.
  $resolvedImage = $ExecutionContext.SessionState.Path.GetUnresolvedProviderPathFromPSPath($Image)
  if (-not (Test-Path -LiteralPath $resolvedImage)) {
    throw "Image not found: $resolvedImage"
  }

  $engine = $null
  if (-not [string]::IsNullOrWhiteSpace($Language)) {
    $requested = New-Object Windows.Globalization.Language $Language
    if (-not [Windows.Media.Ocr.OcrEngine]::IsLanguageSupported($requested)) {
      $tags = ($availableLanguages | ForEach-Object { $_.tag }) -join ", "
      $hint = "OCR language pack '$Language' is not installed. Available: $tags."
      if ($Language -like "ja*") {
        $hint += " Install with: Add-WindowsCapability -Online -Name 'Language.OCR~~~ja-JP~0.0.1.0' (admin required)."
      }
      throw $hint
    }
    $engine = [Windows.Media.Ocr.OcrEngine]::TryCreateFromLanguage($requested)
  } else {
    $engine = [Windows.Media.Ocr.OcrEngine]::TryCreateFromUserProfileLanguages()
  }
  if ($null -eq $engine) {
    $tags = ($availableLanguages | ForEach-Object { $_.tag }) -join ", "
    throw "Unable to create a Windows OCR engine. Available languages: $tags."
  }

  $stream = $null
  $softwareBitmap = $null
  try {
    $file = Await ([Windows.Storage.StorageFile]::GetFileFromPathAsync($resolvedImage)) ([Windows.Storage.StorageFile])
    $stream = Await ($file.OpenAsync([Windows.Storage.FileAccessMode]::Read)) ([Windows.Storage.Streams.IRandomAccessStream])
    $decoder = Await ([Windows.Graphics.Imaging.BitmapDecoder]::CreateAsync($stream)) ([Windows.Graphics.Imaging.BitmapDecoder])
    $softwareBitmap = Await ($decoder.GetSoftwareBitmapAsync(
      [Windows.Graphics.Imaging.BitmapPixelFormat]::Bgra8,
      [Windows.Graphics.Imaging.BitmapAlphaMode]::Premultiplied
    )) ([Windows.Graphics.Imaging.SoftwareBitmap])

    $maxDimension = [Windows.Media.Ocr.OcrEngine]::MaxImageDimension
    if ($softwareBitmap.PixelWidth -gt $maxDimension -or $softwareBitmap.PixelHeight -gt $maxDimension) {
      throw "Image exceeds OCR max dimension ($maxDimension px): $($softwareBitmap.PixelWidth)x$($softwareBitmap.PixelHeight)"
    }

    $result = Await ($engine.RecognizeAsync($softwareBitmap)) ([Windows.Media.Ocr.OcrResult])

    $lines = @($result.Lines | ForEach-Object {
      [ordered]@{ text = $_.Text }
    })

    [pscustomobject]@{
      backend = "windows-media-ocr"
      language = $engine.RecognizerLanguage.LanguageTag
      text = $result.Text
      lines = $lines
      imageWidth = $softwareBitmap.PixelWidth
      imageHeight = $softwareBitmap.PixelHeight
    } | ConvertTo-Json -Depth 5 -Compress
  } finally {
    if ($softwareBitmap) { $softwareBitmap.Dispose() }
    if ($stream) { $stream.Dispose() }
  }
} catch {
  log -Level "ERROR" -Message "Windows OCR failed: $($_.Exception.Message)"
  exit 1
}
