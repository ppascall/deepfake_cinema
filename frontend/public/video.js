"use strict";

const videoElement = document.querySelector("#live-video");
const statusElement = document.querySelector("#video-status");
const params = new URLSearchParams(window.location.search);
const selectedVideo = params.get("file") || localStorage.getItem("deepfake-selected-video") || "movie.mp4";
const experienceMode = params.get("experience") === "1";
const autoplayResult = params.get("autoplayResult") !== "false";
let lastPreviewUrl = "";
let lastStatusState = null;
let sourcePlaybackEnded = false;

function setSource(src) {
  const normalizedSrc = new URL(src, window.location.href).toString();
  if (lastPreviewUrl === normalizedSrc) {
    return;
  }
  lastPreviewUrl = normalizedSrc;
  videoElement.src = src;
  videoElement.load();
}

function playSource() {
  videoElement.play().catch(() => {
    statusElement.textContent = "Press play to start the film.";
  });
}

async function refreshPreview() {
  try {
    const response = await fetch("/api/status", { cache: "no-store" });
    if (!response.ok) {
      throw new Error(`Status error (${response.status}).`);
    }
    const status = await response.json();
    const nextState = status.state;

    if (nextState === "succeeded") {
      if (!experienceMode || sourcePlaybackEnded) {
        const outputIsCurrent = lastPreviewUrl.includes("/api/output");
        if (!outputIsCurrent) {
          setSource(`/api/output?run=${Date.now()}`);
        }
        statusElement.textContent = "The complete stitched film is ready.";
        if (autoplayResult && !outputIsCurrent) {
          videoElement.play().catch(() => {
            statusElement.textContent = "The complete film is ready. Press play to watch it.";
          });
        }
      } else {
        if (!lastPreviewUrl) {
          setSource(`/api/video?file=${encodeURIComponent(selectedVideo)}`);
          playSource();
        }
        statusElement.textContent = "Render complete. The finished film will start after this playback ends.";
      }
      lastStatusState = nextState;
      return;
    }

    if (nextState === "failed") {
      statusElement.textContent = "Processing failed. The source film is still available; check the setup page log.";
      lastStatusState = nextState;
      return;
    }

    if (nextState === "running") {
      if (lastStatusState !== "running") {
        setSource(`/api/video?file=${encodeURIComponent(selectedVideo)}`);
        playSource();
      }
      statusElement.textContent = experienceMode
        ? "Playing the selected film. The completed version will replace this tab after playback."
        : "Processing selected frames; previewing the source clip in real time.";
      lastStatusState = nextState;
      return;
    }

    if (lastStatusState !== "idle") {
      setSource(`/api/video?file=${encodeURIComponent(selectedVideo)}`);
    }
    if (lastStatusState !== "idle") {
      playSource();
    }
    statusElement.textContent = experienceMode
      ? "Your film is ready to play. Processing starts when the setup page launches the experience."
      : "Previewing the selected source video.";
    lastStatusState = "idle";
  } catch (error) {
    statusElement.textContent = error.message;
  }
}

localStorage.setItem("deepfake-selected-video", selectedVideo);
videoElement.addEventListener("error", () => {
  statusElement.textContent = "The selected clip could not be loaded. Pick an MP4 from the input folder.";
});

videoElement.addEventListener("ended", () => {
  sourcePlaybackEnded = true;
  void refreshPreview();
});

refreshPreview();
window.setInterval(refreshPreview, 1000);
