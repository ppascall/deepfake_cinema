# Movie Face-Swap Prototype --- Instructions for VS Code AI

## Goal

Build a local Linux prototype that takes a **prerecorded MP4 video** and
a **consenting source person's photo**, replaces the face of one visible
character with the source person's face, and saves a new MP4 with the
original audio.

This is an **offline video-processing prototype**, not real-time
playback. It should run on a Linux laptop with an Intel 7th-generation
CPU and at least 16 GB RAM, using CPU inference. It may be slow. Do not
promise real-time performance.

Use only footage and face images I have permission to process. Keep
files local by default. Do not upload them to third-party services.

## Instructions for the coding AI

Implement the project in this VS Code workspace. First inspect the
workspace and OS assumptions; then create the files described below.
Prefer a small, understandable Python project with clear error messages
and a README. Do not download model weights automatically from
unofficial sources or bypass model access/licensing restrictions. Make
model setup explicit and document the applicable model license. Do not
bundle weights in the repository.

### Required behavior

1.  Accept a video at `input/movie.mp4`.
2.  Accept a source face photo at `input/person.jpg`.
3.  Provide a command to create a short test clip from the original
    video.
4.  Detect faces in the source image and video frames.
5.  Swap only one target face per frame by default (the largest detected
    face is acceptable for the first prototype).
6.  Process frames offline using CPU inference.
7.  Preserve the original video audio and synchronize it with the
    processed frames.
8.  Save the final result to `output/final_movie.mp4`.
9.  Show progress, elapsed time, processed frame count, and errors.
10. Never silently overwrite an existing output; ask for a different
    filename or require an explicit `--overwrite` option.
11. Handle missing files, unreadable videos, no face detected in the
    source photo, invalid frame rate/dimensions, and missing model files
    with helpful messages.
12. Keep the input video unchanged.
13. Add a `--max-seconds` option so I can limit processing to a short
    test segment.
14. Add a `--scale-height` option (default 720) to reduce video
    dimensions for faster processing while keeping aspect ratio.
15. Add a dry-run / environment-check command that verifies Python
    packages, FFmpeg/ffprobe, CPU inference availability, and model-file
    presence without processing a movie.

### Recommended stack

-   Python 3.10+ (choose a version compatible with the selected
    face-analysis package)
-   OpenCV (`opencv-python`) for frame reading/writing
-   FFmpeg/ffprobe for clip preparation and audio muxing
-   ONNX Runtime CPU for inference
-   InsightFace or another actively maintained, compatible face-swapping
    implementation, only if its package and pretrained-model licenses
    permit the intended use

**Important model licensing:** Python package availability does not
automatically grant rights to use pretrained model weights. The commonly
used InsightFace InSwapper weights may have restrictions. The
application must not download restricted weights or imply that a
non-commercial/research model is cleared for commercial use. Document
where to obtain authorized weights and the exact expected path. If no
authorized compatible model is present, the program should stop and
explain what is missing.

### Suggested project layout

Create:

``` text
movie-face-demo/
├── README.md
├── requirements.txt
├── .gitignore
├── instructions.md
├── input/
│   └── .gitkeep
├── output/
│   └── .gitkeep
├── models/
│   └── .gitkeep
├── scripts/
│   ├── check_environment.py
│   └── make_test_clip.sh
└── src/
    ├── __init__.py
    ├── detect_faces.py
    └── process_video.py
```

The AI may adjust the layout if it keeps the same user-facing commands
and documents the change.

## My setup steps

### 1. Install system tools (Ubuntu/Debian)

Open a terminal:

``` bash
sudo apt update
sudo apt install -y python3 python3-venv python3-pip ffmpeg git
```

Check:

``` bash
python3 --version
ffmpeg -version
ffprobe -version
lscpu | head -n 15
free -h
```

If I use Fedora, Arch, or another distribution, document the equivalent
package commands instead of running Ubuntu commands blindly.

### 2. Open the folder in VS Code

Create/open the project folder in VS Code. Paste this file into the
project as `instructions.md` and ask the coding AI:

> Implement the project described in `instructions.md`. Create the
> files, make the setup reproducible, and update README.md with exact
> commands. Do not fetch restricted model weights. Add a CPU-only
> environment check and validate the workflow with a synthetic/test
> video if possible. Before finishing, list files created and commands I
> should run.

### 3. Create and activate a virtual environment

From the project root:

``` bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
```

The AI should create `requirements.txt`. Install from it:

``` bash
python -m pip install -r requirements.txt
```

### 4. Put my files in the right places

