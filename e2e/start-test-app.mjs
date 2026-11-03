import { spawnSync } from "node:child_process";
import { execFile } from "node:child_process";
import { randomUUID } from "node:crypto";
import { createServer } from "node:http";
import { promisify } from "node:util";
import { copyFile, mkdtemp, mkdir, chmod, writeFile, rm } from "node:fs/promises";
import { fileURLToPath } from "node:url";
import os from "node:os";
import path from "node:path";
import process from "node:process";

const execFileAsync = promisify(execFile);
const projectRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const port = Number(process.env.E2E_PORT || 3317);
const composeProject = `deepfake-cinema-e2e-${process.pid}`;
const token = `${randomUUID()}-${randomUUID()}`;
const tempRoot = await mkdtemp(path.join(os.tmpdir(), "deepfake-cinema-e2e-"));
const inputDir = path.join(tempRoot, "input");
const outputDir = path.join(tempRoot, "output");
const modelsDir = path.join(tempRoot, "models");
const bridgeTokenFile = path.join(tempRoot, "bridge-token");
const composeOverride = path.join(tempRoot, "compose.override.yml");
let bridgeServer;
let shuttingDown = false;

function run(command, args, options = {}) {
  const result = spawnSync(command, args, { encoding: "utf8", ...options });
  if (result.status !== 0) {
    throw new Error(`${command} ${args.join(" ")} failed: ${result.stderr || result.stdout}`);
  }
  return result.stdout?.trim() || "";
}

async function createTestVideos() {
  const videoPath = path.join(inputDir, "test-source.mp4");
  run("ffmpeg", [
    "-hide_banner", "-loglevel", "error", "-y",
    "-f", "lavfi", "-i", "testsrc2=size=640x360:rate=15",
    "-t", "8", "-an", "-c:v", "libx264", "-pix_fmt", "yuv420p", videoPath,
  ]);
  await copyFile(videoPath, path.join(inputDir, "failure.mp4"));
  run("ffmpeg", [
    "-hide_banner", "-loglevel", "error", "-y",
    "-f", "lavfi", "-i", "color=c=gray:s=256x256",
    "-frames:v", "1", path.join(inputDir, "person.jpg"),
  ]);
}

function createMockBridge(bridgeToken) {
  const jobs = new Map();
  return createServer((request, response) => {
    response.setHeader("content-type", "application/json; charset=utf-8");
    response.setHeader("cache-control", "no-store");
    if (request.headers.authorization !== `Bearer ${bridgeToken}`) {
      response.writeHead(401);
      response.end(JSON.stringify({ error: "Runcrate bridge authorization failed." }));
      return;
    }

    if (request.method === "POST" && request.url === "/jobs") {
      let raw = "";
      request.setEncoding("utf8");
      request.on("data", (chunk) => { raw += chunk; });
      request.on("end", () => {
        let body;
        try {
          body = JSON.parse(raw);
        } catch {
          response.writeHead(400);
          response.end(JSON.stringify({ error: "Invalid JSON" }));
          return;
        }
        if (body.instance !== "e2e-mock" || typeof body.video !== "string"
            || typeof body.output !== "string") {
          response.writeHead(400);
          response.end(JSON.stringify({ error: "Unexpected test job configuration." }));
          return;
        }
        const jobId = randomUUID();
        const job = {
          id: jobId,
          state: "running",
          exitCode: null,
          logs: ["Mock GPU job accepted.", "Processing selected test scene..."],
        };
        jobs.set(jobId, job);
        response.writeHead(202);
        response.end(JSON.stringify({ jobId, state: job.state }));
        const delay = path.basename(body.video).includes("failure") ? 1000 : 3500;
        setTimeout(async () => {
          if (path.basename(body.video).includes("failure")) {
            job.state = "failed";
            job.exitCode = 2;
            job.logs.push("Mock processing failure for the failure-path test.");
            return;
          }
          const videoPath = path.resolve(tempRoot, body.video);
          const outputPath = path.resolve(tempRoot, body.output);
          if (!videoPath.startsWith(`${tempRoot}${path.sep}`)
              || !outputPath.startsWith(`${outputDir}${path.sep}`)) {
            job.state = "failed";
            job.exitCode = 2;
            job.logs.push("Test bridge rejected a path outside its temporary volumes.");
            return;
          }
          try {
            await mkdir(path.dirname(outputPath), { recursive: true });
            await execFileAsync("ffmpeg", [
              "-hide_banner", "-loglevel", "error", "-y",
              "-i", videoPath, "-vf", "hue=s=0",
              "-an", "-c:v", "libx264", "-preset", "ultrafast",
              "-pix_fmt", "yuv420p", outputPath,
            ]);
            job.state = "succeeded";
            job.exitCode = 0;
            job.logs.push("Mock processed scene downloaded to the test output volume.");
          } catch (error) {
            job.state = "failed";
            job.exitCode = 2;
            job.logs.push(`Mock media processing failed: ${error.message}`);
          }
        }, delay);
      });
      return;
    }

    const match = /^\/jobs\/([a-f0-9-]+)$/i.exec(request.url || "");
    if (request.method === "GET" && match) {
      const job = jobs.get(match[1]);
      if (!job) {
        response.writeHead(404);
        response.end(JSON.stringify({ error: "Test job not found." }));
        return;
      }
      response.end(JSON.stringify(job));
      return;
    }
    response.writeHead(404);
    response.end(JSON.stringify({ error: "Test bridge endpoint not found." }));
  });
}

