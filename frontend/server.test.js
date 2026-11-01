"use strict";

const assert = require("node:assert/strict");
const { EventEmitter } = require("node:events");
const fs = require("node:fs/promises");
const os = require("node:os");
const path = require("node:path");
const { PassThrough } = require("node:stream");
const test = require("node:test");

const { createServer } = require("./server");

class FakeChild extends EventEmitter {
  constructor() {
    super();
    this.stdout = new PassThrough();
    this.stderr = new PassThrough();
  }
}

async function withServer(server, callback) {
  await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
  const address = server.address();
  try {
    await callback(`http://127.0.0.1:${address.port}`);
  } finally {
    await new Promise((resolve, reject) => {
      server.close((error) => error ? reject(error) : resolve());
    });
  }
}

test("serves the UI and reports processing lifecycle and logs", async () => {
  const captureDirectory = await fs.mkdtemp(path.join(os.tmpdir(), "deepfake-cinema-"));
  const image = Buffer.from([0xff, 0xd8, 0x01, 0x02, 0xff, 0xd9]);
  const children = [];
  const calls = [];
  const server = createServer({
    python: "test-python",
    captureDirectory,
    spawnProcess(command, args) {
      calls.push({ command, args });
      const child = new FakeChild();
      children.push(child);
      return child;
    },
  });

  await withServer(server, async (baseUrl) => {
    const page = await fetch(baseUrl);
    assert.equal(page.status, 200);
    const pageText = await page.text();
    assert.match(pageText, /Start experience/);
    assert.match(pageText, /Existing photo/);
    assert.match(pageText, /Live camera/);
    assert.doesNotMatch(pageText, /output\/final_movie_YYYYMMDD/);
    assert.match(pageText, /Live camera preview/);
    assert.match(pageText, /Play the stitched film when it is ready/);

    const unavailableVideo = await fetch(`${baseUrl}/api/output`);
    assert.equal(unavailableVideo.status, 404);

    const initial = await fetch(`${baseUrl}/api/status`);
    assert.equal((await initial.json()).state, "idle");

    const deniedCapture = await fetch(`${baseUrl}/api/capture`, {
      method: "POST",
      headers: { "content-type": "image/jpeg" },
      body: image,
    });
    assert.equal(deniedCapture.status, 403);

    const captured = await fetch(`${baseUrl}/api/capture`, {
      method: "POST",
      headers: { "content-type": "image/jpeg", "x-participant-consent": "true" },
      body: image,
    });
    assert.equal(captured.status, 201);
    const captureResult = await captured.json();
    assert.match(captureResult.sourceFace, /^capture:face_/);
    assert.equal(children.length, 0);
    const faces = await fetch(`${baseUrl}/api/faces`);
    assert.equal((await faces.json()).faces[0].sourceFace, captureResult.sourceFace);

    const started = await fetch(`${baseUrl}/api/start`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({
        video: "movie.mp4",
        sourceFace: captureResult.sourceFace,
        overwrite: false,
        participantConsent: true,
      }),
    });
    assert.equal(started.status, 202);
    assert.equal(calls[0].command, "test-python");
    assert.deepEqual(calls[0].args.slice(0, 3), ["-m", "src.process_video", "--source-face"]);
    assert.deepEqual(calls[0].args[3], path.join(captureDirectory, path.basename(captureResult.sourceFace.slice("capture:".length))));

    const concurrent = await fetch(`${baseUrl}/api/start`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ video: "movie.mp4", sourceFace: captureResult.sourceFace }),
    });
    assert.equal(concurrent.status, 409);

    children[0].stdout.write("Processing started\n");
    const outputIndex = calls[0].args.indexOf("--output");
    await fs.writeFile(calls[0].args[outputIndex + 1], Buffer.from("sample processed video"));
    children[0].emit("close", 0);

    const video = await fetch(`${baseUrl}/api/output`, {
      headers: { range: "bytes=0-5" },
    });
    assert.equal(video.status, 206);
    assert.equal(video.headers.get("content-type"), "video/mp4");
    assert.equal(video.headers.get("content-range"), "bytes 0-5/22");
    assert.equal(await video.text(), "sample");

    const finished = await fetch(`${baseUrl}/api/status`);
    const status = await finished.json();
    assert.equal(status.state, "succeeded");
    assert.equal(status.exitCode, 0);
    assert.equal(status.logs[0], "Processing started");

    const restarted = await fetch(`${baseUrl}/api/start`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({
        video: "movie.mp4",
        sourceFace: captureResult.sourceFace,
        overwrite: true,
        participantConsent: true,
      }),
    });
    assert.equal(restarted.status, 202);
    assert.deepEqual(calls[1].args.slice(0, 3), ["-m", "src.process_video", "--source-face"]);
    assert.equal(calls[1].args.at(-1), "--overwrite");
    children[1].emit("close", 2);
  }).finally(async () => {
    await fs.rm(captureDirectory, { recursive: true, force: true });
  });
});

