from __future__ import annotations

import csv
import html
import json
import os
import re
import shutil
import subprocess
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

import requests
from bs4 import BeautifulSoup
from PIL import Image, ImageDraw, ImageFont, ImageOps

BATCH_INDEX = int(sys.argv[1])
BATCH_COUNT = int(sys.argv[2])
INVENTORY_PATH = Path(sys.argv[3])
OUT = Path(sys.argv[4])
OUT.mkdir(parents=True, exist_ok=True)
(OUT / "items").mkdir(exist_ok=True)
(OUT / "logs").mkdir(exist_ok=True)

FOCUS_ID = "nucIUdFTkZY"
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/151 Safari/537.36"
SESSION = requests.Session()
SESSION.headers.update({
    "User-Agent": UA,
    "Accept-Language": "ko-KR,ko;q=0.9,en-US;q=0.8,en;q=0.7",
})

TAXONOMY: dict[str, list[str]] = {
    "거래대금·수급": ["거래대금", "거래량", "수급", "외국인", "기관", "순매수", "상승률 상위", "거래대금 상위"],
    "시장·섹터": ["시장", "코스피", "코스닥", "섹터", "테마", "순환매", "관련주", "동반 상승"],
    "대장주 선정": ["대장주", "주도주", "대장", "상대강도", "종목 선정", "종목선정", "후보 종목", "관심 종목"],
    "재료 등급": ["재료", "뉴스", "공시", "A급", "B급", "S급", "호재", "악재", "이슈"],
    "기준봉·상한가": ["기준봉", "상한가", "장대양봉", "장대 음봉", "기준 캔들", "상한가 이후"],
    "매집·물량 소화": ["매집", "물량 소화", "물량소화", "매물 소화", "매물대 소화", "세력 물량", "대량 물량", "물량을 받"],
    "차트 패턴": ["양음양", "적삼병", "유성형", "십자형", "도지", "U자", "N파", "쌍바닥", "V자", "박스권", "신고가", "갭", "전고점"],
    "이평선·추세": ["이평선", "이동평균", "5일선", "20일선", "60일선", "120일선", "240일선", "추세선", "상승 추세", "하락 추세"],
    "눌림 매수": ["눌림", "눌림목", "분할 매수", "분할매수", "지지선 매수", "저점 매수", "지지 확인"],
    "돌파 매수": ["돌파", "전고 돌파", "고가 돌파", "신고가 돌파", "박스 상단", "상단 돌파", "추세 돌파"],
    "시가·종가": ["시초가", "시가 매매", "시가매매", "종가 매매", "종가매매", "종가 베팅", "갭상승", "갭 하락"],
    "호가·체결": ["호가", "체결", "체결강도", "매도벽", "매수벽", "잔량", "프로그램 매수", "프로그램 매도", "VI", "브이아이"],
    "스캘핑": ["스캘핑", "초단타", "1분봉", "3분봉", "5분봉", "틱봉"],
    "스윙": ["스윙", "단기 스윙", "단기스윙", "중기", "보유"],
    "청산·익절": ["청산", "익절", "매도", "수익 실현", "수익실현", "상승 1파", "1파 매도", "원칙 매도"],
    "손절·무효화": ["손절", "손절가", "손절선", "이탈", "무효화", "매수 근거", "시나리오 무효", "대응선"],
    "위험·비중": ["비중", "몰빵", "분할", "리스크", "위험", "계좌", "최대 손실", "손실 관리"],
}

CUE_TS_RE = re.compile(r"(?P<s>\d{1,2}:\d{2}:\d{2}[.,]\d{3}|\d{1,2}:\d{2}[.,]\d{3})\s+-->\s+(?P<e>\d{1,2}:\d{2}:\d{2}[.,]\d{3}|\d{1,2}:\d{2}[.,]\d{3})")
TAG_RE = re.compile(r"<[^>]+>")
SPACE_RE = re.compile(r"\s+")


