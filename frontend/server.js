"use strict";

const http = require("node:http");
const { spawn } = require("node:child_process");
const fs = require("node:fs/promises");
const path = require("node:path");

const PROJECT_ROOT = path.resolve(__dirname, "..");
const MAX_LOG_LINES = 500;
const MAX_LOG_LINE_LENGTH = 2000;
const STATIC_FILES = new Map([
  ["/", ["index.html", "text/html; charset=utf-8"]],
  ["/index.html", ["index.html", "text/html; charset=utf-8"]],
  ["/app.js", ["app.js", "text/javascript; charset=utf-8"]],
  ["/styles.css", ["styles.css", "text/css; charset=utf-8"]],
]);

function createServer({
  spawnProcess = spawn,
  python = process.env.PYTHON || "python3",
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

  async function readStartRequest(request) {
    if (!request.headers["content-type"]?.startsWith("application/json")) {
      return { error: "Send the start options as application/json." };
    }

    let body = "";
    for await (const chunk of request) {
      body += chunk.toString("utf8");
      if (body.length > 1024) {
        return { error: "The start request is too large." };
      }
    }
    try {
      const options = JSON.parse(body || "{}");
      if (options === null || typeof options !== "object" || Array.isArray(options)
          || (options.overwrite !== undefined && typeof options.overwrite !== "boolean")) {
        return { error: "The overwrite option must be true or false." };
      }
      return { overwrite: options.overwrite === true };
    } catch {
      return { error: "The start request must contain valid JSON." };
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
      const options = await readStartRequest(request);
      if (options.error) {
        return sendJson(response, 400, { error: options.error });
      }

      run.state = "running";
      run.startedAt = new Date().toISOString();
      run.finishedAt = null;
      run.exitCode = null;
      run.logs = [];
      run.pendingOutput = { stdout: "", stderr: "" };

      const args = ["-m", "src.process_video"];
      if (options.overwrite) {
        args.push("--overwrite");
      }

      try {
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
