from __future__ import annotations

import csv
import datetime as dt
import html
import json
import re
import subprocess
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

import requests
from bs4 import BeautifulSoup

OUT = Path(sys.argv[1] if len(sys.argv) > 1 else "largo_history_output")
OUT.mkdir(parents=True, exist_ok=True)
(OUT / "logs").mkdir(exist_ok=True)
CHANNEL = "https://www.youtube.com/@user-stock97"
TELEGRAM = "https://t.me/s/scalpinglove"
FOCUS = "nucIUdFTkZY"
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/151 Safari/537.36"
S = requests.Session()
S.headers.update({"User-Agent": UA, "Accept-Language": "ko-KR,ko;q=0.9,en;q=0.7"})
ID_RE = re.compile(r"^[0-9A-Za-z_-]{11}$")

CONCEPTS = {
    "시장·섹터": ["주도섹터", "섹터", "순환매", "개별장세", "시장분석"],
    "재료 등급": ["S급재료", "A급재료", "B급재료", "재료분석", "상승모멘텀"],
    "거래대금·수급": ["거래대금", "수급", "프로그램 순매수", "외국인", "기관"],
    "대장주 선정": ["대장주", "S급대장주", "주도주", "후보군"],
    "기준봉·상한가": ["기준봉", "상한가", "장대양봉", "장대음봉", "팽이형", "도지"],
    "물량 소화·매집": ["물량소화", "물량 소화", "매집", "차익물량", "본전물량", "매물대 소화"],
    "이평선·추세": ["5일선", "20일선", "60일선", "120일선", "이평선", "장기이평", "상승추세"],
    "차트 패턴": ["U자형", "V자반등", "양음양", "N파", "박스", "수렴", "시가갭", "신규상장"],
    "눌림 매수": ["눌림매매", "눌림", "분할매수", "지지라인", "추세 지지라인"],
    "돌파 매수": ["돌파매매", "전고점 돌파", "고가돌파", "상단라인돌파", "박스 상단"],
    "호가·체결": ["호가창", "대량물량", "매수세", "매도세", "체결강도", "VI발동", "프로그램매도", "프로그램매수"],
    "시가·종가": ["시초가", "시가갭", "종가반", "종가 공략", "종가매매", "시가매매"],
    "청산": ["단기상승1파", "단기상승 1파", "수익실현", "매도타이밍", "원칙매도", "포지션 종료"],
    "스윙": ["단기스윙", "스윙", "추세 이탈하지 않는다면"],
    "손절·무효화": ["손절", "지지선 이탈", "추세 이탈", "비중축소", "비중 축소"],
}


def dump(path: Path, data: Any) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2, default=str), encoding="utf-8")


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    if not rows:
        path.write_text("", encoding="utf-8-sig")
        return
    keys: list[str] = []
    for row in rows:
        for key in row:
            if key not in keys:
                keys.append(key)
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=keys, extrasaction="ignore")
        w.writeheader(); w.writerows(rows)


def video_id(value: str) -> str | None:
    if not value:
        return None
    value = html.unescape(value).replace("\\u0026", "&").replace("\\/", "/")
    if ID_RE.fullmatch(value):
        return value
    try:
        p = urlparse(value)
        host = p.netloc.lower()
        if host.endswith("youtu.be"):
            candidate = p.path.strip("/").split("/")[0]
            return candidate if ID_RE.fullmatch(candidate) else None
        if "youtube.com" in host or "youtube-nocookie.com" in host:
            q = parse_qs(p.query)
            candidate = (q.get("v") or [None])[0]
            if candidate and ID_RE.fullmatch(candidate):
                return candidate
            parts = p.path.strip("/").split("/")
            for prefix in ("shorts", "live", "embed", "v"):
                if prefix in parts:
                    i = parts.index(prefix)
                    if i + 1 < len(parts) and ID_RE.fullmatch(parts[i + 1]):
                        return parts[i + 1]
    except Exception:
        pass
    m = re.search(r"(?:v=|youtu\.be/|shorts/|live/|embed/)([0-9A-Za-z_-]{11})", value)
    return m.group(1) if m else None


