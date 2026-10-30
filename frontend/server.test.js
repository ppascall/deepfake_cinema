"use strict";

const assert = require("node:assert/strict");
const { EventEmitter } = require("node:events");
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
  const children = [];
  const calls = [];
  const server = createServer({
    python: "test-python",
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
    assert.match(await page.text(), /Start processing/);

    const initial = await fetch(`${baseUrl}/api/status`);
    assert.equal((await initial.json()).state, "idle");

    const started = await fetch(`${baseUrl}/api/start`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ overwrite: false }),
    });
    assert.equal(started.status, 202);
    assert.deepEqual(calls[0], {
      command: "test-python",
      args: ["-m", "src.process_video"],
    });

    const concurrent = await fetch(`${baseUrl}/api/start`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: "{}",
    });
    assert.equal(concurrent.status, 409);

    children[0].stdout.write("Processing started\n");
    children[0].emit("close", 0);

    const finished = await fetch(`${baseUrl}/api/status`);
    const status = await finished.json();
    assert.equal(status.state, "succeeded");
    assert.equal(status.exitCode, 0);
    assert.deepEqual(status.logs, ["Processing started"]);

    const restarted = await fetch(`${baseUrl}/api/start`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ overwrite: true }),
    });
    assert.equal(restarted.status, 202);
    assert.deepEqual(calls[1].args, ["-m", "src.process_video", "--overwrite"]);
    children[1].emit("close", 2);
  });
});
