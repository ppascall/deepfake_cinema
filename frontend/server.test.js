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
  const outputVideo = path.join(captureDirectory, "final_movie.mp4");
  const image = Buffer.from([0xff, 0xd8, 0x01, 0x02, 0xff, 0xd9]);
  const children = [];
  const calls = [];
  const server = createServer({
    python: "test-python",
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
    const page = await fetch(baseUrl);
    assert.equal(page.status, 200);
    const pageText = await page.text();
    assert.match(pageText, /Capture face and process/);
    assert.match(pageText, /Live camera preview/);
    assert.match(pageText, /Show processed video when finished/);

    const unavailableVideo = await fetch(`${baseUrl}/api/output`);
    assert.equal(unavailableVideo.status, 404);

    const initial = await fetch(`${baseUrl}/api/status`);
    assert.equal((await initial.json()).state, "idle");

    const started = await fetch(`${baseUrl}/api/start`, {
      method: "POST",
      headers: { "content-type": "image/jpeg", "x-overwrite": "false" },
      body: image,
    });
    assert.equal(started.status, 202);
    assert.equal(calls[0].command, "test-python");
    assert.deepEqual(calls[0].args.slice(0, 3), ["-m", "src.process_video", "--source-face"]);
    assert.deepEqual(await fs.readFile(calls[0].args[3]), image);

    const concurrent = await fetch(`${baseUrl}/api/start`, {
      method: "POST",
      headers: { "content-type": "image/jpeg", "x-overwrite": "false" },
      body: image,
    });
    assert.equal(concurrent.status, 409);

    children[0].stdout.write("Processing started\n");
    await fs.writeFile(outputVideo, Buffer.from("sample processed video"));
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
    assert.match(status.logs[0], /^Captured face saved to /);
    assert.equal(status.logs[1], "Processing started");

    const restarted = await fetch(`${baseUrl}/api/start`, {
      method: "POST",
      headers: { "content-type": "image/jpeg", "x-overwrite": "true" },
      body: image,
    });
    assert.equal(restarted.status, 202);
    assert.deepEqual(calls[1].args.slice(0, 3), ["-m", "src.process_video", "--source-face"]);
    assert.equal(calls[1].args.at(-1), "--overwrite");
    children[1].emit("close", 2);
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