def classify(text: str) -> list[str]:
    text = re.sub(r"\s+", " ", text or "").lower()
    return [name for name, terms in CONCEPTS.items() if any(term.lower() in text for term in terms)]


def crawl_telegram(max_pages: int = 300) -> list[dict[str, Any]]:
    posts: dict[int, dict[str, Any]] = {}
    before: int | None = None
    seen: set[int] = set()
    for page in range(max_pages):
        url = TELEGRAM if before is None else f"{TELEGRAM}?before={before}"
        try:
            r = S.get(url, timeout=60)
            r.raise_for_status()
        except Exception as exc:
            (OUT / "logs" / "telegram.log").open("a", encoding="utf-8").write(f"{url}\t{exc}\n")
            break
        soup = BeautifulSoup(r.text, "html.parser")
        ids = []
        for node in soup.select(".tgme_widget_message"):
            data_post = node.get("data-post", "")
            m = re.search(r"/(\d+)$", data_post)
            if not m:
                continue
            post_id = int(m.group(1)); ids.append(post_id)
            text_node = node.select_one(".tgme_widget_message_text")
            text = text_node.get_text("\n", strip=True) if text_node else ""
            time_node = node.select_one("time")
            date = time_node.get("datetime", "") if time_node else ""
            urls = [a.get("href", "") for a in node.select("a[href]")]
            urls += re.findall(r"https?://[^\s<>\"']+", html.unescape(str(node)).replace("\\/", "/"))
            videos = []
            for u in dict.fromkeys(urls):
                vid = video_id(u)
                if vid:
                    videos.append({"video_id": vid, "url": html.unescape(u)})
            posts[post_id] = {
                "post_id": post_id,
                "date": date,
                "text": text,
                "post_url": f"https://t.me/scalpinglove/{post_id}",
                "videos": videos,
                "concepts": classify(text),
            }
        if not ids:
            break
        new_before = min(ids)
        if new_before <= 1 or new_before in seen or (before is not None and new_before >= before):
            break
        seen.add(new_before); before = new_before
        time.sleep(0.12)
    result = [posts[k] for k in sorted(posts)]
    dump(OUT / "telegram_posts.json", result)
    write_csv(OUT / "telegram_posts.csv", [{
        "post_id": p["post_id"], "date": p["date"], "post_url": p["post_url"],
        "video_ids": "|".join(v["video_id"] for v in p["videos"]),
        "concepts": "|".join(p["concepts"]), "text": p["text"],
    } for p in result])
    return result


def ytdlp_flat() -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for tab in ("videos", "streams", "shorts"):
        cmd = ["yt-dlp", "--flat-playlist", "--ignore-errors", "--dump-json", f"{CHANNEL}/{tab}"]
        try:
            proc = subprocess.run(cmd, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=1800)
            (OUT / "logs" / f"ytdlp_{tab}.log").write_text(proc.stdout, encoding="utf-8", errors="replace")
            for line in proc.stdout.splitlines():
                if not line.lstrip().startswith("{"):
                    continue
                try:
                    data = json.loads(line)
                except Exception:
                    continue
                vid = str(data.get("id") or "")
                if not ID_RE.fullmatch(vid):
                    continue
                rows.append({
                    "video_id": vid, "title": data.get("title") or "", "tab": tab,
                    "url": data.get("url") or f"https://www.youtube.com/watch?v={vid}",
                    "duration": data.get("duration_string") or data.get("duration") or "",
                    "upload_date": data.get("upload_date") or "", "timestamp": data.get("timestamp") or "",
                    "live_status": data.get("live_status") or "",
                })
        except Exception as exc:
            (OUT / "logs" / f"ytdlp_{tab}.log").write_text(str(exc), encoding="utf-8")
    write_csv(OUT / "youtube_flat_inventory.csv", rows)
    return rows


def oembed(vid: str) -> dict[str, Any]:
    try:
        r = S.get("https://www.youtube.com/oembed", params={"url": f"https://www.youtube.com/watch?v={vid}", "format": "json"}, timeout=30)
        return r.json() if r.ok else {"status": r.status_code}
    except Exception as exc:
        return {"error": str(exc)}


