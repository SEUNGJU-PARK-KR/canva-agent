# Codex Canva Content Agent

OpenAnalystInc/10x-Content-Expert의 아이디어를 Codex와 OpenAI Responses API 중심으로 다시 구성한 클린룸 프로젝트입니다. Claude Code 전용 스킬 구조를 그대로 옮기지 않고, Codex가 읽는 `AGENTS.md`, 프로젝트 단위 `.codex/config.toml`, Python CLI, Canva Connect API 래퍼, 원격 MCP 호출 도우미로 나눴습니다.

이 프로젝트는 다음 흐름을 목표로 합니다.

1. 브리프와 레퍼런스 파일을 읽습니다.
2. OpenAI 모델이 콘텐츠 전략과 산출물을 만듭니다.
3. Canva Connect API로 이미지·영상 에셋을 업로드합니다.
4. 브랜드 템플릿 Autofill 또는 기존 디자인 내보내기로 Canva 산출물을 만듭니다.
5. Codex CLI 안에서 같은 규칙과 도구를 반복 사용할 수 있게 합니다.

## 왜 이렇게 바꿨나

원본 프로젝트는 Claude Code skills plugin 성격이 강합니다. 이 버전은 Codex CLI에서 바로 다루기 쉽게 다음 요소를 중심으로 바꿨습니다.

- `AGENTS.md`에 에이전트 작업 규칙을 둡니다.
- `.codex/config.toml`에 Canva MCP 서버 설정 예시를 둡니다.
- OpenAI Responses API를 기본 콘텐츠 생성 엔진으로 씁니다.
- Canva Connect API는 별도 Python 클라이언트로 감쌉니다.
- 프롬프트, 레퍼런스, 산출물 폴더를 분리합니다.
- GitHub 프라이빗 저장소로 옮기기 쉬운 스크립트를 넣었습니다.

## 빠른 시작

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e .[dev]
cp .env.example .env
```

`.env`에 OpenAI 키와 Canva 토큰을 넣습니다.

```bash
OPENAI_API_KEY=...
CANVA_ACCESS_TOKEN=...
```

브리프 예시로 콘텐츠 계획을 만듭니다.

```bash
codex-canva plan input/brief.example.md --out output/content-plan.json --markdown output/content-plan.md
```

Canva에 공개 URL 이미지를 에셋으로 업로드합니다.

```bash
codex-canva canva upload-url \
  --name "launch-cover" \
  --url "https://example.com/cover.png" \
  --out output/upload-job.json
```

디자인을 PDF로 내보내는 작업을 생성합니다.

```bash
codex-canva canva export \
  --design-id "DAGxxxxxxxx" \
  --format pdf \
  --out output/export-job.json
```

## Codex에서 쓰기

Codex CLI를 저장소 루트에서 실행하면 `AGENTS.md`의 지시를 먼저 읽도록 설계했습니다. Canva 관련 MCP는 `.codex/config.toml`에 예시로 넣어두었습니다.

```bash
codex
```

Codex 안에서는 이런 식으로 요청하면 됩니다.

```text
브리프 input/brief.example.md를 읽고, 링크드인 캐러셀 7장 구성안을 만든 다음 output/content-plan.md에 저장해줘.
```

또는 Canva API 토큰이 설정된 상태에서 다음처럼 요청할 수 있습니다.

```text
output/content-plan.json을 바탕으로 Canva 브랜드 템플릿 Autofill 데이터 매핑을 만들어줘. 부족한 필드는 templates/canva_autofill_map.example.json을 참고해줘.
```

## 폴더 구조

```text
.
├── AGENTS.md
├── .codex/config.toml
├── src/codex_canva_agent/
│   ├── cli.py
│   ├── openai_content.py
│   ├── canva_connect.py
│   ├── reference_loader.py
│   └── schemas.py
├── prompts/
├── templates/
├── references/
├── input/
├── output/
├── docs/
└── scripts/
```

## 주요 명령

```bash
codex-canva doctor
codex-canva plan input/brief.example.md --out output/content-plan.json --markdown output/content-plan.md
codex-canva canva upload-url --name sample --url https://example.com/image.png
codex-canva canva export --design-id DAGxxx --format pdf
codex-canva canva poll-export --export-id export-job-id
codex-canva canva autofill --brand-template-id Bxxx --data templates/canva_autofill_map.example.json
```

## Canva 사용 방식

이 프로젝트는 Canva 스톡 라이브러리를 무단으로 긁어오는 구조가 아닙니다. Canva 계정, 브랜드 템플릿, 업로드한 에셋, 사용자가 접근 권한을 가진 디자인을 API로 다루는 구조입니다.

Connect API는 웹앱 통합용입니다. 공개 통합은 Canva 검수를 거쳐야 합니다. 팀 내부 자동화는 비공개 통합이나 Enterprise 흐름으로 잡는 편이 좋습니다.

## 보안 메모

- `.env`는 절대 커밋하지 않습니다.
- Canva 토큰과 OpenAI 키는 GitHub Actions Secret이나 로컬 환경변수로 관리합니다.
- 원격 MCP 서버는 신뢰할 수 있는 서버만 연결합니다.
- Canva에서 받은 다운로드 URL은 만료될 수 있습니다. 산출물을 장기 보관하려면 별도 스토리지로 복사합니다.

## 라이선스

이 프로젝트 템플릿은 MIT로 배포할 수 있게 구성했습니다. 원본 프로젝트의 코드를 복사하지 않은 클린룸 재구성입니다. 실제 배포 전에는 사용하는 API, 템플릿, 이미지, 폰트, 브랜드 에셋의 이용 조건을 따로 확인하세요.
