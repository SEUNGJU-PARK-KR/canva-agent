# 라르고 v12 보고서 한국어 팟캐스트

기존 보고서를 진행자와 해설자의 대화로 설명합니다. 새 백테스트를 실행하지 않습니다.

## 입력과 원본
input/REPORT_SOURCE.md는 대화 원문에서 확인한 발췌본입니다. 원본 전체가 아닙니다. 전체 원본 V12_REPORT.md는 대화 작업공간 /mnt/data/largo_v12_podcast/input/V12_REPORT.md에 별도로 보존했습니다. 제공되는 ZIP은 원문 발췌와 음성 제작 재현 자료를 포함합니다.

## 사용 도구
Podcastfy 0.4.3의 실제 EdgeTTS 공급자 클래스를 사용합니다. Python 패키지는 Apache-2.0 오픈소스입니다. 음성 합성은 Microsoft Edge 온라인 서비스를 사용하므로 전체가 로컬 또는 오픈 가중치 엔진인 것은 아닙니다. 승인된 낭독문만 음성 서비스로 보냅니다. 외부 언어모델에 보고서를 보내지 않습니다. 금융 계좌 연결과 주문 기능은 없습니다. 실존 인물의 음성을 복제하지 않습니다.

## 실행
Python 3.11과 시스템 FFmpeg가 필요합니다.
```sh
python -m pip install --no-deps podcastfy==0.4.3
python -m pip install edge-tts==7.2.8 nest-asyncio==1.6.0 pydub==0.25.1
PROJECT_DIR=./largo_v12_podcast python source/generate_podcast.py
```

## 결과
output/largo-v12-podcast.mp3를 재생하세요. output/player.html은 목차와 원고가 있는 오프라인 플레이어입니다. MP3와 같은 폴더에 두세요. output/PODCAST_TRANSCRIPT.md와 segment-aligned 자막이 포함됩니다. 자막은 발화 구간 정렬이며 단어별 강제 정렬이 아닙니다.

## 검수
원고의 핵심 숫자를 보고서 발췌와 대조했습니다. 합성 구간 수, 오디오 디코딩, 길이, 음량, 파일 해시를 자동 검사합니다. 사람이 전 구간을 직접 청취한 검수로 표시하지 않습니다. /home/oai는 임시 실행공간입니다. 장기 보관에는 제공 파일이나 저장소 사본을 사용하세요.
