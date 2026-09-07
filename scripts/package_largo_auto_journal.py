#!/usr/bin/env python3
"""Create the user's reproducible project archive from verified Actions outputs."""
from __future__ import annotations
import argparse
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import shutil
import zipfile


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--project", type=Path, default=Path("/home/oai/my-project/files/2026-09-07_largo_auto_journal"))
    parser.add_argument("--export", type=Path, default=Path("/mnt/data/largo_auto_journal"))
    args = parser.parse_args()
    root = args.project
    for name in ("input", "work", "source", "output", "qc"):
        (root / name).mkdir(parents=True, exist_ok=True)

    def copy(src, dest):
        src, dest = Path(src), Path(dest)
        if not src.exists():
            return
        dest.parent.mkdir(parents=True, exist_ok=True)
        if src.is_dir():
            shutil.copytree(src, dest, dirs_exist_ok=True, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
        else:
            shutil.copy2(src, dest)

    copy("manual-work/source-state-before-journal.json", root / "input/source-state.json")
    copy("manual-work/dashboard-before-journal.html", root / "input/previous-dashboard.html")
    copy("manual-source", root / "input/unchanged-engine")
    copy("manual-raw", root / "input/raw")
    copy("manual-public/manual-1515", root / "output/manual-1515")
    for path in ("scripts/largo_auto_journal.py", "scripts/validate_largo_auto_journal_browser.py", "scripts/package_largo_auto_journal.py", "tests/test_largo_auto_journal.py", "research/largo-manual-1515/auto-dashboard.html", "research/largo-manual-1515/README.md", ".github/workflows/largo-manual-1515.yml"):
        copy(path, root / "source" / path)
    copy("bundles/largo_manual_1515_v1", root / "source/bundles/largo_manual_1515_v1")
    copy("bundles/largo_close_site_v2", root / "source/bundles/largo_close_site_v2")
    copy("manual-work/unit-tests.txt", root / "qc/original-unit-tests.txt")
    copy("manual-work/auto-journal-tests.txt", root / "qc/auto-journal-tests.txt")
    copy("manual-work/journal-validation.json", root / "qc/journal-validation.json")
    copy("manual-work/browser", root / "qc/browser")
    now = dt.datetime.now(dt.timezone.utc).isoformat()
    context = {"created_at_utc": now, "repository": os.environ.get("GITHUB_REPOSITORY"),
               "commit": os.environ.get("GITHUB_SHA"), "ref": os.environ.get("GITHUB_REF"),
               "run_id": os.environ.get("GITHUB_RUN_ID"), "event": os.environ.get("GITHUB_EVENT_NAME"),
               "execution_environment": "GitHub Actions", "orders_enabled": False,
               "live_market_sequence_tested": False}
    (root / "qc/execution-context.json").write_text(json.dumps(context, ensure_ascii=False, indent=2), encoding="utf-8")
    copy("research/largo-manual-1515/README.md", root / "output/사용안내.md")
    readme = f"""# LARGO 자동 모의일지 프로젝트

생성 시각(UTC): {now}

## 목적
15:15에 고정된 종목을 당일 확정 종가에 매수하고 다음 시장 거래일 시가에 전량 매도했다고 가정하는 자동일지입니다. 직접 입력·계좌 조회·실제 주문을 하지 않습니다.

## 입력
input/source-state.json은 기존 엔진의 원본 상태입니다. 원본 엔진과 이전 화면은 input에 별도로 보존했습니다. 입력 파일은 수정하지 않습니다. 사용자 브라우저에 저장된 개인 기록은 가져오거나 전송하지 않았습니다.

## 작업
기존 수집·선정·가격 보충 엔진은 유지했습니다. source/scripts/largo_auto_journal.py가 고정된 상태를 읽어 자동일지와 요약을 생성합니다. UI에서는 수동 체결 입력폼을 제거했습니다. 재실행 시 날짜별 일지가 중복되지 않습니다.

## 결과
output/manual-1515/index.html은 실행 시점의 독립 보관 화면입니다. journal.csv와 journal.json에 자동일지가 있습니다. state.json은 원래 선정 기록과 자동일지를 함께 담습니다. 계속 갱신되는 대시보드는 공개 manual-1515 경로에 있습니다. 신호가 아직 없으면 일지와 성과가 비어 있는 것이 정상입니다.

## 검수
qc의 단위검사·상태 호환성 검사·브라우저 검사 결과를 확인하세요. 브라우저 검사는 가상 자료를 사용하는 실제 Chromium 검사입니다. 캡처 파일에 DEMO 성격을 명시했습니다. 실제 장중 정시 수집부터 다음 거래일 가격 보충까지를 검증한 자료는 아닙니다.

## 재현
프로젝트 루트에서 다음 명령을 실행합니다.

```bash
PYTHONPATH=source/scripts python -m unittest discover -s source/tests -p test_largo_auto_journal.py -v
python source/scripts/largo_auto_journal.py --state input/source-state.json --out work/reproduced --template source/research/largo-manual-1515/auto-dashboard.html
```

input/work/source/output/qc로 구분했습니다. manifest.json에는 파일 경로·크기·수정 시각·포장 시각과 SHA-256이 있습니다. manifest 자체의 해시는 자기 참조를 피하려고 비워 뒀습니다. /home/oai는 임시 작업공간이며 영구 보관을 뜻하지 않습니다. 소스·완성본을 /mnt/data/largo_auto_journal에 복사하고 프로젝트 ZIP도 생성했습니다.
"""
    (root / "README.md").write_text(readme, encoding="utf-8")
    files = []
    for path in sorted(root.rglob("*")):
        if path.is_file() and path.name != "manifest.json":
            files.append({"path": path.relative_to(root).as_posix(), "bytes": path.stat().st_size,
                          "sha256": hashlib.sha256(path.read_bytes()).hexdigest(), "packaged_at_utc": now,
                          "modified_at_utc": dt.datetime.fromtimestamp(path.stat().st_mtime, dt.timezone.utc).isoformat()})
    files.append({"path": "manifest.json", "sha256": None, "packaged_at_utc": now, "note": "self hash excluded"})
    (root / "manifest.json").write_text(json.dumps({"created_at_utc": now, "files": files}, ensure_ascii=False, indent=2), encoding="utf-8")
    args.export.mkdir(parents=True, exist_ok=True)
    shutil.copytree(root, args.export, dirs_exist_ok=True)
    archive = args.export.parent / "largo_auto_journal_project.zip"
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as z:
        for path in sorted(root.rglob("*")):
            if path.is_file():
                z.write(path, Path("largo_auto_journal") / path.relative_to(root))
    with zipfile.ZipFile(archive) as z:
        assert z.testzip() is None
    print(json.dumps({"project": str(root), "export": str(args.export), "zip": str(archive), "files": len(files), "zip_sha256": hashlib.sha256(archive.read_bytes()).hexdigest()}, ensure_ascii=False))


if __name__ == "__main__":
    main()
