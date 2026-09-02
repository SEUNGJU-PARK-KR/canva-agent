from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import time
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlparse, urlunparse

import requests

UA_DISCORD = "Mozilla/5.0 (compatible; Discordbot/2.0; +https://discordapp.com)"
UA_BROWSER = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/145 Safari/537.36"


def run(command: list[str], timeout: int = 300, check: bool = True) -> subprocess.CompletedProcess[str]:
    proc = subprocess.run(command, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=timeout)
    if check and proc.returncode != 0:
        raise RuntimeError(f"command failed ({proc.returncode}): {' '.join(command)}\n{proc.stdout[-5000:]}")
    return proc


def fetch_koutu_html(video_id: str) -> str:
    url = f"https://koutu.be/{video_id}"
    js = (
        "const u=" + json.dumps(url) + ";"
        "const r=await fetch(u,{headers:{'user-agent':" + json.dumps(UA_DISCORD) + ",'accept':'text/html,application/xhtml+xml'}});"
        "const t=await r.text();"
        "if(!r.ok){console.error('HTTP '+r.status+' '+t.slice(0,1000));process.exit(2)};"
        "process.stdout.write(t);"
    )
    proc = run(["bun", "-e", js], timeout=90, check=False)
    if proc.returncode == 0 and "twitter:player:stream" in proc.stdout:
        return proc.stdout
    proc = run(["curl", "-sS", "-L", "--fail", "--retry", "4", "--max-time", "60", "-A", UA_DISCORD, url], timeout=90, check=False)
    if proc.returncode != 0:
        raise RuntimeError(f"Koutube source page failed: {proc.stdout[-1500:]}")
    return proc.stdout


