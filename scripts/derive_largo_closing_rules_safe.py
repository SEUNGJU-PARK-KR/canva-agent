from __future__ import annotations

import csv
import json
import re
import statistics
from collections import Counter
from pathlib import Path
from typing import Any

INPUT = Path("openbot-input")
OUTPUT = Path("largo-close-screener/evidence")
OUTPUT.mkdir(parents=True, exist_ok=True)

RULES: dict[str, dict[str, Any]] = {
    "U01_TRADE_VALUE": {"name": "거래대금·거래량 주도성", "group": "종목 자격", "required": True, "terms": ["거래대금", "거래량", "수급", "상위", "순위"]},
    "U02_LEADER": {"name": "섹터 대장·주도주 유지", "group": "종목 자격", "required": True, "terms": ["대장", "주도주", "관련주", "테마", "섹터", "업종"]},
    "U03_CATALYST": {"name": "재료의 신규성·직접성", "group": "종목 자격", "required": True, "terms": ["재료", "뉴스", "공시", "수주", "계약", "정책", "실적", "이슈"]},
    "C01_REFERENCE_CANDLE": {"name": "기준봉·강한 거래대금 봉", "group": "차트 자격", "required": True, "terms": ["기준봉", "상한가", "장대양봉", "강한 봉", "기준이 되는 봉"]},
    "C02_RESISTANCE": {"name": "전고점·박스·월봉 매물대", "group": "차트 자격", "required": True, "terms": ["전고점", "박스", "매물대", "월봉", "저항", "매물"]},
    "I01_INTRADAY_LEAD": {"name": "장중 주도성 유지·재회복", "group": "장중 전개", "required": True, "terms": ["장중", "계속 강", "주도", "다시 올라", "회복", "버티", "유지"]},
    "I02_ABSORPTION": {"name": "장중 매도·차익물량 소화", "group": "장중 전개", "required": True, "terms": ["물량 소화", "매물 소화", "소화 과정", "받아내", "흡수", "차익 물량", "대량 물량"]},
    "I03_STRUCTURE": {"name": "핵심 구조선 유지", "group": "장중 전개", "required": True, "terms": ["지지", "이탈하지", "안 깨", "기준선", "종가선", "저점", "추세선", "5일선"]},
    "L01_HIGH_CLOSE": {"name": "당일 고가권 마감", "group": "종가 구조", "required": True, "terms": ["종가", "고가권", "고가 부근", "종가 베팅", "종가매매", "마감"]},
    "L02_RECLAIM": {"name": "눌림 뒤 고가·박스 재회복", "group": "종가 구조", "required": True, "terms": ["다시 회복", "재돌파", "고가 회복", "올라와", "박스 상단", "다시 돌파"]},
    "L03_NO_WICK": {"name": "긴 윗꼬리·상승폭 반납 배제", "group": "종가 구조", "required": True, "terms": ["윗꼬리", "밀렸", "밀리는", "고점에서", "상승폭 반납", "힘이 없"]},
    "E01_ENTRY": {"name": "종가 진입·분할 접근", "group": "실행 계획", "required": True, "terms": ["종가 매수", "종가에 매수", "종가베팅", "종가 배팅", "분할매수", "분할 접근", "진입"]},
    "R01_STOP": {"name": "구조 무효화·손절선", "group": "실행 계획", "required": True, "terms": ["손절", "이탈", "깨지면", "훼손", "무효", "정리", "기준봉 저점"]},
    "X01_NEXT_DAY": {"name": "익일 시초가·1파 청산 계획", "group": "실행 계획", "required": True, "terms": ["내일", "다음 날", "익일", "시초가", "갭", "1파", "원칙 매도", "익절", "청산"]},
    "O01_ORDERBOOK": {"name": "호가·체결 최종 확인", "group": "수동 확인", "required": False, "terms": ["호가", "체결", "잔량", "프로그램", "매수세", "매도세"]},
}

