"use strict";

const startButton = document.querySelector("#start");
const cancelButton = document.querySelector("#cancel");
const overwriteInput = document.querySelector("#overwrite");
const statusElement = document.querySelector("#status");
const logsElement = document.querySelector("#logs");
const cameraStatus = document.querySelector("#camera-status");
const preview = document.querySelector("#preview");
let captureInProgress = false;
let cancelRequested = false;

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
    if (!captureInProgress) {
      statusElement.textContent = stateLabels[status.state] || status.state;
    }
    startButton.disabled = captureInProgress || status.state === "running";
    logsElement.textContent = status.logs.length ? status.logs.join("\n") : "No output yet.";
    logsElement.scrollTop = logsElement.scrollHeight;
  } catch (error) {
    if (!captureInProgress) {
      statusElement.textContent = error.message;
    }
  }
}

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

cancelButton.addEventListener("click", () => {
  cancelRequested = true;
  cameraStatus.textContent = "Cancelling camera capture…";
  cancelButton.disabled = true;
});

startButton.addEventListener("click", async () => {
  captureInProgress = true;
  cancelRequested = false;
  startButton.disabled = true;
  cancelButton.hidden = false;
  cancelButton.disabled = false;
  cameraStatus.textContent = "Requesting camera access…";
  statusElement.textContent = "Starting camera…";
  let stream;
  try {
    if (!navigator.mediaDevices?.getUserMedia) {
      throw new Error("Camera access requires HTTPS or localhost in a supported browser.");
    }
    stream = await navigator.mediaDevices.getUserMedia({
      audio: false,
      video: { facingMode: "user" },
    });
    if (cancelRequested) {
      return;
    }
    preview.srcObject = stream;
    await preview.play();

    for (let seconds = 3; seconds > 0; seconds -= 1) {
      cameraStatus.textContent = `Camera is on. Capturing in ${seconds}…`;
      statusElement.textContent = `Hold still — capturing in ${seconds}…`;
      await wait(1000);
      if (cancelRequested) {
        return;
      }
    }

    cameraStatus.textContent = "Capturing face…";
    const image = await captureJpeg(stream);
    stream = null;
    cancelButton.hidden = true;
    cameraStatus.textContent = "Snapshot ready. Starting the model…";
    const response = await fetch("/api/start", {
      method: "POST",
      headers: {
        "content-type": "image/jpeg",
        "x-overwrite": String(overwriteInput.checked),
      },
      body: image,
    });
    const result = await response.json();
    if (!response.ok) {
      throw new Error(result.error || `Could not start processing (${response.status}).`);
    }
    cameraStatus.textContent = "Snapshot saved locally. Camera is off.";
    statusElement.textContent = "Face captured — processing video…";
    captureInProgress = false;
    cancelButton.hidden = true;
    await updateStatus();
  } catch (error) {
    statusElement.textContent = error.message;
    cameraStatus.textContent = "Camera capture did not start.";
  } finally {
    if (stream) {
      stopCamera(stream);
    }
    if (cancelRequested) {
      cameraStatus.textContent = "Capture cancelled. Camera is off.";
    }
    captureInProgress = false;
    cancelButton.hidden = true;
    cancelButton.disabled = false;
    await updateStatus();
  }
});

updateStatus();
window.setInterval(updateStatus, 1000);
