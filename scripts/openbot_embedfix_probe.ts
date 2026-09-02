import { chromium } from "playwright";
import { appendFile, mkdir, writeFile } from "node:fs/promises";
import path from "node:path";

const videoId = process.env.VIDEO_ID || "nucIUdFTkZY";
const out = process.env.OUT || "/workspace/embedfix-probe";
const pages = [
  `https://fxyoutu.be/${videoId}`,
  `https://www.yfxtube.com/watch?v=${videoId}`,
  `https://koutu.be/${videoId}`,
];
const userAgents = [
  "Mozilla/5.0 (compatible; Discordbot/2.0; +https://discordapp.com)",
  "TelegramBot (like TwitterBot)",
  "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/145 Safari/537.36",
];

async function saveJson(file: string, value: unknown) {
  await mkdir(path.dirname(file), { recursive: true });
  await writeFile(file, JSON.stringify(value, null, 2), "utf8");
}
async function log(message: string) {
  await appendFile(path.join(out, "progress.log"), `${new Date().toISOString()} ${message}\n`, "utf8");
}
async function fetchTimed(url: string, init: RequestInit, ms = 30000) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), ms);
  try { return await fetch(url, { ...init, signal: controller.signal, redirect: "follow" }); }
  finally { clearTimeout(timer); }
}
function mediaCandidates(html: string, base: string) {
  const patterns = [
    /<meta[^>]+(?:property|name)=["'](?:og:video(?::url)?|twitter:player:stream)["'][^>]+content=["']([^"']+)["']/gi,
    /<meta[^>]+content=["']([^"']+)["'][^>]+(?:property|name)=["'](?:og:video(?::url)?|twitter:player:stream)["']/gi,
    /<(?:video|source)[^>]+src=["']([^"']+)["']/gi,
  ];
  const rows: string[] = [];
  for (const pattern of patterns) {
    for (const match of html.matchAll(pattern)) {
      try {
        const decoded = match[1].replaceAll("&amp;", "&");
        const resolved = new URL(decoded, base).toString();
        if (resolved.startsWith("http") && !rows.includes(resolved)) rows.push(resolved);
      } catch {}
    }
  }
  return rows;
}
async function byteProbe(url: string) {
  try {
    const response = await fetchTimed(url, { headers: { Range: "bytes=0-262143", Accept: "*/*", "User-Agent": userAgents[2] } }, 35000);
    const bytes = new Uint8Array(await response.arrayBuffer());
    return {
      status: response.status,
      finalHost: new URL(response.url).host,
      contentType: response.headers.get("content-type"),
      contentLength: response.headers.get("content-length"),
      contentRange: response.headers.get("content-range"),
      bytes: bytes.byteLength,
      prefixHex: [...bytes.slice(0, 24)].map((x) => x.toString(16).padStart(2, "0")).join(""),
      mediaLike: response.ok && bytes.byteLength > 4096 && !String(response.headers.get("content-type") || "").includes("text/html"),
    };
  } catch (error) { return { mediaLike: false, error: String(error) }; }
}
async function playbackProbe(url: string, dir: string) {
  const browser = await chromium.launch({ headless: false, args: ["--no-sandbox", "--disable-dev-shm-usage", "--autoplay-policy=no-user-gesture-required", "--window-size=1280,720"] });
  const context = await browser.newContext({ viewport: { width: 1280, height: 720 } });
  const page = await context.newPage();
  await page.setContent(`<!doctype html><html><body style="margin:0;background:#111"><video id="v" autoplay muted controls style="width:100vw;height:100vh" src="${url.replaceAll("&", "&amp;").replaceAll('"', '&quot;')}"></video></body></html>`);
  const states: any[] = [];
  for (let second = 1; second <= 12; second++) {
    await page.waitForTimeout(1000);
    states.push({ second, ...(await page.evaluate(async () => {
      const v = document.querySelector("video")!;
      v.muted = true; v.playbackRate = 2;
      try { await v.play(); } catch {}
      return { currentTime:Number(v.currentTime||0),duration:Number.isFinite(v.duration)?Number(v.duration):null,paused:v.paused,readyState:v.readyState,networkState:v.networkState,width:v.videoWidth||0,height:v.videoHeight||0,error:v.error?{code:v.error.code,message:v.error.message||""}:null };
    }).catch((error) => ({ error:String(error) })) ) });
    if ([2,6,12].includes(second)) await page.screenshot({ path:path.join(dir,`play_${String(second).padStart(2,"0")}.png`) }).catch(()=>undefined);
  }
  const times=states.map((x)=>Number(x.currentTime||0));
  const pixels=states.map((x)=>Number(x.width||0)*Number(x.height||0));
  const result={states,timeDelta:times.length>1?Math.max(...times)-Math.min(...times):0,maxPixels:pixels.length?Math.max(...pixels):0};
  await context.close(); await browser.close();
  return {...result,success:result.timeDelta>=4&&result.maxPixels>=320*180};
}

await mkdir(out,{recursive:true});
const responses: any[]=[];
let selected: {url:string,page:string,agent:string,probe:any}|null=null;
for(const pageUrl of pages){
  for(const agent of userAgents){
    const rec:any={pageUrl,userAgent:agent};
    await log(`fetch ${pageUrl} ${agent.slice(0,18)}`);
    try{
      const response=await fetchTimed(pageUrl,{headers:{"User-Agent":agent,Accept:"text/html,application/xhtml+xml"}},30000);
      const html=await response.text();
      rec.status=response.status;rec.finalUrl=response.url;rec.contentType=response.headers.get("content-type");rec.bytes=html.length;rec.preview=html.slice(0,1200);
      const candidates=mediaCandidates(html,response.url);
      rec.candidateCount=candidates.length;rec.candidates=[];
      for(const candidate of candidates.slice(0,10)){
        const probe=await byteProbe(candidate);
        rec.candidates.push({host:new URL(candidate).host,probe});
        if(!selected&&probe.mediaLike) selected={url:candidate,page:pageUrl,agent,probe};
      }
    }catch(error){rec.error=String(error)}
    responses.push(rec);await saveJson(path.join(out,"responses.partial.json"),responses);
    if(selected)break;
  }
  if(selected)break;
}
let playback:any=null;
if(selected){
  const dir=path.join(out,"playback");await mkdir(dir,{recursive:true});
  await saveJson(path.join(dir,"selected.json"),{page:selected.page,agent:selected.agent,host:new URL(selected.url).host,probe:selected.probe});
  playback=await playbackProbe(selected.url,dir).catch((error)=>({success:false,error:String(error)}));
}
const summary={videoId,runtime:"CopilotKit/OpenBot agent-computer",sourceFound:Boolean(selected),selected:selected?{page:selected.page,agent:selected.agent,host:new URL(selected.url).host,probe:selected.probe}:null,playback,success:Boolean(playback?.success),responses};
await saveJson(path.join(out,"summary.json"),summary);
console.log(JSON.stringify(summary,null,2));
