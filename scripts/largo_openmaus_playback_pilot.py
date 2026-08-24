from __future__ import annotations

import asyncio
import json
import os
import re
import sys
from pathlib import Path
from typing import Any

from playwright.async_api import async_playwright

OUT = Path(sys.argv[1] if len(sys.argv) > 1 else "largo_openmaus_playback_output")
OUT.mkdir(parents=True, exist_ok=True)
VIDEO_IDS = ["nucIUdFTkZY", "rYLFvo7LPfc"]
VARIANTS = [
    ("watch", "https://www.youtube.com/watch?v={id}&hl=ko&gl=KR"),
    ("embed", "https://www.youtube.com/embed/{id}?autoplay=1&mute=1&controls=1&playsinline=1&hl=ko"),
    ("nocookie", "https://www.youtube-nocookie.com/embed/{id}?autoplay=1&mute=1&controls=1&playsinline=1&hl=ko"),
]
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/151 Safari/537.36"


def dump(path: Path, obj: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")


async def state(page) -> dict[str, Any]:
    try:
        return await page.evaluate("""() => {const v=document.querySelector('video');if(!v)return {found:false};return {found:true,currentTime:Number(v.currentTime||0),duration:Number.isFinite(v.duration)?Number(v.duration):null,paused:Boolean(v.paused),readyState:Number(v.readyState),networkState:Number(v.networkState),width:Number(v.videoWidth||0),height:Number(v.videoHeight||0),error:v.error?{code:v.error.code,message:v.error.message||''}:null,src:v.currentSrc||v.src||''};}""")
    except Exception as exc:
        return {"found": False, "error": f"{type(exc).__name__}: {exc}"}


async def force_play(page, rate: float = 1.0) -> bool:
    try:
        playing = bool(await page.evaluate("""async rate => {const v=document.querySelector('video');if(!v)return false;v.muted=true;v.volume=0;v.playbackRate=rate;try{await v.play()}catch(e){}return !v.paused;}""", rate))
        if playing:
            return True
    except Exception:
        pass
    for selector in ["button.ytp-large-play-button", ".ytp-cued-thumbnail-overlay", "button.ytp-play-button", "video"]:
        try:
            loc = page.locator(selector)
            if await loc.count() and await loc.first.is_visible(timeout=400):
                await loc.first.click(force=True, timeout=1500)
                await page.wait_for_timeout(300)
                st = await state(page)
                if st.get("found") and not st.get("paused"):
                    try:
                        await page.evaluate("rate => {const v=document.querySelector('video');if(v)v.playbackRate=rate}", rate)
                    except Exception:
                        pass
                    return True
        except Exception:
            pass
    return False


async def dismiss(page) -> None:
    for label in [r"모두 수락", r"모두 거부", r"동의", r"Accept all", r"Reject all", r"I agree", r"No thanks"]:
        try:
            loc = page.get_by_role("button", name=re.compile(label, re.I))
            if await loc.count() and await loc.first.is_visible(timeout=400):
                await loc.first.click(timeout=1500)
                await page.wait_for_timeout(500)
                return
        except Exception:
            pass


async def main() -> None:
    summary: dict[str, Any] = {"videos": {}}
    async with async_playwright() as p:
        candidates = [os.getenv("CHROME_PATH", ""), "/usr/bin/google-chrome", "/usr/bin/google-chrome-stable", "/usr/bin/chromium"]
        executable = next((x for x in candidates if x and Path(x).exists()), None)
        summary["chrome"] = executable
        browser = await p.chromium.launch(executable_path=executable, headless=True, args=["--no-sandbox", "--disable-dev-shm-usage", "--autoplay-policy=no-user-gesture-required", "--disable-blink-features=AutomationControlled", "--window-size=1600,1000", "--lang=ko-KR"])
        context = await browser.new_context(viewport={"width":1600,"height":1000}, locale="ko-KR", timezone_id="Asia/Seoul", user_agent=UA)
        await context.add_init_script("Object.defineProperty(navigator,'webdriver',{get:()=>undefined})")
        for vid in VIDEO_IDS:
            result: dict[str, Any] = {"attempts": []}
            for name, template in VARIANTS:
                page = await context.new_page()
                d = OUT / vid / name
                d.mkdir(parents=True, exist_ok=True)
                media_urls: list[str] = []
                page.on("response", lambda r: media_urls.append(r.url) if ("googlevideo.com" in r.url or "videoplayback" in r.url or ".m3u8" in r.url) and r.url not in media_urls else None)
                rec: dict[str, Any] = {"variant": name, "url": template.format(id=vid), "states": []}
                try:
                    await page.goto(rec["url"], wait_until="domcontentloaded", timeout=90000)
                    await dismiss(page)
                    await page.wait_for_timeout(3500)
                    rec["title"] = await page.title()
                    rec["final_url"] = page.url
                    try:
                        body = (await page.locator("body").inner_text())[:8000]
                    except Exception:
                        body = ""
                    rec["body_preview"] = body
                    rec["markers"] = [m for m in ["Sign in to confirm", "로그인하여 본인 확인", "Video unavailable", "동영상을 재생할 수 없음", "This content isn't available", "봇이 아님을"] if m.lower() in body.lower()]
                    await page.screenshot(path=str(d / "viewport_initial.png"), full_page=False)
                    await force_play(page)
                    for i in range(1, 17):
                        await page.wait_for_timeout(1000)
                        st = await state(page)
                        st["wall_seconds"] = i
                        rec["states"].append(st)
                        if i in {1,2,3,5,8,12,16}:
                            target = d / f"frame_{i:02d}_{i:03d}s.png"
                            try:
                                v = page.locator("video")
                                if await v.count() and await v.first.is_visible(timeout=250):
                                    await v.first.screenshot(path=str(target))
                                else:
                                    await page.screenshot(path=str(target), full_page=False)
                            except Exception:
                                pass
                        if st.get("paused") or i in {6, 12}:
                            await force_play(page)
                    times = [float(x.get("currentTime") or 0) for x in rec["states"] if x.get("found")]
                    dims = [(int(x.get("width") or 0), int(x.get("height") or 0)) for x in rec["states"] if x.get("found")]
                    rec["time_delta"] = max(times) - min(times) if len(times) > 1 else 0
                    rec["max_dimensions"] = max(dims, key=lambda x:x[0]*x[1]) if dims else [0,0]
                    rec["media_url_count"] = len(media_urls)
                    rec["playback_success"] = rec["time_delta"] >= 6 and rec["max_dimensions"][0] >= 320
                    (d / "media_urls.txt").write_text("\n".join(media_urls), encoding="utf-8")
                except Exception as exc:
                    rec["error"] = f"{type(exc).__name__}: {exc}"
                dump(d / "diagnostic.json", rec)
                result["attempts"].append(rec)
                await page.close()
                if rec.get("playback_success"):
                    result["success_variant"] = name
                    break
            summary["videos"][vid] = result
        await context.close()
        await browser.close()
    summary["successful_videos"] = [k for k,v in summary["videos"].items() if v.get("success_variant")]
    dump(OUT / "summary.json", summary)
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
