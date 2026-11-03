"use strict";

const startButton = document.querySelector("#start");
const resetButton = document.querySelector("#reset-run");
const uploadVideoButton = document.querySelector("#upload-video");
const captureFaceButton = document.querySelector("#capture-face");
const cancelButton = document.querySelector("#cancel");
const statusElement = document.querySelector("#status");
const logsElement = document.querySelector("#logs");
const cameraStatus = document.querySelector("#camera-status");
const preview = document.querySelector("#preview");
const sourceFaceSelect = document.querySelector("#source-face-select");
const existingFacePanel = document.querySelector("#existing-face-panel");
const liveFacePanel = document.querySelector("#live-face-panel");
const faceModeInputs = [...document.querySelectorAll('input[name="face-mode"]')];
const videoSelect = document.querySelector("#video-select");
const clipStartInput = document.querySelector("#clip-start");
const clipEndInput = document.querySelector("#clip-end");
const videoUploadInput = document.querySelector("#video-upload");
const showVideoInput = document.querySelector("#show-video");
const shutdownAfterJobInput = document.querySelector("#shutdown-after-job");
const participantConsent = document.querySelector("#participant-consent");
let captureInProgress = false;
let cancelRequested = false;
let processingState = "idle";
let liveSourceFace = "";
let lastReportedState = null;

const stateLabels = {
  idle: "Ready",
  running: "Processing…",
  succeeded: "Finished successfully",
  failed: "Processing failed",
};

async function updateStatus() {
  try {
    const response = await fetch("/api/status", { cache: "no-store" });
    if (!response.ok) {
      throw new Error(`Status request failed (${response.status}).`);
    }
    const status = await response.json();
    processingState = status.state;
    // Only announce a state when it changes, so validation or error messages are not overwritten
    // every second by a stale "Finished successfully" from the previous run.
    if (!captureInProgress && status.state !== lastReportedState) {
      if (status.state !== "idle" || lastReportedState !== null) {
        statusElement.textContent = stateLabels[status.state] || status.state;
      }
    }
    lastReportedState = status.state;
    resetButton.hidden = !["succeeded", "failed"].includes(status.state);
    const runInProgress = captureInProgress || status.state === "running";
    startButton.disabled = runInProgress;
    captureFaceButton.disabled = runInProgress || captureInProgress;
    uploadVideoButton.disabled = runInProgress;
    logsElement.textContent = status.logs.length ? status.logs.join("\n") : "No output yet.";
    logsElement.scrollTop = logsElement.scrollHeight;
  } catch (error) {
    if (!captureInProgress) {
      statusElement.textContent = error.message;
    }
  }
}

async function loadVideoOptions() {
  try {
    const response = await fetch("/api/videos", { cache: "no-store" });
    if (!response.ok) {
      throw new Error(`Could not load videos (${response.status}).`);
    }
    const result = await response.json();
    const items = result.videos || [];
    const firstName = items[0]?.name || "movie.mp4";
    const currentValue = videoSelect.value || localStorage.getItem("deepfake-selected-video") || firstName;
    videoSelect.innerHTML = "";
    for (const item of items) {
      const option = document.createElement("option");
      option.value = item.name;
      option.textContent = item.name;
      if (item.name === currentValue) {
        option.selected = true;
      }
      videoSelect.append(option);
    }
    if (!items.some((item) => item.name === currentValue)) {
      const fallback = document.createElement("option");
      fallback.value = firstName;
      fallback.textContent = firstName;
      fallback.selected = true;
      videoSelect.append(fallback);
    }
    localStorage.setItem("deepfake-selected-video", videoSelect.value || firstName);
  } catch (error) {
    const fallback = document.createElement("option");
    fallback.value = "movie.mp4";
    fallback.textContent = "movie.mp4";
    videoSelect.append(fallback);
    statusElement.textContent = error.message;
  }
}

async function loadSourceFaces() {
  try {
    const response = await fetch("/api/faces", { cache: "no-store" });
    if (!response.ok) {
      throw new Error(`Could not load source faces (${response.status}).`);
    }
    const result = await response.json();
    const faces = result.faces || [];
    const currentValue = sourceFaceSelect.value;
    sourceFaceSelect.innerHTML = "";
    for (const face of faces) {
      const option = document.createElement("option");
      option.value = face.sourceFace;
      option.textContent = face.name;
      sourceFaceSelect.append(option);
    }
    if (currentValue && faces.some((face) => face.sourceFace === currentValue)) {
      sourceFaceSelect.value = currentValue;
    }
    if (!faces.length) {
      const option = document.createElement("option");
      option.value = "";
      option.textContent = "No saved face photos";
      sourceFaceSelect.append(option);
    }
  } catch (error) {
    sourceFaceSelect.innerHTML = "";
    const option = document.createElement("option");
    option.value = "";
    option.textContent = "Could not load source faces";
    sourceFaceSelect.append(option);
    statusElement.textContent = error.message;
  }
}

