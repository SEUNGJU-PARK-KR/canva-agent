from __future__ import annotations

import asyncio
import csv
import hashlib
import io
import json
import os
import re
import sys
import time
from pathlib import Path
from typing import Any

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont
from playwright.async_api import async_playwright

OUT = Path(sys.argv[1] if len(sys.argv) > 1 else "largo_full_target_output")
OUT.mkdir(parents=True, exist_ok=True)
VIDEO_ID = "nucIUdFTkZY"
VARIANTS = [
    ("watch", f"https://www.youtube.com/watch?v={VIDEO_ID}&hl=ko&gl=KR"),
    ("embed", f"https://www.youtube.com/embed/{VIDEO_ID}?autoplay=1&mute=1&controls=1&playsinline=1&hl=ko"),
    ("nocookie", f"https://www.youtube-nocookie.com/embed/{VIDEO_ID}?autoplay=1&mute=1&controls=1&playsinline=1&hl=ko"),
]
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/151 Safari/537.36"


def dump(path: Path, obj: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        path.write_text("", encoding="utf-8-sig")
        return
    fields: list[str] = []
    for row in rows:
        for key in row:
            if key not in fields:
                fields.append(key)
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def frame_metrics(data: bytes, previous: np.ndarray | None) -> tuple[dict[str, Any], np.ndarray]:
    arr = np.asarray(Image.open(io.BytesIO(data)).convert("RGB"))
    bgr = cv2.cvtColor(arr, cv2.COLOR_RGB2BGR)
    small = cv2.resize(bgr, (360, max(1, int(360 * bgr.shape[0] / bgr.shape[1]))))
    hsv = cv2.cvtColor(small, cv2.COLOR_BGR2HSV)
    gray = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)
    red = ((cv2.inRange(hsv, (0, 65, 45), (13, 255, 255)) > 0) | (cv2.inRange(hsv, (168, 65, 45), (180, 255, 255)) > 0))
    blue = cv2.inRange(hsv, (88, 45, 35), (140, 255, 255)) > 0
    edges = cv2.Canny(gray, 55, 145) > 0
    red_ratio = float(red.mean())
    blue_ratio = float(blue.mean())
    edge_ratio = float(edges.mean())
    scene_score = float(cv2.absdiff(gray, previous).mean()) if previous is not None else 0.0
    orderbook_score = min(1.0, (red_ratio + blue_ratio) * 9.5 + edge_ratio * 2.2)
    likely_orderbook = bool(red_ratio > 0.0022 and blue_ratio > 0.0022 and edge_ratio > 0.045 and orderbook_score > 0.30)
    digest = hashlib.sha256(data).hexdigest()
    return {
        "red_ratio": round(red_ratio, 6),
        "blue_ratio": round(blue_ratio, 6),
        "edge_ratio": round(edge_ratio, 6),
        "scene_score": round(scene_score, 4),
        "orderbook_score": round(orderbook_score, 4),
        "likely_orderbook": likely_orderbook,
        "sha256": digest,
    }, gray


