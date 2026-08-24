from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path
from typing import Any

from playwright.async_api import async_playwright

OUT = Path(sys.argv[1] if len(sys.argv) > 1 else "largo_fast_playback_output")
OUT.mkdir(parents=True, exist_ok=True)
VIDEOS = ["nucIUdFTkZY", "rYLFvo7LPfc"]
VARIANTS = [
    ("embed", "https://www.youtube.com/embed/{id}?autoplay=1&mute=1&controls=1&playsinline=1&hl=ko"),
    ("nocookie", "https://www.youtube-nocookie.com/embed/{id}?autoplay=1&mute=1&controls=1&playsinline=1&hl=ko"),
]
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/151 Safari/537.36"


def dump(path: Path, obj: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")


async def get_state(page) -> dict[str, Any]:
    try:
        return await page.evaluate("""() => {const v=document.querySelector('video');if(!v)return {found:false};return {found:true,currentTime:Number(v.currentTime||0),duration:Number.isFinite(v.duration)?Number(v.duration):null,paused:Boolean(v.paused),readyState:Number(v.readyState),networkState:Number(v.networkState),width:Number(v.videoWidth||0),height:Number(v.videoHeight||0),error:v.error?{code:v.error.code,message:v.error.message||''}:null};}""")
    except Exception as exc:
        return {"found": False, "error": f"{type(exc).__name__}: {exc}"}


async def ensure_play(page) -> bool:
    try:
        if await page.evaluate("""async()=>{const v=document.querySelector('video');if(!v)return false;v.muted=true;v.volume=0;try{await v.play()}catch(e){}return !v.paused;}"""):
            return True
    except Exception:
        pass
    for selector in ["button.ytp-large-play-button", ".ytp-cued-thumbnail-overlay", "button.ytp-play-button"]:
        try:
            loc = page.locator(selector)
            if await loc.count() and await loc.first.is_visible(timeout=300):
                await loc.first.click(force=True, timeout=1200)
                await page.wait_for_timeout(300)
                state = await get_state(page)
                if state.get("found") and not state.get("paused"):
                    return True
        except Exception:
            pass
    return False


async def main() -> int:
    result: dict[str, Any] = {"videos": {}}
    async with async_playwright() as p:
        candidates = [os.getenv("CHROME_PATH", ""), "/usr/bin/google-chrome", "/usr/bin/google-chrome-stable", "/usr/bin/chromium"]
        executable = next((x for x in candidates if x and Path(x).exists()), None)
        result["chrome"] = executable
        browser = await p.chromium.launch(executable_path=executable, headless=True, args=["--no-sandbox", "--disable-dev-shm-usage", "--autoplay-policy=no-user-gesture-required", "--disable-blink-features=AutomationControlled", "--window-size=1280,800"])
        context = await browser.new_context(viewport={"width":1280,"height":800},locale="ko-KR",timezone_id="Asia/Seoul",user_agent=UA)
        await context.add_init_script("Object.defineProperty(navigator,'webdriver',{get:()=>undefined})")
        for video_id in VIDEOS:
            video_result: dict[str, Any] = {"attempts": []}
            for name, template in VARIANTS:
                page = await context.new_page()
                d = OUT / video_id / name
                d.mkdir(parents=True, exist_ok=True)
                attempt: dict[str, Any] = {"variant": name, "states": []}
                try:
                    await page.goto(template.format(id=video_id), wait_until="domcontentloaded", timeout=20_000)
                    await page.wait_for_timeout(2500)
                    await page.screenshot(path=str(d / "initial.png"), full_page=False)
                    attempt["play_called"] = await ensure_play(page)
                    for index in range(12):
                        await page.wait_for_timeout(750)
                        state = await get_state(page)
                        state["sample"] = index
                        attempt["states"].append(state)
                        if index in {3, 7, 11}:
                            try:
                                video = page.locator("video")
                                if await video.count() and await video.first.is_visible(timeout=250):
                                    await video.first.screenshot(path=str(d / f"video_{index:02d}.png"))
                                else:
                                    await page.screenshot(path=str(d / f"viewport_{index:02d}.png"), full_page=False)
                            except Exception:
                                pass
                        if state.get("paused"):
                            await ensure_play(page)
                    times = [float(x.get("currentTime") or 0) for x in attempt["states"] if x.get("found")]
                    dims = [(int(x.get("width") or 0), int(x.get("height") or 0)) for x in attempt["states"] if x.get("found")]
                    attempt["time_delta"] = max(times)-min(times) if len(times)>1 else 0
                    attempt["max_dimensions"] = max(dims,key=lambda x:x[0]*x[1]) if dims else [0,0]
                    attempt["success"] = attempt["time_delta"] >= 6 and attempt["max_dimensions"][0] >= 320
                except Exception as exc:
                    attempt["error"] = f"{type(exc).__name__}: {exc}"
                dump(d / "diagnostic.json", attempt)
                video_result["attempts"].append(attempt)
                await page.close()
                if attempt.get("success"):
                    video_result["success_variant"] = name
                    break
            result["videos"][video_id] = video_result
        await context.close()
        await browser.close()
    result["successful_videos"] = [k for k,v in result["videos"].items() if v.get("success_variant")]
    dump(OUT / "summary.json", result)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
