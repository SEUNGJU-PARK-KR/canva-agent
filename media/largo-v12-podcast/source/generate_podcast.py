#!/usr/bin/env python3
"""Narrate the archived V12 report, not a new backtest.

Podcastfy is Apache-2.0 open-source software. Its Edge provider sends the
approved spoken script to Microsoft's online speech service. This is NOT
fully local/open-weight synthesis. No LLM API, broker, or trading action is used.
"""
from __future__ import annotations
import datetime as dt
import hashlib
import html
import importlib.metadata
import io
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import time
import zipfile

ROOT = Path(os.environ.get('PROJECT_DIR', './largo_v12_podcast')).resolve()
SOURCE = Path(__file__).resolve()
TITLE = '승률 82.9%의 이면 — 라르고 v12 보고서 함께 읽기'
VOICES = {'진행자': 'ko-KR-SunHiNeural', '해설자': 'ko-KR-InJoonNeural'}
# This is an authored, fact-checked script. No third-party LLM may rewrite numbers.
TURNS = [
 ('보고서의 결론부터', '진행자', '승률이 82.9%이고 누적수익이 두 배를 넘었다면, 이제 실매매를 시작해도 될까요? 오늘은 라르고 종가베팅 v12 보고서를 같이 읽어보겠습니다.'),
 ('보고서의 결론부터', '해설자', '먼저 이 방송의 두 목소리는 인공지능 합성 음성입니다. 내용은 기존 보고서의 해설입니다. 새로운 백테스트를 실행하거나 수익을 인증한 방송은 아닙니다. 보고서의 결론은 좋은 과거 조합을 찾았다는 것이지, 앞으로도 같은 수익이 난다는 것은 아닙니다.'),
 ('보고서의 결론부터', '진행자', '이번 목표는 일주일에 최소 두 날짜 이상 거래하되, 두 번 했다고 멈추지 않는 것이었죠. 여기에 낙폭 30% 제한도 붙었고요.'),
 ('보고서의 결론부터', '해설자', '맞습니다. 좋은 후보가 있으면 주 후반에도 계속 진입합니다. 비교 가능한 최근 마흔두 주는 모두 두 거래일 이상을 채웠습니다. 다만 미래에도 매주 두 번을 보장하는 규칙은 아닙니다. 위험한 종목을 억지로 넣어 횟수부터 맞추자는 취지는 아닙니다.'),
 ('수익 숫자를 읽는 법', '진행자', '가장 눈길을 끈 숫자부터 정리해 주세요.'),
 ('수익 숫자를 읽는 법', '해설자', '계좌의 80%를 투입한 균형안은 최근 열 달의 승률이 82.9%였습니다. 누적 복리수익은 104.81%였고, 청산 후 잔고 기준 최대 낙폭은 19.57%였습니다. 기간은 2025년 7월부터 2026년 4월까지입니다. 이 숫자를 월 수익률이나 실제 계좌 성적으로 읽으면 안 됩니다.'),
 ('수익 숫자를 읽는 법', '진행자', '열 달 전체를 보고 고른 조건이라는 점이 중요하겠네요.'),
 ('수익 숫자를 읽는 법', '해설자', '그렇습니다. 처음에는 앞선 기간에서 조건을 고정해 다음 기간에 적용했습니다. 그때 균형안은 후속 낙폭이 34.25%로 제한을 넘었습니다. 이후 이미 본 전체 자료에서 다시 조합을 골라 지금 결과가 나왔습니다. 과거 시험문제를 여러 번 보며 답을 고친 효과가 들어 있습니다.'),
 ('무엇을 고르고 어떻게 파나', '진행자', '종목 조건은 예전보다 많이 단순해졌나요?'),
 ('무엇을 고르고 어떻게 파나', '해설자', '네. 당일 상승률은 영에서 20%까지 허용했습니다. 종가 위치는 40% 이상, 몸통 비율은 20% 이상입니다. 거래대금 소화율은 영 점 일에서 삼까지입니다. 테마 등급과 패턴 고득점은 필수에서 뺐습니다. 대신 기록된 위험과 부정 재료는 계속 제외했습니다.'),
 ('무엇을 고르고 어떻게 파나', '진행자', '최종 한 종목은 무엇을 보고 고르나요?'),
 ('무엇을 고르고 어떻게 파나', '해설자', '그날의 안전 후보끼리 순위를 매깁니다. 과거 이십 일의 시가갭 평균 순위에 절반의 가중치를 줍니다. 낮은 변동성 순위에 삼십 퍼센트, 거래대금 순위에 이십 퍼센트를 줍니다. 매수일 뒤의 정보를 보고 고르는 방식은 아닙니다. 다만 과거 테마와 상장 목록을 완전히 복원하지 못한 한계는 남습니다.'),
 ('무엇을 고르고 어떻게 파나', '진행자', '세 종목 분산도 검증했는데, 이번에는 다시 한 종목이네요.'),
 ('무엇을 고르고 어떻게 파나', '해설자', '한 종목부터 세 종목까지 비교한 뒤, 이번 과거 탐색에서는 한 종목과 현금 조합이 선택됐습니다. 80% 투입형은 나머지 20%를 현금으로 둡니다. 이것은 계산용 배분 가정입니다. 한 종목 집중 위험이 사라지지는 않으며, 실제 투자 비중을 권하는 숫자도 아닙니다.'),
 ('작은 익절과 높은 승률', '진행자', '익절 목표가 0.5%면 너무 작지 않나요?'),
 ('작은 익절과 높은 승률', '해설자', '작은 움직임에서도 이익을 확정해 승률을 높이는 구조입니다. 목표 0.5%에서 왕복 비용 0.3%포인트를 빼면 종목 순수익은 0.2%입니다. 계좌의 80%만 투입했으니 계좌 수익은 0.16%가 됩니다. 목표에 못 닿으면 다음 날 종가에 정리하는 계산이고, 선택된 기본안에는 장중 손절이 없습니다.'),
 ('작은 익절과 높은 승률', '진행자', '그러면 여러 번 이겨도 한 번의 큰 손실 때문에 월 수익이 마이너스일 수 있겠네요.'),
 ('작은 익절과 높은 승률', '해설자', '실제 보고서에 그런 달이 있습니다. 2025년 7월은 스무 번 이기고 세 번 졌습니다. 승률은 87%였지만 월 수익은 마이너스 12.2%였습니다. 승률은 이긴 횟수만 말해 줍니다. 얼마나 벌었고 얼마나 잃었는지는 별도로 봐야 합니다.'),
 ('낙폭 30%와 비용의 함정', '진행자', '그럼 낙폭 30% 이하라는 조건은 어디까지 믿어야 하나요?'),
 ('낙폭 30%와 비용의 함정', '해설자', '이번 낙폭은 매일 청산한 뒤 계좌 잔고의 최고점에서 이후 저점까지 떨어진 비율입니다. 종목의 구조 위험률이나 실제 손절률과는 다른 숫자입니다. 장중에 견뎌야 했던 손실과도 다릅니다. 과거 계산에서 30%를 지켰다는 관측이지, 미래 계좌가 그 아래로 안 떨어진다는 약속이 아닙니다.'),
 ('낙폭 30%와 비용의 함정', '진행자', '비용을 조금만 바꿔도 결과가 많이 달라진다고 했죠?'),
 ('낙폭 30%와 비용의 함정', '해설자', '왕복 비용을 0.3%에서 0.5%로 높이면 80% 투입형의 전체 기간 낙폭은 42.77%로 커집니다. 최근 승률도 48.3%로 낮아집니다. 0.5% 목표에 판 거래가 비용을 빼면 본전이 되기 때문입니다. 본전은 승리로 세지 않습니다. 그래서 높은 승률의 일부는 아주 작은 순이익에 의존합니다.'),
 ('낙폭 30%와 비용의 함정', '진행자', '보고서의 40% 투입형은 이 문제를 어떻게 바꾸나요?'),
 ('낙폭 30%와 비용의 함정', '해설자', '같은 종목에 계좌의 40%만 투입하고 나머지 60%는 현금으로 둡니다. 기본 비용에서 최근 누적수익은 45.88%로 줄어듭니다. 전체 기간 낙폭도 13.41%로 작아집니다. 비용을 0.5%로 올린 경우의 전체 낙폭은 23.12%였습니다. 수익을 일부 포기하고 위험 여유를 확보하는 비교입니다. 이 역시 미래 낙폭 보장은 아닙니다.'),
 ('시가갭과 실제 매도 시간', '진행자', '작은 익절을 반복하는데 누적수익이 크게 나온 이유는 뭔가요?'),
 ('시가갭과 실제 매도 시간', '해설자', '크게 상승 출발한 종목의 이익을 반영했기 때문입니다. 다음 날 시가가 목표보다 높으면 그 시가에 매도했다고 가정합니다. 그 큰 이익까지 전부 0.5%로 제한하면 같은 균형안의 최근 누적수익은 마이너스 65.65%가 됩니다. 별도 체결 가정의 비교입니다. 실제로 그 시가에 팔 수 있었는지 확인하는 일이 핵심입니다.'),
 ('시가갭과 실제 매도 시간', '진행자', '우리가 원래 하려던 건 다음 날 09:06 전 청산이었잖아요. 이번 보고서도 그 시간 기준인가요?'),
 ('시가갭과 실제 매도 시간', '해설자', '아닙니다. 이번 성과는 종가를 매수가로 대신 쓰고, 다음 날 하루 전체 고가와 종가로 계산했습니다. 오전에 손실이었다가 오후에 반등해도 승리로 잡힐 수 있습니다. 따라서 승률 82.9%를 장초반 승률이라고 부르면 안 됩니다. 15:18에 실제 매수 가능한 가격과 다음 날 09:05까지의 매도 가능한 호가가 필요합니다.'),
 ('다음 검증의 방향', '진행자', '골드만삭스 오픈소스를 썼다는 점은 어떤 의미인가요?'),
 ('다음 검증의 방향', '해설자', '보고서는 과거 상관계수를 GS Quant 계산과 대조한 자료를 사용했습니다. 이번 조건 탐색과 수익 계산은 별도의 자체 코드였습니다. 골드만삭스가 이 전략을 추천하거나 성과를 인증했다는 의미는 없습니다. 특히 이번 최종안은 한 종목이라 종목 사이 분산 효과도 없습니다.'),
 ('다음 검증의 방향', '진행자', '그럼 이 보고서를 읽고 다음에 해야 할 일은 무엇일까요?'),
 ('다음 검증의 방향', '해설자', '해설자로서의 제안은 조건을 고정한 뒤 새로운 날짜에서 모의 기록부터 쌓는 것입니다. 매수 시점과 매도 시점을 맞추고, 수량과 체결 지연까지 확인해야 합니다. 승률뿐 아니라 평균 이익, 평균 손실, 비용을 높인 경우의 낙폭도 함께 봅니다. 보고서가 보여 준 것은 검증할 후보입니다. 아직 실매매 성과가 아닙니다.'),
]

