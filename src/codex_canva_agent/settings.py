from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv


@dataclass(frozen=True)
class Settings:
    openai_api_key: str | None
    openai_model: str
    canva_access_token: str | None
    canva_api_base_url: str
    canva_mcp_server_url: str | None
    canva_mcp_bearer_token: str | None
    reference_max_chars: int
    output_dir: Path


def load_settings(env_file: str | Path | None = None) -> Settings:
    if env_file:
        load_dotenv(env_file)
    else:
        load_dotenv()

    return Settings(
        openai_api_key=os.getenv("OPENAI_API_KEY") or None,
        openai_model=os.getenv("OPENAI_MODEL", "gpt-5.5"),
        canva_access_token=os.getenv("CANVA_ACCESS_TOKEN") or None,
        canva_api_base_url=os.getenv("CANVA_API_BASE_URL", "https://api.canva.com/rest/v1"),
        canva_mcp_server_url=os.getenv("CANVA_MCP_SERVER_URL") or None,
        canva_mcp_bearer_token=os.getenv("CANVA_MCP_BEARER_TOKEN") or None,
        reference_max_chars=int(os.getenv("REFERENCE_MAX_CHARS", "24000")),
        output_dir=Path(os.getenv("OUTPUT_DIR", "output")),
    )