def make_sheet(paths: list[Path], output: Path, cols: int = 4) -> None:
    images: list[tuple[Path, Image.Image]] = []
    for path in paths:
        try:
            images.append((path, Image.open(path).convert("RGB")))
        except Exception:
            pass
    if not images:
        return
    tw, th, lh = 400, 225, 26
    rows = (len(images) + cols - 1) // cols
    sheet = Image.new("RGB", (tw * cols, (th + lh) * rows), "white")
    draw = ImageDraw.Draw(sheet)
    font = ImageFont.load_default()
    for i, (path, image) in enumerate(images):
        image.thumbnail((tw, th))
        cell = Image.new("RGB", (tw, th), "#111827")
        cell.paste(image, ((tw - image.width) // 2, (th - image.height) // 2))
        x = (i % cols) * tw
        y = (i // cols) * (th + lh)
        sheet.paste(cell, (x, y))
        draw.text((x + 6, y + th + 6), path.name[:58], fill="#111827", font=font)
    output.parent.mkdir(parents=True, exist_ok=True)
    sheet.save(output, quality=90)


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


async def get_state(page) -> dict[str, Any]:
    try:
        return await page.evaluate("""() => {const v=document.querySelector('video');if(!v)return {found:false};return {found:true,currentTime:Number(v.currentTime||0),duration:Number.isFinite(v.duration)?Number(v.duration):null,paused:v.paused,readyState:v.readyState,networkState:v.networkState,width:v.videoWidth||0,height:v.videoHeight||0,error:v.error?{code:v.error.code,message:v.error.message||''}:null};}""")
    except Exception as exc:
        return {"found": False, "state_error": f"{type(exc).__name__}: {exc}"}


async def force_play(page, rate: float = 2.0) -> None:
    try:
        await page.evaluate("""async (rate) => {const v=document.querySelector('video');if(v){v.muted=true;v.volume=0;v.playbackRate=rate;try{await v.play()}catch(e){}}}""", rate)
    except Exception:
        pass
    for selector in ["button.ytp-large-play-button", "button.ytp-play-button", ".ytp-cued-thumbnail-overlay", "video"]:
        try:
            loc = page.locator(selector)
            if await loc.count() and await loc.first.is_visible(timeout=300):
                await loc.first.click(force=True, timeout=1200)
                await page.wait_for_timeout(300)
        except Exception:
            pass
    try:
        await page.keyboard.press("c")
    except Exception:
        pass


async def captions(page) -> str:
    try:
        values = await page.locator(".ytp-caption-segment").all_inner_texts()
        return " ".join(x.strip() for x in values if x.strip())
    except Exception:
        return ""


async def choose_variant(context) -> tuple[str | None, list[dict[str, Any]]]:
    tests: list[dict[str, Any]] = []
    for name, url in VARIANTS:
        page = await context.new_page()
        rec: dict[str, Any] = {"variant": name, "url": url, "states": []}
        try:
            await page.goto(url, wait_until="domcontentloaded", timeout=90000)
            await dismiss(page)
            await page.wait_for_timeout(4000)
            await force_play(page, 2.0)
            for i in range(12):
                await page.wait_for_timeout(750)
                rec["states"].append(await get_state(page))
                if i in {4, 8}:
                    await force_play(page, 2.0)
            times = [float(x.get("currentTime") or 0) for x in rec["states"] if x.get("found")]
            dims = [(int(x.get("width") or 0), int(x.get("height") or 0)) for x in rec["states"] if x.get("found")]
            rec["time_delta"] = max(times) - min(times) if len(times) > 1 else 0
            rec["max_dimensions"] = max(dims, key=lambda p: p[0] * p[1]) if dims else [0, 0]
            rec["success"] = rec["time_delta"] >= 5 and rec["max_dimensions"][0] >= 320
        except Exception as exc:
            rec["error"] = f"{type(exc).__name__}: {exc}"
        tests.append(rec)
        await page.close()
        if rec.get("success"):
            return name, tests
    return None, tests


async def main() -> int:
    frames_dir = OUT / "frames"
    frames_dir.mkdir(parents=True, exist_ok=True)
    summary: dict[str, Any] = {"video_id": VIDEO_ID, "capture_rows": 0, "saved_frames": 0}
    async with async_playwright() as p:
        candidates = [os.getenv("CHROME_PATH", ""), "/usr/bin/google-chrome", "/usr/bin/google-chrome-stable", "/usr/bin/chromium"]
        executable = next((x for x in candidates if x and Path(x).exists()), None)
        summary["chrome"] = executable
        browser = await p.chromium.launch(executable_path=executable, headless=True, args=["--no-sandbox", "--disable-dev-shm-usage", "--autoplay-policy=no-user-gesture-required", "--disable-blink-features=AutomationControlled", "--window-size=1600,1000", "--lang=ko-KR"])
        context = await browser.new_context(viewport={"width": 1600, "height": 1000}, locale="ko-KR", timezone_id="Asia/Seoul", user_agent=UA)
        await context.add_init_script("Object.defineProperty(navigator,'webdriver',{get:()=>undefined})")
        variant, tests = await choose_variant(context)
        dump(OUT / "variant_tests.json", tests)
        summary["chosen_variant"] = variant
        if not variant:
            summary["error"] = "No tested YouTube page produced genuine advancing video playback"
            dump(OUT / "summary.json", summary)
            await context.close()
            await browser.close()
            return 2
        url = dict(VARIANTS)[variant]
        page = await context.new_page()
        await page.goto(url, wait_until="domcontentloaded", timeout=90000)
        await dismiss(page)
        await page.wait_for_timeout(4000)
        await force_play(page, 2.0)
        rows: list[dict[str, Any]] = []
        caption_rows: list[dict[str, Any]] = []
        previous: np.ndarray | None = None
        last_saved_t = -999.0
        dense_until = -1.0
        last_caption = ""
        stalls = 0
        last_t = -1.0
        wall_start = time.monotonic()
        while True:
            await page.wait_for_timeout(500)
            st = await get_state(page)
            if not st.get("found"):
                stalls += 1
                if stalls > 30:
                    break
                await force_play(page, 2.0)
                continue
            t = float(st.get("currentTime") or 0)
            duration = float(st.get("duration") or 0)
            if t <= last_t + 0.02:
                stalls += 1
            else:
                stalls = 0
            last_t = max(last_t, t)
            if stalls >= 4:
                await force_play(page, 2.0)
            v = page.locator("video")
            try:
                data = await v.screenshot(type="jpeg", quality=78)
            except Exception:
                data = await page.screenshot(type="jpeg", quality=78, full_page=False)
            metrics, previous = frame_metrics(data, previous)
            if metrics["likely_orderbook"]:
                dense_until = max(dense_until, t + 12.0)
            save_interval = 0.5 if t <= dense_until else 1.0
            save = (t - last_saved_t >= save_interval) or metrics["scene_score"] >= 18.0
            frame_name = ""
            if save:
                frame_name = f"t_{t:010.3f}s.jpg"
                (frames_dir / frame_name).write_bytes(data)
                last_saved_t = t
            cap = await captions(page)
            if cap and cap != last_caption:
                caption_rows.append({"time_seconds": round(t, 3), "caption": cap})
                last_caption = cap
            rows.append({
                "time_seconds": round(t, 3),
                "duration_seconds": round(duration, 3),
                "wall_seconds": round(time.monotonic() - wall_start, 3),
                "saved_frame": frame_name,
                "caption": cap,
                **metrics,
            })
            if duration and t >= duration - 0.8:
                break
            if time.monotonic() - wall_start > 900:
                break
        await page.close()
        await context.close()
        await browser.close()

    write_csv(OUT / "frame_manifest.csv", rows)
    write_csv(OUT / "captions_from_screen.csv", caption_rows)
    saved = [frames_dir / row["saved_frame"] for row in rows if row.get("saved_frame")]
    orderbook = [frames_dir / row["saved_frame"] for row in rows if row.get("saved_frame") and row.get("likely_orderbook")]
    scene = sorted((row for row in rows if row.get("saved_frame")), key=lambda x: float(x.get("scene_score") or 0), reverse=True)[:48]
    make_sheet([frames_dir / row["saved_frame"] for row in scene], OUT / "contact_sheet_scene_changes.jpg")
    make_sheet(orderbook[:96], OUT / "contact_sheet_orderbook_candidates.jpg")
    max_t = max((float(row.get("time_seconds") or 0) for row in rows), default=0)
    duration = max((float(row.get("duration_seconds") or 0) for row in rows), default=0)
    summary.update({
        "capture_rows": len(rows),
        "saved_frames": len(saved),
        "orderbook_candidate_frames": len(orderbook),
        "screen_caption_events": len(caption_rows),
        "max_video_time": round(max_t, 3),
        "duration_seconds": round(duration, 3),
        "completed_to_end": bool(duration and max_t >= duration - 1.5),
        "wall_seconds": round(rows[-1]["wall_seconds"], 3) if rows else 0,
    })
    dump(OUT / "summary.json", summary)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if summary["completed_to_end"] else 3


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