def load_previous() -> set[str]:
    p = Path("data/largo_previous_inventory.csv")
    if not p.exists(): return set()
    with p.open(encoding="utf-8-sig", newline="") as f:
        return {row.get("영상ID", "") for row in csv.DictReader(f) if ID_RE.fullmatch(row.get("영상ID", ""))}


def merge(telegram: list[dict[str, Any]], youtube: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    records: dict[str, dict[str, Any]] = {}
    def get(vid: str) -> dict[str, Any]:
        return records.setdefault(vid, {"video_id": vid, "title": "", "tab": "", "url": f"https://www.youtube.com/watch?v={vid}", "duration": "", "upload_date": "", "telegram_posts": [], "telegram_dates": [], "telegram_texts": [], "sources": set()})
    for row in youtube:
        r = get(row["video_id"]); r["sources"].add("youtube_flat")
        for key in ("title", "tab", "url", "duration", "upload_date"):
            if row.get(key): r[key] = row[key]
    for post in telegram:
        for link in post["videos"]:
            r = get(link["video_id"]); r["sources"].add("telegram")
            r["telegram_posts"].append(post["post_id"]); r["telegram_dates"].append(post["date"]); r["telegram_texts"].append(post["text"])
    get(FOCUS)["sources"].add("user_supplied")
    return records


def enrich(records: dict[str, dict[str, Any]]) -> None:
    for i, (vid, r) in enumerate(records.items(), 1):
        if not r["title"] or vid == FOCUS:
            meta = oembed(vid)
            if meta.get("title"): r["title"] = meta["title"]
            r["oembed"] = meta
        text = "\n".join([r["title"], *r["telegram_texts"]])
        r["concepts"] = classify(text)
        r["video_type"] = "short" if r["tab"] == "shorts" else ("live" if r["tab"] == "streams" or "라이브" in text or "실시간" in text else "upload")
        if not r["upload_date"] and r["telegram_dates"]:
            dates = sorted(x[:10] for x in r["telegram_dates"] if re.match(r"20\d\d-\d\d-\d\d", x))
            r["upload_date"] = dates[0] if dates else ""
        if i % 30 == 0: dump(OUT / "progress.json", {"processed": i, "total": len(records)})
        time.sleep(0.04)


def report(records: dict[str, dict[str, Any]], previous: set[str], telegram_count: int, youtube_count: int) -> None:
    rows = []
    for r in records.values():
        rows.append({
            "video_id": r["video_id"], "upload_date": r["upload_date"], "video_type": r["video_type"], "title": r["title"], "url": r["url"], "duration": r["duration"],
            "concepts": "|".join(r["concepts"]), "sources": "|".join(sorted(r["sources"])),
            "telegram_posts": "|".join(map(str, sorted(set(r["telegram_posts"])))),
            "telegram_text": "\n---\n".join(dict.fromkeys(r["telegram_texts"])),
            "previously_known": "yes" if r["video_id"] in previous else "no",
        })
    rows.sort(key=lambda x: (x["upload_date"] or "9999", x["video_type"], x["title"]))
    write_csv(OUT / "full_video_inventory.csv", rows)
    new_rows = [r for r in rows if r["previously_known"] == "no"]
    write_csv(OUT / "newly_discovered.csv", new_rows)
    concept_rows = []
    for row in rows:
        for concept in row["concepts"].split("|"):
            if concept: concept_rows.append({"video_id": row["video_id"], "upload_date": row["upload_date"], "video_type": row["video_type"], "title": row["title"], "concept": concept, "sources": row["sources"], "telegram_posts": row["telegram_posts"]})
    write_csv(OUT / "video_concept_matrix.csv", concept_rows)
    counts = Counter(r["concept"] for r in concept_rows)
    write_csv(OUT / "concept_counts.csv", [{"concept": k, "video_count": v} for k, v in counts.most_common()])
    focus = next((r for r in rows if r["video_id"] == FOCUS), None)
    dump(OUT / "focus_video.json", focus)
    focus_md = f"# 사용자 지정 영상 {FOCUS}\n\n"
    if focus:
        focus_md += f"- 제목: {focus['title'] or '미확인'}\n- 날짜: {focus['upload_date'] or '미확인'}\n- 유형: {focus['video_type']}\n- 개념: {focus['concepts'] or '텍스트 근거 부족'}\n- 공식 게시물: {focus['telegram_posts'] or '연결 없음'}\n\n## 공식 게시물 문맥\n\n{focus['telegram_text'] or '공식 게시물 문맥을 찾지 못했습니다.'}\n"
    (OUT / "focus_video_nucIUdFTkZY.md").write_text(focus_md, encoding="utf-8")
    summary = {"generated_at": dt.datetime.now(dt.timezone.utc).isoformat(), "telegram_posts": telegram_count, "youtube_flat_rows": youtube_count, "total_video_ids": len(rows), "previous_inventory_ids": len(previous), "newly_discovered": len(new_rows), "by_type": dict(Counter(r["video_type"] for r in rows)), "source_counts": dict(Counter(s for rec in records.values() for s in rec["sources"])), "concept_counts": dict(counts), "focus_video": focus}
    dump(OUT / "summary.json", summary)
    cards = "".join(f"<article><b>{html.escape(k)}</b><strong>{v}</strong></article>" for k,v in counts.most_common())
    table = "".join(f"<tr><td>{html.escape(r['upload_date'])}</td><td>{html.escape(r['video_type'])}</td><td><a href='{html.escape(r['url'])}'>{html.escape(r['title'] or r['video_id'])}</a></td><td>{html.escape(r['concepts'])}</td><td>{html.escape(r['sources'])}</td></tr>" for r in rows)
    focus_box = f"<section class='focus'><h2>지정 영상</h2><b>{html.escape((focus or {}).get('title') or FOCUS)}</b><p>{html.escape((focus or {}).get('concepts') or '분류 근거 부족')}</p></section>"
    css = "body{margin:0;background:#eef3f8;color:#152238;font-family:Arial,'Malgun Gothic',sans-serif}main{max-width:1500px;margin:24px auto;background:white;padding:34px;border:1px solid #d7e0ea;border-radius:18px}h1{margin:0}.sub{color:#64748b}.stats,.concepts{display:grid;grid-template-columns:repeat(4,1fr);gap:10px;margin:18px 0}.stats div,.concepts article,.focus{border:1px solid #d7e0ea;background:#fbfcfe;border-radius:12px;padding:15px}.stats b,.concepts strong{display:block;font-size:26px;color:#1463d6}.concepts strong{color:#11854f}table{width:100%;border-collapse:collapse;font-size:11px}th,td{border:1px solid #e2e8f0;padding:7px;text-align:left}th{background:#f1f5f9}a{color:#1463d6;text-decoration:none}.wrap{overflow:auto}@media(max-width:800px){main{margin:0;padding:16px}.stats,.concepts{grid-template-columns:1fr 1fr}}"
    doc = f"<!doctype html><html lang='ko'><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'><title>라르고TV 전 기간 재분석</title><style>{css}</style></head><body><main><h1>라르고TV 전 기간 공개영상 재분석</h1><p class='sub'>유튜브 채널 전체 탭과 공식 텔레그램 과거 게시물을 다시 수집해 기존 40편 목록과 비교했습니다.</p><div class='stats'><div>전체 영상 ID<b>{len(rows)}</b></div><div>기존 목록 밖 신규<b>{len(new_rows)}</b></div><div>공식 게시물<b>{telegram_count}</b></div><div>개념 근거 영상<b>{len({r['video_id'] for r in concept_rows})}</b></div></div>{focus_box}<h2>개념별 영상 수</h2><div class='concepts'>{cards}</div><h2>전체 영상 근거표</h2><div class='wrap'><table><thead><tr><th>날짜</th><th>유형</th><th>영상</th><th>개념</th><th>근거</th></tr></thead><tbody>{table}</tbody></table></div></main></body></html>"
    (OUT / "largo_full_history_report.html").write_text(doc, encoding="utf-8")


def main() -> None:
    telegram = crawl_telegram(); youtube = ytdlp_flat(); records = merge(telegram, youtube); enrich(records); report(records, load_previous(), len(telegram), len(youtube)); print((OUT / "summary.json").read_text(encoding="utf-8"))

if __name__ == "__main__": main()
