from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path
from typing import Any

from playwright.async_api import async_playwright

OUT = Path(sys.argv[1] if len(sys.argv) > 1 else "largo_referer_playback_output")
OUT.mkdir(parents=True, exist_ok=True)
VIDEOS = ["nucIUdFTkZY", "rYLFvo7LPfc"]
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/151 Safari/537.36"
HOST = "http://largo.local"


def dump(path: Path, obj: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")


async def video_state(frame) -> dict[str, Any]:
    try:
        return await frame.evaluate("""() => {const v=document.querySelector('video');if(!v)return {found:false};return {found:true,currentTime:Number(v.currentTime||0),duration:Number.isFinite(v.duration)?Number(v.duration):null,paused:Boolean(v.paused),readyState:Number(v.readyState),networkState:Number(v.networkState),width:Number(v.videoWidth||0),height:Number(v.videoHeight||0),error:v.error?{code:v.error.code,message:v.error.message||''}:null};}""")
    except Exception as exc:
        return {"found": False, "error": f"{type(exc).__name__}: {exc}"}


async def play(frame) -> bool:
    try:
        if await frame.evaluate("""async()=>{const v=document.querySelector('video');if(!v)return false;v.muted=true;v.volume=0;try{await v.play()}catch(e){}return !v.paused;}"""):
            return True
    except Exception:
        pass
    for selector in ["button.ytp-large-play-button", ".ytp-cued-thumbnail-overlay", "button.ytp-play-button"]:
        try:
            loc = frame.locator(selector)
            if await loc.count() and await loc.first.is_visible(timeout=400):
                await loc.first.click(force=True, timeout=1200)
                await frame.page.wait_for_timeout(300)
                st = await video_state(frame)
                if st.get("found") and not st.get("paused"):
                    return True
        except Exception:
            pass
    return False


def wrapper_html(video_id: str, host: str) -> str:
    origin = host.replace(":", "%3A").replace("/", "%2F")
    src = f"https://www.youtube.com/embed/{video_id}?autoplay=1&mute=1&controls=1&playsinline=1&enablejsapi=1&origin={origin}&hl=ko"
    return f"""<!doctype html><html><head><meta charset='utf-8'><meta name='referrer' content='strict-origin-when-cross-origin'><style>html,body{{margin:0;background:#111;width:100%;height:100%}}iframe{{border:0;width:100%;height:100%}}</style></head><body><iframe id='player' src='{src}' allow='autoplay; encrypted-media; picture-in-picture' referrerpolicy='strict-origin-when-cross-origin' allowfullscreen></iframe></body></html>"""


async def main() -> int:
    result: dict[str, Any] = {"videos": {}}
    async with async_playwright() as p:
        candidates = [os.getenv("CHROME_PATH", ""), "/usr/bin/google-chrome", "/usr/bin/google-chrome-stable", "/usr/bin/chromium"]
        executable = next((x for x in candidates if x and Path(x).exists()), None)
        result["chrome"] = executable
        browser = await p.chromium.launch(executable_path=executable, headless=True, args=["--no-sandbox", "--disable-dev-shm-usage", "--autoplay-policy=no-user-gesture-required", "--disable-blink-features=AutomationControlled", "--window-size=1280,800"])
        context = await browser.new_context(viewport={"width":1280,"height":800}, locale="ko-KR", timezone_id="Asia/Seoul", user_agent=UA)
        await context.add_init_script("Object.defineProperty(navigator,'webdriver',{get:()=>undefined})")
        for video_id in VIDEOS:
            d = OUT / video_id
            d.mkdir(parents=True, exist_ok=True)
            page = await context.new_page()
            async def route_handler(route, request, vid=video_id):
                if request.url.startswith(HOST):
                    await route.fulfill(status=200, content_type="text/html; charset=utf-8", body=wrapper_html(vid, HOST))
                else:
                    await route.continue_()
            await page.route(f"{HOST}/**", route_handler)
            record: dict[str, Any] = {"states": [], "wrapper": f"{HOST}/{video_id}"}
            try:
                await page.goto(f"{HOST}/{video_id}", wait_until="domcontentloaded", timeout=20_000)
                await page.wait_for_timeout(5000)
                await page.screenshot(path=str(d / "wrapper_initial.png"), full_page=False)
                youtube_frames = [f for f in page.frames if "youtube.com/embed" in f.url]
                record["frame_urls"] = [f.url for f in page.frames]
                if not youtube_frames:
                    record["error"] = "YouTube iframe was not attached"
                else:
                    frame = youtube_frames[0]
                    record["play_called"] = await play(frame)
                    for i in range(16):
                        await page.wait_for_timeout(750)
                        st = await video_state(frame)
                        st["sample"] = i
                        record["states"].append(st)
                        if i in {3,7,11,15}:
                            try:
                                await page.locator("iframe#player").screenshot(path=str(d / f"iframe_{i:02d}.png"))
                            except Exception:
                                pass
                        if st.get("paused"):
                            await play(frame)
                    times = [float(x.get("currentTime") or 0) for x in record["states"] if x.get("found")]
                    dims = [(int(x.get("width") or 0), int(x.get("height") or 0)) for x in record["states"] if x.get("found")]
                    record["time_delta"] = max(times)-min(times) if len(times)>1 else 0
                    record["max_dimensions"] = max(dims,key=lambda x:x[0]*x[1]) if dims else [0,0]
                    record["success"] = record["time_delta"] >= 7 and record["max_dimensions"][0] >= 320
            except Exception as exc:
                record["error"] = f"{type(exc).__name__}: {exc}"
            dump(d / "diagnostic.json", record)
            result["videos"][video_id] = record
            await page.close()
        await context.close()
        await browser.close()
    result["successful_videos"] = [k for k,v in result["videos"].items() if v.get("success")]
    dump(OUT / "summary.json", result)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