def safe_name(value: str) -> str:
    value = re.sub(r"[^0-9A-Za-z가-힣._-]+", "_", value or "")
    return value.strip("_")[:120] or "item"


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, default=str), encoding="utf-8")


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


def run(cmd: list[str], log_path: Path, timeout: int = 240) -> tuple[int, str]:
    try:
        proc = subprocess.run(cmd, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=timeout)
        text = proc.stdout or ""
        log_path.parent.mkdir(parents=True, exist_ok=True)
        log_path.write_text(text, encoding="utf-8", errors="replace")
        return proc.returncode, text
    except subprocess.TimeoutExpired as exc:
        text = (exc.stdout or "") if isinstance(exc.stdout, str) else ""
        text += f"\nTIMEOUT after {timeout}s\n"
        log_path.write_text(text, encoding="utf-8", errors="replace")
        return 124, text
    except Exception as exc:
        text = f"{type(exc).__name__}: {exc}"
        log_path.write_text(text, encoding="utf-8", errors="replace")
        return 125, text


def parse_time(value: str) -> float:
    value = value.replace(",", ".")
    parts = value.split(":")
    try:
        if len(parts) == 3:
            return int(parts[0]) * 3600 + int(parts[1]) * 60 + float(parts[2])
        if len(parts) == 2:
            return int(parts[0]) * 60 + float(parts[1])
    except ValueError:
        pass
    return 0.0


def clean_cue(text: str) -> str:
    text = html.unescape(TAG_RE.sub(" ", text))
    text = text.replace("&nbsp;", " ")
    return SPACE_RE.sub(" ", text).strip()


def parse_vtt(path: Path) -> list[dict[str, Any]]:
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    cues: list[dict[str, Any]] = []
    i = 0
    while i < len(lines):
        match = CUE_TS_RE.search(lines[i])
        if not match:
            i += 1
            continue
        start = parse_time(match.group("s"))
        end = parse_time(match.group("e"))
        i += 1
        buf: list[str] = []
        while i < len(lines) and lines[i].strip():
            if not CUE_TS_RE.search(lines[i]):
                buf.append(lines[i])
            i += 1
        text = clean_cue(" ".join(buf))
        if text:
            cues.append({"start": round(start, 3), "end": round(end, 3), "text": text})
        i += 1
    # YouTube auto captions often duplicate rolling text. Remove exact consecutive duplicates.
    deduped: list[dict[str, Any]] = []
    for cue in cues:
        if deduped and cue["text"] == deduped[-1]["text"]:
            deduped[-1]["end"] = cue["end"]
        else:
            deduped.append(cue)
    return deduped


def classify_text(text: str) -> dict[str, list[str]]:
    found: dict[str, list[str]] = {}
    low = text.lower()
    for concept, keywords in TAXONOMY.items():
        hits = [kw for kw in keywords if kw.lower() in low]
        if hits:
            found[concept] = hits
    return found


def captions_for_item(item_dir: Path) -> list[Path]:
    files = list(item_dir.glob("*.vtt")) + list(item_dir.glob("*.srt"))
    def rank(p: Path) -> tuple[int, str]:
        name = p.name.lower()
        if ".ko-orig." in name:
            return (0, name)
        if ".ko." in name:
            return (1, name)
        if ".ko-" in name:
            return (2, name)
        if ".en." in name:
            return (3, name)
        return (9, name)
    return sorted(set(files), key=rank)


