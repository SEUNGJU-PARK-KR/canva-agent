from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from openai import OpenAI

from .reference_loader import format_references, load_references
from .schemas import ContentPlan
from .settings import Settings

SYSTEM_PROMPT = """
당신은 콘텐츠 전략가이자 Canva 자동화 설계자입니다.
브리프와 참고 자료를 읽고 Canva 템플릿에 넣기 쉬운 구조화된 콘텐츠 계획을 만듭니다.
반드시 JSON만 출력합니다. Markdown 설명이나 코드블록은 출력하지 않습니다.
""".strip()

SCHEMA_HINT = """
다음 JSON 구조를 지켜주세요.
{
  "title": "string",
  "audience": "string",
  "channel": "string",
  "objective": "string",
  "voice": "string",
  "summary": "string",
  "slides": [
    {
      "slide_number": 1,
      "title": "string",
      "goal": "string",
      "assets": [
        {"kind": "headline", "text": "string", "canva_field": "optional string"}
      ],
      "design_notes": ["string"]
    }
  ],
  "export_formats": ["pdf", "png"],
  "review_checklist": ["string"]
}
""".strip()


def build_content_plan(
    brief_path: Path,
    reference_dir: Path,
    settings: Settings,
    channel: str | None = None,
) -> ContentPlan:
    if not settings.openai_api_key:
        raise RuntimeError("OPENAI_API_KEY가 설정되어 있지 않습니다.")
    if not brief_path.exists():
        raise FileNotFoundError(f"브리프 파일을 찾을 수 없습니다: {brief_path}")

    brief = brief_path.read_text(encoding="utf-8")
    refs = load_references(reference_dir, settings.reference_max_chars)
    payload = f"""
브리프:
{brief}

희망 채널:
{channel or "브리프에서 판단"}

참고 자료:
{format_references(refs)}

{SCHEMA_HINT}
""".strip()

    client = OpenAI(api_key=settings.openai_api_key)
    response = client.responses.create(
        model=settings.openai_model,
        input=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": payload},
        ],
    )
    raw = getattr(response, "output_text", None) or str(response)
    data = _extract_json(raw)
    return ContentPlan.model_validate(data)


def run_with_remote_canva_mcp(prompt: str, settings: Settings) -> str:
    """Call a remote Canva MCP server through the OpenAI Responses API.

    This is optional. For most local Codex workflows, use `.codex/config.toml` instead.
    """

    if not settings.openai_api_key:
        raise RuntimeError("OPENAI_API_KEY가 설정되어 있지 않습니다.")
    if not settings.canva_mcp_server_url:
        raise RuntimeError("CANVA_MCP_SERVER_URL이 설정되어 있지 않습니다.")

    tool: dict[str, Any] = {
        "type": "mcp",
        "server_label": "canva",
        "server_url": settings.canva_mcp_server_url,
    }
    if settings.canva_mcp_bearer_token:
        tool["authorization"] = f"Bearer {settings.canva_mcp_bearer_token}"

    client = OpenAI(api_key=settings.openai_api_key)
    response = client.responses.create(
        model=settings.openai_model,
        tools=[tool],
        input=prompt,
    )
    return getattr(response, "output_text", None) or str(response)


def _extract_json(text: str) -> dict[str, Any]:
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", text, flags=re.DOTALL)
        if not match:
            raise
        return json.loads(match.group(0))
