param(
    [string]$Input = "input/movie.mp4",
    [string]$Output = "input/test_clip.mp4",
    [string]$Start = "00:00:30",
    [int]$Duration = 10,
    [int]$Height = 720,
    [switch]$Force
)

$ErrorActionPreference = "Stop"

if (-not (Get-Command ffmpeg -ErrorAction SilentlyContinue)) {
    throw "ffmpeg not found on PATH. Install FFmpeg (for example: winget install Gyan.Dev.FFmpeg)."
}
if (-not (Get-Command ffprobe -ErrorAction SilentlyContinue)) {
    throw "ffprobe not found on PATH. Install FFmpeg (for example: winget install Gyan.Dev.FFmpeg)."
}
if (-not (Test-Path $Input -PathType Leaf)) {
    throw "Input video not found: $Input"
}
if (-not $Output.EndsWith(".mp4", [System.StringComparison]::OrdinalIgnoreCase)) {
    throw "Output must end in .mp4"
}
if ((Resolve-Path $Output -ErrorAction SilentlyContinue).Path -eq (Resolve-Path $Input -ErrorAction SilentlyContinue).Path) {
    throw "Output would overwrite the input video."
}
if ((Test-Path $Output) -and -not $Force) {
    throw "Output already exists. Use -Force to overwrite or choose another name."
}

$total = & ffprobe -v error -show_entries format=duration -of default=nw=1:nk=1 $Input 2>$null
if (-not $total -or $total -eq "N/A") {
    throw "Could not read the duration of $Input (corrupt file?)."
}

$startSec = 0
$parts = $Start -split ":"
for ($i = 0; $i -lt $parts.Length; $i++) {
    $startSec = $startSec * 60 + [double]$parts[$i]
}
if ($startSec -ge [double]$total) {
    throw "Start time $Start is beyond the end of the video ($total s). Choose an earlier -Start."
}

$dir = Split-Path -Parent $Output
if ($dir) { New-Item -ItemType Directory -Force -Path $dir | Out-Null }
Write-Host "Creating ${Duration}s clip from $Input at $Start -> $Output"
& ffmpeg -hide_banner -loglevel error -y -ss $Start -i $Input -t $Duration -map 0:v:0 -map "0:a:0?" -vf "scale=-2:'min($Height,ih)'" -c:v libx264 -preset fast -crf 23 -pix_fmt yuv420p -c:a aac -b:a 192k -movflags +faststart $Output
Write-Host "Done: $Output"
