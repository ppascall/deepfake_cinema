"use strict";

const http = require("node:http");
const { spawn } = require("node:child_process");
const { createReadStream } = require("node:fs");
const fs = require("node:fs/promises");
const path = require("node:path");

const PROJECT_ROOT = path.resolve(__dirname, "..");
const MAX_LOG_LINES = 500;
const MAX_LOG_LINE_LENGTH = 2000;
const MAX_CAPTURE_BYTES = 5 * 1024 * 1024;
const DEFAULT_CAPTURE_DIRECTORY = path.join(PROJECT_ROOT, "output", "captured_faces");
const DEFAULT_OUTPUT_VIDEO = path.join(PROJECT_ROOT, "output", "final_movie.mp4");
const DEFAULT_INPUT_DIRECTORY = path.join(PROJECT_ROOT, "input");
const OUTPUT_VIDEO_PREFIX = "final_movie";
const STATIC_FILES = new Map([
  ["/", ["index.html", "text/html; charset=utf-8"]],
  ["/index.html", ["index.html", "text/html; charset=utf-8"]],
  ["/video", ["video.html", "text/html; charset=utf-8"]],
  ["/video.html", ["video.html", "text/html; charset=utf-8"]],
  ["/app.js", ["app.js", "text/javascript; charset=utf-8"]],
  ["/video.js", ["video.js", "text/javascript; charset=utf-8"]],
  ["/styles.css", ["styles.css", "text/css; charset=utf-8"]],
]);