function selectedFaceMode() {
  return faceModeInputs.find((input) => input.checked)?.value || "existing";
}

function updateFaceMode() {
  const liveMode = selectedFaceMode() === "live";
  existingFacePanel.hidden = liveMode;
  liveFacePanel.hidden = !liveMode;
  if (!liveMode) {
    preview.hidden = true;
  }
}

for (const input of faceModeInputs) {
  input.addEventListener("change", updateFaceMode);
}

videoSelect.addEventListener("change", () => {
  localStorage.setItem("deepfake-selected-video", videoSelect.value || "movie.mp4");
});

async function uploadSelectedVideo() {
  const file = videoUploadInput.files[0];
  if (!file) {
    statusElement.textContent = "Choose a video file before uploading it.";
    return;
  }
  try {
    statusElement.textContent = `Uploading ${file.name}…`;
    const response = await fetch("/api/upload", {
      method: "POST",
      headers: {
        "content-type": file.type || "video/mp4",
        "x-filename": file.name,
      },
      body: file,
    });
    const result = await response.json();
    if (!response.ok) {
      throw new Error(result.error || `Upload failed (${response.status}).`);
    }
    await loadVideoOptions();
    videoSelect.value = result.name;
    localStorage.setItem("deepfake-selected-video", result.name);
    statusElement.textContent = `${result.name} was added to the input folder.`;
    videoUploadInput.value = "";
  } catch (error) {
    statusElement.textContent = error.message;
  }
}

uploadVideoButton.addEventListener("click", uploadSelectedVideo);
captureFaceButton.addEventListener("click", async () => {
  if (!participantConsent.checked) {
    statusElement.textContent = "Confirm participant consent before capturing a face.";
    return;
  }
  captureInProgress = true;
  cancelRequested = false;
  captureFaceButton.disabled = true;
  cancelButton.hidden = false;
  cancelButton.disabled = false;
  preview.hidden = false;
  cameraStatus.textContent = "Requesting camera access…";
  statusElement.textContent = "Starting camera capture…";
  let stream;
  try {
    if (!navigator.mediaDevices?.getUserMedia) {
      throw new Error("Camera access requires HTTPS or localhost in a supported browser.");
    }
    stream = await navigator.mediaDevices.getUserMedia({ audio: false, video: { facingMode: "user" } });
    if (cancelRequested) {
      return;
    }
    preview.srcObject = stream;
    await preview.play();
    for (let seconds = 3; seconds > 0; seconds -= 1) {
      cameraStatus.textContent = `Camera is on. Capturing in ${seconds}…`;
      statusElement.textContent = `Capturing in ${seconds}…`;
      await wait(1000);
      if (cancelRequested) {
        return;
      }
    }
    cameraStatus.textContent = "Capturing face…";
    const image = await captureJpeg(stream);
    stream = null;
    const response = await fetch("/api/capture", {
      method: "POST",
      headers: { "content-type": "image/jpeg", "x-participant-consent": "true" },
      body: image,
    });
    const result = await response.json();
    if (!response.ok) {
      throw new Error(result.error || `Could not save the face (${response.status}).`);
    }
    await loadSourceFaces();
    sourceFaceSelect.value = result.sourceFace;
    liveSourceFace = result.sourceFace;
    cameraStatus.textContent = `${result.name} captured and saved locally.`;
    statusElement.textContent = "Live photo ready. Configure the film and scene, then start.";
  } catch (error) {
    statusElement.textContent = error.message;
    cameraStatus.textContent = "Camera capture failed.";
  } finally {
    if (stream) {
      stopCamera(stream);
    }
    preview.hidden = true;
    captureInProgress = false;
    cancelButton.hidden = true;
    cancelButton.disabled = false;
    captureFaceButton.disabled = processingState === "running";
    if (cancelRequested) {
      cameraStatus.textContent = "Capture cancelled. Camera is off.";
      statusElement.textContent = "Capture cancelled.";
    }
    await updateStatus();
  }
});

