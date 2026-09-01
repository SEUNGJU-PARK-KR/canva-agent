# Largo target3 20-day retained archive

2026년 9월 1일 생성된 라르고 +3% 20거래일 GitHub Actions 산출물을 복구해 보관합니다.

## 원본 실행

- workflow run: `33492708036`
- artifact: `9794530194`
- source commit: `a05d1e2f1197a8dae87ab1f1041966fe9ba5c35c`
- original retention: 14 days

## 복구 결과

- 20거래일 후보 480건 재구성
- 정확한 15:18 진입호가와 다음 거래일 09:06 전 매수호가를 함께 확보한 범위는 6거래일 144건
- 평가 가능 전체 후보의 +3% 도달은 20건, 13.9%
- 고정 규칙 통과는 2건이며 1건이 +3%에 도달
- 규칙 발굴일을 제외한 2026-08-24~26에는 통과 신호가 없어 독립 승률을 계산하지 않음

## 보관 정책

- Actions artifact retention: 90 days
- exact recovered ZIP: repository version history
- report and raw tables: GitHub Pages `target3-20day.html` and `data/` directory

후보 재구성에는 현재 네이버 테마 구성과 현재 주식 수를 일부 대용치로 사용했습니다. 20거래일 전체를 당시 화면 그대로 복원한 결과로 해석하지 않습니다.
