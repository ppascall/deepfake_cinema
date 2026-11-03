# Movie face-swap prototype (CPU or CUDA)

Replaces one visible face in a **prerecorded MP4** with the face from a **photo of a consenting person**,
and writes a new MP4 that keeps the original audio. It runs on CPU by default and can optionally use an NVIDIA
CUDA GPU or offload processing to an existing Runcrate GPU instance. Local processing does not upload files;
cloud processing transfers the selected media and authorized model files to the rented machine. Model weights are
never downloaded automatically.

> Only process footage and face photos you have the right to use, and only swap in the face of someone who
> has consented. Do not publish results in a way that could mislead viewers about who appears in them.

## How it works

```
input/movie.mp4 --OpenCV--> frame --detect largest face--> swap (InSwapper, ONNX Runtime CPU/CUDA)
                                                              |
                          output/final_movie.mp4 <--FFmpeg mux (original audio)-- silent temp video
```

* One face per frame is swapped: the **largest detected face**. If the scene changes and a different
  character becomes the largest face, the wrong character gets swapped.
* Frames without a detected face are left unchanged.
* Frames are processed one at a time; the video is never loaded into RAM.

## 1. System packages

Windows (PowerShell):

```powershell
winget install Gyan.Dev.FFmpeg
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

Ubuntu / Debian / Linux Mint:

```bash
sudo apt update
sudo apt install -y python3 python3-venv python3-pip ffmpeg git
```

Other distributions (equivalents, not tested here):

| Distribution | Command |
|---|---|
| Fedora | `sudo dnf install python3 python3-pip ffmpeg git` (FFmpeg needs the RPM Fusion repository enabled) |
| Arch | `sudo pacman -S python python-pip ffmpeg git` |

Check: `python --version`, `ffmpeg -version`, `ffprobe -version`. Python 3.10+ is required (3.12 was tested).

## 2. Python environment

Linux / macOS:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

Windows:

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

Packages: `numpy`, `opencv-python`, `onnxruntime` (CPU by default), and `insightface` (used only as a library for
the SCRFD detector, ArcFace recognizer and the InSwapper wrapper; its model auto-download is never used).

## Run the laptop UI with a Runcrate GPU

The web UI runs in Docker on the laptop, while the Runcrate CLI and its login session stay on the laptop. The
small host-side bridge passes jobs from the container to that authenticated CLI; no Runcrate API token or SSH
private key is copied into Docker. This workflow reuses the already-running `face-swap` instance and never
creates another instance. The default behavior leaves it running after processing.

On the laptop, first authenticate and confirm the named instance is running:

```bash
runcrate login
runcrate instances status face-swap
```

Then start the complete app from the project directory:

```bash
chmod +x scripts/start-with-runcrate.sh
./scripts/start-with-runcrate.sh
```

The script creates a private local bridge token file (ignored by Git), starts the bridge bound to Docker's local
bridge gateway, and runs `docker compose up --build`. Open <http://localhost:3000>, select or capture a face,
select a video and scene range, confirm consent, then choose **Start experience**. The browser shows the source
film while processing; when ready it can play the stitched result. The bridge checks the instance state and waits
while it is deploying. If it is stopped or unavailable, start it yourself in Runcrate and retry.

For a small end-to-end test, put a short authorized video at `input/test_clip.mp4`, select a short scene (for
example 0-3 seconds), use a saved/captured face, confirm consent, and start the experience. Monitor the processing
log. The result is downloaded to `output/` on the laptop. The host worker uploads the selected clip, selected face,
processor source/dependency files, and required model files only; it does not upload the full source film when a
scene range is selected. It removes its temporary remote job directory after processing.

The instance remains running and can continue to incur charges. Check GPU use with:

```bash
runcrate ssh face-swap -- nvidia-smi
```

When finished, shut it down deliberately in the Runcrate dashboard. The UI also offers **Delete the Runcrate
instance after a successful job**; this is off by default and deletes the instance rather than merely pausing it.
Only enable it if you intend to delete that instance.

The Runcrate CLI must be installed and logged in as the same laptop user that runs the startup script. Docker,
the existing Runcrate instance, valid model files at the paths below, and permission to process the selected media
are required. The Docker image includes FFmpeg for scene extraction and stitching. The web app does not set up CLI
authentication or create/restart instances. The instance must have internet access to install the CUDA runtime
dependencies on its first job.

## Optional: NVIDIA CUDA inference

The normal Python environment and Docker image remain CPU-based. For CUDA, use a Linux machine with an NVIDIA
GPU and a driver compatible with CUDA 12.8. The GPU ONNX Runtime build must match the installed CUDA/cuDNN
libraries; `onnxruntime-gpu` is not a substitute for the NVIDIA driver.

For a Python environment on the GPU machine, install the normal dependencies, replace CPU ONNX Runtime with
the GPU package, then verify the provider:

```bash
python -m pip install -r requirements.txt
python -m pip uninstall -y onnxruntime
python -m pip install -r requirements-gpu.txt
python scripts/check_environment.py --device cuda
```

Run the processor with `--device cuda`. The detector, recognizer, and swapper all use CUDA; ONNX Runtime may
fall back to CPU for unsupported model operations. Without the option, CPU remains the default.

For Docker, install the NVIDIA Container Toolkit on the Linux host and use the CUDA Compose overlay:

```bash
docker compose -f docker-compose.yml -f docker-compose.cuda.yml up --build
```

The overlay selects the CUDA image, exposes all host GPUs to the container, and sets the web-launched processor
to CUDA. Check the runtime from the container with:

```bash
docker compose -f docker-compose.yml -f docker-compose.cuda.yml exec deepfake-cinema python -c "import onnxruntime as ort; print(ort.get_available_providers())"
```

The result should include `CUDAExecutionProvider`. For a rented VM, keep the app bound to the VM's loopback and
access it through an SSH tunnel, for example `ssh -L 3000:127.0.0.1:3000 user@your-vm`; do not expose the
processing UI directly to the public internet. Videos, captured photos, and model files are present on that
cloud machine while processing, so remove them when they are no longer needed.

## 3. Models and licensing

The code in this repository does **not** include or download model weights. Python package availability
does not grant rights to the pretrained weights. You must obtain authorized weights yourself and place them
at exactly these paths (or pass other paths with `--model`, `--det-model`, `--rec-model`):

| Purpose | Default path | Notes |
|---|---|---|
| Face swapper | `models/inswapper_128.onnx` | InSwapper-style model: inputs `[1,3,128,128]` image and `[1,512]` embedding |
| Face detector | `models/buffalo_l/det_10g.onnx` | SCRFD detector from the InsightFace `buffalo_l` pack |
| Face recognizer | `models/buffalo_l/w600k_r50.onnx` | ArcFace model from the same pack |

**License caveat.** The InsightFace *code* is MIT-licensed, but the InsightFace project states that its
pretrained models (including the `buffalo_l` pack and the InSwapper `inswapper_128` model) are for
**non-commercial research use only**. Do not treat these weights as cleared for commercial use. Read the
terms in the official repository (<https://github.com/deepinsight/insightface>) and contact the project if
you need commercial rights. This README cannot confirm where an authorized copy of `inswapper_128.onnx` is
currently distributed or that your intended use is permitted; verify that at the source. Do not use random
mirrors or unofficial downloads. If a compatible authorized model is missing, the tools stop and say so.

The model files are in `.gitignore` and must not be committed or redistributed.

## 4. Put your files in place

```text
input/movie.mp4     the prerecorded film or excerpt (never modified)
input/person.jpg    clear, front-facing photo of the consenting person: one face, even lighting, sharp
```

Other filenames work via `--video` and `--source-face`.

## 5. Check the environment

```bash
python scripts/check_environment.py            # tools, packages, CPU provider, models, output dir
python scripts/check_environment.py --inputs   # also checks input/movie.mp4 and input/person.jpg
```

It exits non-zero and prints a hint for every failed item. A full dry run for a specific command (checks +
plan + time estimates, no processing) is available with `--dry-run` on the processor (see below).

## 6. Create a short test clip

```bash
scripts/make_test_clip.sh                         # 10 s from 00:00:30 -> input/test_clip.mp4 (max 720p)
scripts/make_test_clip.sh -s 00:01:10 -d 5        # different start / length
scripts/make_test_clip.sh -h                      # all options
```

The original is only read. Audio is optional (videos without audio work). If the start time is past the end
of the movie, the script says so. On Windows, use PowerShell instead of bash:

```powershell
powershell -ExecutionPolicy Bypass -File scripts\make_test_clip.ps1
powershell -ExecutionPolicy Bypass -File scripts\make_test_clip.ps1 -Video input\movie.mp4 -OutputPath input\test_clip.mp4 -StartTime 00:01:10 -ClipDuration 5
```

The script also accepts the older aliases `-Input`, `-Output`, `-Start`, `-Duration`, and `-Height` for compatibility, but `-Video` and `-OutputPath` are the safer names because PowerShell reserves `$input`.

Optional, check which face would be picked before a long run:

```bash
python -m src.detect_faces --image input/person.jpg
python -m src.detect_faces --video input/test_clip.mp4 --time 3 --annotate output/faces.jpg
```

## 7. Run a short test

```bash
python -m src.process_video \
  --video input/test_clip.mp4 \
  --source-face input/person.jpg \
  --model models/inswapper_128.onnx \
  --output output/test_result.mp4 \
  --device cpu
