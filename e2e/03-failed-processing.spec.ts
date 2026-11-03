import { expect, test } from "@playwright/test";

test("reports a failed remote job and resumes the original video", async ({ page }) => {
  await page.goto("/");
  await page.getByRole("checkbox", { name: /I agree to use this face image/ }).check();
  await page.locator("#video-select").selectOption("failure.mp4");
  await page.locator("#clip-start").fill("0.5");
  await page.locator("#clip-end").fill("1.5");

  const popupPromise = page.waitForEvent("popup");
  await page.getByRole("button", { name: "Start experience" }).click();
  const playback = await popupPromise;

  await expect(page.locator("#status")).toContainText("Processing failed", { timeout: 15_000 });
  await expect(playback.locator("#video-status")).toContainText("Processing failed", { timeout: 10_000 });
  const resumedState = await playback.locator("#live-video").evaluate((video: HTMLVideoElement) => ({
    paused: video.paused,
    source: video.currentSrc,
  }));
  expect(resumedState.source).toContain("/api/video?file=failure.mp4");
  expect(resumedState.paused).toBe(false);
});