def download_with_ytdlp(url: str, item_dir: Path) -> dict[str, Any]:
    outtmpl = str(item_dir / "%(id)s.%(ext)s")
    base = [
        "yt-dlp", "--no-playlist", "--ignore-errors", "--no-overwrites",
        "--skip-download", "--write-info-json", "--write-description",
        "--write-subs", "--write-auto-subs", "--sub-format", "vtt",
        "--sub-langs", "ko-orig,ko.*,ko,en.*", "--js-runtimes", "node",
        "--remote-components", "ejs:github", "--socket-timeout", "35",
        "--retries", "2", "--fragment-retries", "2", "-o", outtmpl, url,
    ]
    code, text = run(base, item_dir / "yt_dlp_default.log", timeout=300)
    if not captions_for_item(item_dir):
        alt = base[:-2]
        alt[1:1] = ["--extractor-args", "youtube:player_client=web_embedded,mweb,tv,web;formats=missing_pot"]
        alt += [outtmpl, url]
        code2, text2 = run(alt, item_dir / "yt_dlp_alt_clients.log", timeout=300)
        code = code if code == 0 else code2
        text += "\n--- ALT CLIENTS ---\n" + text2
    return {
        "exit_code": code,
        "caption_files": [p.name for p in captions_for_item(item_dir)],
        "info_json": [p.name for p in item_dir.glob("*.info.json")],
        "description": [p.name for p in item_dir.glob("*.description")],
        "login_required": "Sign in to confirm" in text or "LOGIN_REQUIRED" in text,
        "error_tail": "\n".join(text.splitlines()[-12:]),
    }


def try_direct_timedtext(video_id: str, item_dir: Path) -> list[str]:
    saved: list[str] = []
    candidates = [
        ("ko", ""), ("ko", "asr"), ("ko-KR", ""), ("en", ""), ("en", "asr"),
    ]
    for lang, kind in candidates:
        params = {"v": video_id, "lang": lang, "fmt": "vtt"}
        if kind:
            params["kind"] = kind
        try:
            r = SESSION.get("https://www.youtube.com/api/timedtext", params=params, timeout=35)
            if r.status_code == 200 and "-->" in r.text and len(r.text) > 120:
                name = f"{video_id}.{lang}{'-'+kind if kind else ''}.direct.vtt"
                (item_dir / name).write_text(r.text, encoding="utf-8")
                saved.append(name)
        except Exception:
            pass
    return saved


def try_transcript_api(video_id: str, item_dir: Path) -> dict[str, Any]:
    try:
        from youtube_transcript_api import YouTubeTranscriptApi
        api = YouTubeTranscriptApi()
        transcript_list = api.list(video_id)
        chosen = None
        for lang in ("ko", "ko-KR", "en"):
            try:
                chosen = transcript_list.find_transcript([lang])
                break
            except Exception:
                pass
        if chosen is None:
            try:
                chosen = transcript_list.find_generated_transcript(["ko", "ko-KR", "en"])
            except Exception:
                pass
        if chosen is None:
            return {"status": "not_found"}
        fetched = chosen.fetch()
        rows = []
        for item in fetched:
            text = getattr(item, "text", "")
            start = float(getattr(item, "start", 0.0))
            duration = float(getattr(item, "duration", 0.0))
            rows.append({"start": start, "end": start + duration, "text": clean_cue(text)})
        write_json(item_dir / f"{video_id}.transcript_api.json", rows)
        return {"status": "saved", "language": getattr(chosen, "language_code", ""), "cues": len(rows)}
    except Exception as exc:
        return {"status": "error", "error": f"{type(exc).__name__}: {exc}"}


