"use strict";

const http = require("node:http");
const { spawn } = require("node:child_process");
const fs = require("node:fs/promises");
const path = require("node:path");

const PROJECT_ROOT = path.resolve(__dirname, "..");
const MAX_LOG_LINES = 500;
const MAX_LOG_LINE_LENGTH = 2000;
const MAX_CAPTURE_BYTES = 5 * 1024 * 1024;
const DEFAULT_CAPTURE_DIRECTORY = path.join(PROJECT_ROOT, "output", "captured_faces");
const STATIC_FILES = new Map([
  ["/", ["index.html", "text/html; charset=utf-8"]],
  ["/index.html", ["index.html", "text/html; charset=utf-8"]],
  ["/app.js", ["app.js", "text/javascript; charset=utf-8"]],
  ["/styles.css", ["styles.css", "text/css; charset=utf-8"]],
]);

function createServer({
  spawnProcess = spawn,
  python = process.env.PYTHON || "python3",
  captureDirectory = DEFAULT_CAPTURE_DIRECTORY,
} = {}) {
  const run = {
    state: "idle",
    startedAt: null,
    finishedAt: null,
    exitCode: null,
    logs: [],
    child: null,
    pendingOutput: { stdout: "", stderr: "" },
  };

  function addLog(line) {
    const clipped = line.length > MAX_LOG_LINE_LENGTH
      ? `${line.slice(0, MAX_LOG_LINE_LENGTH)} [line truncated]`
      : line;
    run.logs.push(clipped);
    if (run.logs.length > MAX_LOG_LINES) {
      run.logs.splice(0, run.logs.length - MAX_LOG_LINES);
    }
  }

  function collectOutput(stream, chunk) {
    let text = run.pendingOutput[stream] + chunk.toString("utf8");
    const lines = text.split(/\r?\n/);
    run.pendingOutput[stream] = lines.pop() || "";

    for (const line of lines) {
      addLog(line);
    }

    if (run.pendingOutput[stream].length > MAX_LOG_LINE_LENGTH) {
      addLog(run.pendingOutput[stream]);
      run.pendingOutput[stream] = "";
    }
  }

  function finishRun(state, exitCode) {
    for (const stream of ["stdout", "stderr"]) {
      if (run.pendingOutput[stream]) {
        addLog(run.pendingOutput[stream]);
        run.pendingOutput[stream] = "";
      }
    }
    run.state = state;
    run.exitCode = exitCode;
    run.finishedAt = new Date().toISOString();
    run.child = null;
  }

  function sendJson(response, statusCode, data) {
    response.writeHead(statusCode, {
      "content-type": "application/json; charset=utf-8",
      "cache-control": "no-store",
    });
    response.end(JSON.stringify(data));
  }

  function statusSnapshot() {
    return {
      state: run.state,
      startedAt: run.startedAt,
      finishedAt: run.finishedAt,
      exitCode: run.exitCode,
      logs: run.logs,
    };
  }

  async function readCapture(request) {
    if (request.headers["content-type"]?.split(";")[0].trim().toLowerCase() !== "image/jpeg") {
      return { error: "Send the captured photo as a JPEG image.", statusCode: 415 };
    }

    if (request.headers["x-overwrite"] !== "true" && request.headers["x-overwrite"] !== "false") {
      return { error: "The overwrite option must be true or false.", statusCode: 400 };
    }

    const chunks = [];
    let size = 0;
    for await (const chunk of request) {
      size += chunk.length;
      if (size > MAX_CAPTURE_BYTES) {
        return { error: "The captured photo exceeds the 5 MB limit.", statusCode: 413 };
      }
      chunks.push(chunk);
    }
    const image = Buffer.concat(chunks);
    if (image.length < 4 || image[0] !== 0xff || image[1] !== 0xd8
        || image[image.length - 2] !== 0xff || image[image.length - 1] !== 0xd9) {
      return { error: "The camera did not provide a valid JPEG image.", statusCode: 400 };
    }
    return { image, overwrite: request.headers["x-overwrite"] === "true" };
  }

  async function saveCapture(image) {
    await fs.mkdir(captureDirectory, { recursive: true });
    const timestamp = new Date().toISOString()
      .replace(/[-:]/g, "")
      .replace(/\.\d{3}Z$/, "Z");
    for (let suffix = 0; ; suffix += 1) {
      const extra = suffix ? `_${suffix}` : "";
      const filePath = path.join(captureDirectory, `face_${timestamp}${extra}.jpg`);
      let file;
      try {
        file = await fs.open(filePath, "wx");
      } catch (error) {
        if (error.code === "EEXIST") {
          continue;
        }
        throw error;
      }
      try {
        await file.writeFile(image);
        await file.close();
        return filePath;
      } catch (error) {
        await file.close();
        await fs.unlink(filePath);
        throw error;
      }
    }
  }

  async function serveStatic(pathname, response) {
    const entry = STATIC_FILES.get(pathname);
    if (!entry) {
      return false;
    }
    const [fileName, contentType] = entry;
    try {
      const content = await fs.readFile(path.join(__dirname, "public", fileName));
      response.writeHead(200, {
        "content-type": contentType,
        "cache-control": "no-cache",
        "x-content-type-options": "nosniff",
      });
      response.end(content);
    } catch (error) {
      console.error(`Could not read frontend asset ${fileName}:`, error);
      sendJson(response, 500, { error: "The web interface could not be loaded." });
    }
    return true;
  }

  const server = http.createServer(async (request, response) => {
    const pathname = new URL(request.url, "http://localhost").pathname;

    if (request.method === "GET" && pathname === "/api/status") {
      return sendJson(response, 200, statusSnapshot());
    }

    if (request.method === "POST" && pathname === "/api/start") {
      if (run.state === "running") {
        return sendJson(response, 409, { error: "Processing is already running." });
      }
      const capture = await readCapture(request);
      if (capture.error) {
        return sendJson(response, capture.statusCode, { error: capture.error });
      }

      run.state = "running";
      run.startedAt = new Date().toISOString();
      run.finishedAt = null;
      run.exitCode = null;
      run.logs = [];
      run.pendingOutput = { stdout: "", stderr: "" };

      try {
        const sourceFace = await saveCapture(capture.image);
        addLog(`Captured face saved to ${sourceFace}`);
        const args = ["-m", "src.process_video", "--source-face", sourceFace];
        if (capture.overwrite) {
          args.push("--overwrite");
        }
        const child = spawnProcess(python, args, {
          cwd: PROJECT_ROOT,
          env: { ...process.env, PYTHONUNBUFFERED: "1" },
          stdio: ["ignore", "pipe", "pipe"],
        });
        run.child = child;
        child.stdout.on("data", (chunk) => collectOutput("stdout", chunk));
        child.stderr.on("data", (chunk) => collectOutput("stderr", chunk));
        child.on("error", (error) => {
          addLog(`Could not start the processor: ${error.message}`);
          if (run.state === "running") {
            finishRun("failed", 1);
          }
        });
        child.on("close", (code) => {
          if (run.state === "running") {
            finishRun(code === 0 ? "succeeded" : "failed", code ?? 1);
          }
        });
        return sendJson(response, 202, statusSnapshot());
      } catch (error) {
        addLog(`Could not start the processor: ${error.message}`);
        finishRun("failed", 1);
        return sendJson(response, 500, statusSnapshot());
      }
    }

    if (request.method === "GET" && await serveStatic(pathname, response)) {
      return;
    }

    if (pathname.startsWith("/api/")) {
      return sendJson(response, 404, { error: "API endpoint not found." });
    }
    response.writeHead(404, { "content-type": "text/plain; charset=utf-8" });
    response.end("Not found");
  });

  return server;
}

if (require.main === module) {
  const port = Number.parseInt(process.env.PORT || "3000", 10);
  if (!Number.isInteger(port) || port < 1 || port > 65535) {
    console.error("PORT must be an integer between 1 and 65535.");
    process.exitCode = 1;
  } else {
    const server = createServer();
    server.listen(port, "0.0.0.0", () => {
      console.log(`Deepfake Cinema web UI listening on http://0.0.0.0:${port}`);
    });
    server.on("error", (error) => {
      console.error("Could not start the web server:", error.message);
      process.exitCode = 1;
    });
  }
}

module.exports = { createServer };
