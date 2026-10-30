"use strict";

const startButton = document.querySelector("#start");
const overwriteInput = document.querySelector("#overwrite");
const statusElement = document.querySelector("#status");
const logsElement = document.querySelector("#logs");

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
    statusElement.textContent = stateLabels[status.state] || status.state;
    startButton.disabled = status.state === "running";
    logsElement.textContent = status.logs.length ? status.logs.join("\n") : "No output yet.";
    logsElement.scrollTop = logsElement.scrollHeight;
  } catch (error) {
    statusElement.textContent = error.message;
  }
}

startButton.addEventListener("click", async () => {
  startButton.disabled = true;
  statusElement.textContent = "Starting…";
  try {
    const response = await fetch("/api/start", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ overwrite: overwriteInput.checked }),
    });
    const result = await response.json();
    if (!response.ok) {
      throw new Error(result.error || `Could not start processing (${response.status}).`);
    }
    await updateStatus();
  } catch (error) {
    statusElement.textContent = error.message;
    startButton.disabled = false;
  }
});

updateStatus();
window.setInterval(updateStatus, 1000);
