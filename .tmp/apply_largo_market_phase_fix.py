#!/usr/bin/env python3
from pathlib import Path

rules = Path('scripts/largo_winrate_v4.py')
text = rules.read_text(encoding='utf-8')
old = '''def phase(source_at: dt.datetime | None) -> dict[str, str]:
    if source_at is None:
        return {
            "id": "UNKNOWN", "title": "기준 시각 확인 필요", "label": "기준 시각 확인 불가",
            "action": "최신 데이터 생성 시각을 확인합니다.",
        }
    current = source_at.time()
    if current < dt.time(14, 35):
        phase_id, title, action = "EARLY", "조기 관찰", "재료·주도주·차트 구조를 먼저 준비합니다."
    elif current < dt.time(15, 5):
        phase_id, title, action = "PRE_CLOSE", "마감 구조 관찰", "고가권·윗꼬리·매물 소화를 확인합니다."
    elif current < dt.time(15, 18):
        phase_id, title, action = "ENTRY_PREP", "진입 준비", "구조 손절과 반복 유지를 확인합니다."
    elif current < dt.time(15, 20):
        phase_id, title, action = "FINAL_CHECK", "15:18 최종 확인", "종가 보유형과 익일 확인형을 분리합니다."
    elif current < dt.time(15, 30):
        phase_id, title, action = "AUCTION", "종가 단일가 검토", "종가 보유형만 최종 검토합니다."
    else:
        phase_id, title, action = "FINAL_CLOSE", "종가 확정 검증", "신규 진입이 아니라 결과를 기록합니다."
    return {
        "id": phase_id,
        "title": title,
        "label": f"{source_at:%Y-%m-%d %H:%M} · {title}",
        "action": action,
    }
'''
new = '''def phase(source_at: dt.datetime | None) -> dict[str, str]:
    if source_at is None:
        return {
            "id": "UNKNOWN", "title": "기준 시각 확인 필요", "label": "기준 시각 확인 불가",
            "action": "최신 데이터 생성 시각을 확인합니다.",
        }
    current = source_at.time()
    if source_at.weekday() >= 5:
        phase_id, title, action = "MARKET_CLOSED", "휴장일 기록", "신규 진입 판단에 사용하지 않습니다."
    elif current < dt.time(9, 0):
        phase_id, title, action = "PRE_MARKET", "장 시작 전 · 전일 기록", "신규 진입 판단에 사용하지 않습니다."
    elif current < dt.time(13, 38):
        phase_id, title, action = "MORNING", "장중 데이터 축적", "종가 후보를 확정하지 않고 데이터만 축적합니다."
    elif current < dt.time(14, 35):
        phase_id, title, action = "EARLY", "조기 관찰", "재료·주도주·차트 구조를 먼저 준비합니다."
    elif current < dt.time(15, 5):
        phase_id, title, action = "PRE_CLOSE", "마감 구조 관찰", "고가권·윗꼬리·매물 소화를 확인합니다."
    elif current < dt.time(15, 18):
        phase_id, title, action = "ENTRY_PREP", "진입 준비", "구조 손절과 반복 유지를 확인합니다."
    elif current < dt.time(15, 20):
        phase_id, title, action = "FINAL_CHECK", "15:18 최종 확인", "종가 보유형과 익일 확인형을 분리합니다."
    elif current < dt.time(15, 30):
        phase_id, title, action = "AUCTION", "종가 단일가 검토", "종가 보유형만 최종 검토합니다."
    else:
        phase_id, title, action = "FINAL_CLOSE", "종가 확정 검증", "신규 진입이 아니라 결과를 기록합니다."
    return {
        "id": phase_id,
        "title": title,
        "label": f"{source_at:%Y-%m-%d %H:%M} · {title}",
        "action": action,
    }
'''
if new not in text:
    if old not in text:
        raise SystemExit('phase block not found')
    text = text.replace(old, new, 1)
old_lane = '''    if active_hard_failures or unresolved_hard or risk.get("status") != "PASS" or negative_catalyst:
        lane = "EXCLUDE"
    elif close_fit and phase_id in {"FINAL_CHECK", "AUCTION"}:
'''
new_lane = '''    if active_hard_failures or unresolved_hard or risk.get("status") != "PASS" or negative_catalyst:
        lane = "EXCLUDE"
    elif phase_id in {"PRE_MARKET", "MORNING", "MARKET_CLOSED", "UNKNOWN"}:
        lane = "WATCH"
    elif close_fit and phase_id in {"FINAL_CHECK", "AUCTION"}:
'''
if new_lane not in text:
    if old_lane not in text:
        raise SystemExit('lane block not found')
    text = text.replace(old_lane, new_lane, 1)
rules.write_text(text, encoding='utf-8')

validator = Path('scripts/validate_largo_winrate_v41.py')
text = validator.read_text(encoding='utf-8')
text = text.replace(
    'from largo_winrate_v4 import KST, VERSION, analyze_candidate, event_item_matches, theme_breadth',
    'from largo_winrate_v4 import KST, VERSION, analyze_candidate, event_item_matches, phase, theme_breadth',
)
marker = '''    source_at = dt.datetime(2026, 9, 1, 15, 18, tzinfo=KST)

    core = candidate()
'''
replacement = '''    source_at = dt.datetime(2026, 9, 1, 15, 18, tzinfo=KST)
    pre_market = dt.datetime(2026, 9, 1, 1, 32, tzinfo=KST)
    morning = dt.datetime(2026, 9, 1, 10, 30, tzinfo=KST)
    weekend = dt.datetime(2026, 9, 5, 14, 0, tzinfo=KST)
    assert phase(pre_market)["id"] == "PRE_MARKET"
    assert phase(morning)["id"] == "MORNING"
    assert phase(weekend)["id"] == "MARKET_CLOSED"

    core = candidate()
'''
if replacement not in text:
    if marker not in text:
        raise SystemExit('validator phase marker not found')
    text = text.replace(marker, replacement, 1)
marker = '''    result = analyze_candidate(core, source_at, history(core["code"]))
    assert result["lane"] == "CLOSE_ENTRY" and result["close_variant"] == "CORE", result

    elite = candidate'''
replacement = '''    result = analyze_candidate(core, source_at, history(core["code"]))
    assert result["lane"] == "CLOSE_ENTRY" and result["close_variant"] == "CORE", result
    pre_market_result = analyze_candidate(core, pre_market, history(core["code"]))
    assert pre_market_result["lane"] == "WATCH", pre_market_result

    elite = candidate'''
if replacement not in text:
    if marker not in text:
        raise SystemExit('validator lane marker not found')
    text = text.replace(marker, replacement, 1)
text = text.replace(
    '"missing_stats": "PASS"})',
    '"missing_stats": "PASS", "market_phase": "PASS"})',
)
validator.write_text(text, encoding='utf-8')
print('market phase safety patch applied')
