# Codex Canva Content Agent 지침

이 저장소에서 작업할 때는 Codex를 콘텐츠 전략가, Canva 자동화 엔지니어, 품질 검수자 역할로 함께 사용한다.

## 기본 원칙

- Claude 전용 명령이나 Anthropic SDK를 새로 추가하지 않는다.
- OpenAI Responses API와 Codex CLI를 중심으로 구현한다.
- Canva 작업은 Connect API 또는 MCP 서버를 거쳐 수행한다.
- 실제 토큰, 클라이언트 시크릿, OAuth 코드, 다운로드 URL을 커밋하지 않는다.
- 생성 결과는 `output/`에 저장한다.
- 레퍼런스 자료는 `references/` 아래에서 읽는다.
- 사용자에게 보여줄 최종 콘텐츠는 한국어로 자연스럽게 다듬는다.

## 작업 흐름

새 콘텐츠를 만들 때는 먼저 브리프를 읽고, 브랜드 보이스와 참고 자료를 확인한다. 그다음 콘텐츠 계획 JSON을 만들고, 사람이 검토하기 쉬운 Markdown을 함께 만든다.

Canva 디자인으로 옮길 때는 브랜드 템플릿 필드 이름을 먼저 확인한다. 필드 이름이 불명확하면 `templates/canva_autofill_map.example.json` 형식으로 매핑 초안을 만들고, 바로 API 호출하지 않는다.

## 파일 규칙

- Python 소스는 `src/codex_canva_agent/` 아래에 둔다.
- 프롬프트는 `prompts/` 아래에 둔다.
- 사용자 입력 예시는 `input/`에 둔다.
- Canva 템플릿 매핑 예시는 `templates/`에 둔다.
- 테스트는 `tests/` 아래에 둔다.

## 검증

코드를 바꾼 뒤에는 다음 명령을 실행한다.

```bash
python -m compileall src
pytest
```

API 호출 코드를 바꿨다면 실제 호출 전에 `codex-canva doctor`로 환경변수 상태를 확인한다.

## Canva 안전장치

원격 MCP나 Connect API가 디자인, 에셋, 브랜드 템플릿을 수정할 수 있다면 항상 사용자 확인을 받는다. 대량 생성, 대량 업로드, 공개 내보내기, 외부 공유 링크 생성은 특히 조심한다.
