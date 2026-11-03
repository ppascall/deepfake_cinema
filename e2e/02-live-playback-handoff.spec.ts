import { expect, test } from "@playwright/test";

test("captures a face, plays the source up to the scene, then hands off to the processed film", async ({ page }) => {
  await page.goto("/");
  await page.getByRole("checkbox", { name: /I agree to use this face image/ }).check();
  await page.getByRole("radio", { name: "Live camera" }).check();
  await page.getByRole("button", { name: "Capture live photo" }).click();
  await expect(page.locator("#camera-status")).toContainText("captured and saved locally", { timeout: 12_000 });

  await page.locator("#video-select").selectOption("test-source.mp4");
  await page.locator("#clip-start").fill("0.5");
  await page.locator("#clip-end").fill("2.5");
  await expect(page.locator("#shutdown-after-job")).not.toBeChecked();

  const popupPromise = page.waitForEvent("popup");
  await page.getByRole("button", { name: "Start experience" }).click();
  const playback = await popupPromise;
  await expect(playback).toHaveURL(/startAt=0\.5/);
  await expect(playback.locator("#live-video")).toHaveAttribute("src", /\/api\/video\?file=test-source\.mp4/);

  await expect(playback.locator("#video-status")).toContainText("Holding at the cut", { timeout: 10_000 });
  const heldState = await playback.locator("#live-video").evaluate((video: HTMLVideoElement) => ({
    currentTime: video.currentTime,
    paused: video.paused,
    source: video.currentSrc,
  }));
  expect(heldState.paused).toBe(true);
  expect(heldState.currentTime).toBeGreaterThanOrEqual(0.45);
  expect(heldState.currentTime).toBeLessThanOrEqual(0.7);
  expect(heldState.source).toContain("/api/video?file=test-source.mp4");

  await expect(playback.locator("#video-status")).toContainText("Switched to the processed scene", {
    timeout: 20_000,
  });
  await expect(page.locator("#status")).toContainText("Finished successfully", { timeout: 20_000 });
  const processedState = await playback.locator("#live-video").evaluate((video: HTMLVideoElement) => ({
    currentTime: video.currentTime,
    paused: video.paused,
    source: video.currentSrc,
  }));
  expect(processedState.source).toContain("/api/output");
  expect(processedState.currentTime).toBeGreaterThanOrEqual(0.45);
  expect(processedState.currentTime).toBeLessThanOrEqual(0.8);
  expect(processedState.paused).toBe(false);
});

test("the stitched-film autoplay option can be turned off", async ({ page }) => {
  await page.goto("/");
  await page.getByRole("checkbox", { name: /I agree to use this face image/ }).check();
  await page.locator("#video-select").selectOption("test-source.mp4");
  await page.locator("#clip-start").fill("0.5");
  await page.locator("#clip-end").fill("2.5");
  await page.locator("#show-video").uncheck();

  const popupPromise = page.waitForEvent("popup");
  await page.getByRole("button", { name: "Start experience" }).click();
  const playback = await popupPromise;

  await expect(playback.locator("#video-status")).toContainText("processed scene is ready", { timeout: 20_000 });
  const pausedState = await playback.locator("#live-video").evaluate((video: HTMLVideoElement) => ({
    currentTime: video.currentTime,
    paused: video.paused,
    source: video.currentSrc,
  }));
  expect(pausedState.source).toContain("/api/output");
  expect(pausedState.currentTime).toBeGreaterThanOrEqual(0.45);
  expect(pausedState.currentTime).toBeLessThanOrEqual(0.8);
  expect(pausedState.paused).toBe(true);
});
