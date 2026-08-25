from __future__ import annotations

import csv
import html
import json
import math
import os
import re
import shutil
import subprocess
import sys
import time
from collections import defaultdict
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlencode, urlparse, urlunparse

import cv2
import numpy as np
import requests
from bs4 import BeautifulSoup
from faster_whisper import WhisperModel
from PIL import Image, ImageDraw, ImageFont

VIDEO_ID = os.environ.get("VIDEO_ID", "nucIUdFTkZY").strip()
OUT = Path(os.environ.get("OUT", "/workspace/analysis"))
MODEL_NAME = os.environ.get("WHISPER_MODEL", "small")
MODEL_ROOT = os.environ.get("WHISPER_MODEL_ROOT", "/opt/whisper-models")
OUT.mkdir(parents=True, exist_ok=True)
WORK = OUT / "work"
WORK.mkdir(exist_ok=True)
LOG = OUT / "analysis.log"
UA_DISCORD = "Mozilla/5.0 (compatible; Discordbot/2.0; +https://discordapp.com)"
UA_BROWSER = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/145 Safari/537.36"
SESSION = requests.Session()
SESSION.headers.update({"User-Agent": UA_BROWSER, "Accept-Language": "ko-KR,ko;q=0.9,en-US;q=0.8,en;q=0.7"})

KEYWORD_GROUPS: dict[str, list[str]] = {
    "호가창": ["호가", "잔량", "매수벽", "매도벽", "체결강도", "체결", "호가창"],
    "진입": ["매수", "진입", "들어가", "담아", "잡아", "스캘핑", "스캘프"],
    "청산": ["매도", "익절", "청산", "나갔", "나갑", "수익 실현", "원칙 매도"],
    "손절": ["손절", "컷", "정리", "이탈", "무효", "시나리오 훼손"],
    "물량소화": ["물량", "소화", "매물", "받아내", "흡수"],
    "수급": ["수급", "프로그램", "외국인", "기관", "거래대금", "거래량"],
    "종목선정": ["대장", "주도주", "종목 선정", "관심 종목", "섹터", "테마", "재료"],
    "차트": ["기준봉", "전고점", "돌파", "눌림", "지지", "박스", "이평선", "오일선", "십일선"],
    "평가발언": ["좋아 보", "괜찮", "강하", "약하", "스윙", "단기 스윙", "오늘 종목"],
}
IMPORTANT_GROUPS = {"호가창", "진입", "청산", "손절", "물량소화", "평가발언"}


def log(message: str) -> None:
    line = f"{time.strftime('%Y-%m-%d %H:%M:%S')} {message}"
    print(line, flush=True)
    with LOG.open("a", encoding="utf-8") as file:
        file.write(line + "\n")


