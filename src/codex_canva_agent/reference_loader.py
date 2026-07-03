from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

TEXT_EXTENSIONS = {
    ".txt",
    ".md",
    ".markdown",
    ".csv",
    ".json",
    ".yaml",
    ".yml",
    ".html",
}


@dataclass(frozen=True)
class ReferenceDocument:
    path: Path
    content: str

    @property
    def label(self) -> str:
        return str(self.path)


def load_references(reference_dir: Path, max_chars: int = 24000) -> list[ReferenceDocument]:
    if not reference_dir.exists():
        return []

    docs: list[ReferenceDocument] = []
    used = 0
    for path in sorted(reference_dir.rglob("*")):
        if not path.is_file() or path.suffix.lower() not in TEXT_EXTENSIONS:
            continue
        remaining = max_chars - used
        if remaining <= 0:
            break
        text = path.read_text(encoding="utf-8", errors="replace").strip()
        if not text:
            continue
        text = text[:remaining]
        used += len(text)
        docs.append(ReferenceDocument(path=path, content=text))
    return docs


def format_references(docs: list[ReferenceDocument]) -> str:
    if not docs:
        return "참고 자료가 없습니다."

    parts = []
    for doc in docs:
        parts.append(f"---\n파일: {doc.label}\n\n{doc.content}")
    return "\n\n".join(parts)
