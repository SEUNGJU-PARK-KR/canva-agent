# Setup

## 1. Python 환경

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e .[dev]
```

## 2. 환경변수

```bash
cp .env.example .env
```

필수 값은 다음과 같습니다.

```bash
OPENAI_API_KEY=...
CANVA_ACCESS_TOKEN=...
```

## 3. Codex 설정

`.codex/config.toml`은 프로젝트 단위 설정입니다. 전역 설정은 `~/.codex/config.toml`에 둡니다.

Canva Dev MCP 서버는 Canva 앱이나 통합 개발을 돕는 서버입니다. 실제 디자인 자동화는 Connect API 또는 별도 원격 MCP 서버로 구성하세요.

## 4. 점검

```bash
codex-canva doctor
python -m compileall src
pytest
```