test("lists videos and accepts start/end settings for a selected clip", async () => {
  const inputDirectory = await fs.mkdtemp(path.join(os.tmpdir(), "deepfake-input-"));
  const captureDirectory = await fs.mkdtemp(path.join(os.tmpdir(), "deepfake-capture-"));
  const outputVideo = path.join(captureDirectory, "selected_clip.mp4");
  await fs.writeFile(path.join(captureDirectory, "face_manual.jpg"), Buffer.from("face"));
  await fs.writeFile(path.join(inputDirectory, "alpha.mp4"), Buffer.from("alpha"));
  await fs.writeFile(path.join(inputDirectory, "beta.mp4"), Buffer.from("beta"));
  const children = [];
  const calls = [];
  const server = createServer({
    inputDirectory,
    captureDirectory,
    outputVideo,
    spawnProcess(command, args) {
      calls.push({ command, args });
      const child = new FakeChild();
      children.push(child);
      return child;
    },
  });

  await withServer(server, async (baseUrl) => {
    const videos = await fetch(`${baseUrl}/api/videos`);
    assert.equal(videos.status, 200);
    assert.deepEqual((await videos.json()).videos.map((video) => video.name).sort(), ["alpha.mp4", "beta.mp4"]);

    const started = await fetch(`${baseUrl}/api/start`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({
        video: "alpha.mp4",
        startTime: 12.5,
        endTime: 20,
        overwrite: false,
        sourceFace: "capture:face_manual.jpg",
        participantConsent: true,
      }),
    });
    assert.equal(started.status, 202);
    assert.equal(calls[0].args.includes("--video"), true);
    assert.equal(calls[0].args.includes("--max-seconds"), true);
    assert.equal(calls[0].args[calls[0].args.indexOf("--max-seconds") + 1], "7.5");
    assert.equal(calls[0].args[calls[0].args.indexOf("--video") + 1], path.join(inputDirectory, "alpha.mp4"));
    children[0].emit("close", 0);
    await fs.writeFile(outputVideo, Buffer.from("sample processed video"));
    const output = await fetch(`${baseUrl}/api/output`);
    assert.equal(output.status, 200);
  }).finally(async () => {
    await fs.rm(inputDirectory, { recursive: true, force: true });
    await fs.rm(captureDirectory, { recursive: true, force: true });
  });
});