```

By default only the first **5 seconds** are processed and the output is **20 fps**; use `--max-seconds 0` for
the whole video and `--fps 0` to keep the source frame rate. Add `--dry-run` to only validate everything and see the
plan. Progress (percent, frame count, fps, elapsed, ETA, swapped / no-face / error counts) is printed every
few seconds. Frames that fail are left unchanged and reported; 50 consecutive failures abort the run.

Inspect the result with a media player or `ffplay output/test_result.mp4`. Check face placement, flicker,
head turns and audio sync.

## Optional: consent-based installation web launcher

The Docker web page is organized in order: choose an existing saved photo or **Live camera**, select the
film, set the scene range, then set playback options. Existing photos include `input/person.jpg` and prior
camera captures. In live mode, **Capture live photo** requests camera access, shows a preview and a
three-second countdown, and saves one still locally in `output/captured_faces/`. After the participant
affirms consent, **Start experience** opens the selected film in a separate tab and starts background
processing and stitching.

FFmpeg replaces the selected interval in the full-length film, retaining the original footage before and
after it and the original audio. Once the source playback ends and rendering is complete, the finished film
starts from the beginning automatically (when that option is enabled).

This is not frame-by-frame live replacement: if CPU rendering is still running when the first playback
ends, the page waits and then replays the completed version. The photo is sent to this local server first.
If Runcrate is configured, the selected face photo and video segment are then transferred to the remote GPU
instance; otherwise processing stays local. Camera access is available from `localhost` or another HTTPS origin.
The server also requires a consent marker on capture and processing requests.

Output videos use timestamped filenames in `output/`, and the page serves the newest MP4. **Process
selected range** remains available for an already-saved face, but the one-button installation flow always
captures a fresh still.

On Linux, run as your own user so the container can read your files and write the result into `output/`:

```bash
APP_UID="$(id -u)" APP_GID="$(id -g)" docker compose up --build
```

On other systems, `docker compose up --build` uses UID/GID 1000 by default. When the image is ready, open
<http://localhost:3000>. The page only listens on your machine; the input folder accepts UI video uploads, the
model folder is mounted read-only, and generated files and captured faces are written to `output/`. Allow camera access in the
browser to start the experience. Stop the service with `Ctrl+C` (or `docker compose down`).

The first build downloads the base image and installs the Python dependencies; model files are never
automatically downloaded. With Runcrate configured, selected media and model files are transferred to the
remote instance; without it, processing stays local. If you deny camera access, capture fails without starting
processing. The command-line capture workflow remains available below.

## Optional: consent-based live face capture

The capture utility is a separate, opt-in tool. It detects faces in a live camera feed and saves a still
JPEG only when you press `S`; it does not recognize identities, record video, upload images, or start the
face-swap pipeline. Use it only with the photographed person's knowledge and consent, and with an authorized
camera feed. The largest detected face is highlighted as the selected face.

The existing OpenCV, InsightFace, ONNX Runtime, NumPy, and SCRFD detector model are used; the capture tool
does not load the ArcFace recognizer or swapper. No new package is required. Ensure your OpenCV install
supports display windows before using interactive capture.

For a single command that waits five seconds, captures the highlighted face, and immediately runs the
existing processor on the default `input/movie.mp4`:

```bash
python -m src.capture_face --capture-and-process
```

The preview remains visible during the countdown. Be the only intended face in view when the countdown
ends; if no face is detected yet, capture waits until one is visible. Press `Q` or Escape to cancel without
processing. The captured image is saved locally, then passed as `--source-face` to the existing processor.
The default video and output are `input/movie.mp4` and `output/final_movie.mp4`; use `--video` and `--output`
to change them, `--model` to select the authorized swapper, or `--capture-delay` to adjust the countdown.
The existing processor defaults to a five-second output; add `--max-seconds 0` to process the whole video.
Use `--overwrite` only if you intend to replace an existing output file. This convenience mode does not
replace the ordinary manual capture mode below.

```bash
# Default webcam (camera index 0)
python -m src.capture_face --camera 0

