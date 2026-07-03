from pathlib import Path

from codex_canva_agent.reference_loader import format_references, load_references


def test_load_references_reads_text_files(tmp_path: Path) -> None:
    (tmp_path / "a.md").write_text("hello", encoding="utf-8")
    (tmp_path / "b.png").write_bytes(b"not text")

    docs = load_references(tmp_path, max_chars=100)

    assert len(docs) == 1
    assert docs[0].content == "hello"
    assert "hello" in format_references(docs)
