from __future__ import annotations

import re
import sys
from pathlib import Path


def replace_once(text: str, pattern: str, replacement: str, label: str, *, flags: int = 0) -> str:
    updated, count = re.subn(pattern, replacement, text, count=1, flags=flags)
    if count != 1:
        raise SystemExit(f"{label} patch count={count}")
    return updated


def main() -> None:
    if len(sys.argv) != 2:
        raise SystemExit("usage: patch_largo_fullscan_analyzer.py <analyzer.py>")
    path = Path(sys.argv[1])
    text = path.read_text(encoding="utf-8")

    koutu = r'''def koutu_source(video_id: str) -> tuple[str, dict[str, Any]]:
    url = f"https://koutu.be/{video_id}"
    js = (
        "const u=" + json.dumps(url) + ";"
        "const r=await fetch(u,{headers:{'user-agent':" + json.dumps(UA_DISCORD) + ",'accept':'text/html,application/xhtml+xml'}});"
        "const t=await r.text();"
        "if(!r.ok){console.error('HTTP '+r.status+' '+t.slice(0,500));process.exit(2)};"
        "process.stdout.write(t);"
    )
    proc = run(["bun", "-e", js], timeout=90, check=False)
    body = proc.stdout
    if proc.returncode != 0 or "twitter:player:stream" not in body:
        proc = run([
            "curl", "-sS", "-L", "--fail", "--retry", "3", "--max-time", "60",
            "-A", UA_DISCORD, url,
        ], timeout=90, check=False)
        body = proc.stdout
    if proc.returncode != 0:
        raise RuntimeError(f"Koutube source page failed: {body[-1500:]}")
    soup = BeautifulSoup(body, "html.parser")
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
        "html_bytes": len(body.encode("utf-8")),
        "source_page_fetcher": "OpenBot Bun fetch",
    }
    return stream, metadata


def set_itag'''
    text = replace_once(
        text,
        r"def koutu_source\(video_id: str\).*?\n\ndef set_itag",
        koutu,
        "koutu_source",
        flags=re.S,
    )

    keyword_anchor = '    "평가발언": ["좋아 보", "괜찮", "강하", "약하", "스윙", "단기 스윙", "오늘 종목"],\n}'
    keyword_replacement = '''    "평가발언": ["좋아 보", "괜찮", "강하", "약하", "스윙", "단기 스윙", "오늘 종목", "관심", "보자", "보겠습니다"],
    "시초가": ["시초가", "시가", "장초반", "장 초반", "동시호가", "갭상승", "갭 상승", "갭하락", "갭 하락", "시가 지지", "시가 이탈", "9시", "구시"],
    "종가": ["종가", "장마감", "장 마감", "마감", "종가매매", "종가 매매", "종가베팅", "종가 베팅", "오버나잇", "익일", "내일", "다음날", "다음 날"],
    "재료": ["재료", "뉴스", "공시", "이슈", "정책", "계약", "수주", "실적", "테마", "일정"],
    "상한가": ["상한가", "상따", "상한가 이력", "상한가 이후", "기준봉"],
    "종목제외": ["제외", "거르", "안 봐", "보지 않", "매수 금지", "거래대금 부족", "호가 비", "관리종목"],
}'''
    if keyword_anchor not in text:
        raise SystemExit("keyword anchor not found")
    text = text.replace(keyword_anchor, keyword_replacement, 1)

    text = replace_once(
        text,
        r'IMPORTANT_GROUPS = \{[^\n]+\}',
        'IMPORTANT_GROUPS = {"호가창", "진입", "청산", "손절", "물량소화", "평가발언", "시초가", "종가", "종목선정", "재료", "상한가", "종목제외"}',
        "important groups",
    )

    text = replace_once(
        text,
        r'sparse_frames = extract_sparse_frames\(source, sparse_dir, interval=5\.0\)',
        'sparse_interval = float(os.environ.get("SPARSE_INTERVAL", "5"))\n    sparse_frames = extract_sparse_frames(source, sparse_dir, interval=sparse_interval)',
        "sparse interval",
    )
    text = replace_once(
        text,
        r'for index, event in enumerate\(events, 1\):',
        'event_frame_limit = int(os.environ.get("EVENT_FRAME_LIMIT", "999999"))\n    for index, event in enumerate(events[:event_frame_limit], 1):',
        "event frame limit",
    )
    text = replace_once(
        text,
        r'windows = merge_windows\(events, file_probe\["duration"\], limit=30\)',
        'dense_limit = int(os.environ.get("DENSE_WINDOWS", "30"))\n    windows = merge_windows(events, file_probe["duration"], limit=dense_limit)',
        "dense window limit",
    )
    text = text.replace(
        'scene_top = sorted(frame_rows, key=lambda row: float(row["scene_change"]), reverse=True)[:48]',
        'scene_top = sorted(frame_rows, key=lambda row: float(row["scene_change"]), reverse=True)[:24]',
        1,
    )
    text = text.replace(
        'order_top = sorted(frame_rows, key=lambda row: float(row["orderbook_score"]), reverse=True)[:64]',
        'order_top = sorted(frame_rows, key=lambda row: float(row["orderbook_score"]), reverse=True)[:28]',
        1,
    )

    path.write_text(text, encoding="utf-8")
    print(f"patched {path}")


if __name__ == "__main__":
    main()