# An operator-supplied IP-camera/RTSP stream; the URL is not displayed or saved by the tool
python -m src.capture_face --stream-url "rtsp://camera-host:554/stream"

# Check dependencies and the detector file without opening a camera
python -m src.capture_face --dry-run
```

In ordinary capture mode, press `S` only when the intended consenting person is visible and the highlighted
face is correct. In either mode, the image is cropped with a small margin and saved under `input/captured_faces` as
`face_YYYYMMDD_HHMMSS.jpg`; an existing name is preserved and a numeric suffix is added. Press `Q` or
Escape to quit. Use `--output-dir` to choose another local directory and `--prefix` to change the filename
prefix. `--det-model` and `--det-size` configure the detector.

In manual capture mode, inspect the saved JPEG before using it; it is not passed to the swapper unless you
select it explicitly in a later run. The separate `--capture-and-process` option intentionally captures
and starts processing in one command:

```bash
python -m src.process_video \
  --video input/test_clip.mp4 \
  --source-face input/captured_faces/face_YYYYMMDD_HHMMSS.jpg \
  --model models/inswapper_128.onnx \
  --output output/test_result.mp4 \
  --device cpu
```

### Options

| Option | Default | Meaning |
|---|---|---|
| `--video` | `input/movie.mp4` | input video, never modified |
| `--source-face` | `input/person.jpg` | photo of the consenting person |
| `--model` / `--det-model` / `--rec-model` | see section 3 | model paths |
| `--output` | `output/final_movie.mp4` | must end in `.mp4` |
| `--overwrite` | off | replace an existing output (otherwise you are asked for another filename, or the run stops when not interactive) |
| `--max-seconds N` | `5` | process only the first N seconds (`0` = whole video) |
| `--fps R` | `20` | output frame rate; source frames are dropped by timestamp, never duplicated, so audio stays in sync (`0` = keep source rate) |
| `--scale-height H` | `720` | downscale to height H, aspect ratio kept, never upscales (`0` = keep) |
| `--det-size N` | `640` | detector input size (multiple of 32); smaller is faster, may miss small faces |
| `--device` | `cpu` | `cpu` or `cuda`; CUDA requires the GPU ONNX Runtime build and compatible NVIDIA runtime |
| `--keep-temp` | off | keep the temporary directory on success |
| `--dry-run` | off | checks and plan only |

Temporary files live in `output/.tmp_<name>_<pid>/`. They are removed on success; on failure or Ctrl+C they
are kept and the path is printed, so you can look at `silent.mp4` and `ffmpeg_writer.log`. The final file is
only moved into place after audio muxing succeeds.

## 8. Increase duration carefully

Run time is roughly `N / r` seconds (`N` = frames, `r` = processed frames per second) plus encoding. A
10-minute video at 2 fps contains about 14,400 frames and takes about 2 hours.

On an older laptop CPU expect on the order of **1-4 processed frames per second at 720p**; this is an
estimate, not a measurement: the program prints the real speed. Suggested progression: 5-10 s, then 10-30 s,
then 1-2 min, and the full movie only after calculating the expected runtime and disk space (about 3 MB per
second of video is reserved, plus a margin).

High frame rates multiply the cost: `input/movie.mp4` is 60 fps, so every second is 60 frames. The default
`--fps 20` already processes only one in three frames of that source. To use another rate, pass `--fps`;
you can also make a lower-rate working copy first (audio kept, original untouched):

```bash
ffmpeg -i input/movie.mp4 -map 0:v:0 -map 0:a:0? -r 30 -c:v libx264 -crf 18 -c:a aac input/movie_30fps.mp4
```

Then use `--video input/movie_30fps.mp4`. Lower `--scale-height` (e.g. 540 or 480) also helps. Watch CPU
temperature on long runs.

## 9. Pipeline self-test (no models needed)

```bash
python scripts/selftest_pipeline.py
```

Builds a synthetic video with audio and runs the real pipeline with stub face models. It verifies frame
count, scaling, audio mux, temp cleanup and overwrite protection, not swap quality.

## Project layout

```text
README.md  requirements.txt  .gitignore  instructions.md
input/  output/  models/                 (contain only .gitkeep in git)
scripts/check_environment.py             environment check
scripts/make_test_clip.sh                short test clip
scripts/selftest_pipeline.py             pipeline self-test with stub models
src/process_video.py                     main command
src/capture_face.py                      optional, explicit live still capture
src/detect_faces.py                      detection/embedding + face inspection command
src/environment.py  src/common.py        shared checks and helpers (added to the suggested layout)
```

The project root is this folder (not a nested `movie-face-demo/`).

## Troubleshooting

* **Dependency or version conflicts**: recreate the environment (`rm -rf .venv`, repeat section 2). Keep
  `numpy<2` as pinned. If `insightface` fails to build, install build tools
  (`sudo apt install build-essential python3-dev`) and retry; it is only used as a library.
* **FFmpeg not installed**: see section 1; verify with `ffmpeg -version`.
* **No face detected in the source photo**: use a larger, sharper, front-facing, evenly lit photo, no
  sunglasses or masks, face at least ~200 px wide. Test with `python -m src.detect_faces --image ...`.
* **No face detected in the video** (`no-face` count high, nothing swapped): check a frame with
  `python -m src.detect_faces --video ... --time N --annotate output/faces.jpg`; try larger `--det-size`,
  a higher `--scale-height`, or a different scene. Profiles, occlusion and low light are hard cases.
* **Camera capture cannot open or read**: check operating-system camera permissions, close other apps
  using the webcam, confirm the `--camera` index, or verify the operator-provided stream is reachable and
  authorized. Stream credentials are not printed by the capture utility.
* **Capture window does not open**: use an OpenCV installation with GUI support and run from a desktop
  session; headless OpenCV builds cannot show the live preview.
* **No face detected during capture**: improve lighting, bring the face closer and into view, and wait for
  the on-screen status to show a selected face. Saving is only enabled after a face is detected.
* **Missing or incompatible ONNX model**: the tools print the missing path or the signature problem; see
  section 3. Files under ~1 KB or truncated copies are rejected.
* **Model license / access issues**: see section 3. Do not substitute unofficial downloads.
* **Output has no audio**: the input may have no audio track (the program says so); otherwise inspect with
  `ffprobe input/test_clip.mp4`. Audio is re-encoded to AAC and taken from the first audio stream.
* **Audio drifts**: variable frame rate input is warned about; convert to constant frame rate first
  (`ffmpeg -i in.mp4 -r 30 -c:a copy cfr.mp4`).
* **Slow processing or overheating**: use `--max-seconds`, a 30 fps copy, lower `--scale-height` / `--det-size`,
  close other programs, ensure the laptop vents are clear.
* **Wrong character swapped**: the largest face is chosen every frame. Use a clip where the target is the
  largest face, or crop/cut the clip with `scripts/make_test_clip.sh`.
* **Insufficient disk space**: the program checks before starting. Free space, shorten with `--max-seconds`,
  or point `--output` at another disk.
* **"already exists"**: pass `--overwrite` or choose another `--output`.
