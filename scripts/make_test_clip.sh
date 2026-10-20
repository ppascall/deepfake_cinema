#!/usr/bin/env bash
# Create a short test clip from the original video (the original is never modified).
set -euo pipefail

INPUT="input/movie.mp4"
OUTPUT="input/test_clip.mp4"
START="00:00:30"
DURATION=10
HEIGHT=720
OVERWRITE=0

usage() {
  cat <<EOF
Usage: scripts/make_test_clip.sh [-i input] [-o output] [-s start] [-d seconds] [-H height] [-f]
  -i  input video          (default: $INPUT)
  -o  output clip          (default: $OUTPUT)
  -s  start time, seconds or HH:MM:SS (default: $START)
  -d  clip length in seconds (default: $DURATION)
  -H  max height in pixels, never upscales (default: $HEIGHT)
  -f  overwrite the output if it exists
EOF
}

while getopts "i:o:s:d:H:fh" opt; do
  case "$opt" in
    i) INPUT=$OPTARG ;;
    o) OUTPUT=$OPTARG ;;
    s) START=$OPTARG ;;
    d) DURATION=$OPTARG ;;
    H) HEIGHT=$OPTARG ;;
    f) OVERWRITE=1 ;;
    h) usage; exit 0 ;;
    *) usage; exit 2 ;;
  esac
done

for tool in ffmpeg ffprobe; do
  command -v "$tool" >/dev/null || { echo "ERROR: $tool not found. Install FFmpeg (e.g. sudo apt install ffmpeg)." >&2; exit 1; }
done
[[ -f "$INPUT" ]] || { echo "ERROR: input video not found: $INPUT" >&2; exit 1; }
[[ "$OUTPUT" == *.mp4 ]] || { echo "ERROR: output must end in .mp4" >&2; exit 2; }
[[ "$(realpath -m "$OUTPUT")" != "$(realpath "$INPUT")" ]] || { echo "ERROR: output would overwrite the input video." >&2; exit 2; }
if [[ -e "$OUTPUT" && $OVERWRITE -eq 0 ]]; then
  echo "ERROR: $OUTPUT already exists. Use -f to overwrite or -o for another name." >&2
  exit 1
fi

TOTAL=$(ffprobe -v error -show_entries format=duration -of default=nw=1:nk=1 "$INPUT" || true)
[[ -n "$TOTAL" && "$TOTAL" != "N/A" ]] || { echo "ERROR: could not read the duration of $INPUT (corrupt file?)." >&2; exit 1; }

START_SEC=$(awk -v t="$START" 'BEGIN{n=split(t,a,":"); s=0; for(i=1;i<=n;i++) s=s*60+a[i]; print s}')
if awk -v s="$START_SEC" -v t="$TOTAL" 'BEGIN{exit !(s>=t)}'; then
  echo "ERROR: start time $START is beyond the end of the video (${TOTAL}s). Choose an earlier -s." >&2
  exit 1
fi

mkdir -p "$(dirname "$OUTPUT")"
echo "Creating ${DURATION}s clip from $INPUT at $START -> $OUTPUT"
# Map explicitly: the first real video stream and, if present, the first audio stream.
ffmpeg -hide_banner -loglevel error -y -ss "$START" -i "$INPUT" -t "$DURATION" \
  -map 0:v:0 -map "0:a:0?" \
  -vf "scale=-2:'min($HEIGHT,ih)'" -c:v libx264 -preset fast -crf 23 -pix_fmt yuv420p \
  -c:a aac -b:a 192k -movflags +faststart "$OUTPUT"
echo "Done: $OUTPUT"
