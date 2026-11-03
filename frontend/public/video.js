"use strict";

const videoElement = document.querySelector("#live-video");
const statusElement = document.querySelector("#video-status");
const params = new URLSearchParams(window.location.search);
const selectedVideo = params.get("file") || localStorage.getItem("deepfake-selected-video") || "movie.mp4";
const experienceMode = params.get("experience") === "1";
const autoplayResult = params.get("autoplayResult") !== "false";
const transitionAtParameter = params.get("startAt");
const transitionAt = Number(transitionAtParameter);
const endAt = Number(params.get("endAt"));
const transitionEnabled = experienceMode && transitionAtParameter !== null
  && Number.isFinite(transitionAt) && transitionAt >= 0;
let lastPreviewUrl = "";
let lastStatusState = null;
let sourcePlaybackEnded = false;
let transitionWaiting = false;
let outputIsReady = false;
let transitionInProgress = false;
let switchedToOutput = false;
let segmentMode = false;

function setStatus(message) {
  if (statusElement.textContent !== message) {
    statusElement.textContent = message;
  }
}

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
    setStatus("Press play to start the film.");
  });
}

async function switchToProcessedFilm() {
  if (switchedToOutput || transitionInProgress || !outputIsReady) {
    return;
  }
  switchedToOutput = true;
  transitionInProgress = true;
  const resumePlayback = !videoElement.paused || transitionWaiting;
  transitionWaiting = false;
  const cutTime = transitionAt;
  setSource(segmentMode ? `/api/segment?run=${Date.now()}` : `/api/output?run=${Date.now()}`);
  const onMetadata = () => {
    videoElement.removeEventListener("loadedmetadata", onMetadata);
    if (segmentMode) {
      videoElement.currentTime = 0;
      setStatus("Switched to the processed scene; the deepfaked scene is playing.");
    } else {
      videoElement.currentTime = Math.min(cutTime, Math.max(videoElement.duration - 0.05, 0));
      setStatus("Switched to the processed scene; the stitched film is playing.");
    }
    if (autoplayResult && resumePlayback) {
      playSource();
    } else if (autoplayResult && !experienceMode) {
      playSource();
    } else if (!autoplayResult) {
      setStatus("The processed scene is ready at the cut. Press play to continue.");
    }
    transitionInProgress = false;
  };
  videoElement.addEventListener("loadedmetadata", onMetadata);
  if (videoElement.readyState >= HTMLMediaElement.HAVE_METADATA) {
    onMetadata();
  }
}

function returnToOriginalFilm() {
  setSource(`/api/video?file=${encodeURIComponent(selectedVideo)}&run=${Date.now()}`);
  const onMetadata = () => {
    videoElement.removeEventListener("loadedmetadata", onMetadata);
    videoElement.currentTime = Math.min(endAt, Math.max(videoElement.duration - 0.05, 0));
    setStatus("Back to the original film after the processed scene.");
    playSource();
  };
  videoElement.addEventListener("loadedmetadata", onMetadata);
}

function checkTransitionBoundary() {
  if (!experienceMode || !transitionEnabled || switchedToOutput || transitionInProgress
      || lastPreviewUrl.includes("/api/output")) {
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
    setStatus("The selected scene starts here. Holding at the cut until the processed scene is ready…");
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
      segmentMode = Boolean(status.segmentReady) && Number.isFinite(endAt) && endAt > transitionAt;
      outputIsReady = segmentMode || !status.stitching;
      if (!outputIsReady && !(experienceMode && transitionEnabled)) {
        setStatus("Building the complete film…");
        lastStatusState = nextState;
        return;
      }
      if (experienceMode && transitionEnabled) {
        if (!lastPreviewUrl) {
          setSource(`/api/video?file=${encodeURIComponent(selectedVideo)}`);
          playSource();
        }
        if (switchedToOutput) {
          // The processed film is already loaded; reloading it here would restart it at the cut.
        } else if (transitionWaiting || videoElement.currentTime + 0.05 >= transitionAt) {
          await switchToProcessedFilm();
        } else {
          setStatus(`Playing the original film until ${transitionAt}s; the processed scene is ready.`);
        }
      } else if (!experienceMode || sourcePlaybackEnded) {
        const outputIsCurrent = lastPreviewUrl.includes("/api/output");
        if (!outputIsCurrent) {
          setSource(`/api/output?run=${Date.now()}`);
        }
        setStatus("The complete stitched film is ready.");
        if (autoplayResult && !outputIsCurrent) {
          playSource();
        }
      } else {
        if (!lastPreviewUrl) {
          setSource(`/api/video?file=${encodeURIComponent(selectedVideo)}`);
          playSource();
        }
        setStatus("Render complete. The finished film will start after this playback ends.");
      }
      lastStatusState = nextState;
      return;
    }

    if (nextState === "failed") {
      if (transitionWaiting) {
        transitionWaiting = false;
        playSource();
      }
      setStatus("Processing failed. The source film is still available; check the setup page log.");
      lastStatusState = nextState;
      return;
    }

    if (nextState === "running") {
      if (lastStatusState !== "running" && !lastPreviewUrl.includes("/api/video")) {
        setSource(`/api/video?file=${encodeURIComponent(selectedVideo)}`);
        playSource();
      }
      setStatus(experienceMode
        ? `Playing the original film. It will switch to the processed scene at ${transitionEnabled ? `${transitionAt}s` : "the selected scene"} once ready.`
        : "Processing selected frames; previewing the source clip in real time.");
      lastStatusState = nextState;
      return;
    }

    if (lastStatusState !== "idle" && !lastPreviewUrl) {
      setSource(`/api/video?file=${encodeURIComponent(selectedVideo)}`);
    }
    if (lastStatusState !== "idle") {
      playSource();
    }
    setStatus(experienceMode
      ? "Your film is ready to play. Processing starts when the setup page launches the experience."
      : "Previewing the selected source video.");
    lastStatusState = "idle";
  } catch (error) {
    setStatus(error.message);
  }
}

localStorage.setItem("deepfake-selected-video", selectedVideo);
videoElement.addEventListener("error", () => {
  if (outputIsReady && experienceMode && transitionEnabled
      && (lastPreviewUrl.includes("/api/output") || lastPreviewUrl.includes("/api/segment"))) {
    transitionWaiting = false;
    transitionInProgress = false;
    switchedToOutput = false;
    setSource(`/api/video?file=${encodeURIComponent(selectedVideo)}`);
    setStatus("The processed video could not be loaded yet. Retrying the handoff at the selected scene…");
    playSource();
    return;
  }
  setStatus("The selected clip could not be loaded. Pick an MP4 from the input folder.");
});
videoElement.addEventListener("timeupdate", checkTransitionBoundary);
videoElement.addEventListener("ended", () => {
  if (segmentMode && lastPreviewUrl.includes("/api/segment")) {
    returnToOriginalFilm();
    return;
  }
  sourcePlaybackEnded = true;
  if (experienceMode && transitionEnabled && outputIsReady) {
    void switchToProcessedFilm();
  } else {
    void refreshPreview();
  }
});

refreshPreview();
window.setInterval(refreshPreview, 1000);