test("uses a timestamped output name and serves the newest generated video", async () => {
  const captureDirectory = await fs.mkdtemp(path.join(os.tmpdir(), "deepfake-newest-"));
  const olderVideo = path.join(captureDirectory, "final_movie_20240101_010101.mp4");
  const newestVideo = path.join(captureDirectory, "final_movie_20240102_020202.mp4");
  await fs.writeFile(path.join(captureDirectory, "face_manual.jpg"), Buffer.from("face"));
  const children = [];
  const server = createServer({
    captureDirectory,
    outputVideo: path.join(captureDirectory, "final_movie.mp4"),
    spawnProcess(command, args) {
      const child = new FakeChild();
      child.args = args;
      children.push(child);
      return child;
    },
  });

  await withServer(server, async (baseUrl) => {
    await fs.writeFile(olderVideo, Buffer.from("older"));
    await fs.writeFile(newestVideo, Buffer.from("newest"));

    const started = await fetch(`${baseUrl}/api/start`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ sourceFace: "capture:face_manual.jpg", overwrite: true, participantConsent: true }),
    });
    assert.equal(started.status, 202);
    const outputArgs = children[0].args || [];
    const outputIndex = outputArgs.indexOf("--output");
    assert.notEqual(outputIndex, -1);
    const generatedName = outputArgs[outputIndex + 1];
    assert.match(path.basename(generatedName), /^final_movie_\d{8}_\d{6}\.mp4$/);

    children[0].emit("close", 0);

    const response = await fetch(`${baseUrl}/api/output`);
    assert.equal(response.status, 200);
    assert.equal(await response.text(), "newest");
  }).finally(async () => {
    await fs.rm(captureDirectory, { recursive: true, force: true });
  });
});

test("stitches an opted-in processed range into the original full video", async () => {
  const inputDirectory = await fs.mkdtemp(path.join(os.tmpdir(), "deepfake-stitch-input-"));
  const captureDirectory = await fs.mkdtemp(path.join(os.tmpdir(), "deepfake-stitch-output-"));
  const sourceVideo = path.join(inputDirectory, "film.mp4");
  const sourceFace = path.join(captureDirectory, "face_manual.jpg");
  await fs.writeFile(sourceVideo, Buffer.from("source"));
  await fs.writeFile(sourceFace, Buffer.from("face"));
  const children = [];
  const processorCalls = [];
  const ffmpegCalls = [];
  const server = createServer({
    inputDirectory,
    captureDirectory,
    outputVideo: path.join(captureDirectory, "final_movie.mp4"),
    probeVideoDuration: async () => 60,
    spawnFFmpeg(command, args) {
      ffmpegCalls.push(args);
      const child = new FakeChild();
      void fs.writeFile(args.at(-1), Buffer.from("ffmpeg output")).then(() => child.emit("close", 0));
      return child;
    },
    spawnProcess(command, args) {
      processorCalls.push(args);
      const child = new FakeChild();
      children.push(child);
      return child;
    },
  });

  await withServer(server, async (baseUrl) => {
    const started = await fetch(`${baseUrl}/api/start`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({
        video: "film.mp4",
        sourceFace: "capture:face_manual.jpg",
        startTime: 20,
        endTime: 25,
        stitchFullVideo: true,
        participantConsent: true,
      }),
    });
    assert.equal(started.status, 202);
    assert.equal(ffmpegCalls.length, 1);
    assert.equal(processorCalls.length, 1);
    const processorOutput = processorCalls[0][processorCalls[0].indexOf("--output") + 1];
    assert.match(path.basename(processorOutput), /\.segment\.mp4$/);
    await fs.writeFile(processorOutput, Buffer.from("processed segment"));
    children[0].emit("close", 0);

    let status;
    for (let attempt = 0; attempt < 50; attempt += 1) {
      status = await (await fetch(`${baseUrl}/api/status`)).json();
      if (status.state !== "running") {
        break;
      }
      await new Promise((resolve) => setTimeout(resolve, 10));
    }
    assert.equal(status.state, "succeeded");
    assert.equal(ffmpegCalls.length, 2);
    const stitchArgs = ffmpegCalls[1];
    const filterGraph = stitchArgs[stitchArgs.indexOf("-filter_complex") + 1];
    assert.match(filterGraph, /trim=start=0:end=20/);
    assert.match(filterGraph, /trim=start=25:end=60/);
    assert.match(filterGraph, /concat=n=3:v=1:a=0/);
    assert.equal(stitchArgs.includes("0:a?"), true);

    const output = await fetch(`${baseUrl}/api/output`);
    assert.equal(output.status, 200);
    assert.equal(await output.text(), "ffmpeg output");
  }).finally(async () => {
    await fs.rm(inputDirectory, { recursive: true, force: true });
    await fs.rm(captureDirectory, { recursive: true, force: true });
  });
});

