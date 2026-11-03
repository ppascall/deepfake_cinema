"use strict";

const videoElement = document.querySelector("#live-video");
const statusElement = document.querySelector("#video-status");
const params = new URLSearchParams(window.location.search);
const selectedVideo = params.get("file") || localStorage.getItem("deepfake-selected-video") || "movie.mp4";
const experienceMode = params.get("experience") === "1";
const autoplayResult = params.get("autoplayResult") !== "false";
const transitionAtParameter = params.get("startAt");
const transitionAt = Number(transitionAtParameter);
const transitionEnabled = experienceMode && transitionAtParameter !== null
  && Number.isFinite(transitionAt) && transitionAt >= 0;
let lastPreviewUrl = "";
let lastStatusState = null;
let sourcePlaybackEnded = false;
let transitionWaiting = false;
let outputIsReady = false;
let transitionInProgress = false;

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

async function switchToProcessedFilm() {
  if (transitionInProgress || !outputIsReady) {
    return;
  }
  transitionInProgress = true;
  const resumePlayback = !videoElement.paused || transitionWaiting;
  transitionWaiting = false;
  const cutTime = transitionAt;
  setSource(`/api/output?run=${Date.now()}`);
  const onMetadata = () => {
    videoElement.removeEventListener("loadedmetadata", onMetadata);
    videoElement.currentTime = Math.min(cutTime, Math.max(videoElement.duration - 0.05, 0));
    statusElement.textContent = "Switched to the processed scene; the stitched film is playing.";
    if (autoplayResult && resumePlayback) {
      playSource();
    } else if (autoplayResult && !experienceMode) {
      playSource();
    } else if (!autoplayResult) {
      statusElement.textContent = "The processed scene is ready at the cut. Press play to continue.";
    }
    transitionInProgress = false;
  };
  videoElement.addEventListener("loadedmetadata", onMetadata);
  if (videoElement.readyState >= HTMLMediaElement.HAVE_METADATA) {
    onMetadata();
  }
}

function checkTransitionBoundary() {
  if (!experienceMode || !transitionEnabled || transitionInProgress || lastPreviewUrl.includes("/api/output")) {
    return;
  }
  if (videoElement.currentTime + 0.05 < transitionAt) {
    return;
  }
  videoElement.pause();
  videoElement.currentTime = transitionAt;
  if (outputIsReady) {
    void switchToProcessedFilm();
  } else {
    transitionWaiting = true;
    statusElement.textContent = "The selected scene starts here. Holding at the cut until the processed scene is ready…";
  }
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
      outputIsReady = true;
      if (experienceMode && transitionEnabled) {
        if (!lastPreviewUrl) {
          setSource(`/api/video?file=${encodeURIComponent(selectedVideo)}`);
          playSource();
        }
        if (transitionWaiting || videoElement.currentTime + 0.05 >= transitionAt) {
          await switchToProcessedFilm();
        } else {
          statusElement.textContent = `Playing the original film until ${transitionAt}s; the processed scene is ready.`;
        }
      } else if (!experienceMode || sourcePlaybackEnded) {
        const outputIsCurrent = lastPreviewUrl.includes("/api/output");
        if (!outputIsCurrent) {
          setSource(`/api/output?run=${Date.now()}`);
        }
        statusElement.textContent = "The complete stitched film is ready.";
        if (autoplayResult && !outputIsCurrent) {
          playSource();
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
      if (transitionWaiting) {
        transitionWaiting = false;
        playSource();
      }
      statusElement.textContent = "Processing failed. The source film is still available; check the setup page log.";
      lastStatusState = nextState;
      return;
    }

    if (nextState === "running") {
      if (lastStatusState !== "running" && !lastPreviewUrl.includes("/api/video")) {
        setSource(`/api/video?file=${encodeURIComponent(selectedVideo)}`);
        playSource();
      }
      statusElement.textContent = experienceMode
        ? `Playing the original film. It will switch to the processed scene at ${transitionEnabled ? `${transitionAt}s` : "the selected scene"} once ready.`
        : "Processing selected frames; previewing the source clip in real time.";
      lastStatusState = nextState;
      return;
    }

    if (lastStatusState !== "idle" && !lastPreviewUrl) {
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
videoElement.addEventListener("timeupdate", checkTransitionBoundary);
videoElement.addEventListener("ended", () => {
  sourcePlaybackEnded = true;
  if (experienceMode && transitionEnabled && outputIsReady) {
    void switchToProcessedFilm();
  } else {
    void refreshPreview();
  }
});

refreshPreview();
window.setInterval(refreshPreview, 1000);