function createServer({
  spawnProcess = spawn,
  spawnFFmpeg = spawn,
  probeVideoDuration = getVideoDuration,
  python = process.env.PYTHON || "python3",
  captureDirectory = DEFAULT_CAPTURE_DIRECTORY,
  inputDirectory = DEFAULT_INPUT_DIRECTORY,
  outputVideo = DEFAULT_OUTPUT_VIDEO,
} = {}) {
  let currentOutputVideo = outputVideo;
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

  function createTimestampedOutputPath(basePath = currentOutputVideo || outputVideo) {
    const directory = path.dirname(basePath);
    const extension = path.extname(basePath);
    const baseName = path.basename(basePath, extension).replace(/_\d{8}_\d{6}$/u, "") || OUTPUT_VIDEO_PREFIX;
    const now = new Date();
    const stamp = [
      now.getFullYear(),
      String(now.getMonth() + 1).padStart(2, "0"),
      String(now.getDate()).padStart(2, "0"),
      "_",
      String(now.getHours()).padStart(2, "0"),
      String(now.getMinutes()).padStart(2, "0"),
      String(now.getSeconds()).padStart(2, "0"),
    ].join("");
    return path.join(directory, `${baseName}_${stamp}${extension}`);
  }

  async function findNewestOutputVideo() {
    const directory = path.dirname(currentOutputVideo || outputVideo);
    try {
      const entries = await fs.readdir(directory, { withFileTypes: true });
      const videos = [];
      for (const entry of entries) {
        if (!entry.isFile() || !/\.mp4$/i.test(entry.name)) {
          continue;
        }
        const candidate = path.join(directory, entry.name);
        try {
          const stats = await fs.stat(candidate);
          if (stats.isFile()) {
            videos.push({ name: entry.name, path: candidate, mtimeMs: stats.mtimeMs });
          }
        } catch (error) {
          // ignore files that disappear between the directory scan and stat
        }
      }
      if (!videos.length) {
        return currentOutputVideo;
      }
      videos.sort((left, right) => right.mtimeMs - left.mtimeMs);
      return videos[0]?.path || currentOutputVideo;
    } catch (error) {
      return currentOutputVideo;
    }
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

  async function readCapture(request, { requireOverwrite = true } = {}) {
    if (request.headers["content-type"]?.split(";")[0].trim().toLowerCase() !== "image/jpeg") {
      return { error: "Send the captured photo as a JPEG image.", statusCode: 415 };
    }

    if (requireOverwrite && request.headers["x-overwrite"] !== "true" && request.headers["x-overwrite"] !== "false") {
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

  async function listSourceFaces() {
    const faces = [];
    const defaultFace = path.join(inputDirectory, "person.jpg");
    try {
      const stats = await fs.stat(defaultFace);
      if (stats.isFile()) {
        faces.push({ name: "person.jpg", sourceFace: "person.jpg", modifiedAt: stats.mtime.toISOString() });
      }
    } catch (error) {
      if (error.code !== "ENOENT") {
        throw error;
      }
    }

    await fs.mkdir(captureDirectory, { recursive: true });
    const entries = await fs.readdir(captureDirectory, { withFileTypes: true });
    const captures = [];
    for (const entry of entries) {
      if (!entry.isFile() || !/^face_.*\.jpg$/i.test(entry.name)) {
        continue;
      }
      const stats = await fs.stat(path.join(captureDirectory, entry.name));
      captures.push({
        name: `${entry.name} (camera capture)`,
        sourceFace: `capture:${entry.name}`,
        modifiedAt: stats.mtime.toISOString(),
      });
    }
    captures.sort((left, right) => right.modifiedAt.localeCompare(left.modifiedAt));
    return [...captures, ...faces];
  }

  async function resolveSourceFace(sourceFace) {
    let filePath;
    if (sourceFace === "person.jpg") {
      filePath = path.join(inputDirectory, "person.jpg");
    } else if (typeof sourceFace === "string" && sourceFace.startsWith("capture:")) {
      const fileName = sourceFace.slice("capture:".length);
      if (path.basename(fileName) !== fileName || !/^face_.*\.jpg$/i.test(fileName)) {
        throw new Error("The selected source face is invalid. Capture a face again and select it.");
      }
      filePath = path.join(captureDirectory, fileName);
    } else {
      throw new Error("Choose a saved source face or capture one first.");
    }

    try {
      const stats = await fs.stat(filePath);
      if (!stats.isFile()) {
        throw new Error("The selected source face is not a file.");
      }
    } catch (error) {
      if (error.code === "ENOENT") {
        throw new Error("Source face photo not found. Capture a face first or add input/person.jpg.");
      }
      throw error;
    }
    return filePath;
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

  async function streamVideoFile(response, filePath, fileName, request) {
    let fileInfo;
    try {
      fileInfo = await fs.stat(filePath);
      if (!fileInfo.isFile()) {
        response.writeHead(404, { "cache-control": "no-store" });
        response.end();
        return;
      }
    } catch (error) {
      if (error.code === "ENOENT") {
        response.writeHead(404, { "cache-control": "no-store" });
        response.end();
        return;
      }
      console.error(`Could not inspect the video ${fileName}:`, error);
      sendJson(response, 500, { error: `The video ${fileName} could not be opened.` });
      return;
    }

    const headers = {
      "content-type": "video/mp4",
      "accept-ranges": "bytes",
      "cache-control": "no-store",
      "x-content-type-options": "nosniff",
    };
    const range = request.headers.range;
    let start = 0;
    let end = fileInfo.size - 1;
    let statusCode = 200;
    if (range) {
      const match = /^bytes=(\d*)-(\d*)$/.exec(range);
      if (!match || (!match[1] && !match[2])) {
        response.writeHead(416, { ...headers, "content-range": `bytes */${fileInfo.size}` });
        response.end();
        return;
      }
      if (!match[1]) {
        const suffixLength = Number(match[2]);
        start = Math.max(fileInfo.size - suffixLength, 0);
      } else {
        start = Number(match[1]);
        if (match[2]) {
          end = Number(match[2]);
        }
      }
      if (start >= fileInfo.size || end < start) {
        response.writeHead(416, { ...headers, "content-range": `bytes */${fileInfo.size}` });
        response.end();
        return;
      }
      end = Math.min(end, fileInfo.size - 1);
      statusCode = 206;
      headers["content-range"] = `bytes ${start}-${end}/${fileInfo.size}`;
    }

    headers["content-length"] = end - start + 1;
    response.writeHead(statusCode, headers);
    const videoStream = createReadStream(filePath, { start, end });
    videoStream.on("error", (error) => {
      console.error(`Could not stream the video ${fileName}:`, error);
      response.destroy(error);
    });
    videoStream.pipe(response);
  }

  async function serveOutputVideo(request, response) {
    if (run.state !== "succeeded") {
      response.writeHead(404, { "cache-control": "no-store" });
      response.end();
      return;
    }
    const newestOutput = await findNewestOutputVideo();
    return streamVideoFile(response, newestOutput, "processed output", request);
  }

  async function serveInputVideo(request, response) {
    const url = new URL(request.url, "http://localhost");
    const fileName = url.searchParams.get("file");
    if (!fileName) {
      return sendJson(response, 400, { error: "A video file name is required." });
    }
    const sanitizedName = path.basename(fileName);
    const resolvedPath = path.resolve(inputDirectory, sanitizedName);
    if (!resolvedPath.startsWith(path.resolve(inputDirectory))) {
      return sendJson(response, 403, { error: "The requested video path is invalid." });
    }
    return streamVideoFile(response, resolvedPath, sanitizedName, request);
  }

  async function listInputVideos() {
    await fs.mkdir(inputDirectory, { recursive: true });
    const entries = await fs.readdir(inputDirectory, { withFileTypes: true });
    const videos = [];
    for (const entry of entries) {
      if (!entry.isFile()) {
        continue;
      }
      if (/\.(mp4|mov|m4v|webm|avi)$/i.test(entry.name)) {
        const stats = await fs.stat(path.join(inputDirectory, entry.name));
        videos.push({
          name: entry.name,
          size: stats.size,
          modifiedAt: stats.mtime.toISOString(),
        });
      }
    }
    videos.sort((left, right) => left.name.localeCompare(right.name));
    return videos;
  }

  async function readJsonBody(request) {
    const chunks = [];
    for await (const chunk of request) {
      chunks.push(chunk);
    }
    const raw = Buffer.concat(chunks).toString("utf8");
    if (!raw) {
      return null;
    }
    try {
      return JSON.parse(raw);
    } catch (error) {
      throw new Error("The request body was not valid JSON.");
    }
  }

  async function prepareClipForRange(videoPath, startTime, endTime, { strict = false } = {}) {
    const start = Number(startTime);
    const end = Number(endTime);
    if (!Number.isFinite(start) || !Number.isFinite(end) || end <= start) {
      return null;
    }
    const duration = end - start;
    const clipDirectory = path.join(path.dirname(currentOutputVideo || outputVideo), "clips");
    await fs.mkdir(clipDirectory, { recursive: true });
    const baseName = path.basename(videoPath, path.extname(videoPath));
    const clipPath = path.join(clipDirectory, `${baseName}_${start.toFixed(2)}_${end.toFixed(2)}.mp4`);
    try {
      await new Promise((resolve, reject) => {
        const ffmpeg = spawnFFmpeg("ffmpeg", [
          "-hide_banner",
          "-loglevel",
          "error",
          "-y",
          "-ss",
          String(start),
          "-i",
          videoPath,
          "-t",
          String(duration),
          "-map",
          "0:v:0",
          "-map",
          "0:a:0?",
          "-c:v",
          "libx264",
          "-preset",
          "veryfast",
          "-crf",
          "23",
          "-pix_fmt",
          "yuv420p",
          "-c:a",
          "aac",
          "-movflags",
          "+faststart",
          clipPath,
        ]);
        ffmpeg.on("error", reject);
        ffmpeg.on("close", (code) => {
          if (code === 0) {
            resolve();
          } else {
            reject(new Error(`FFmpeg exited with code ${code} while creating a clip.`));
          }
        });
      });
      return clipPath;
    } catch (error) {
      if (strict) {
        throw error;
      }
      addLog(`Could not create a trimmed clip for ${path.basename(videoPath)}; using the full source video instead.`);
      return videoPath;
    }
  }

  async function stitchProcessedRange(sourcePath, processedPath, outputPath, startTime, endTime) {
    const duration = await probeVideoDuration(sourcePath);
    const segments = [];
    const filters = [];
    let segmentIndex = 0;
    if (startTime > 0) {
      const label = `segment${segmentIndex++}`;
      filters.push(`[0:v]trim=start=0:end=${startTime},setpts=PTS-STARTPTS,scale=-2:720,fps=20,setsar=1,format=yuv420p[${label}]`);
      segments.push(`[${label}]`);
    }
    const processedLabel = `segment${segmentIndex++}`;
    filters.push(`[1:v]setpts=PTS-STARTPTS,scale=-2:720,fps=20,setsar=1,format=yuv420p[${processedLabel}]`);
    segments.push(`[${processedLabel}]`);
    if (duration > endTime) {
      const label = `segment${segmentIndex++}`;
      filters.push(`[0:v]trim=start=${endTime}:end=${duration},setpts=PTS-STARTPTS,scale=-2:720,fps=20,setsar=1,format=yuv420p[${label}]`);
      segments.push(`[${label}]`);
    }

    const outputLabel = segmentIndex === 1 ? segments[0] : "[stitched]";
    if (segmentIndex > 1) {
      filters.push(`${segments.join("")}concat=n=${segmentIndex}:v=1:a=0[stitched]`);
    }
    const args = ["-hide_banner", "-loglevel", "error", "-y", "-i", sourcePath, "-i", processedPath,
      "-filter_complex", filters.join(";"), "-map", outputLabel, "-map", "0:a?",
      "-c:v", "libx264", "-preset", "veryfast", "-crf", "18", "-pix_fmt", "yuv420p",
      "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", outputPath];
    await new Promise((resolve, reject) => {
      const ffmpeg = spawnFFmpeg("ffmpeg", args);
      let stderr = "";
      ffmpeg.stderr?.on("data", (chunk) => {
        stderr = `${stderr}${chunk.toString("utf8")}`.slice(-2000);
      });
      ffmpeg.on("error", reject);
      ffmpeg.on("close", (code) => {
        if (code === 0) {
          resolve();
        } else {
          reject(new Error(`FFmpeg exited with code ${code} while stitching the full video. ${stderr.trim()}`));
        }
      });
    });
  }

  const server = http.createServer(async (request, response) => {
    const url = new URL(request.url, "http://localhost");
    const pathname = url.pathname;

    if (request.method === "GET" && pathname === "/api/status") {
      return sendJson(response, 200, statusSnapshot());
    }

    if (request.method === "GET" && pathname === "/api/videos") {
      return sendJson(response, 200, { videos: await listInputVideos() });
    }

    if (request.method === "GET" && pathname === "/api/faces") {
      return sendJson(response, 200, { faces: await listSourceFaces() });
    }

    if (request.method === "POST" && pathname === "/api/capture") {
      if (request.headers["x-participant-consent"] !== "true") {
        return sendJson(response, 403, { error: "Participant consent is required before saving a face photo." });
      }
      const capture = await readCapture(request, { requireOverwrite: false });
      if (capture.error) {
        return sendJson(response, capture.statusCode, { error: capture.error });
      }
      const filePath = await saveCapture(capture.image);
      return sendJson(response, 201, {
        name: path.basename(filePath),
        sourceFace: `capture:${path.basename(filePath)}`,
      });
    }

    if (request.method === "GET" && pathname === "/api/video") {
      return serveInputVideo(request, response);
    }

    if (request.method === "GET" && pathname === "/api/output") {
      return serveOutputVideo(request, response);
    }

    if (request.method === "POST" && pathname === "/api/upload") {
      const fileName = request.headers["x-filename"] || "uploaded_video.mp4";
      const safeFileName = path.basename(fileName);
      if (!safeFileName || !/\.(mp4|mov|m4v|webm|avi)$/i.test(safeFileName)) {
        return sendJson(response, 400, { error: "Only common video files can be uploaded here." });
      }
      await fs.mkdir(inputDirectory, { recursive: true });
      const chunks = [];
      for await (const chunk of request) {
        chunks.push(chunk);
      }
      const filePath = path.join(inputDirectory, safeFileName);
      await fs.writeFile(filePath, Buffer.concat(chunks));
      return sendJson(response, 201, { name: safeFileName, path: filePath });
    }

    if (request.method === "POST" && pathname === "/api/start") {
      if (run.state === "running") {
        return sendJson(response, 409, { error: "Processing is already running." });
      }

      const startProcessing = async ({ videoPath, sourceFacePath, startTime, endTime, overwrite, stitchFullVideo = false }) => {
        let selectedVideo = videoPath || path.join(PROJECT_ROOT, "input", "movie.mp4");
        const start = Number(startTime);
        const end = Number(endTime);
        let clipDuration = null;
        currentOutputVideo = createTimestampedOutputPath();

        if (stitchFullVideo) {
          if (!Number.isFinite(start) || !Number.isFinite(end) || start < 0 || end <= start) {
            throw new Error("Choose a valid start and end time for the scene to alter.");
          }
          const sourceDuration = await probeVideoDuration(videoPath);
          if (start >= sourceDuration) {
            throw new Error("The selected scene starts after the end of the video.");
          }
        }

        if (Number.isFinite(start) && Number.isFinite(end) && end > start) {
          const clipPath = await prepareClipForRange(selectedVideo, start, end, { strict: stitchFullVideo });
          if (!clipPath) {
            throw new Error("The selected time range was invalid.");
          }
          selectedVideo = clipPath;
          clipDuration = end - start;
          addLog(`Deepfake range: ${start}s to ${end}s -> ${path.basename(selectedVideo)}`);
        }

        run.state = "running";
        run.startedAt = new Date().toISOString();
        run.finishedAt = null;
        run.exitCode = null;
        run.logs = [];
        run.pendingOutput = { stdout: "", stderr: "" };

        try {
          const processorOutput = stitchFullVideo
            ? path.join(path.dirname(currentOutputVideo), `.${path.basename(currentOutputVideo, ".mp4")}.segment.mp4`)
            : currentOutputVideo;
          const args = ["-m", "src.process_video", "--source-face", sourceFacePath, "--video", selectedVideo];
          args.push("--output", processorOutput);
          if (overwrite) {
            args.push("--overwrite");
          }
          if (clipDuration !== null && clipDuration > 0) {
            args.push("--max-seconds", String(clipDuration));
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
              if (code === 0 && stitchFullVideo) {
                void stitchProcessedRange(videoPath, processorOutput, currentOutputVideo, start, end)
                  .then(() => fs.unlink(processorOutput).catch(() => {}))
                  .then(() => finishRun("succeeded", 0))
                  .catch((error) => {
                    void fs.unlink(processorOutput).catch(() => {});
                    addLog(`Could not stitch the processed range into the full video: ${error.message}`);
                    finishRun("failed", 1);
                  });
              } else {
                finishRun(code === 0 ? "succeeded" : "failed", code ?? 1);
              }
            }
          });
          return sendJson(response, 202, statusSnapshot());
        } catch (error) {
          addLog(`Could not start the processor: ${error.message}`);
          finishRun("failed", 1);
          return sendJson(response, 500, statusSnapshot());
        }
      };

      const contentType = request.headers["content-type"]?.split(";")[0].trim().toLowerCase();
      const hasJsonRequest = contentType === "application/json" || request.headers["x-requested-json"] === "true";
      if (hasJsonRequest) {
        try {
          const config = await readJsonBody(request);
          if (!config || typeof config !== "object") {
            return sendJson(response, 400, { error: "The JSON request must contain a configuration object." });
          }
          if (config.participantConsent !== true) {
            return sendJson(response, 403, { error: "Participant consent is required before processing a face." });
          }
          const sourceFacePath = await resolveSourceFace(config.sourceFace || config.source_face);
          const requestedVideo = config.video || "movie.mp4";
          const videoPath = path.resolve(inputDirectory, path.basename(requestedVideo));
          if (!videoPath.startsWith(path.resolve(inputDirectory))) {
            return sendJson(response, 400, { error: "The selected video is not in the input directory." });
          }
          return startProcessing({
            videoPath,
            sourceFacePath,
            startTime: config.startTime ?? config.start_time,
            endTime: config.endTime ?? config.end_time,
            overwrite: config.overwrite === true,
            stitchFullVideo: config.stitchFullVideo === true,
          });
        } catch (error) {
          return sendJson(response, 400, { error: error.message || "The JSON request could not be processed." });
        }
      }

      const capture = await readCapture(request);
      if (capture.error) {
        return sendJson(response, capture.statusCode, { error: capture.error });
      }
      if (request.headers["x-participant-consent"] !== "true") {
        return sendJson(response, 403, { error: "Participant consent is required before saving a face photo." });
      }

      try {
        const sourceFace = await saveCapture(capture.image);
        return sendJson(response, 201, {
          name: path.basename(sourceFace),
          sourceFace: `capture:${path.basename(sourceFace)}`,
        });
      } catch (error) {
        return sendJson(response, 500, { error: `Could not save the captured face: ${error.message}` });
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

function getVideoDuration(videoPath) {
  return new Promise((resolve, reject) => {
    const ffprobe = spawn("ffprobe", ["-v", "error", "-show_entries", "format=duration",
      "-of", "default=noprint_wrappers=1:nokey=1", videoPath]);
    let stdout = "";
    let stderr = "";
    ffprobe.stdout.on("data", (chunk) => { stdout += chunk.toString("utf8"); });
    ffprobe.stderr.on("data", (chunk) => { stderr += chunk.toString("utf8"); });
    ffprobe.on("error", reject);
    ffprobe.on("close", (code) => {
      const duration = Number.parseFloat(stdout.trim());
      if (code === 0 && Number.isFinite(duration) && duration > 0) {
        resolve(duration);
      } else {
        reject(new Error(`Could not read the source video duration. ${stderr.trim()}`));
      }
    });
  });
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