def extract_stream(html: str) -> tuple[str, str]:
    patterns = [
        r'<meta[^>]+name=["\']twitter:player:stream["\'][^>]+content=["\']([^"\']+)',
        r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+name=["\']twitter:player:stream["\']',
        r'<meta[^>]+property=["\']og:video(?::secure_url)?["\'][^>]+content=["\']([^"\']+)',
    ]
    stream = ""
    for pattern in patterns:
        match = re.search(pattern, html, flags=re.I)
        if match:
            stream = match.group(1).replace("&amp;", "&")
            break
    title_match = re.search(r'<meta[^>]+name=["\']twitter:title["\'][^>]+content=["\']([^"\']+)', html, flags=re.I)
    title = title_match.group(1).replace("&amp;", "&") if title_match else ""
    if not stream:
        raise RuntimeError("Koutube page did not include twitter:player:stream")
    return stream, title


def set_itag(url: str, itag: int) -> str:
    parsed = urlparse(url)
    query = parse_qs(parsed.query)
    query["itag"] = [str(itag)]
    return urlunparse(parsed._replace(query=urlencode(query, doseq=True)))


def parse_content_range(value: str | None) -> tuple[int, int, int] | None:
    if not value:
        return None
    match = re.fullmatch(r"bytes\s+(\d+)-(\d+)/(\d+|\*)", value.strip(), flags=re.I)
    if not match or match.group(3) == "*":
        return None
    return int(match.group(1)), int(match.group(2)), int(match.group(3))


def range_probe(session: requests.Session, url: str) -> dict:
    response = session.get(url, headers={"Range": "bytes=0-0", "Accept": "*/*"}, timeout=45, allow_redirects=True)
    prefix = response.content[:32].hex()
    content_range = parse_content_range(response.headers.get("content-range"))
    record = {
        "status": response.status_code,
        "content_type": response.headers.get("content-type"),
        "content_length": response.headers.get("content-length"),
        "content_range": response.headers.get("content-range"),
        "parsed_content_range": content_range,
        "bytes": len(response.content),
        "prefix_hex": prefix,
        "final_host": urlparse(response.url).netloc,
    }
    if response.status_code != 206 or not content_range:
        raise RuntimeError(f"Range probe was not a valid 206 response: {record}")
    return record


def download_ranges(url: str, output: Path, total: int, chunk_size: int) -> list[dict]:
    session = requests.Session()
    session.headers.update({"User-Agent": UA_BROWSER, "Accept-Language": "ko-KR,ko;q=0.9,en;q=0.8", "Referer": "https://koutu.be/"})
    records: list[dict] = []
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("wb") as file:
        for start in range(0, total, chunk_size):
            end = min(total - 1, start + chunk_size - 1)
            expected = end - start + 1
            last_error = ""
            for attempt in range(1, 7):
                try:
                    response = session.get(
                        url,
                        headers={"Range": f"bytes={start}-{end}", "Accept": "*/*"},
                        timeout=120,
                        allow_redirects=True,
                    )
                    parsed = parse_content_range(response.headers.get("content-range"))
                    body = response.content
                    if response.status_code != 206:
                        raise RuntimeError(f"status={response.status_code}")
                    if parsed != (start, end, total):
                        raise RuntimeError(f"content-range={response.headers.get('content-range')} expected={start}-{end}/{total}")
                    if len(body) != expected:
                        raise RuntimeError(f"length={len(body)} expected={expected}")
                    file.write(body)
                    file.flush()
                    records.append({
                        "start": start,
                        "end": end,
                        "bytes": len(body),
                        "attempt": attempt,
                        "status": response.status_code,
                        "content_range": response.headers.get("content-range"),
                        "final_host": urlparse(response.url).netloc,
                    })
                    break
                except Exception as error:
                    last_error = f"{type(error).__name__}: {error}"
                    time.sleep(min(12, attempt * 2))
            else:
                raise RuntimeError(f"range {start}-{end} failed after retries: {last_error}")
    if output.stat().st_size != total:
        raise RuntimeError(f"final size mismatch: {output.stat().st_size} != {total}")
    return records


def ffprobe_file(path: Path) -> dict:
    proc = run(["ffprobe", "-v", "error", "-show_streams", "-show_format", "-of", "json", str(path)], timeout=180)
    data = json.loads(proc.stdout)
    video = next((item for item in data.get("streams", []) if item.get("codec_type") == "video"), {})
    audio = next((item for item in data.get("streams", []) if item.get("codec_type") == "audio"), {})
    fmt = data.get("format", {})
    return {
        "duration": float(fmt.get("duration") or video.get("duration") or audio.get("duration") or 0),
        "size": int(fmt.get("size") or path.stat().st_size),
        "width": int(video.get("width") or 0),
        "height": int(video.get("height") or 0),
        "video_codec": video.get("codec_name"),
        "audio_codec": audio.get("codec_name"),
        "format_name": fmt.get("format_name"),
    }


def decode_end_frames(source: Path, duration: float, output_dir: Path) -> list[dict]:
    output_dir.mkdir(parents=True, exist_ok=True)
    rows = []
    for ratio in [0.5, 0.85, 0.95, 0.99]:
        point = max(0, duration * ratio)
        target = output_dir / f"frame_{ratio:.2f}_{point:.3f}s.jpg"
        proc = run([
            "ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-ss", f"{point:.3f}",
            "-i", str(source), "-frames:v", "1", "-vf", "scale=960:-2", "-q:v", "3", str(target),
        ], timeout=180, check=False)
        rows.append({"ratio": ratio, "point": point, "returncode": proc.returncode, "exists": target.exists(), "size": target.stat().st_size if target.exists() else 0})
    return rows


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--video-id", required=True)
    parser.add_argument("--expected-duration", required=True, type=float)
    parser.add_argument("--output", required=True)
    parser.add_argument("--chunk-mib", type=int, default=4)
    args = parser.parse_args()

    out = Path(args.output)
    out.mkdir(parents=True, exist_ok=True)
    html = fetch_koutu_html(args.video_id)
    base_stream, title = extract_stream(html)
    url = set_itag(base_stream, 18)
    session = requests.Session()
    session.headers.update({"User-Agent": UA_BROWSER, "Referer": "https://koutu.be/"})
    probe = range_probe(session, url)
    total = int(probe["parsed_content_range"][2])
    source = out / "source_complete.mp4"
    ranges = download_ranges(url, source, total, args.chunk_mib * 1024 * 1024)
    media = ffprobe_file(source)
    frames = decode_end_frames(source, media["duration"], out / "end_frames")
    coverage = media["duration"] / args.expected_duration if args.expected_duration > 0 else 0
    valid = bool(
        source.stat().st_size == total
        and media["width"] >= 640
        and media["audio_codec"]
        and coverage >= 0.98
        and all(row["exists"] and row["size"] > 1000 for row in frames)
    )
    report = {
        "video_id": args.video_id,
        "title": title,
        "expected_duration": args.expected_duration,
        "range_probe": probe,
        "total_bytes": total,
        "chunks": len(ranges),
        "range_records": ranges,
        "media": media,
        "coverage_ratio": round(coverage, 6),
        "end_frame_checks": frames,
        "valid_complete_source": valid,
    }
    (out / "range_download_report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if valid else 3


if __name__ == "__main__":
    raise SystemExit(main())
