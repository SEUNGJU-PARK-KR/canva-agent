import { chromium, type Frame, type Page } from "playwright";
import { appendFile, mkdir, writeFile } from "node:fs/promises";
import path from "node:path";

const id = process.env.VIDEO_ID || "nucIUdFTkZY";
const out = process.env.OUT || "/workspace/embed-probe";

async function saveJson(file: string, value: unknown) {
  await mkdir(path.dirname(file), { recursive: true });
  await writeFile(file, JSON.stringify(value, null, 2), "utf8");
}
async function log(message: string) {
  await appendFile(path.join(out, "progress.log"), `${new Date().toISOString()} ${message}\n`, "utf8");
}
async function bounded<T>(promise: Promise<T>, ms: number, fallback: T): Promise<T> {
  return await Promise.race([
    promise.catch(() => fallback),
    new Promise<T>((resolve) => setTimeout(() => resolve(fallback), ms)),
  ]);
}
async function videoState(frame: Frame) {
  return await bounded(
    frame.evaluate(() => {
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
    }),
    2500,
    { found: false, timeout: true },
  );
}
async function allVideoStates(page: Page) {
  const rows: unknown[] = [];
  for (const frame of page.frames()) {
    const state = await videoState(frame);
    rows.push({ frameUrl: frame.url(), state });
  }
  return rows;
}
async function forceAll(page: Page) {
  for (const frame of page.frames()) {
    await bounded(
      frame.evaluate(async () => {
        const v = document.querySelector("video") as HTMLVideoElement | null;
        if (!v) return;
        v.muted = true;
        v.volume = 0;
        v.playbackRate = 2;
        try { await v.play(); } catch {}
      }),
      2000,
      undefined,
    );
    for (const selector of ["button.ytp-large-play-button", "button.ytp-play-button", ".ytp-cued-thumbnail-overlay", "video"]) {
      try {
        const node = frame.locator(selector).first();
        if ((await bounded(node.count(), 600, 0)) > 0 && (await bounded(node.isVisible(), 600, false))) {
          await bounded(node.click({ force: true, timeout: 700 }), 1000, undefined);
        }
      } catch {}
    }
  }
}
async function screenshot(page: Page, file: string) {
  await bounded(page.screenshot({ path: file, fullPage: false }), 5000, undefined);
}

await mkdir(out, { recursive: true });
const browser = await chromium.launch({
  headless: false,
  args: [
    "--no-sandbox",
    "--disable-dev-shm-usage",
    "--autoplay-policy=no-user-gesture-required",
    "--disable-blink-features=AutomationControlled",
    "--window-size=1600,1000",
  ],
});
const context = await browser.newContext({
  viewport: { width: 1600, height: 1000 },
  locale: "ko-KR",
  timezoneId: "Asia/Seoul",
});
await context.addInitScript(() => Object.defineProperty(navigator, "webdriver", { get: () => undefined }));

const wrapperHtml = (host: string) => `<!doctype html><html><head><meta charset="utf-8"><style>html,body{margin:0;background:#111;height:100%;overflow:hidden}iframe{width:100%;height:100%;border:0}</style></head><body><iframe id="player" allow="autoplay; encrypted-media; picture-in-picture" allowfullscreen src="https://${host}/embed/${id}?autoplay=1&mute=1&controls=1&playsinline=1&enablejsapi=1&hl=ko&origin=https%3A%2F%2Fexample.com"></iframe></body></html>`;
const variants = [
  { name: "wrapper_youtube", pageUrl: "https://example.com/largo-youtube", wrapper: wrapperHtml("www.youtube.com") },
  { name: "wrapper_nocookie", pageUrl: "https://example.com/largo-nocookie", wrapper: wrapperHtml("www.youtube-nocookie.com") },
  { name: "direct_embed", pageUrl: `https://www.youtube.com/embed/${id}?autoplay=1&mute=1&controls=1&playsinline=1&enablejsapi=1&hl=ko&origin=https%3A%2F%2Fexample.com` },
  { name: "watch", pageUrl: `https://www.youtube.com/watch?v=${id}&hl=ko&gl=KR` },
];
const results: Record<string, any>[] = [];

for (const variant of variants) {
  await log(`${variant.name} begin`);
  const page = await context.newPage();
  const dir = path.join(out, variant.name);
  await mkdir(dir, { recursive: true });
  const media = new Set<string>();
  page.on("response", (response) => {
    const u = response.url();
    if (u.includes("googlevideo.com") || u.includes("videoplayback") || u.includes(".m3u8")) media.add(u);
  });
  if (variant.wrapper) {
    await page.route(variant.pageUrl, async (route) => {
      await route.fulfill({ status: 200, contentType: "text/html; charset=utf-8", body: variant.wrapper! });
    });
  }
  const item: Record<string, any> = { name: variant.name, url: variant.pageUrl, samples: [] };
  try {
    const navigation = await bounded(
      page.goto(variant.pageUrl, { waitUntil: "commit", timeout: 12000 }),
      14000,
      null,
    );
    item.navigationCommitted = Boolean(navigation);
    await page.waitForTimeout(5000);
    item.finalUrl = page.url();
    item.title = await bounded(page.title(), 1500, "");
    await screenshot(page, path.join(dir, "initial.png"));
    await forceAll(page);
    for (let second = 1; second <= 12; second++) {
      await page.waitForTimeout(1000);
      const states = await allVideoStates(page);
      item.samples.push({ second, states });
      if ([2, 6, 12].includes(second)) await screenshot(page, path.join(dir, `wall_${String(second).padStart(2, "0")}.png`));
      if (second === 6) await forceAll(page);
    }
    const validStates = item.samples.flatMap((sample: any) => sample.states)
      .map((row: any) => row.state)
      .filter((state: any) => state?.found);
    const times = validStates.map((state: any) => Number(state.currentTime || 0));
    const pixels = validStates.map((state: any) => Number(state.width || 0) * Number(state.height || 0));
    item.timeDelta = times.length > 1 ? Math.max(...times) - Math.min(...times) : 0;
    item.maxPixels = pixels.length ? Math.max(...pixels) : 0;
    item.duration = validStates.findLast((state: any) => Number(state.duration || 0) > 0)?.duration || null;
    item.mediaUrlCount = media.size;
    item.playbackSuccess = item.timeDelta >= 4 && item.maxPixels >= 320 * 180;
    item.frameUrls = page.frames().map((frame) => frame.url());
    item.visibleTexts = [];
    for (const frame of page.frames()) {
      const text = await bounded(frame.locator("body").innerText(), 1500, "");
      if (text) item.visibleTexts.push({ frameUrl: frame.url(), text: text.slice(0, 2500) });
    }
  } catch (error) {
    item.error = String(error);
  }
  await writeFile(path.join(dir, "media_urls.txt"), [...media].join("\n"), "utf8");
  await saveJson(path.join(dir, "diagnostic.json"), item);
  results.push(item);
  await log(`${variant.name} end success=${Boolean(item.playbackSuccess)} delta=${item.timeDelta ?? 0}`);
  await bounded(page.close(), 2500, undefined);
  if (item.playbackSuccess) break;
}

const summary = {
  videoId: id,
  runtime: "CopilotKit/OpenBot agent-computer",
  success: results.some((item) => item.playbackSuccess),
  successVariant: results.find((item) => item.playbackSuccess)?.name || null,
  results,
};
await saveJson(path.join(out, "summary.json"), summary);
await log(`finished success=${summary.success}`);
await bounded(context.close(), 3000, undefined);
await bounded(browser.close(), 3000, undefined);
console.log(JSON.stringify(summary, null, 2));