test("uploads a file into the input directory", async () => {
  const inputDirectory = await fs.mkdtemp(path.join(os.tmpdir(), "deepfake-upload-"));
  const server = createServer({ inputDirectory });

  await withServer(server, async (baseUrl) => {
    const upload = await fetch(`${baseUrl}/api/upload`, {
      method: "POST",
      headers: { "content-type": "video/mp4", "x-filename": "custom_upload.mp4" },
      body: Buffer.from("video-bytes"),
    });

    assert.equal(upload.status, 201);
    const saved = await fs.readFile(path.join(inputDirectory, "custom_upload.mp4"));
    assert.equal(saved.toString(), "video-bytes");
  }).finally(async () => {
    await fs.rm(inputDirectory, { recursive: true, force: true });
  });
});

test("rejects a missing source face without launching processing", async () => {
  const inputDirectory = await fs.mkdtemp(path.join(os.tmpdir(), "deepfake-face-input-"));
  const children = [];
  const server = createServer({
    inputDirectory,
    spawnProcess() {
      children.push(true);
      return new FakeChild();
    },
  });

  await withServer(server, async (baseUrl) => {
    const denied = await fetch(`${baseUrl}/api/start`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ video: "movie.mp4", sourceFace: "person.jpg" }),
    });
    assert.equal(denied.status, 403);

    const response = await fetch(`${baseUrl}/api/start`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ video: "movie.mp4", sourceFace: "person.jpg", participantConsent: true }),
    });

    assert.equal(response.status, 400);
    assert.match((await response.json()).error, /Capture a face first/);
    assert.equal(children.length, 0);
  }).finally(async () => {
    await fs.rm(inputDirectory, { recursive: true, force: true });
  });
});

test("legacy camera requests save a face without starting the default video", async () => {
  const captureDirectory = await fs.mkdtemp(path.join(os.tmpdir(), "deepfake-legacy-capture-"));
  const children = [];
  const server = createServer({
    captureDirectory,
    spawnProcess() {
      children.push(true);
      return new FakeChild();
    },
  });

  await withServer(server, async (baseUrl) => {
    const response = await fetch(`${baseUrl}/api/start`, {
      method: "POST",
      headers: { "content-type": "image/jpeg", "x-overwrite": "false", "x-participant-consent": "true" },
      body: Buffer.from([0xff, 0xd8, 0x01, 0x02, 0xff, 0xd9]),
    });

    assert.equal(response.status, 201);
    assert.match((await response.json()).sourceFace, /^capture:face_/);
    assert.equal(children.length, 0);
  }).finally(async () => {
    await fs.rm(captureDirectory, { recursive: true, force: true });
  });
});

test("rejects invalid photo content without starting the processor", async () => {
  const children = [];
  const server = createServer({
    spawnProcess() {
      children.push(true);
      return new FakeChild();
    },
  });

  await withServer(server, async (baseUrl) => {
    const response = await fetch(`${baseUrl}/api/start`, {
      method: "POST",
      headers: { "content-type": "image/jpeg", "x-overwrite": "false" },
      body: Buffer.from("not a jpeg"),
    });

    assert.equal(response.status, 400);
    assert.match((await response.json()).error, /valid JPEG/);
    assert.equal(children.length, 0);
    assert.equal((await (await fetch(`${baseUrl}/api/status`)).json()).state, "idle");
  });
});
