import { chromium } from "playwright";
import { appendFile, mkdir, writeFile } from "node:fs/promises";
import path from "node:path";

const videoId = process.env.VIDEO_ID || "nucIUdFTkZY";
const out = process.env.OUT || "/workspace/piped-probe";
const instances = [
  "https://pipedapi.kavin.rocks",
  "https://pipedapi.tokhmi.xyz",
  "https://pipedapi.moomoo.me",
  "https://pipedapi.syncpundit.io",
  "https://api-piped.mha.fi",
  "https://piped-api.garudalinux.org",
  "https://pipedapi.rivo.lol",
  "https://pipedapi.leptons.xyz",
  "https://piped-api.lunar.icu",
  "https://ytapi.dc09.ru",
  "https://pipedapi.colinslegacy.com",
  "https://yapi.vyper.me",
  "https://api.looleh.xyz",
  "https://piped-api.cfe.re",
  "https://pipedapi.r4fo.com",
  "https://pipedapi.nosebs.ru",
];

async function saveJson(file: string, value: unknown) {
  await mkdir(path.dirname(file), { recursive: true });
  await writeFile(file, JSON.stringify(value, null, 2), "utf8");
}
async function log(message: string) {
  await appendFile(path.join(out, "progress.log"), `${new Date().toISOString()} ${message}\n`, "utf8");
}
async function fetchBounded(url: string, init: RequestInit = {}, timeoutMs = 25000) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);
  try {
    return await fetch(url, { ...init, signal: controller.signal, redirect: "follow" });
  } finally {
    clearTimeout(timer);
  }
}
function candidateUrls(data: any): Array<{ kind: string; url: string; quality?: string; mimeType?: string }> {
  const rows: Array<{ kind: string; url: string; quality?: string; mimeType?: string }> = [];
  if (typeof data?.hls === "string" && data.hls.startsWith("http")) rows.push({ kind: "hls", url: data.hls });
  for (const item of data?.videoStreams || []) {
    if (typeof item?.url !== "string" || !item.url.startsWith("http")) continue;
    rows.push({ kind: "video", url: item.url, quality: item.quality || item.qualityLabel, mimeType: item.mimeType || item.format });
  }
  for (const item of data?.audioStreams || []) {
    if (typeof item?.url !== "string" || !item.url.startsWith("http")) continue;
    rows.push({ kind: "audio", url: item.url, quality: item.quality, mimeType: item.mimeType || item.format });
  }
  return rows;
}
async function byteProbe(candidate: { kind: string; url: string; quality?: string; mimeType?: string }) {
  const record: Record<string, unknown> = { kind: candidate.kind, quality: candidate.quality, declaredMimeType: candidate.mimeType };
  try {
    const response = await fetchBounded(candidate.url, {
      headers: {
        Range: "bytes=0-131071",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/145 Safari/537.36",
        Accept: "*/*",
      },
    }, 30000);
    const buffer = new Uint8Array(await response.arrayBuffer());
    record.status = response.status;
    record.contentType = response.headers.get("content-type");
    record.contentLength = response.headers.get("content-length");
    record.contentRange = response.headers.get("content-range");
    record.finalUrlHost = new URL(response.url).host;
    record.bytes = buffer.byteLength;
    record.prefixHex = [...buffer.slice(0, 24)].map((value) => value.toString(16).padStart(2, "0")).join("");
    record.mediaLike = response.ok && buffer.byteLength > 4096 && !String(record.contentType || "").includes("text/html");
  } catch (error) {
    record.error = String(error);
    record.mediaLike = false;
  }
  return record;
}
async function browserProbe(url: string, dir: string) {
  const browser = await chromium.launch({
    headless: false,
    args: ["--no-sandbox", "--disable-dev-shm-usage", "--autoplay-policy=no-user-gesture-required", "--window-size=1280,720"],
  });
  const context = await browser.newContext({ viewport: { width: 1280, height: 720 } });
  const page = await context.newPage();
  await page.setContent(`<!doctype html><html><body style="margin:0;background:#111"><video id="v" controls autoplay muted style="width:100vw;height:100vh" src="${url.replaceAll("&", "&amp;").replaceAll('"', '&quot;')}"></video></body></html>`);
  const states: any[] = [];
  for (let second = 1; second <= 14; second++) {
    await page.waitForTimeout(1000);
    const state = await page.evaluate(async () => {
      const v = document.querySelector("video")!;
      v.muted = true;
      v.playbackRate = 2;
      try { await v.play(); } catch {}
      return {
        currentTime: Number(v.currentTime || 0),
        duration: Number.isFinite(v.duration) ? Number(v.duration) : null,
        paused: v.paused,
        readyState: v.readyState,
        networkState: v.networkState,
        width: v.videoWidth || 0,
        height: v.videoHeight || 0,
        error: v.error ? { code: v.error.code, message: v.error.message || "" } : null,
      };
    }).catch((error) => ({ error: String(error) }));
    states.push({ second, ...state });
    if ([2, 7, 14].includes(second)) await page.screenshot({ path: path.join(dir, `play_${String(second).padStart(2, "0")}.png`) }).catch(() => undefined);
  }
  const times = states.map((state) => Number(state.currentTime || 0));
  const pixels = states.map((state) => Number(state.width || 0) * Number(state.height || 0));
  const result = {
    states,
    timeDelta: times.length > 1 ? Math.max(...times) - Math.min(...times) : 0,
    maxPixels: pixels.length ? Math.max(...pixels) : 0,
  };
  await context.close();
  await browser.close();
  return { ...result, success: result.timeDelta >= 4 && result.maxPixels >= 320 * 180 };
}