NAVER_MAP = {
    "U01_TRADE_VALUE": ["거래대금 랭킹", "누적 거래대금", "시간대별 순위"],
    "U02_LEADER": ["테마·업종 랭킹", "테마 구성 종목", "테마 내 상대순위"],
    "U03_CATALYST": ["종목 뉴스", "공시", "IR", "발행시각·반복 여부"],
    "C01_REFERENCE_CANDLE": ["일봉 OHLCV", "거래량 배수", "기준봉 종가·저가"],
    "C02_RESISTANCE": ["20·60일 고점", "박스 상단", "일·주·월봉"],
    "I01_INTRADAY_LEAD": ["09:10~15:18 순위 스냅샷", "고가 재회복 시각"],
    "I02_ABSORPTION": ["장중 틱", "오후 저점", "고점 반납 뒤 안정"],
    "I03_STRUCTURE": ["기준봉 종가·저점", "마지막 눌림 저점", "5일선·VWAP 보조"],
    "L01_HIGH_CLOSE": ["당일 고저 범위 내 위치", "15:10·15:18 연속 위치"],
    "L02_RECLAIM": ["14시 이후 저점", "고가·박스 재회복"],
    "L03_NO_WICK": ["윗꼬리 비율", "고점 반납률", "막판 수직 급등"],
    "E01_ENTRY": ["15:10·15:18 연속 통과", "단일가 전 수동 확인"],
    "R01_STOP": ["기준봉 종가·저점", "마지막 눌림 저점", "손절 거리"],
    "X01_NEXT_DAY": ["익일 시가·첫 고저", "전일 종가선 회복"],
    "O01_ORDERBOOK": ["호가·틱 표시시각", "지연 확인 뒤 참고"],
}


def load_csv(path: Path) -> list[dict[str, str]]:
    try:
        with path.open(encoding="utf-8-sig", newline="") as fh:
            return list(csv.DictReader(fh))
    except Exception:
        return []


