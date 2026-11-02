# Coding Agent Instructions: Add Consent-Based Face Capture to the Existing Movie Face-Swap Prototype

## Goal

Extend the existing offline, CPU-based movie face-swap prototype with an optional **face-capture utility** that reads a live camera stream, detects faces, and saves a JPEG image for a person who has consented to being captured. Keep the current prerecorded-MP4 face-swap pipeline intact.

The current project README describes:
- An offline, CPU-only Python pipeline.
- OpenCV for video frames.
- InsightFace/SCRFD face detection and ArcFace recognition components.
- InSwapper-style ONNX face swapping.
- FFmpeg for muxing the original audio back into the output MP4.
- Explicit model-path handling; do not auto-download pretrained model weights.
- A rule that source-face photos must be of a cheyt onsenting person and footage/photos must be authorized.

## Safety and scope requirements

1. Implement **face detection and still-image capture only**. Do not add identity matching, face recognition, identity tracking across sessions, or automatic feeding of captured images into the face-swapper.
2. Make capture explicit and locally controlled: require an operator to start the capture process and press a key to save a detected face. Do not silently or covertly collect images.
3. Display a visible capture window and status (e.g. `Face detected — press S to save; Q to quit`). Do not save automatically merely because someone enters the frame.
4. Show a clear startup notice that capture should only be used with the knowledge and consent of the person being photographed, and only with authorized camera feeds.
5. Save only to a configurable local output directory. Do not upload images, add network calls, or send captured images to any external service.
6. Do not automatically pass captured images to `src/process_video.py`; require the operator to deliberately choose a saved image as `--source-face` for a later run.
7. Avoid storing more data than needed. Do not keep a camera recording or a history of frames; save only the still image explicitly requested by the operator.
8. Include tests for camera failure, no-face detection, successful JPEG save, output-directory creation, and refusal to overwrite existing files by default.

## Fit with the existing repository

Read the existing code before changing it, especially `src/detect_faces.py`, `src/environment.py`, `src/common.py`, `src/process_video.py`, `requirements.txt`, `.gitignore`, and the README. Preserve the existing command-line conventions and shared helpers. Reuse the existing OpenCV/InsightFace detector path if it is suitable; do not create a second model-loading path unnecessarily.

The current README says model files are not bundled or auto-downloaded and must be supplied from an authorized source. Preserve this behavior. Do not add automatic model downloads. If the current detector cannot be reused cleanly for live capture, explain the trade-off and use the smallest compatible implementation.

## Suggested implementation

Create `src/capture_face.py` with a CLI that:
- Opens a webcam by default (camera index `0`).
- Optionally accepts an IP-camera/RTSP URL supplied by the operator, without logging credentials. Do not persist the URL.
- Offers arguments such as:
  - `--camera 0` for a local camera index.
  - `--stream-url <URL>` for an operator-provided stream.
  - `--output-dir input/captured_faces` or another safe local default.
  - `--prefix face` for saved filenames.
  - `--det-size 640` if compatible with the current detector.
  - `--dry-run` to validate arguments and dependencies without opening or saving from a camera.
- Reads frames continuously and detects faces using the repository's existing detector if possible.
- Draws a temporary rectangle around detected face(s) for operator feedback.
- If no face is detected, display `No face detected`.
- If one or more faces are detected, allow the operator to select which face to save (e.g. cycle through detections with arrow keys, or click a face). If selection is too complex, save the largest face only after the operator presses `S`, and clearly show which face is selected.
- On `S`, crop the selected face with a modest margin, clamp the crop to image boundaries, validate the crop is non-empty, and save it as a JPEG at a useful resolution.
- Generate a timestamped filename such as `face_20261027_102015.jpg`; if the name already exists, add a numeric suffix rather than overwrite.
- On `Q` or Escape, release the camera and destroy OpenCV windows in a `finally` block.
- Handle camera-open/read errors with actionable messages and non-zero exit status.
- Never include stream credentials in logs or exception output. Sanitize displayed stream identifiers.

## Integration with the current face-swap workflow

The README currently instructs users to place a consenting person's photo at `input/person.jpg` and run the existing processor with `--source-face input/person.jpg`. Add a section explaining the optional capture workflow:

1. Start capture: `python -m src.capture_face --camera 0`
2. Press `S` only when the intended consenting person is visible and the selected face is correct.
3. Press `Q` to quit.
4. Inspect the saved JPEG before using it.
5. If appropriate and authorized, use it explicitly in a later run:
   `python -m src.process_video --video input/test_clip.mp4 --source-face input/captured_faces/face_YYYYMMDD_HHMMSS.jpg --model models/inswapper_128.onnx --output output/test_result.mp4 --device cpu`

Do not change the existing default input photo or existing face-swap behavior. Do not automatically launch the processor after capture.

## Dependencies and compatibility

- Prefer dependencies already in `requirements.txt`.
- Do not add a new face-recognition library if OpenCV and the existing detector are sufficient.
- Keep CPU-only operation and offline behavior.
- Follow the repository's Python version expectations and pinned NumPy compatibility.
- If any dependency change is necessary, document why and update the relevant tests.
- Do not add large model weights to Git; keep model paths in `.gitignore`.

## Tests and quality checks

Add unit/integration tests using mocks or synthetic frames so tests do not require a physical camera or real face model. At minimum verify:
- Camera fails to open -> clear error and non-zero exit.
- Frame read fails -> clean shutdown and error.
- No detected face -> no file saved.
- Pressing `S` with a valid detection -> valid JPEG is written and can be reopened by OpenCV.
- Crop coordinates are clamped safely at each image edge.
- Output directory is created when absent.
- Existing file is never overwritten.
- Stream URL credentials are not written to logs.
- `Q`/Escape always releases the capture object and closes windows.

Run the repository's existing checks as well, including:
- `python scripts/check_environment.py`
- `python scripts/selftest_pipeline.py`
- The new capture tests.

If a check requires missing authorized model files or physical hardware, report that clearly rather than downloading weights or claiming the test passed.

## Documentation

Update README.md with:
- Purpose and consent/privacy behavior.
- Supported camera input modes.
- Installation requirements (no unnecessary new packages).
- CLI examples for webcam and stream URL.
- Key controls (`S`, `Q`, Escape).
- Output location and filename format.
- Troubleshooting for camera permissions, unavailable streams, missing model weights, and no-face detection.
- A reminder that captured images are not automatically sent to the swapper and must be selected explicitly.
- Model licensing caveat already present in the README; do not weaken or remove it.

## Acceptance criteria

- Existing prerecorded-video workflow continues to behave exactly as before.
- New utility is opt-in and does not run during normal face swapping.
- No hidden/automatic capture: saving requires an explicit operator keypress.
- Images remain local; no uploads, telemetry, or automatic model downloads.
- No automatic identity recognition or matching.
- Captured JPEG is usable as a manually selected source photo after the operator reviews it.
- Tests cover error paths, privacy requirements, and cleanup.
- All changes are documented and summarized with exact files changed and commands/tests run.
