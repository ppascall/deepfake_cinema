import { expect, test } from "@playwright/test";

test("consent, camera capture, upload, scene options, and processing log controls work", async ({ page }) => {
  await page.goto("/");
  await expect(page.getByRole("heading", { name: "Deepfake Cinema" })).toBeVisible();

  await page.getByRole("button", { name: "Start experience" }).click();
  await expect(page.locator("#status")).toContainText("Confirm participant consent");

  await page.getByRole("radio", { name: "Live camera" }).check();
  await page.getByRole("button", { name: "Capture live photo" }).click();
  await expect(page.locator("#status")).toContainText("Confirm participant consent");

  await page.getByRole("checkbox", { name: /I agree to use this face image/ }).check();
  await page.getByRole("button", { name: "Start experience" }).click();
  await expect(page.locator("#status")).toContainText("Capture a live photo before starting");

  await page.getByRole("button", { name: "Capture live photo" }).click();
  await expect(page.getByRole("button", { name: "Cancel" })).toBeVisible();
  await page.getByRole("button", { name: "Cancel" }).click();
  await expect(page.locator("#status")).toContainText("Capture cancelled.");
  await expect(page.locator("#preview")).toBeHidden();

  await page.getByRole("button", { name: "Capture live photo" }).click();
  await expect(page.locator("#camera-status")).toContainText("captured and saved locally", { timeout: 12_000 });
  await expect(page.locator("#source-face-select")).toHaveValue(/^capture:face_/);

  await page.locator("#video-upload").setInputFiles({
    name: "upload-smoke.mp4",
    mimeType: "video/mp4",
    buffer: Buffer.from("browser upload control test"),
  });
  await page.getByRole("button", { name: "Add selected video" }).click();
  await expect(page.locator("#video-select")).toHaveValue("upload-smoke.mp4");
  await expect(page.locator("#status")).toContainText("was added to the input folder");
  await page.locator("#clip-end").fill("0.1");
  await page.getByRole("button", { name: "Start experience" }).click();
  await expect(page.locator("#status")).toContainText("Set a valid scene range");
  await page.locator("#clip-start").fill("5");
  await page.locator("#clip-end").fill("10");

  await expect(page.locator("#show-video")).toBeChecked();
  await page.locator("#show-video").uncheck();
  await expect(page.locator("#show-video")).not.toBeChecked();
  await page.locator("#show-video").check();

  await expect(page.locator("#shutdown-after-job")).not.toBeChecked();
  await page.locator("#shutdown-after-job").check();
  await expect(page.locator("#shutdown-after-job")).toBeChecked();
  await page.locator("#shutdown-after-job").uncheck();

  await page.getByText("Processing log").click();
  await expect(page.locator("#logs")).toBeVisible();
});