Copy my prerecorded film or a short excerpt into:

``` text
input/movie.mp4
```

Copy a clear, front-facing photo of the person who has consented to the
face swap into:

``` text
input/person.jpg
```

Use a photo with one face, even lighting, and a reasonably sharp image.
Keep the movie and photo on my computer.

The exact filenames above should work by default. The AI should also
support command-line paths so I can use other filenames if needed.

### 5. How long can the MP4 be?

There is no fixed duration limit imposed by the basic workflow; the
practical limit is processing time and disk space. On a 7th-generation
Intel CPU, face detection and swapping may be much slower than playback.
Do **not** start with a full-length film.

Recommended test lengths:

-   **5--10 seconds:** first smoke test.
-   **10--30 seconds:** initial quality test once the first run works.
-   **1--2 minutes:** next test after measuring speed.
-   **Full movie:** only after calculating the expected runtime and
    confirming enough disk space.

The application must report measured processing speed. If processing
speed is `r` frames per second and the video has `N` frames, approximate
processing time is `N / r` seconds, plus audio/video encoding overhead.
For example, a 10-minute video at 2 processed frames/second contains
roughly 14,400 frames and would take about 2 hours to process, plus
overhead. Actual speed may be faster or slower.

For an easy first run, create a 10-second test clip. For example:

``` bash
mkdir -p input output
ffmpeg -ss 00:00:30 -i input/movie.mp4 -t 10 \
  -vf "scale=-2:720" -c:v libx264 -preset fast -crf 23 \
  -c:a aac input/test_clip.mp4
```

This takes 10 seconds starting at 30 seconds into the movie. Change the
start time as desired. If the source is shorter than the chosen start
time, choose an earlier timestamp. If audio is absent, FFmpeg should
still be able to create the video; the implementation must support
videos with no audio.

### 6. Run the environment check

The AI should implement and document a command such as:

``` bash
python scripts/check_environment.py
```

It should verify: - Python version and required imports - FFmpeg and
ffprobe availability - ONNX Runtime CPU provider - input file presence
(when requested) - authorized model file presence - writable output
directory

Do not begin a long run if required components are missing.

### 7. Run a short test

The AI should implement a documented command similar to:

``` bash
python -m src.process_video \
  --video input/test_clip.mp4 \
  --source-face input/person.jpg \
  --model models/authorized_swapper.onnx \
  --output output/test_result.mp4 \
  --device cpu
```

The exact model filename and arguments can differ, but the README must
provide a command that matches the implemented code. Do not use a
guessed model filename or claim the command works until it has been
checked against the actual model API.

The processor should: - process frames sequentially; - detect the target
face in each frame; - use one target face by default; - avoid loading
the whole video into RAM; - report progress every few seconds; - write a
temporary silent video; - mux the original audio with FFmpeg, preserving
timing; - clean up temporary files on success and explain how to inspect
them on failure.

If a target face is missing in a frame, leave that frame unchanged. For
the initial prototype, choosing the largest face is acceptable, but
document that it can select the wrong character if the scene changes. Do
not attempt to replace every face automatically in the first version.

### 8. Inspect the result

The expected final file is:

``` text
output/test_result.mp4
```

Open it with a media player or run:

``` bash
ffplay output/test_result.mp4
```

Check face placement, flicker, scenes with head turns, and audio
synchronization. Face swaps can look poor in profile views, occlusions,
rapid motion, and low-light scenes.

### 9. Increase duration carefully

Only after the short test works, try a 30-second clip, then a 1--2
minute clip. Measure the actual frame-processing speed before deciding
whether a full movie is practical. Keep the original movie untouched and
make sure output disk space is sufficient.

### 10. Troubleshooting requirements

The README should explain how to handle: - Python dependency or version
conflicts - FFmpeg not installed - no face detected in the source
photo - no face detected in the video - missing or incompatible ONNX
model - model license/access issues - output video has no audio - slow
CPU inference or overheating - incorrect target face selected -
insufficient disk space

Do not recommend random model downloads, disabling security controls, or
uploading private face photos to unknown services.

## Definition of done

The project is complete when:

1.  A new user can follow README.md on Linux from a clean project
    folder.
2.  The environment check gives actionable results.
3.  The user can create a 5--10 second test clip from `input/movie.mp4`.
4.  With an authorized compatible model present, the script processes
    that clip using CPU inference.
5.  The output video is saved with original audio to the requested
    output path.
6.  The app reports progress and gives clear errors rather than crashing
    obscurely.
7.  The README states realistic performance expectations and model
    licensing caveats.