function stopCamera(stream) {
  for (const track of stream.getTracks()) {
    track.stop();
  }
  preview.srcObject = null;
}

function wait(milliseconds) {
  return new Promise((resolve) => window.setTimeout(resolve, milliseconds));
}

async function captureJpeg(stream) {
  const width = preview.videoWidth;
  const height = preview.videoHeight;
  if (!width || !height) {
    throw new Error("The camera did not provide a usable video frame.");
  }

  const canvas = document.createElement("canvas");
  canvas.width = width;
  canvas.height = height;
  const context = canvas.getContext("2d");
  if (!context) {
    throw new Error("Could not prepare an image canvas for the camera snapshot.");
  }
  context.drawImage(preview, 0, 0, width, height);
  stopCamera(stream);

  return new Promise((resolve, reject) => {
    canvas.toBlob((blob) => {
      if (blob) {
        resolve(blob);
      } else {
        reject(new Error("Could not capture a JPEG from the camera."));
      }
    }, "image/jpeg", 0.92);
  });
}

resetButton.addEventListener("click", async () => {
  resetButton.disabled = true;
  try {
    const response = await fetch("/api/reset", { method: "POST" });
    const result = await response.json().catch(() => ({}));
    if (!response.ok) {
      throw new Error(result.error || `Could not reset (${response.status}).`);
    }
    statusElement.textContent = "Ready for a new run.";
    await updateStatus();
  } catch (error) {
    statusElement.textContent = error.message;
  } finally {
    resetButton.disabled = false;
  }
});

cancelButton.addEventListener("click", () => {
  cancelRequested = true;
  cameraStatus.textContent = "Cancelling camera capture…";
  cancelButton.disabled = true;
});

startButton.addEventListener("click", async () => {
  if (!participantConsent.checked) {
    statusElement.textContent = "Confirm participant consent before starting the experience.";
    return;
  }
  const faceMode = selectedFaceMode();
  const sourceFace = faceMode === "live" ? liveSourceFace : sourceFaceSelect.value;
  if (!sourceFace) {
    statusElement.textContent = faceMode === "live"
      ? "Capture a live photo before starting the experience."
      : "Choose a saved face photo before starting the experience.";
    return;
  }
  const videoName = videoSelect.value || "movie.mp4";
  const startTime = Number.parseFloat(clipStartInput.value);
  const endTime = Number.parseFloat(clipEndInput.value);
  if (!Number.isFinite(startTime) || !Number.isFinite(endTime) || startTime < 0 || endTime <= startTime) {
    statusElement.textContent = "Set a valid scene range with an end later than its start.";
    return;
  }
  const playbackTab = window.open("about:blank", "_blank");
  if (!playbackTab) {
    statusElement.textContent = "Allow pop-ups for this page so the film can open in its own tab.";
    return;
  }
  playbackTab.document.title = "Preparing film playback";
  playbackTab.document.body.textContent = "Preparing your film…";
  startButton.disabled = true;
  uploadVideoButton.disabled = true;
  statusElement.textContent = `Preparing ${videoName}…`;
  try {
    const processResponse = await fetch("/api/start", {
      method: "POST",
      headers: { "content-type": "application/json", "x-requested-json": "true" },
      body: JSON.stringify({
        video: videoName,
        startTime,
        endTime,
        overwrite: false,
        sourceFace,
        stitchFullVideo: true,
        shutdownAfterJob: shutdownAfterJobInput.checked,
        participantConsent: true,
      }),
    });
    const processResult = await processResponse.json();
    if (!processResponse.ok) {
      throw new Error(processResult.error || `Could not start processing (${processResponse.status}).`);
    }
    localStorage.setItem("deepfake-selected-video", videoName);
    const playbackUrl = `/video?file=${encodeURIComponent(videoName)}&experience=1`
      + `&startAt=${encodeURIComponent(startTime)}&endAt=${encodeURIComponent(endTime)}`
      + `&autoplayResult=${showVideoInput.checked}`;
    playbackTab.location.href = playbackUrl;
    statusElement.textContent = `Playing ${videoName} in the new tab while the full film is prepared…`;
    await updateStatus();
  } catch (error) {
    playbackTab.close();
    statusElement.textContent = error.message;
    startButton.disabled = processingState === "running";
    uploadVideoButton.disabled = processingState === "running";
  }
});

loadVideoOptions();
loadSourceFaces();
updateFaceMode();
updateStatus();
window.setInterval(updateStatus, 1000);