def dump_json(path: Path, value: Any) -> None:
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
    with path.open("w", encoding="utf-8-sig", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def run(command: list[str], timeout: int | None = None, check: bool = True) -> subprocess.CompletedProcess[str]:
    log("RUN " + " ".join(command[:8]) + (" ..." if len(command) > 8 else ""))
    proc = subprocess.run(command, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=timeout)
    if check and proc.returncode != 0:
        raise RuntimeError(f"command failed ({proc.returncode}): {' '.join(command)}\n{proc.stdout[-5000:]}")
    return proc


def seconds_label(seconds: float) -> str:
    seconds = max(0, float(seconds))
    hours = int(seconds // 3600)
    minutes = int(seconds % 3600 // 60)
    secs = seconds % 60
    return f"{hours:02d}:{minutes:02d}:{secs:06.3f}"


def koutu_source(video_id: str) -> tuple[str, dict[str, Any]]:
    url = f"https://koutu.be/{video_id}"
    response = SESSION.get(url, headers={"User-Agent": UA_DISCORD}, timeout=45)
    response.raise_for_status()
    soup = BeautifulSoup(response.text, "html.parser")
    stream = ""
    for selector in [
        ('meta[name="twitter:player:stream"]', "content"),
        ('meta[property="og:video"]', "content"),
        ('meta[property="og:video:secure_url"]', "content"),
    ]:
        node = soup.select_one(selector[0])
        if node and node.get(selector[1]):
            stream = str(node.get(selector[1]))
            break
    if not stream:
        raise RuntimeError("Koutube response did not contain a media stream URL")
    title_node = soup.select_one('meta[name="twitter:title"]')
    description_node = soup.select_one('meta[name="twitter:description"]')
    metadata = {
        "source_page": url,
        "media_host": urlparse(stream).netloc,
        "title": title_node.get("content") if title_node else "",
        "description": description_node.get("content") if description_node else "",
        "html_bytes": len(response.content),
    }
    return stream, metadata


def set_itag(url: str, itag: int) -> str:
    parsed = urlparse(url)
    query = parse_qs(parsed.query)
    query["itag"] = [str(itag)]
    return urlunparse(parsed._replace(query=urlencode(query, doseq=True)))


def ffprobe_url(url: str, timeout: int = 70) -> dict[str, Any] | None:
    proc = run([
        "ffprobe", "-v", "error", "-user_agent", UA_BROWSER,
        "-show_streams", "-show_format", "-of", "json", url,
    ], timeout=timeout, check=False)
    if proc.returncode != 0:
        return None
    try:
        data = json.loads(proc.stdout)
    except Exception:
        return None
    video = next((stream for stream in data.get("streams", []) if stream.get("codec_type") == "video"), {})
    audio = next((stream for stream in data.get("streams", []) if stream.get("codec_type") == "audio"), {})
    fmt = data.get("format", {})
    return {
        "width": int(video.get("width") or 0),
        "height": int(video.get("height") or 0),
        "video_codec": video.get("codec_name"),
        "audio_codec": audio.get("codec_name"),
        "duration": float(fmt.get("duration") or video.get("duration") or audio.get("duration") or 0),
        "format_name": fmt.get("format_name"),
        "bit_rate": int(fmt.get("bit_rate") or 0),
    }


def choose_source(base_url: str) -> tuple[str, dict[str, Any]]:
    attempts = []
    for itag in [22, 18]:
        candidate = set_itag(base_url, itag)
        probe = ffprobe_url(candidate)
        attempts.append({"itag": itag, "probe": probe})
        if probe and probe.get("width", 0) >= 640 and probe.get("duration", 0) > 1:
            return candidate, {"chosen_itag": itag, "attempts": attempts, "probe": probe}
    raise RuntimeError(f"No verified combined MP4 source: {attempts}")


def download_source(url: str, output: Path) -> None:
    run([
        "curl", "-L", "--fail", "--retry", "5", "--retry-all-errors", "--connect-timeout", "30",
        "--max-time", "7200", "-A", UA_BROWSER, "-o", str(output), url,
    ], timeout=7400)
    if not output.exists() or output.stat().st_size < 1_000_000:
        raise RuntimeError("Downloaded source file is missing or too small")


def probe_file(path: Path) -> dict[str, Any]:
    proc = run(["ffprobe", "-v", "error", "-show_streams", "-show_format", "-of", "json", str(path)], timeout=120)
    data = json.loads(proc.stdout)
    video = next((stream for stream in data.get("streams", []) if stream.get("codec_type") == "video"), {})
    audio = next((stream for stream in data.get("streams", []) if stream.get("codec_type") == "audio"), {})
    fmt = data.get("format", {})
    return {
        "width": int(video.get("width") or 0),
        "height": int(video.get("height") or 0),
        "fps": video.get("avg_frame_rate"),
        "video_codec": video.get("codec_name"),
        "audio_codec": audio.get("codec_name"),
        "duration": float(fmt.get("duration") or 0),
        "size": int(fmt.get("size") or path.stat().st_size),
        "bit_rate": int(fmt.get("bit_rate") or 0),
    }


def extract_audio(source: Path, output: Path) -> None:
    run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-i", str(source), "-vn", "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le", str(output)], timeout=3600)


def transcribe(audio: Path) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    log(f"Loading faster-whisper model {MODEL_NAME}")
    model = WhisperModel(MODEL_NAME, device="cpu", compute_type="int8", cpu_threads=max(1, os.cpu_count() or 2), download_root=MODEL_ROOT)
    iterator, info = model.transcribe(
        str(audio), language="ko", beam_size=1, best_of=1, vad_filter=True,
        condition_on_previous_text=True, word_timestamps=False, temperature=0.0,
    )
    rows: list[dict[str, Any]] = []
    for index, segment in enumerate(iterator):
        text = segment.text.strip()
        if not text:
            continue
        rows.append({
            "index": index,
            "start": round(float(segment.start), 3),
            "end": round(float(segment.end), 3),
            "start_label": seconds_label(segment.start),
            "end_label": seconds_label(segment.end),
            "text": text,
            "avg_logprob": round(float(segment.avg_logprob), 4),
            "no_speech_prob": round(float(segment.no_speech_prob), 4),
        })
    metadata = {
        "language": info.language,
        "language_probability": float(info.language_probability),
        "duration": float(info.duration),
        "duration_after_vad": float(info.duration_after_vad),
        "model": MODEL_NAME,
    }
    return rows, metadata


def write_srt(path: Path, rows: list[dict[str, Any]]) -> None:
    def stamp(value: float) -> str:
        ms = int(round(value * 1000))
        h, rem = divmod(ms, 3_600_000)
        m, rem = divmod(rem, 60_000)
        s, milli = divmod(rem, 1000)
        return f"{h:02d}:{m:02d}:{s:02d},{milli:03d}"
    parts = []
    for index, row in enumerate(rows, 1):
        parts.append(f"{index}\n{stamp(row['start'])} --> {stamp(row['end'])}\n{row['text']}\n")
    path.write_text("\n".join(parts), encoding="utf-8")


def classify_transcript(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    events = []
    for row in rows:
        text = row["text"]
        groups = []
        hits = []
        lowered = text.lower()
        for group, terms in KEYWORD_GROUPS.items():
            matched = [term for term in terms if term.lower() in lowered]
            if matched:
                groups.append(group)
                hits.extend(matched)
        if groups:
            events.append({
                "start": row["start"], "end": row["end"], "time_label": row["start_label"],
                "text": text, "groups": "|".join(groups), "keywords": "|".join(dict.fromkeys(hits)),
                "priority": 2 if any(group in IMPORTANT_GROUPS for group in groups) else 1,
            })
    return events


def extract_sparse_frames(source: Path, directory: Path, interval: float = 5.0) -> list[Path]:
    directory.mkdir(parents=True, exist_ok=True)
    run([
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-i", str(source),
        "-vf", f"fps=1/{interval},scale=640:-2", "-q:v", "4", str(directory / "%06d.jpg"),
    ], timeout=5400)
    return sorted(directory.glob("*.jpg"))


def frame_metrics(path: Path, previous_gray: np.ndarray | None, timestamp: float) -> tuple[dict[str, Any], np.ndarray]:
    image = cv2.imread(str(path))
    if image is None:
        raise RuntimeError(f"Could not read frame {path}")
    height, width = image.shape[:2]
    scale_width = 480
    small = cv2.resize(image, (scale_width, max(1, int(height * scale_width / width))))
    hsv = cv2.cvtColor(small, cv2.COLOR_BGR2HSV)
    gray = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)
    red = ((cv2.inRange(hsv, (0, 65, 45), (13, 255, 255)) > 0) | (cv2.inRange(hsv, (168, 65, 45), (180, 255, 255)) > 0))
    blue = cv2.inRange(hsv, (88, 45, 35), (140, 255, 255)) > 0
    edges = cv2.Canny(gray, 55, 145) > 0
    right = slice(int(gray.shape[1] * 0.55), gray.shape[1])
    left = slice(0, int(gray.shape[1] * 0.45))
    red_ratio = float(red.mean())
    blue_ratio = float(blue.mean())
    edge_ratio = float(edges.mean())
    right_red = float(red[:, right].mean())
    right_blue = float(blue[:, right].mean())
    right_edge = float(edges[:, right].mean())
    left_edge = float(edges[:, left].mean())
    scene_change = float(cv2.absdiff(gray, previous_gray).mean()) if previous_gray is not None else 0.0
    horizontal_kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (17, 1))
    vertical_kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (1, 11))
    horizontal = cv2.morphologyEx((edges.astype(np.uint8) * 255), cv2.MORPH_OPEN, horizontal_kernel) > 0
    vertical = cv2.morphologyEx((edges.astype(np.uint8) * 255), cv2.MORPH_OPEN, vertical_kernel) > 0
    line_ratio = float(horizontal.mean() + vertical.mean())
    color_balance = min(red_ratio, blue_ratio) * 2
    right_color = min(right_red, right_blue) * 2
    hts_score = min(1.0, edge_ratio * 2.8 + line_ratio * 4.5 + (red_ratio + blue_ratio) * 7.0)
    orderbook_score = min(1.0, right_edge * 2.5 + right_color * 9.0 + (right_red + right_blue) * 4.0 + line_ratio * 3.2)
    likely_hts = bool(hts_score >= 0.28 and red_ratio > 0.0012 and blue_ratio > 0.0012)
    likely_orderbook = bool(orderbook_score >= 0.27 and right_red > 0.001 and right_blue > 0.001 and right_edge > 0.035)
    return ({
        "frame": path.name, "timestamp": round(timestamp, 3), "time_label": seconds_label(timestamp),
        "red_ratio": round(red_ratio, 6), "blue_ratio": round(blue_ratio, 6), "edge_ratio": round(edge_ratio, 6),
        "right_red": round(right_red, 6), "right_blue": round(right_blue, 6), "right_edge": round(right_edge, 6),
        "left_edge": round(left_edge, 6), "line_ratio": round(line_ratio, 6), "scene_change": round(scene_change, 4),
        "hts_score": round(hts_score, 4), "orderbook_score": round(orderbook_score, 4),
        "likely_hts": likely_hts, "likely_orderbook": likely_orderbook,
    }, gray)


def copy_selected(paths: list[Path], target: Path) -> list[Path]:
    target.mkdir(parents=True, exist_ok=True)
    copied = []
    for path in paths:
        destination = target / path.name
        shutil.copy2(path, destination)
        copied.append(destination)
    return copied


def contact_sheet(items: list[tuple[Path, str]], output: Path, columns: int = 4, width: int = 360, height: int = 203) -> None:
    if not items:
        return
    label_height = 34
    rows = math.ceil(len(items) / columns)
    canvas = Image.new("RGB", (columns * width, rows * (height + label_height)), "white")
    draw = ImageDraw.Draw(canvas)
    font = ImageFont.load_default()
    for index, (path, label) in enumerate(items):
        try:
            image = Image.open(path).convert("RGB")
        except Exception:
            continue
        image.thumbnail((width, height))
        cell = Image.new("RGB", (width, height), "#111827")
        cell.paste(image, ((width - image.width) // 2, (height - image.height) // 2))
        x = (index % columns) * width
        y = (index // columns) * (height + label_height)
        canvas.paste(cell, (x, y))
        draw.text((x + 6, y + height + 5), label[:64], fill="#111827", font=font)
    output.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(output, quality=88)


def extract_frame(source: Path, timestamp: float, output: Path) -> bool:
    output.parent.mkdir(parents=True, exist_ok=True)
    proc = run([
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-ss", f"{max(0,timestamp):.3f}",
        "-i", str(source), "-frames:v", "1", "-vf", "scale=640:-2", "-q:v", "3", str(output),
    ], timeout=120, check=False)
    return proc.returncode == 0 and output.exists() and output.stat().st_size > 1000


def merge_windows(events: list[dict[str, Any]], duration: float, limit: int = 30) -> list[dict[str, Any]]:
    ordered = sorted(events, key=lambda event: (-int(event.get("priority", 1)), float(event["start"])))
    selected: list[dict[str, Any]] = []
    for event in ordered:
        center = float(event["start"])
        start = max(0.0, center - 8.0)
        end = min(duration, center + 12.0)
        if any(not (end < item["start"] - 3 or start > item["end"] + 3) for item in selected):
            continue
        selected.append({"start": start, "end": end, "center": center, "groups": event["groups"], "text": event["text"]})
        if len(selected) >= limit:
            break
    return sorted(selected, key=lambda item: item["start"])


def extract_dense_windows(source: Path, windows: list[dict[str, Any]], root: Path) -> list[dict[str, Any]]:
    results = []
    for index, window in enumerate(windows, 1):
        directory = root / f"window_{index:03d}_{window['start']:.1f}_{window['end']:.1f}"
        directory.mkdir(parents=True, exist_ok=True)
        duration = max(0.5, window["end"] - window["start"])
        run([
            "ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-ss", f"{window['start']:.3f}",
            "-i", str(source), "-t", f"{duration:.3f}", "-vf", "fps=2,scale=640:-2", "-q:v", "4",
            str(directory / "%05d.jpg"),
        ], timeout=600)
        frames = sorted(directory.glob("*.jpg"))
        metrics = []
        previous = None
        for frame_index, frame in enumerate(frames):
            timestamp = window["start"] + frame_index * 0.5
            row, previous = frame_metrics(frame, previous, timestamp)
            metrics.append(row)
        write_csv(directory / "frame_metrics.csv", metrics)
        contact_sheet([(frame, f"{seconds_label(window['start'] + i*0.5)}") for i, frame in enumerate(frames[:48])], directory / "contact_sheet.jpg", columns=4)
        results.append({**window, "directory": directory.name, "frames": len(frames), "max_orderbook_score": max((row["orderbook_score"] for row in metrics), default=0), "max_scene_change": max((row["scene_change"] for row in metrics), default=0)})
    return results


def create_report(metadata: dict[str, Any], transcript: list[dict[str, Any]], events: list[dict[str, Any]], frame_summary: dict[str, Any], dense: list[dict[str, Any]]) -> None:
    event_rows = "".join(
        f"<tr><td>{html.escape(event['time_label'])}</td><td>{html.escape(event['groups'])}</td><td>{html.escape(event['text'])}</td><td>{html.escape(event.get('frame_file',''))}</td></tr>"
        for event in events
    )
    transcript_rows = "".join(
        f"<tr><td>{html.escape(row['start_label'])}</td><td>{html.escape(row['end_label'])}</td><td>{html.escape(row['text'])}</td></tr>"
        for row in transcript
    )
    dense_rows = "".join(
        f"<tr><td>{seconds_label(row['start'])}</td><td>{seconds_label(row['end'])}</td><td>{html.escape(row['groups'])}</td><td>{row['frames']}</td><td>{row['max_orderbook_score']:.3f}</td><td><a href='dense_windows/{row['directory']}/contact_sheet.jpg'>시트</a></td></tr>"
        for row in dense
    )
    css = """body{margin:0;background:#eef3f8;color:#142033;font-family:Arial,'Malgun Gothic',sans-serif}main{max-width:1500px;margin:22px auto;background:white;border:1px solid #d7e0ea;border-radius:16px;padding:28px}h1{margin:0 0 8px}.sub{color:#64748b}.cards{display:grid;grid-template-columns:repeat(5,1fr);gap:10px;margin:18px 0}.card{padding:14px;border:1px solid #dbe4ed;border-radius:12px;background:#f8fafc}.card b{font-size:22px;color:#1463d6;display:block}.sheet{max-width:100%;border:1px solid #d7e0ea;border-radius:10px}table{border-collapse:collapse;width:100%;font-size:11px;margin:12px 0 28px}th,td{border:1px solid #dfe6ee;padding:6px;text-align:left;vertical-align:top}th{background:#f1f5f9;position:sticky;top:0}.scroll{max-height:520px;overflow:auto;border:1px solid #dfe6ee}.warn{padding:12px;background:#fff7e8;border:1px solid #efd28d;border-radius:10px;color:#7c5100}@media(max-width:900px){main{margin:0;border-radius:0;padding:14px}.cards{grid-template-columns:1fr 1fr}}"""
    doc = f"""<!doctype html><html lang='ko'><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'><title>{html.escape(metadata.get('title') or VIDEO_ID)}</title><style>{css}</style></head><body><main><h1>{html.escape(metadata.get('title') or VIDEO_ID)}</h1><p class='sub'>영상 ID {VIDEO_ID} · 실제 MP4 전체 다운로드, 음성 전사, 5초 전수 프레임, 발언 구간 고밀도 프레임 분석</p><div class='cards'><div class='card'>길이<b>{seconds_label(metadata['file_probe']['duration'])}</b></div><div class='card'>해상도<b>{metadata['file_probe']['width']}×{metadata['file_probe']['height']}</b></div><div class='card'>전사 구간<b>{len(transcript)}</b></div><div class='card'>전략 발언<b>{len(events)}</b></div><div class='card'>호가 후보<b>{frame_summary['orderbook_candidates']}</b></div></div><div class='warn'>호가잔량 숫자의 정확한 OCR은 360p 원본에서 신뢰도가 낮아 임의 판독하지 않습니다. 화면 변화와 발언 대응은 원본 시간축으로 기록합니다.</div><h2>장면 변화 대표 프레임</h2><img class='sheet' src='contact_sheet_scene.jpg'><h2>호가창 후보 프레임</h2><img class='sheet' src='contact_sheet_orderbook.jpg'><h2>발언 중심 고밀도 분석 구간</h2><table><thead><tr><th>시작</th><th>종료</th><th>분류</th><th>프레임</th><th>호가 점수</th><th>자료</th></tr></thead><tbody>{dense_rows}</tbody></table><h2>전략 발언과 화면 프레임</h2><table><thead><tr><th>시각</th><th>분류</th><th>발언</th><th>프레임</th></tr></thead><tbody>{event_rows}</tbody></table><h2>전체 전사</h2><div class='scroll'><table><thead><tr><th>시작</th><th>종료</th><th>텍스트</th></tr></thead><tbody>{transcript_rows}</tbody></table></div></main></body></html>"""
    (OUT / "report.html").write_text(doc, encoding="utf-8")


def main() -> int:
    started = time.time()
    source = WORK / "source.mp4"
    audio = WORK / "audio.wav"
    sparse_dir = WORK / "sparse_frames"
    log(f"Starting full analysis for {VIDEO_ID}")
    base_url, page_metadata = koutu_source(VIDEO_ID)
    chosen_url, source_choice = choose_source(base_url)
    dump_json(OUT / "source_verification.json", {**page_metadata, **source_choice})
    log(f"Downloading verified itag {source_choice['chosen_itag']} from {page_metadata['media_host']}")
    download_source(chosen_url, source)
    file_probe = probe_file(source)
    if file_probe["duration"] < 2 or file_probe["width"] < 320:
        raise RuntimeError(f"Downloaded source failed verification: {file_probe}")
    extract_audio(source, audio)
    transcript, transcript_meta = transcribe(audio)
    dump_json(OUT / "transcript.json", {"metadata": transcript_meta, "segments": transcript})
    write_csv(OUT / "transcript.csv", transcript)
    write_srt(OUT / "transcript.srt", transcript)
    events = classify_transcript(transcript)
    event_frames_dir = OUT / "event_frames"
    for index, event in enumerate(events, 1):
        filename = f"event_{index:04d}_{event['start']:.3f}s.jpg"
        if extract_frame(source, float(event["start"]), event_frames_dir / filename):
            event["frame_file"] = f"event_frames/{filename}"
    write_csv(OUT / "strategy_events.csv", events)
    dump_json(OUT / "strategy_events.json", events)

    sparse_frames = extract_sparse_frames(source, sparse_dir, interval=5.0)
    frame_rows = []
    previous = None
    for index, frame in enumerate(sparse_frames):
        row, previous = frame_metrics(frame, previous, index * 5.0)
        frame_rows.append(row)
    write_csv(OUT / "sparse_frame_metrics.csv", frame_rows)
    scene_top = sorted(frame_rows, key=lambda row: float(row["scene_change"]), reverse=True)[:48]
    order_top = sorted(frame_rows, key=lambda row: float(row["orderbook_score"]), reverse=True)[:64]
    scene_paths = copy_selected([sparse_dir / row["frame"] for row in scene_top], OUT / "selected_scene_frames")
    order_paths = copy_selected([sparse_dir / row["frame"] for row in order_top], OUT / "selected_orderbook_frames")
    contact_sheet([(path, f"{scene_top[i]['time_label']} change={scene_top[i]['scene_change']}") for i, path in enumerate(scene_paths)], OUT / "contact_sheet_scene.jpg")
    contact_sheet([(path, f"{order_top[i]['time_label']} score={order_top[i]['orderbook_score']}") for i, path in enumerate(order_paths)], OUT / "contact_sheet_orderbook.jpg")

    windows = merge_windows(events, file_probe["duration"], limit=30)
    dense = extract_dense_windows(source, windows, OUT / "dense_windows")
    write_csv(OUT / "dense_windows.csv", dense)
    frame_summary = {
        "sparse_frames": len(sparse_frames),
        "likely_hts_frames": sum(1 for row in frame_rows if row["likely_hts"]),
        "orderbook_candidates": sum(1 for row in frame_rows if row["likely_orderbook"]),
        "selected_scene_frames": len(scene_paths),
        "selected_orderbook_frames": len(order_paths),
        "dense_windows": len(dense),
        "dense_frames": sum(int(row["frames"]) for row in dense),
    }
    metadata = {
        "video_id": VIDEO_ID,
        "title": page_metadata.get("title") or VIDEO_ID,
        "description": page_metadata.get("description"),
        "media_host": page_metadata.get("media_host"),
        "chosen_itag": source_choice["chosen_itag"],
        "file_probe": file_probe,
        "transcript": transcript_meta,
        "frame_summary": frame_summary,
        "strategy_events": len(events),
        "elapsed_seconds": round(time.time() - started, 2),
    }
    dump_json(OUT / "summary.json", metadata)
    create_report(metadata, transcript, events, frame_summary, dense)
    shutil.rmtree(WORK, ignore_errors=True)
    log(f"Completed full analysis in {metadata['elapsed_seconds']} seconds")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as error:
        log(f"FATAL {type(error).__name__}: {error}")
        dump_json(OUT / "failure.json", {"video_id": VIDEO_ID, "error_type": type(error).__name__, "error": str(error)})
        raise