def dump(path: Path, obj: object) -> None:
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding='utf-8')

def say_integer(n: int) -> str:
    if n == 0: return '영'
    digits = '영일이삼사오육칠팔구'
    out = ''
    for unit, label in [(10000, '만'), (1000, '천'), (100, '백'), (10, '십'), (1, '')]:
        k, n = divmod(n, unit)
        if k: out += ('' if k == 1 and unit > 1 else digits[k]) + label
    return out

def spoken(text: str) -> str:
    text = text.replace('v12', '브이 십이').replace('GS Quant', '지에스 퀀트')
    text = text.replace('15:18', '오후 세 시 십팔 분').replace('09:06', '오전 아홉 시 육 분').replace('09:05', '오전 아홉 시 오 분')
    def percent(m):
        whole, frac, points = m.group(1), m.group(2), m.group(3)
        value = say_integer(int(whole))
        if frac: value += ' 점 ' + ' '.join('영일이삼사오육칠팔구'[int(x)] for x in frac)
        return value + ' 퍼센트' + (' 포인트' if points else '')
    text = re.sub(r'(\d+)(?:\.(\d+))?%(포인트)?', percent, text)
    text = re.sub(r'(20\d\d)년', lambda m: say_integer(int(m.group(1)))+' 년', text)
    return text

