from pathlib import Path

path = Path("scripts/largo_full_target_capture.py")
text = path.read_text(encoding="utf-8")
old = '''async def force_play(page, rate: float = 2.0) -> None:
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
'''
new = '''async def force_play(page, rate: float = 2.0) -> bool:
    try:
        playing = bool(await page.evaluate("""async rate => {const v=document.querySelector('video');if(!v)return false;v.muted=true;v.volume=0;v.playbackRate=rate;try{await v.play()}catch(e){}return !v.paused;}""", rate))
        if playing:
            return True
    except Exception:
        pass
    for selector in ["button.ytp-large-play-button", ".ytp-cued-thumbnail-overlay", "button.ytp-play-button", "video"]:
        try:
            loc = page.locator(selector)
            if await loc.count() and await loc.first.is_visible(timeout=300):
                await loc.first.click(force=True, timeout=1200)
                await page.wait_for_timeout(250)
                st = await get_state(page)
                if st.get("found") and not st.get("paused"):
                    try:
                        await page.evaluate("rate => {const v=document.querySelector('video');if(v)v.playbackRate=rate}", rate)
                    except Exception:
                        pass
                    return True
        except Exception:
            pass
    return False
'''
if old not in text:
    raise SystemExit("force_play block not found")
text = text.replace(old, new)
needle = '''        await page.wait_for_timeout(4000)
        await force_play(page, 2.0)
        rows: list[dict[str, Any]] = []
'''
replacement = '''        await page.wait_for_timeout(4000)
        await force_play(page, 2.0)
        try:
            await page.keyboard.press("c")
        except Exception:
            pass
        rows: list[dict[str, Any]] = []
'''
if needle not in text:
    raise SystemExit("main play block not found")
text = text.replace(needle, replacement)
path.write_text(text, encoding="utf-8")
print("patched", path)