function shutdown(signal = "SIGTERM") {
  if (shuttingDown) {
    return;
  }
  shuttingDown = true;
  bridgeServer?.close();
  const down = spawnSync("docker", [
    "compose", "-p", composeProject,
    "-f", path.join(projectRoot, "docker-compose.yml"),
    "-f", composeOverride,
    "down", "--remove-orphans",
  ], { cwd: projectRoot, encoding: "utf8", timeout: 60_000 });
  if (down.status !== 0 && down.stderr) {
    process.stderr.write(down.stderr);
  }
  void rm(tempRoot, { recursive: true, force: true }).finally(() => {
    process.exit(signal === "SIGINT" ? 130 : 0);
  });
}

process.on("SIGTERM", () => shutdown("SIGTERM"));
process.on("SIGINT", () => shutdown("SIGINT"));

try {
  if (!Number.isInteger(port) || port < 1 || port > 65535) {
    throw new Error("E2E_PORT must be a valid TCP port.");
  }
  run("docker", ["info"]);
  const gateway = run("docker", [
    "network", "inspect", "bridge", "--format", "{{(index .IPAM.Config 0).Gateway}}",
  ]);
  await Promise.all([
    mkdir(inputDir, { recursive: true }),
    mkdir(outputDir, { recursive: true }),
    mkdir(modelsDir, { recursive: true }),
    writeFile(bridgeTokenFile, token, { mode: 0o600 }),
  ]);
  await chmod(outputDir, 0o777);
  await chmod(inputDir, 0o777);
  await createTestVideos();

  bridgeServer = createMockBridge(token);
  await new Promise((resolve, reject) => {
    bridgeServer.once("error", reject);
    bridgeServer.listen(0, gateway, resolve);
  });
  const bridgePort = bridgeServer.address().port;
  const quote = (value) => JSON.stringify(value);
  await writeFile(composeOverride, `services:\n  deepfake-cinema:\n    ports: !override\n      - "127.0.0.1:${port}:3000"\n    volumes:\n      - ${quote(`${inputDir}:/app/input`)}\n      - ${quote(`${outputDir}:/app/output`)}\n      - ${quote(`${modelsDir}:/app/models:ro`)}\n      - ${quote(`${bridgeTokenFile}:/runcrate-bridge/token:ro`)}\n    environment:\n      RUNCRATE_INSTANCE: e2e-mock\n      RUNCRATE_BRIDGE_URL: http://host.docker.internal:${bridgePort}\n`);

  run("docker", [
    "compose", "-p", composeProject,
    "-f", path.join(projectRoot, "docker-compose.yml"),
    "-f", composeOverride,
    "up", "--build", "--detach",
  ], { cwd: projectRoot, stdio: "inherit" });
  process.stdout.write(`Docker E2E app is starting on port ${port} with a mocked, non-billable GPU bridge.\n`);
  setInterval(() => {}, 60_000);
} catch (error) {
  process.stderr.write(`${error.message}\n`);
  shutdown("SIGTERM");
}
