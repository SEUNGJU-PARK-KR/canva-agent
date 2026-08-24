from __future__ import annotations

import csv
import html
import json
import random
import re
import sys
import time
from pathlib import Path
from urllib.parse import parse_qs, quote_plus, unquote, urlparse

import requests
from bs4 import BeautifulSoup

INV = Path(sys.argv[1])
OUT = Path(sys.argv[2])
OUT.mkdir(parents=True, exist_ok=True)
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/151 Safari/537.36"
s = requests.Session()
s.headers.update({"User-Agent": UA, "Accept-Language": "ko-KR,ko;q=0.9,en;q=0.8"})
NOTE_RE = re.compile(r"https?://lilys\.ai/(?:ko/)?notes/\d+")
TS_RE = re.compile(r"(?<!\d)(\d{1,2}:\d{2}(?::\d{2})?)\s+(.+)")


def urls_from_html(text: str) -> list[str]:
    found: list[str] = []
    soup = BeautifulSoup(text, "html.parser")
    for a in soup.find_all("a", href=True):
        href = html.unescape(a["href"])
        if "uddg=" in href:
            try:
                href = unquote((parse_qs(urlparse(href).query).get("uddg") or [href])[0])
            except Exception:
                pass
        if href.startswith("/url?"):
            try:
                href = (parse_qs(urlparse(href).query).get("q") or [href])[0]
            except Exception:
                pass
        for m in NOTE_RE.findall(href):
            if m not in found:
                found.append(m)
    for m in NOTE_RE.findall(text.replace("\\u0026", "&").replace("\\/", "/")):
        if m not in found:
            found.append(m)
    return found


def search(title: str) -> tuple[list[str], list[dict]]:
    query = f'site:lilys.ai/notes "{title}"'
    engines = [
        ("duckduckgo", "https://html.duckduckgo.com/html/?q=" + quote_plus(query)),
        ("bing", "https://www.bing.com/search?q=" + quote_plus(query)),
        ("google", "https://www.google.com/search?q=" + quote_plus(query)),
    ]
    urls: list[str] = []
    attempts: list[dict] = []
    for name, endpoint in engines:
        try:
            r = s.get(endpoint, timeout=25)
            got = urls_from_html(r.text)
            attempts.append({"engine": name, "status": r.status_code, "size": len(r.text), "urls": got})
            for u in got:
                if u not in urls:
                    urls.append(u)
            if urls:
                break
        except Exception as exc:
            attempts.append({"engine": name, "error": f"{type(exc).__name__}: {exc}"})
    return urls, attempts


def parse_note(url: str) -> dict:
    rec = {"url": url}
    try:
        r = s.get(url, timeout=35)
        rec.update({"status": r.status_code, "size": len(r.text), "final_url": r.url})
        soup = BeautifulSoup(r.text, "html.parser")
        title = ""
        h1 = soup.find("h1")
        if h1:
            title = " ".join(h1.stripped_strings)
        if not title and soup.title:
            title = soup.title.get_text(" ", strip=True)
        rec["title"] = title
        body = soup.get_text("\n", strip=True)
        cues = []
        seen = set()
        for line in body.splitlines():
            line = re.sub(r"\s+", " ", line).strip()
            m = TS_RE.search(line)
            if not m:
                continue
            key = (m.group(1), m.group(2))
            if key in seen:
                continue
            seen.add(key)
            cues.append({"time": m.group(1), "text": m.group(2)})
        rec["cues"] = cues
        rec["cue_count"] = len(cues)
        rec["body_preview"] = body[:5000]
    except Exception as exc:
        rec["error"] = f"{type(exc).__name__}: {exc}"
    return rec


with INV.open(encoding="utf-8-sig", newline="") as f:
    rows = list(csv.DictReader(f))
uploads = [r for r in rows if r.get("video_type") == "upload"]
focus = next(r for r in rows if r.get("video_id") == "nucIUdFTkZY")
# Stable, diverse probe set: focus + old lessons + strategy titles from across the inventory.
keywords = ["손절", "거래대금", "시초가", "종가", "눌림", "돌파", "상한가", "매집", "호가", "대장주", "분할", "차트"]
selected = [focus]
for kw in keywords:
    candidate = next((r for r in uploads if kw in (r.get("title") or "") and r not in selected), None)
    if candidate:
        selected.append(candidate)
for r in uploads:
    if len(selected) >= 20:
        break
    if r not in selected:
        selected.append(r)

known = {"nucIUdFTkZY": "https://lilys.ai/notes/592117"}
results = []
for idx, row in enumerate(selected, 1):
    title = row.get("title") or row["video_id"]
    urls = [known[row["video_id"]]] if row["video_id"] in known else []
    attempts = []
    if not urls:
        urls, attempts = search(title)
    notes = [parse_note(u) for u in urls[:3]]
    results.append({
        "video_id": row["video_id"],
        "title": title,
        "search_urls": urls,
        "attempts": attempts,
        "notes": notes,
    })
    print(idx, row["video_id"], title[:50], len(urls), [n.get("cue_count", 0) for n in notes])
    time.sleep(0.7)

(OUT / "probe.json").write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
summary = {
    "selected": len(selected),
    "videos_with_note_url": sum(bool(x["search_urls"]) for x in results),
    "videos_with_transcript_cues": sum(any(n.get("cue_count", 0) for n in x["notes"]) for x in results),
    "total_cues": sum(sum(n.get("cue_count", 0) for n in x["notes"]) for x in results),
}
(OUT / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
print(json.dumps(summary, ensure_ascii=False, indent=2))
