# Migration notes from 10x-Content-Expert style to Codex style

This project keeps the useful product direction and changes the execution model.

## What changed

Claude Code skill folders are replaced by a root `AGENTS.md` and typed CLI commands.

Anthropic-specific tool-calling assumptions are replaced by OpenAI Responses API calls and optional MCP tool configuration.

The Canva section is split into two tracks. Connect API calls live in `src/codex_canva_agent/canva_connect.py`. MCP configuration lives in `.codex/config.toml` and can be extended for a remote design automation server.

Local file editing becomes ordinary repository work. Codex can inspect, modify, and test the project through the workspace.

## What stayed conceptually similar

The project still supports content generation, reference-aware writing, local file outputs, Canva asset upload, presentation and social creative workflows, and export automation.

## What should be reviewed before production

- OAuth flow and token refresh storage.
- Canva integration review requirements.
- Brand template field mapping.
- Prompt-injection handling for imported references.
- Human approval gates for destructive or large-scale Canva actions.
