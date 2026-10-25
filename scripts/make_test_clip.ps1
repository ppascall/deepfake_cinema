param(
    [Alias("Input")]
    [string]$Video = "input/movie.mp4",
    [Alias("Output")]
    [string]$OutputPath = "input/test_clip.mp4",
    [Alias("Start")]
    [string]$StartTime = "auto",
    [Alias("Duration")]
    [int]$ClipDuration = 10,
    [Alias("Height")]
    [int]$MaxHeight = 720,
    [switch]$Force
)

$ErrorActionPreference = "Stop"

function Format-SecondsForFfmpeg {
    param(
        [double]$Seconds
    )

    $totalWholeSeconds = [Math]::Floor($Seconds)
    $hours = [int]([Math]::Floor($totalWholeSeconds / 3600))
    $minutes = [int]([Math]::Floor(($totalWholeSeconds % 3600) / 60))
    $seconds = [double]($totalWholeSeconds % 60) + ($Seconds - $totalWholeSeconds)
    return '{0:00}:{1:00}:{2:00.000}' -f $hours, $minutes, $seconds
}

if (-not (Get-Command ffmpeg -ErrorAction SilentlyContinue)) {
    throw "ffmpeg not found on PATH. Install FFmpeg (for example: winget install Gyan.Dev.FFmpeg)."
}
if (-not (Get-Command ffprobe -ErrorAction SilentlyContinue)) {
    throw "ffprobe not found on PATH. Install FFmpeg (for example: winget install Gyan.Dev.FFmpeg)."
}
if (-not (Test-Path $Video -PathType Leaf)) {
    throw "Input video not found: $Video"
}
if (-not $OutputPath.EndsWith(".mp4", [System.StringComparison]::OrdinalIgnoreCase)) {
    throw "Output must end in .mp4"
}
if ((Resolve-Path $OutputPath -ErrorAction SilentlyContinue).Path -eq (Resolve-Path $Video -ErrorAction SilentlyContinue).Path) {
    throw "Output would overwrite the input video."
}
if ((Test-Path $OutputPath) -and -not $Force) {
    throw "Output already exists. Use -Force to overwrite or choose another name."
}

$total = & ffprobe -v error -show_entries format=duration -of default=nw=1:nk=1 $Video 2>$null
if (-not $total -or $total -eq "N/A") {
    throw "Could not read the duration of $Video (corrupt file?)."
}

if ($StartTime -eq "auto") {
    $defaultStart = [Math]::Min(30.0, [double]$total - [double]$ClipDuration)
    if ($defaultStart -lt 0.0) {
        $defaultStart = 0.0
    }
    $StartTime = Format-SecondsForFfmpeg -Seconds $defaultStart
}

$startSec = 0
$parts = $StartTime -split ":"
for ($i = 0; $i -lt $parts.Length; $i++) {
    $startSec = $startSec * 60 + [double]$parts[$i]
}
if ($startSec -ge [double]$total) {
    throw "Start time $StartTime is beyond the end of the video ($total s). Choose an earlier -StartTime."
}
if (($startSec + [double]$ClipDuration) -gt [double]$total) {
    throw "Start time $StartTime plus duration $ClipDuration exceeds the end of the video ($total s). Pick a shorter clip or an earlier start."
}

$dir = Split-Path -Parent $OutputPath
if ($dir) { New-Item -ItemType Directory -Force -Path $dir | Out-Null }
Write-Host "Creating ${ClipDuration}s clip from $Video at $StartTime -> $OutputPath"
& ffmpeg -hide_banner -loglevel error -y -ss $StartTime -i $Video -t $ClipDuration -map 0:v:0 -map "0:a:0?" -vf "scale=-2:'min($MaxHeight,ih)'" -c:v libx264 -preset fast -crf 23 -pix_fmt yuv420p -c:a aac -b:a 192k -movflags +faststart $OutputPath
Write-Host "Done: $OutputPath"