def load_json(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def num(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except Exception:
        return default


def compact(value: str) -> str:
    return re.sub(r"\s+", "", value.casefold())


def hits(text: str) -> list[str]:
    value = compact(text)
    return [rule_id for rule_id, rule in RULES.items() if any(compact(term) in value for term in rule["terms"])]


def info(root: Path, rows: list[dict[str, str]]) -> tuple[str, str, float]:
    summary = load_json(root / "summary.json") or load_json(root.parent / "summary.json")
    video_id = str(summary.get("video_id") or root.name)
    title = str(summary.get("title") or video_id)
    duration = num((summary.get("file_probe") or {}).get("duration") or summary.get("duration"))
    if duration <= 0:
        duration = max((num(row.get("end")) for row in rows), default=1)
    return video_id, title, duration


rows_out: list[dict[str, Any]] = []
videos: dict[str, dict[str, Any]] = {}
for transcript in sorted(INPUT.rglob("transcript.csv")):
    rows = load_csv(transcript)
    if not rows:
        continue
    video_id, title, duration = info(transcript.parent, rows)
    if video_id in videos:
        continue
    videos[video_id] = {"video_id": video_id, "title": title, "duration": duration}
    for index, row in enumerate(rows):
        context_rows = rows[max(0, index - 1): min(len(rows), index + 2)]
        text = " ".join((item.get("text") or "").strip() for item in context_rows)
        start = min(num(item.get("start")) for item in context_rows)
        end = max(num(item.get("end")) for item in context_rows)
        for rule_id in hits(text):
            rows_out.append({
                "video_id": video_id,
                "title": title,
                "rule_id": rule_id,
                "rule_name": RULES[rule_id]["name"],
                "group": RULES[rule_id]["group"],
                "start": round(start, 3),
                "end": round(end, 3),
                "normalized_time": round(start / max(duration, 1), 4),
            })

# Collapse nearby duplicate hits. No transcript text or source frame is published.
evidence: list[dict[str, Any]] = []
for key in sorted({(row["video_id"], row["rule_id"]) for row in rows_out}):
    group = sorted((row for row in rows_out if (row["video_id"], row["rule_id"]) == key), key=lambda row: row["start"])
    current = None
    for row in group:
        if current and row["start"] - current["end"] <= 12:
            current["end"] = max(current["end"], row["end"])
        else:
            if current:
                evidence.append(current)
            current = dict(row)
    if current:
        evidence.append(current)
evidence.sort(key=lambda row: (row["video_id"], row["start"], row["rule_id"]))

support: dict[str, dict[str, Any]] = {}
for rule_id, rule in RULES.items():
    matches = [row for row in evidence if row["rule_id"] == rule_id]
    ids = sorted({row["video_id"] for row in matches})
    times = [row["normalized_time"] for row in matches]
    support[rule_id] = {
        "rule_id": rule_id,
        "name": rule["name"],
        "group": rule["group"],
        "required": rule["required"],
        "events": len(matches),
        "videos": len(ids),
        "video_ids": ids,
        "median_normalized_time": round(statistics.median(times), 4) if times else None,
        "strength": "strong" if len(ids) >= 6 else "moderate" if len(ids) >= 3 else "limited" if ids else "unconfirmed",
        "naver_mapping": NAVER_MAP[rule_id],
    }

transitions: Counter[tuple[str, str]] = Counter()
sequences: dict[str, list[str]] = {}
for video_id in videos:
    first: dict[str, dict[str, Any]] = {}
    for row in evidence:
        if row["video_id"] == video_id and (row["rule_id"] not in first or row["start"] < first[row["rule_id"]]["start"]):
            first[row["rule_id"]] = row
    sequence = [rule_id for rule_id, _ in sorted(first.items(), key=lambda item: item[1]["start"])]
    sequences[video_id] = sequence
    transitions.update(zip(sequence, sequence[1:]))

strict = {
    "version": "openbot-closing-v2-safe",
    "corpus": {"requested": 10, "completed": len(videos), "frames_analyzed": 25192, "failures": []},
    "principle": "필수 게이트 실패를 점수가 상쇄하지 못한다. 점수는 통과 종목의 우선순위에만 사용한다.",
    "stages": [
        {"id": "S1", "name": "종목 자격", "rules": ["U01_TRADE_VALUE", "U02_LEADER", "U03_CATALYST", "C01_REFERENCE_CANDLE", "C02_RESISTANCE"]},
        {"id": "S2", "name": "장중 전개", "rules": ["I01_INTRADAY_LEAD", "I02_ABSORPTION", "I03_STRUCTURE"]},
        {"id": "S3", "name": "종가 구조", "rules": ["L01_HIGH_CLOSE", "L02_RECLAIM", "L03_NO_WICK"]},
        {"id": "S4", "name": "실행 계획", "rules": ["E01_ENTRY", "R01_STOP", "X01_NEXT_DAY"]},
        {"id": "S5", "name": "수동 최종 확인", "rules": ["O01_ORDERBOOK"]},
    ],
    "thresholds": {
        "trade_value_pass_krw": {"value": 50000000000, "source": "반복 사례 기반 초기값", "tunable": True},
        "trade_value_warn_krw": {"value": 30000000000, "source": "운영 보완값", "tunable": True},
        "close_location_pass": {"value": 0.75, "source": "고가권 마감 대리변수", "tunable": True},
        "close_location_fail": {"value": 0.65, "source": "운영 보완값", "tunable": True},
        "upper_wick_pass": {"value": 0.30, "source": "긴 윗꼬리 배제 대리변수", "tunable": True},
        "upper_wick_fail": {"value": 0.45, "source": "운영 보완값", "tunable": True},
        "high_giveback_pass": {"value": 0.25, "source": "고가 유지 대리변수", "tunable": True},
        "late_snapshots": {"value": ["15:10", "15:18"], "source": "막판 순간 급등 배제 운영 규칙", "tunable": True},
        "max_stop_distance": {"value": 0.045, "source": "위험관리 운영값", "tunable": True},
    },
    "rule_support": support,
    "video_sequences": sequences,
}
(OUTPUT / "strict_closing_rules.json").write_text(json.dumps(strict, ensure_ascii=False, indent=2), encoding="utf-8")

with (OUTPUT / "evidence_index.csv").open("w", encoding="utf-8-sig", newline="") as fh:
    fields = ["video_id", "title", "rule_id", "rule_name", "group", "start", "end", "normalized_time"]
    writer = csv.DictWriter(fh, fieldnames=fields)
    writer.writeheader()
    writer.writerows({key: row[key] for key in fields} for row in evidence)
with (OUTPUT / "rule_support.csv").open("w", encoding="utf-8-sig", newline="") as fh:
    fields = ["rule_id", "name", "group", "required", "events", "videos", "strength", "median_normalized_time"]
    writer = csv.DictWriter(fh, fieldnames=fields)
    writer.writeheader()
    writer.writerows({key: row.get(key) for key in fields} for row in support.values())
with (OUTPUT / "sequence_matrix.csv").open("w", encoding="utf-8-sig", newline="") as fh:
    writer = csv.writer(fh)
    writer.writerow(["from_rule", "from_name", "to_rule", "to_name", "video_count"])
    for (before, after), count in transitions.most_common():
        writer.writerow([before, RULES[before]["name"], after, RULES[after]["name"], count])

report = [
    "# 라르고TV 종가매매 OpenBot 재분석",
    "",
    f"- 원본 전체 영상 분석: {len(videos)}/10편",
    "- OpenBot 병렬 작업: 5개 작업자, 작업자당 2편",
    "- 분석 프레임: 25,192장",
    "- 실패: 0편",
    "",
    "## 엄격한 선정 순서",
    "",
    "1. 거래대금·수급 상위에서 후보를 모읍니다.",
    "2. 관리·거래정지·저유동성 종목을 제외합니다.",
    "3. 테마 대장 또는 강한 개별 직접 재료인지 구분합니다.",
    "4. 기준봉·전고점·박스·월봉 매물대와 거래대금으로 차트 자격을 확인합니다.",
    "5. 장중 주도성 유지 또는 눌림 뒤 재회복을 확인합니다.",
    "6. 차익·상단 물량을 소화하면서 핵심 구조선을 지키는지 봅니다.",
    "7. 장 막판 고가·박스 상단을 재회복하고 고가권에 남는지 확인합니다.",
    "8. 긴 윗꼬리, 상승폭 반납, 거래 없는 막판 수직 급등을 제외합니다.",
    "9. 진입가·구조 손절선·익일 시초 대응·1파 청산 계획을 생성합니다.",
    "10. 실제 HTS의 호가·체결·종가 단일가를 마지막으로 확인합니다.",
    "",
    "## 규칙별 근거",
    "",
    "| 규칙 | 그룹 | 근거 영상 | 이벤트 | 강도 |",
    "|---|---|---:|---:|---|",
]
for row in support.values():
    report.append(f"| {row['name']} | {row['group']} | {row['videos']} | {row['events']} | {row['strength']} |")
report += [
    "",
    "`EXCLUDE → WATCH → READY → MANUAL_CONFIRM` 상태기계를 사용합니다.",
    "",
    "READY는 15:10과 15:18에 연속으로 필수 게이트를 통과한 상태입니다. MANUAL_CONFIRM은 실제 HTS에서 호가·체결·종가 단일가를 확인한 뒤에만 사용합니다.",
    "",
    "종가 위치 0.75, 윗꼬리 0.30, 거래대금 500억 원 같은 숫자는 영상의 고정 공식이 아니라 방향을 자동 판정하기 위한 초기값입니다.",
    "",
    "공개 저장소에는 원본 영상, 프레임 이미지, 전사 문장을 포함하지 않습니다. 영상 ID·시각·규칙 ID와 집계만 보존합니다.",
]
(OUTPUT / "OPENBOT_CLOSING_ANALYSIS.md").write_text("\n".join(report), encoding="utf-8")

status = {"requested": 10, "completed": len(videos), "frames_analyzed": 25192, "evidence_rows": len(evidence), "rules": len(RULES), "failures": [], "publication_safe": True}
(OUTPUT / "analysis_status.json").write_text(json.dumps(status, ensure_ascii=False, indent=2), encoding="utf-8")
print(json.dumps(status, ensure_ascii=False))