def timestamp(ms: int, vtt: bool=False) -> str:
    h, remainder = divmod(ms, 3600000); minute, remainder = divmod(remainder, 60000); sec, milli = divmod(remainder, 1000)
    return f'{h:02}:{minute:02}:{sec:02}{"." if vtt else ","}{milli:03}'

def prepare() -> None:
    for sub in ['input', 'work', 'source', 'output', 'qc']:
        (ROOT/sub).mkdir(parents=True, exist_ok=True)
    if SOURCE != ROOT/'source/generate_podcast.py': shutil.copy2(SOURCE, ROOT/'source/generate_podcast.py')
    origin = SOURCE.parent.parent/'input/REPORT_SOURCE.md'
    if origin.is_file() and origin != ROOT/'input/REPORT_SOURCE.md': shutil.copy2(origin, ROOT/'input/REPORT_SOURCE.md')
    transcript = ['# '+TITLE, '', '두 화자는 합성 음성입니다. 기존 V12_REPORT.md를 설명하는 원고이며 새 검증 결과가 아닙니다.', '']
    chapter = None
    for title, who, text in TURNS:
        if title != chapter: transcript += ['## '+title, '']; chapter = title
        transcript += ['### '+who, '', text, '']
    (ROOT/'output/PODCAST_TRANSCRIPT.md').write_text('\n'.join(transcript), encoding='utf-8')
    dump(ROOT/'source/dialogue.json', [{'chapter':a,'speaker':b,'text':c,'spoken_text':spoken(c)} for a,b,c in TURNS])
    xml = '\n'.join(f'<Person{1 if b=="진행자" else 2}>{html.escape(spoken(c))}</Person{1 if b=="진행자" else 2}>' for a,b,c in TURNS)
    (ROOT/'source/podcastfy-transcript.txt').write_text(xml, encoding='utf-8')
    (ROOT/'source/requirements.txt').write_text('podcastfy==0.4.3\nedge-tts==7.2.8\nnest-asyncio==1.6.0\npydub==0.25.1\n', encoding='utf-8')
    (ROOT/'README.md').write_text('''# 라르고 v12 보고서 한국어 팟캐스트

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
''', encoding='utf-8')
    (ROOT/'source/THIRD_PARTY_NOTICES.md').write_text('''# 사용한 프로젝트

Podcastfy: https://github.com/souzatharsis/podcastfy — Apache-2.0.
Edge TTS client: https://github.com/rany2/edge-tts — 패키지에 포함된 라이선스를 확인하세요. Microsoft의 온라인 음성 서비스는 별도이며 오픈소스 엔진으로 표시하지 않습니다.
Pydub: https://github.com/jiaaro/pydub — MIT.
FFmpeg: https://ffmpeg.org/legal.html — 실제 시스템 빌드에 따른 LGPL/GPL 조건을 확인하세요.

이 프로젝트는 음성 복제나 투자 성과 인증을 제공하지 않습니다.
''', encoding='utf-8')