def download_preview(video_id: str, item_dir: Path) -> dict[str, Any]:
    result: dict[str, Any] = {}
    urls = [
        ("animated", f"https://i.ytimg.com/an_webp/{video_id}/mqdefault_6s.webp"),
        ("hq", f"https://i.ytimg.com/vi/{video_id}/hqdefault.jpg"),
    ]
    for label, url in urls:
        try:
            r = SESSION.get(url, timeout=40)
            result[label + "_status"] = r.status_code
            result[label + "_size"] = len(r.content)
            if r.status_code == 200 and len(r.content) > 1000:
                ext = ".webp" if label == "animated" else ".jpg"
                path = item_dir / f"{video_id}_{label}{ext}"
                path.write_bytes(r.content)
                result[label + "_file"] = path.name
        except Exception as exc:
            result[label + "_error"] = str(exc)
    webp = item_dir / f"{video_id}_animated.webp"
    if webp.exists():
        try:
            with Image.open(webp) as im:
                n = getattr(im, "n_frames", 1)
                picks = sorted(set(round(i * (n - 1) / 7) for i in range(min(8, n))))
                frames: list[Image.Image] = []
                for idx in picks:
                    im.seek(idx)
                    frame = im.convert("RGB")
                    frame.thumbnail((480, 270), Image.Resampling.LANCZOS)
                    canvas = Image.new("RGB", (480, 292), "white")
                    canvas.paste(frame, ((480 - frame.width) // 2, 0))
                    ImageDraw.Draw(canvas).text((8, 274), f"F{idx+1}/{n}", fill="black")
                    frames.append(canvas)
                cols = 2
                rows = (len(frames) + cols - 1) // cols
                sheet = Image.new("RGB", (480 * cols, 292 * rows), (238, 242, 247))
                for j, frame in enumerate(frames):
                    sheet.paste(frame, ((j % cols) * 480, (j // cols) * 292))
                sheet_path = item_dir / f"{video_id}_preview_sheet.jpg"
                sheet.save(sheet_path, quality=90)
                result["animated_frames"] = n
                result["preview_sheet"] = sheet_path.name
        except Exception as exc:
            result["preview_extract_error"] = f"{type(exc).__name__}: {exc}"
    return result


def extract_focus_frames(video_id: str, item_dir: Path) -> dict[str, Any]:
    focus_dir = item_dir / "focus_full_video"
    focus_dir.mkdir(exist_ok=True)
    outtmpl = str(focus_dir / "source.%(ext)s")
    cmd = [
        "yt-dlp", "--no-playlist", "--ignore-errors", "--js-runtimes", "node",
        "--remote-components", "ejs:github", "--socket-timeout", "40",
        "--retries", "3", "-f", "18/b[height<=480]/best[height<=480]",
        "--merge-output-format", "mp4", "-o", outtmpl,
        f"https://www.youtube.com/watch?v={video_id}",
    ]
    code, text = run(cmd, focus_dir / "download.log", timeout=900)
    media = next((p for p in focus_dir.glob("source.*") if p.suffix not in {".part", ".ytdl"}), None)
    result: dict[str, Any] = {"download_exit": code, "downloaded": bool(media), "error_tail": "\n".join(text.splitlines()[-15:])}
    if not media:
        return result
    result["media_size"] = media.stat().st_size
    frames_dir = focus_dir / "frames_5s"
    frames_dir.mkdir(exist_ok=True)
    run([
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-i", str(media),
        "-vf", "fps=1/5,scale='min(960,iw)':-2", "-q:v", "3", str(frames_dir / "%04d.jpg")
    ], focus_dir / "ffmpeg_5s.log", timeout=300)
    scene_dir = focus_dir / "scene_frames"
    scene_dir.mkdir(exist_ok=True)
    run([
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-i", str(media),
        "-vf", "select='gt(scene,0.11)',scale='min(960,iw)':-2", "-vsync", "vfr", "-q:v", "3", str(scene_dir / "%04d.jpg")
    ], focus_dir / "ffmpeg_scene.log", timeout=300)
    images = sorted(frames_dir.glob("*.jpg"))
    if images:
        chosen = images[:: max(1, len(images) // 24)][:24]
        thumbs: list[Image.Image] = []
        for idx, p in enumerate(chosen):
            im = Image.open(p).convert("RGB")
            im.thumbnail((400, 225), Image.Resampling.LANCZOS)
            canvas = Image.new("RGB", (400, 247), "white")
            canvas.paste(im, ((400 - im.width) // 2, 0))
            ImageDraw.Draw(canvas).text((8, 229), f"sample {idx+1}", fill="black")
            thumbs.append(canvas)
        cols = 3
        rows = (len(thumbs) + cols - 1) // cols
        sheet = Image.new("RGB", (400 * cols, 247 * rows), (235, 240, 246))
        for idx, im in enumerate(thumbs):
            sheet.paste(im, ((idx % cols) * 400, (idx // cols) * 247))
        sheet_path = focus_dir / "focus_contact_sheet.jpg"
        sheet.save(sheet_path, quality=88)
        result["contact_sheet"] = str(sheet_path.relative_to(item_dir))
    result["frames_5s"] = len(images)
    result["scene_frames"] = len(list(scene_dir.glob("*.jpg")))
    # Do not retain or redistribute the full source video; keep only analysis frames.
    try:
        media.unlink()
        result["source_deleted_after_analysis"] = True
    except Exception:
        result["source_deleted_after_analysis"] = False
    return result


def load_inventory() -> list[dict[str, str]]:
    with INVENTORY_PATH.open(encoding="utf-8-sig", newline="") as f:
        rows = list(csv.DictReader(f))
    rows.sort(key=lambda r: (0 if r.get("video_id") == FOCUS_ID else 1, r.get("video_type", ""), r.get("title", "")))
    return rows


def process_row(row: dict[str, str]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    video_id = row["video_id"]
    item_dir = OUT / "items" / video_id
    item_dir.mkdir(parents=True, exist_ok=True)
    url = row.get("url") or f"https://www.youtube.com/watch?v={video_id}"
    status: dict[str, Any] = {
        "batch": BATCH_INDEX,
        "video_id": video_id,
        "video_type": row.get("video_type", ""),
        "title": row.get("title", ""),
        "duration": row.get("duration", ""),
        "inventory_concepts": row.get("concepts", ""),
        "telegram_posts": row.get("telegram_posts", ""),
    }
    ytdlp = download_with_ytdlp(url, item_dir)
    status.update({f"ytdlp_{k}": v for k, v in ytdlp.items()})
    if not captions_for_item(item_dir):
        status["direct_timedtext"] = try_direct_timedtext(video_id, item_dir)
    transcript_api = {"status": "skipped"}
    if not captions_for_item(item_dir):
        transcript_api = try_transcript_api(video_id, item_dir)
    status["transcript_api"] = transcript_api
    preview = download_preview(video_id, item_dir)
    status.update({f"preview_{k}": v for k, v in preview.items()})

    cue_rows: list[dict[str, Any]] = []
    transcript_parts: list[str] = []
    concept_counter: Counter[str] = Counter()
    keyword_counter: Counter[str] = Counter()
    cap_files = captions_for_item(item_dir)
    if cap_files:
        # Use the highest-priority Korean/English caption file for cue analysis.
        chosen = cap_files[0]
        cues = parse_vtt(chosen)
        status["caption_chosen"] = chosen.name
        status["caption_cues"] = len(cues)
        for cue in cues:
            transcript_parts.append(cue["text"])
            found = classify_text(cue["text"])
            for concept, keywords in found.items():
                concept_counter[concept] += 1
                keyword_counter.update(keywords)
                cue_rows.append({
                    "batch": BATCH_INDEX,
                    "video_id": video_id,
                    "title": row.get("title", ""),
                    "video_type": row.get("video_type", ""),
                    "start_sec": cue["start"],
                    "end_sec": cue["end"],
                    "timecode": time.strftime("%H:%M:%S", time.gmtime(cue["start"])),
                    "concept": concept,
                    "keywords": "|".join(keywords),
                    "cue": cue["text"],
                    "url": f"https://www.youtube.com/watch?v={video_id}&t={int(cue['start'])}s",
                })
        plain = "\n".join(dict.fromkeys(transcript_parts))
        (item_dir / f"{video_id}_caption_plain.txt").write_text(plain, encoding="utf-8")
    else:
        api_json = item_dir / f"{video_id}.transcript_api.json"
        if api_json.exists():
            rows = json.loads(api_json.read_text(encoding="utf-8"))
            status["caption_chosen"] = api_json.name
            status["caption_cues"] = len(rows)
            for cue in rows:
                text = cue.get("text", "")
                transcript_parts.append(text)
                found = classify_text(text)
                for concept, keywords in found.items():
                    concept_counter[concept] += 1
                    keyword_counter.update(keywords)
                    cue_rows.append({
                        "batch": BATCH_INDEX,
                        "video_id": video_id,
                        "title": row.get("title", ""),
                        "video_type": row.get("video_type", ""),
                        "start_sec": cue.get("start", 0),
                        "end_sec": cue.get("end", 0),
                        "timecode": time.strftime("%H:%M:%S", time.gmtime(float(cue.get("start", 0)))),
                        "concept": concept,
                        "keywords": "|".join(keywords),
                        "cue": text,
                        "url": f"https://www.youtube.com/watch?v={video_id}&t={int(float(cue.get('start', 0)))}s",
                    })
            (item_dir / f"{video_id}_caption_plain.txt").write_text("\n".join(dict.fromkeys(transcript_parts)), encoding="utf-8")

    status["caption_available"] = bool(transcript_parts)
    status["caption_chars"] = sum(len(x) for x in transcript_parts)
    status["caption_concepts"] = "|".join(x for x, _ in concept_counter.most_common())
    status["caption_concept_counts"] = json.dumps(dict(concept_counter), ensure_ascii=False)
    status["caption_top_keywords"] = "|".join(x for x, _ in keyword_counter.most_common(20))
    if video_id == FOCUS_ID and BATCH_INDEX == 0:
        status["focus_frame_analysis"] = extract_focus_frames(video_id, item_dir)
    write_json(item_dir / "status.json", status)
    return status, cue_rows


def main() -> None:
    inventory = load_inventory()
    selected = [row for idx, row in enumerate(inventory) if idx % BATCH_COUNT == BATCH_INDEX]
    # Ensure the user-supplied focus video is deeply processed by batch 0.
    if BATCH_INDEX == 0 and not any(row.get("video_id") == FOCUS_ID for row in selected):
        focus = next((row for row in inventory if row.get("video_id") == FOCUS_ID), None)
        if focus:
            selected.insert(0, focus)
    statuses: list[dict[str, Any]] = []
    events: list[dict[str, Any]] = []
    for idx, row in enumerate(selected, 1):
        try:
            status, cue_rows = process_row(row)
        except Exception as exc:
            status = {
                "batch": BATCH_INDEX,
                "video_id": row.get("video_id", ""),
                "video_type": row.get("video_type", ""),
                "title": row.get("title", ""),
                "fatal_error": f"{type(exc).__name__}: {exc}",
            }
            cue_rows = []
        statuses.append(status)
        events.extend(cue_rows)
        if idx % 5 == 0 or idx == len(selected):
            write_json(OUT / "progress.json", {"batch": BATCH_INDEX, "processed": idx, "total": len(selected)})
            write_csv(OUT / "batch_summary_partial.csv", statuses)
            write_csv(OUT / "caption_events_partial.csv", events)
    write_csv(OUT / "batch_summary.csv", statuses)
    write_csv(OUT / "caption_events.csv", events)
    coverage = {
        "batch": BATCH_INDEX,
        "batch_count": BATCH_COUNT,
        "selected": len(selected),
        "captions_available": sum(bool(x.get("caption_available")) for x in statuses),
        "preview_animated_saved": sum(bool(x.get("preview_animated_file")) for x in statuses),
        "metadata_saved": sum(bool(x.get("ytdlp_info_json")) for x in statuses),
        "events": len(events),
        "fatal_errors": sum(bool(x.get("fatal_error")) for x in statuses),
    }
    write_json(OUT / "coverage.json", coverage)
    print(json.dumps(coverage, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
