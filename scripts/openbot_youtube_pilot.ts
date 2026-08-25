import { chromium, type Page } from "playwright";
import { mkdir, writeFile } from "node:fs/promises";
import path from "node:path";

const id = process.env.VIDEO_ID || "nucIUdFTkZY";
const out = process.env.OUT || "/workspace/pilot";
const variants = [
  ["watch", `https://www.youtube.com/watch?v=${id}&hl=ko&gl=KR`],
  ["embed", `https://www.youtube.com/embed/${id}?autoplay=1&mute=1&controls=1&playsinline=1&hl=ko&origin=https%3A%2F%2Fwww.youtube.com`],
  ["nocookie", `https://www.youtube-nocookie.com/embed/${id}?autoplay=1&mute=1&controls=1&playsinline=1&hl=ko&origin=https%3A%2F%2Fwww.youtube.com`],
] as const;

async function json(file: string, value: unknown) {
  await mkdir(path.dirname(file), { recursive: true });
  await writeFile(file, JSON.stringify(value, null, 2), "utf8");
}

async function dismiss(page: Page) {
  for (const name of [/모두 수락/i, /모두 거부/i, /동의/i, /Accept all/i, /Reject all/i, /I agree/i]) {
    try {
      const button = page.getByRole("button", { name }).first();
      if ((await button.count()) && (await button.isVisible({ timeout: 300 }))) {
        await button.click({ timeout: 1200 });
        await page.waitForTimeout(500);
        break;
      }
    } catch {}
  }
}

async function forcePlay(page: Page) {
  try {
    await page.evaluate(async () => {
      const v = document.querySelector("video") as HTMLVideoElement | null;
      if (!v) return;
      v.muted = true;
      v.volume = 0;
      v.playbackRate = 2;
      try { await v.play(); } catch {}
    });
  } catch {}
  for (const selector of ["button.ytp-large-play-button", "button.ytp-play-button", ".ytp-cued-thumbnail-overlay", "video"]) {
    try {
      const node = page.locator(selector).first();
      if ((await node.count()) && (await node.isVisible({ timeout: 250 }))) {
        await node.click({ force: true, timeout: 1000 });
      }
    } catch {}
  }
}

async function state(page: Page) {
  return page.evaluate(() => {
    const v = document.querySelector("video") as HTMLVideoElement | null;
    if (!v) return { found: false };
    return {
      found: true,
      currentTime: Number(v.currentTime || 0),
      duration: Number.isFinite(v.duration) ? Number(v.duration) : null,
      paused: v.paused,
      readyState: v.readyState,
      networkState: v.networkState,
      width: v.videoWidth || 0,
      height: v.videoHeight || 0,
      error: v.error ? { code: v.error.code, message: v.error.message || "" } : null,
    };
  }).catch((error) => ({ found: false, error: String(error) }));
}

async function shot(page: Page, file: string) {
  try {
    const video = page.locator("video").first();
    if ((await video.count()) && (await video.isVisible({ timeout: 250 }))) {
      await video.screenshot({ path: file });
      return;
    }
  } catch {}
  await page.screenshot({ path: file, fullPage: false });
}

await mkdir(out, { recursive: true });
const browser = await chromium.launch({
  headless: false,
  args: ["--no-sandbox", "--disable-dev-shm-usage", "--autoplay-policy=no-user-gesture-required", "--disable-blink-features=AutomationControlled", "--window-size=1600,1000"],
});
const context = await browser.newContext({
  viewport: { width: 1600, height: 1000 },
  locale: "ko-KR",
  timezoneId: "Asia/Seoul",
  userAgent: "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/145.0.0.0 Safari/537.36",
});
await context.addInitScript(() => Object.defineProperty(navigator, "webdriver", { get: () => undefined }));
const attempts: Record<string, unknown>[] = [];

for (const [name, url] of variants) {
  const dir = path.join(out, name);
  await mkdir(dir, { recursive: true });
  const page = await context.newPage();
  const media = new Set<string>();
  page.on("response", (r) => {
    const u = r.url();
    if (u.includes("googlevideo.com") || u.includes("videoplayback") || u.includes(".m3u8")) media.add(u);
  });
  const item: Record<string, unknown> = { name, url, states: [] };
  try {
    await page.goto(url, { waitUntil: "domcontentloaded", timeout: 75000 });
    await dismiss(page);
    await page.waitForTimeout(3500);
    item.title = await page.title();
    item.finalUrl = page.url();
    const body = await page.locator("body").innerText().catch(() => "");
    item.bodyPreview = body.slice(0, 5000);
    item.markers = ["Sign in to confirm", "로그인하여 본인 확인", "Video unavailable", "동영상을 재생할 수 없음", "Error 153", "봇이 아님을"].filter((m) => body.toLowerCase().includes(m.toLowerCase()));
    await shot(page, path.join(dir, "initial.png"));
    await forcePlay(page);
    const states: any[] = [];
    for (let second = 1; second <= 18; second++) {
      await page.waitForTimeout(1000);
      states.push({ wallSecond: second, ...(await state(page)) });
      if ([2, 5, 9, 13, 18].includes(second)) await shot(page, path.join(dir, `wall_${String(second).padStart(2, "0")}.png`));
      if ([5, 10, 15].includes(second)) await forcePlay(page);
    }
    item.states = states;
    const times = states.filter((s) => s.found).map((s) => Number(s.currentTime || 0));
    const sizes = states.filter((s) => s.found).map((s) => Number(s.width || 0) * Number(s.height || 0));
    item.timeDelta = times.length > 1 ? Math.max(...times) - Math.min(...times) : 0;
    item.maxPixels = sizes.length ? Math.max(...sizes) : 0;
    item.duration = states.findLast((s) => Number(s.duration || 0) > 0)?.duration || null;
    item.mediaUrlCount = media.size;
    item.success = Number(item.timeDelta) >= 5 && Number(item.maxPixels) >= 320 * 180;
  } catch (error) {
    item.error = String(error);
  }
  await writeFile(path.join(dir, "media_urls.txt"), [...media].join("\n"), "utf8");
  await json(path.join(dir, "diagnostic.json"), item);
  attempts.push(item);
  await page.close();
  if (item.success) break;
}

const result = {
  videoId: id,
  runtime: "CopilotKit/OpenBot agent-computer image",
  success: attempts.some((a) => a.success),
  successVariant: attempts.find((a) => a.success)?.name || null,
  attempts,
};
await json(path.join(out, "summary.json"), result);
await context.close();
await browser.close();
console.log(JSON.stringify(result, null, 2));
