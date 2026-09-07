# Largo unlimited v11

목적: 최소 주 2거래일, 주간 상한 없음, 하루 1~3종목으로 비용 차감 수익·승률·낙폭을 비교합니다.

입력: 교정 아티팩트 9971831156의 원본 ZIP, 후보 CSV와 가격 이력. 원본 해시는 qc/provenance.json에 있습니다.

재현: Python 3.12 환경에서 numpy, pandas, requests, gs-quant==2.1.12 설치 후 source/run_research.py와 source/package_report.py 순서로 실행합니다. LARGO_PROJECT 환경변수로 프로젝트 폴더를 지정할 수 있습니다. 아카이브가 input에 있으면 원본 재다운로드가 필요 없습니다.

결과: output/UNLIMITED_OPTIMIZATION_REPORT.md, summary.json, 일별·월별·주별 CSV, 탐색 전수 결과.

검수: 원본 SHA, 미래 결과 사용 금지, 이전 완료 결과만 학습, 주간 상한 제거, GS 상관 대조, 투자비중과 복리 독립 재계산. 브라우저 시각 검수는 별도 수행하지 않았습니다.

분석은 일봉 대용이며 당시 전 시장 전수검사가 아닙니다. 사이트·운영 매수 규칙은 변경하지 않았습니다. /home/oai는 임시 작업공간입니다.