await mkdir(out, { recursive: true });
const instanceResults: Record<string, any>[] = [];
let selected: { instance: string; candidate: any; byte: any } | null = null;

for (const base of instances) {
  await log(`api begin ${base}`);
  const rec: Record<string, any> = { instance: base };
  try {
    const response = await fetchBounded(`${base}/streams/${videoId}`, {
      headers: { Accept: "application/json", "User-Agent": "OpenBot-LargoTV-Research/1.0" },
    }, 30000);
    rec.status = response.status;
    rec.contentType = response.headers.get("content-type");
    const text = await response.text();
    rec.bytes = text.length;
    rec.bodyPreview = text.slice(0, 1000);
    if (response.ok) {
      const data = JSON.parse(text);
      rec.title = data?.title;
      rec.duration = data?.duration;
      const candidates = candidateUrls(data);
      rec.candidateCount = candidates.length;
      const preferred = [
        ...candidates.filter((item) => item.kind === "video" && /360|480|720/.test(String(item.quality || ""))),
        ...candidates.filter((item) => item.kind === "hls"),
        ...candidates.filter((item) => item.kind === "video"),
      ].slice(0, 8);
      rec.byteProbes = [];
      for (const candidate of preferred) {
        const probe = await byteProbe(candidate);
        rec.byteProbes.push({ candidate: { kind: candidate.kind, quality: candidate.quality, mimeType: candidate.mimeType, host: new URL(candidate.url).host }, probe });
        if (!selected && probe.mediaLike) selected = { instance: base, candidate, byte: probe };
      }
    }
  } catch (error) {
    rec.error = String(error);
  }
  instanceResults.push(rec);
  await saveJson(path.join(out, "instances.partial.json"), instanceResults);
  await log(`api end ${base} selected=${Boolean(selected)}`);
  if (selected) break;
}

let playback: unknown = null;
if (selected) {
  const dir = path.join(out, "playback");
  await mkdir(dir, { recursive: true });
  await writeFile(path.join(dir, "selected.json"), JSON.stringify({ instance: selected.instance, kind: selected.candidate.kind, quality: selected.candidate.quality, mimeType: selected.candidate.mimeType, host: new URL(selected.candidate.url).host, byte: selected.byte }, null, 2));
  playback = await browserProbe(selected.candidate.url, dir).catch((error) => ({ success: false, error: String(error) }));
}
const summary = {
  videoId,
  runtime: "CopilotKit/OpenBot agent-computer",
  testedInstances: instanceResults.length,
  sourceFound: Boolean(selected),
  selected: selected ? { instance: selected.instance, kind: selected.candidate.kind, quality: selected.candidate.quality, mimeType: selected.candidate.mimeType, host: new URL(selected.candidate.url).host, byte: selected.byte } : null,
  playback,
  success: Boolean((playback as any)?.success),
  instanceResults,
};
await saveJson(path.join(out, "summary.json"), summary);
console.log(JSON.stringify(summary, null, 2));