def main() -> None:
    prepare()
    from podcastfy.tts.providers.edge import EdgeTTS
    from pydub import AudioSegment
    provider = EdgeTTS(model='edge')
    # Actual Podcastfy splitter is exercised against the authored transcript.
    transcript_xml = (ROOT/'source/podcastfy-transcript.txt').read_text(encoding='utf-8')
    pairs = provider.split_qa(transcript_xml, '', provider.get_supported_tags())
    assert len(pairs) == len(TURNS)//2, (len(pairs), len(TURNS))
    original = (ROOT/'input/REPORT_SOURCE.md').read_text(encoding='utf-8')
    for number in ['82.9%', '104.81%', '19.57%', '25.90%', '42.77%', '48.3%', '45.88%', '23.12%', '-65.65%', '34.25%']:
        assert number in original, number
    combined = AudioSegment.silent(duration=700, frame_rate=24000).set_channels(1)
    segments, chapters = [], []
    previous = None
    for idx, (chapter, speaker, text) in enumerate(TURNS):
        chunk = ROOT/'work'/f'{idx+1:03d}.mp3'
        if not chunk.is_file() or chunk.stat().st_size < 1024:
            for attempt in range(3):
                try:
                    data = provider.generate_audio(spoken(text), VOICES[speaker], 'edge')
                    if len(data) < 1024: raise ValueError('empty audio response')
                    clip = AudioSegment.from_file(io.BytesIO(data), format='mp3')
                    if len(clip)<500 or clip.rms<10: raise ValueError('short or silent audio response')
                    chunk.write_bytes(data)
                    break
                except Exception:
                    if attempt == 2: raise
                    time.sleep(2*(attempt+1))
        clip = AudioSegment.from_file(chunk, format='mp3').set_frame_rate(24000).set_channels(1)
        assert len(clip)>500 and clip.rms>10
        if chapter != previous:
            if previous is not None: combined += AudioSegment.silent(duration=700, frame_rate=24000)
            chapters.append({'title': chapter, 'start_seconds': len(combined)/1000})
            previous = chapter
        start = len(combined)
        combined += clip
        end = len(combined)
        combined += AudioSegment.silent(duration=350, frame_rate=24000)
        segments.append({'index':idx+1,'speaker':speaker,'chapter':chapter,'start_ms':start,'end_ms':end,'text':text,'voice':VOICES[speaker],'rms':clip.rms})
        print(json.dumps({'segment':idx+1,'total':len(TURNS),'seconds':len(clip)/1000}, ensure_ascii=False), flush=True)
    wav = ROOT/'work/assembled.wav'; combined.export(wav,format='wav')
    mp3 = ROOT/'output/largo-v12-podcast.mp3'
    subprocess.run(['ffmpeg','-y','-hide_banner','-loglevel','error','-i',str(wav),'-af','loudnorm=I=-19:TP=-1.5:LRA=11','-ar','24000','-ac','1','-codec:a','libmp3lame','-b:a','96k','-metadata','title='+TITLE,'-metadata','comment=AI-synthesized Korean report explanation; retrospective proxy results, not investment advice.',str(mp3)],check=True,timeout=90)
    decoded = AudioSegment.from_file(mp3)
    assert abs(len(decoded)-len(combined)) < 1000
    assert len(decoded)>120000 and decoded.rms>10
    probe = json.loads(subprocess.check_output(['ffprobe','-v','error','-show_format','-show_streams','-of','json',str(mp3)],text=True))
    dump(ROOT/'qc/ffprobe.json',probe)
    dump(ROOT/'output/chapters.json',chapters)
    dump(ROOT/'output/segments.json',segments)
    srt = '\n\n'.join(f'{r["index"]}\n{timestamp(r["start_ms"])} --> {timestamp(r["end_ms"])}\n{r["speaker"]}: {r["text"]}' for r in segments)
    vtt = 'WEBVTT\n\n'+'\n\n'.join(f'{timestamp(r["start_ms"],True)} --> {timestamp(r["end_ms"],True)}\n{r["speaker"]}: {r["text"]}' for r in segments)
    (ROOT/'output/podcast.srt').write_text(srt+'\n',encoding='utf-8');(ROOT/'output/podcast.vtt').write_text(vtt+'\n',encoding='utf-8')
    buttons = ''.join(f'<button onclick="document.getElementById(\'audio\').currentTime={c["start_seconds"]};document.getElementById(\'audio\').play()">{html.escape(c["title"])}</button>' for c in chapters)
    turns = ''.join(f'<article><h3>{html.escape(r["speaker"])}</h3><p>{html.escape(r["text"])}</p></article>' for r in segments)
    player = '<!doctype html><html lang="ko"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>'+html.escape(TITLE)+'</title><style>body{font-family:system-ui,sans-serif;max-width:850px;margin:auto;padding:28px;line-height:1.8;background:#f5f7f8;color:#1e2b35}header,article,nav{background:white;padding:20px;border-radius:14px;margin-bottom:16px}h1{font-size:26px}h3{font-size:14px;color:#37636c}audio{width:100%}button{margin:5px;padding:10px;border:1px solid #ccd7da;border-radius:8px;background:#fff;cursor:pointer}.note{font-size:14px;color:#555}</style><header><h1>'+html.escape(TITLE)+'</h1><p>진행자와 해설자의 한국어 합성 음성입니다.</p><audio id="audio" controls preload="metadata" src="largo-v12-podcast.mp3"></audio><p class="note">후향 최적화·일봉 대용 결과를 설명합니다. 실전 09:06 전 성과와 미래 낙폭 30% 이하를 보장하지 않습니다.</p></header><nav>'+buttons+'</nav>'+turns+'<p class="note">Podcastfy 오픈소스 공급자와 Microsoft Edge 온라인 음성을 사용했습니다. 전체 오픈 가중치·로컬 음성 엔진은 아닙니다. 음성 합성 외에는 플레이어의 외부 네트워크 요청이 없습니다.</p></html>'
    (ROOT/'output/player.html').write_text(player,encoding='utf-8')
    qc={'status':'SUCCESS','generated_at':dt.datetime.now(dt.timezone.utc).isoformat(),'duration_seconds':len(decoded)/1000,'segments':len(segments),'chapters':len(chapters),'voices':VOICES,'podcastfy_version':importlib.metadata.version('podcastfy'),'edge_tts_version':importlib.metadata.version('edge-tts'),'actual_podcastfy_provider_used':True,'source_fact_checks':True,'decode_verified':True,'human_full_listening_check':False,'new_backtest_performed':False,'orders_or_account_connections':False,'audio_sha256':hashlib.sha256(mp3.read_bytes()).hexdigest(),'audio_bytes':mp3.stat().st_size,'rms':decoded.rms,'peak_dbfs':decoded.max_dBFS,'source_report':'file_00000000f8e48230a4ae8d44b0f54ee1','speech_service':'Microsoft Edge online via Podcastfy EdgeTTS','fully_local_tts':False}
    dump(ROOT/'qc/status.json',qc)
    files=[]
    for p in sorted(ROOT.rglob('*')):
        if p.is_file() and p.name!='manifest.json':
            files.append({'path':str(p.relative_to(ROOT)),'bytes':p.stat().st_size,'sha256':hashlib.sha256(p.read_bytes()).hexdigest(),'modified_at':dt.datetime.fromtimestamp(p.stat().st_mtime,dt.timezone.utc).isoformat()})
    dump(ROOT/'manifest.json',{'created_at':qc['generated_at'],'files':files})
    delivery=Path(os.environ.get('DELIVERY_DIR','delivery')).resolve();delivery.mkdir(parents=True,exist_ok=True)
    for name in ['largo-v12-podcast.mp3','PODCAST_TRANSCRIPT.md','player.html','chapters.json','podcast.srt']:
        shutil.copy2(ROOT/'output'/name,delivery/name)
    dump(delivery/'status.json',qc)
    archive=delivery/'largo_v12_podcast_project.zip'
    with zipfile.ZipFile(archive,'w',zipfile.ZIP_DEFLATED) as bundle:
        for p in sorted(ROOT.rglob('*')):
            if p.is_file():bundle.write(p,'largo_v12_podcast/'+str(p.relative_to(ROOT)))
    print('PODCAST_RESULT '+json.dumps(qc,ensure_ascii=False),flush=True)

if __name__=='__main__':
    try:main()
    except Exception as exc:
        (ROOT/'qc').mkdir(parents=True,exist_ok=True)
        dump(ROOT/'qc/status.json',{'status':'FAILED','error':f'{type(exc).__name__}: {exc}','audio_completed':False})
        raise
