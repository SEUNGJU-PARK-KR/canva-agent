from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console
from rich.table import Table

from .canva_connect import CanvaConnectClient, wait_for_job
from .openai_content import build_content_plan, run_with_remote_canva_mcp
from .settings import load_settings
from .utils import ensure_parent, read_json, write_json

app = typer.Typer(help="Codex-first content and Canva automation CLI")
canva_app = typer.Typer(help="Canva Connect API commands")
app.add_typer(canva_app, name="canva")
console = Console()


def _canva_client() -> CanvaConnectClient:
    settings = load_settings()
    if not settings.canva_access_token:
        raise typer.BadParameter("CANVA_ACCESS_TOKEN이 설정되어 있지 않습니다.")
    return CanvaConnectClient(
        access_token=settings.canva_access_token,
        base_url=settings.canva_api_base_url,
    )


@app.command()
def doctor() -> None:
    """Check environment variables and local project setup."""
    settings = load_settings()
    table = Table(title="codex-canva 환경 점검")
    table.add_column("항목")
    table.add_column("상태")
    table.add_row("OPENAI_API_KEY", "설정됨" if settings.openai_api_key else "미설정")
    table.add_row("OPENAI_MODEL", settings.openai_model)
    table.add_row("CANVA_ACCESS_TOKEN", "설정됨" if settings.canva_access_token else "미설정")
    table.add_row("CANVA_API_BASE_URL", settings.canva_api_base_url)
    table.add_row("CANVA_MCP_SERVER_URL", settings.canva_mcp_server_url or "미설정")
    table.add_row("OUTPUT_DIR", str(settings.output_dir))
    console.print(table)


@app.command()
def plan(
    brief: Annotated[Path, typer.Argument(help="Path to a Markdown or text brief")],
    references: Annotated[Path, typer.Option(help="Reference material directory")] = Path("references"),
    channel: Annotated[str | None, typer.Option(help="Target channel, such as linkedin-carousel")] = None,
    out: Annotated[Path, typer.Option(help="JSON output path")] = Path("output/content-plan.json"),
    markdown: Annotated[Path | None, typer.Option(help="Optional Markdown output path")] = None,
) -> None:
    """Create a structured content plan with OpenAI."""
    settings = load_settings()
    content_plan = build_content_plan(brief, references, settings, channel=channel)
    write_json(out, content_plan.model_dump())
    console.print(f"JSON 저장 완료: {out}")
    if markdown:
        ensure_parent(markdown)
        markdown.write_text(content_plan.to_markdown(), encoding="utf-8")
        console.print(f"Markdown 저장 완료: {markdown}")


@app.command("mcp-run")
def mcp_run(prompt: Annotated[str, typer.Argument(help="Prompt for remote Canva MCP")]) -> None:
    """Run a prompt against a remote Canva MCP server through OpenAI Responses API."""
    settings = load_settings()
    output = run_with_remote_canva_mcp(prompt, settings)
    console.print(output)


@canva_app.command("upload-url")
def upload_url(
    name: Annotated[str, typer.Option(help="Name for the Canva asset")],
    url: Annotated[str, typer.Option(help="Publicly accessible image or video URL")],
    tag: Annotated[list[str] | None, typer.Option(help="Optional Canva asset tags")] = None,
    out: Annotated[Path | None, typer.Option(help="Optional JSON output file")] = None,
) -> None:
    client = _canva_client()
    result = client.create_url_asset_upload_job(name=name, url=url, tags=tag)
    _print_or_write(result, out)


@canva_app.command("export")
def export_design(
    design_id: Annotated[str, typer.Option(help="Canva design ID")],
    format: Annotated[str, typer.Option(help="pdf, png, jpg, pptx, mp4, etc.")] = "pdf",
    page: Annotated[list[int] | None, typer.Option(help="Optional page numbers")] = None,
    out: Annotated[Path | None, typer.Option(help="Optional JSON output file")] = None,
) -> None:
    client = _canva_client()
    result = client.create_export_job(design_id=design_id, export_format=format, pages=page)
    _print_or_write(result, out)


@canva_app.command("poll-export")
def poll_export(
    export_id: Annotated[str, typer.Option(help="Canva export job ID")],
    timeout: Annotated[int, typer.Option(help="Timeout seconds")] = 120,
    out: Annotated[Path | None, typer.Option(help="Optional JSON output file")] = None,
) -> None:
    client = _canva_client()
    result = wait_for_job(client.get_export_job, export_id, timeout_sec=timeout)
    _print_or_write(result, out)


@canva_app.command("autofill")
def autofill(
    brand_template_id: Annotated[str, typer.Option(help="Canva brand template ID")],
    data: Annotated[Path, typer.Option(help="JSON file with Canva autofill data")],
    out: Annotated[Path | None, typer.Option(help="Optional JSON output file")] = None,
) -> None:
    client = _canva_client()
    result = client.create_autofill_job_from_brand_template(
        brand_template_id=brand_template_id,
        data=read_json(data),
    )
    _print_or_write(result, out)


def _print_or_write(payload: dict, out: Path | None) -> None:
    if out:
        write_json(out, payload)
        console.print(f"저장 완료: {out}")
        return
    console.print_json(json.dumps(payload, ensure_ascii=False))


if __name__ == "__main__":
    app()
