from __future__ import annotations

import argparse
import base64
import csv
import html
import json
import shutil
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any


GROUP_TO_SLUG = {
    "시초가": "opening",
    "종가": "closing",
    "종목선정": "selection",
    "호가창": "orderbook",
    "손절": "stop",
    "진입": "entry",
    "청산": "exit",
    "물량소화": "absorption",
    "수급": "flow",
    "차트": "chart",
    "재료": "catalyst",
    "상한가": "upper_limit",
    "종목제외": "exclusion",
    "평가발언": "evaluation",
}


def dump(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, default=str), encoding="utf-8")


def read_csv(path: Path) -> list[dict[str, str]]:
    if not path.exists() or path.stat().st_size == 0:
        return []
    with path.open(encoding="utf-8-sig", newline="") as file:
        return list(csv.DictReader(file))


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        path.write_text("", encoding="utf-8-sig")
        return
    fields: list[str] = []
    for row in rows:
        for key in row:
            if key not in fields:
                fields.append(key)
    with path.open("w", encoding="utf-8-sig", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def find_shard_roots(root: Path) -> list[Path]:
    found: list[Path] = []
    for summary in root.rglob("shard_summary.json"):
        found.append(summary.parent)
    return sorted(set(found))


def copy_frame(source: Path, target_root: Path, slug: str, video_id: str, index: int) -> str | None:
    if not source.exists() or source.stat().st_size < 500:
        return None
    directory = target_root / "representative_frames" / slug
    directory.mkdir(parents=True, exist_ok=True)
    destination = directory / f"{video_id}_{index:03d}{source.suffix.lower()}"
    shutil.copy2(source, destination)
    return destination.relative_to(target_root).as_posix()


def image_data_uri(path: Path) -> str:
    mime = "image/jpeg" if path.suffix.lower() in {".jpg", ".jpeg"} else "image/png"
    return f"data:{mime};base64," + base64.b64encode(path.read_bytes()).decode("ascii")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()

    input_root = Path(args.input)
    output_root = Path(args.output)
    output_root.mkdir(parents=True, exist_ok=True)
    shard_roots = find_shard_roots(input_root)

    video_rows: list[dict[str, Any]] = []
    blocked_rows: list[dict[str, Any]] = []
    evidence_rows: list[dict[str, Any]] = []
    transcript_rows: list[dict[str, Any]] = []
    category_counts: Counter[str] = Counter()
    group_counts: Counter[str] = Counter()
    completed_duration = 0.0

    for shard_root in shard_roots:
        shard_summary = json.loads((shard_root / "shard_summary.json").read_text(encoding="utf-8"))
        shard = shard_summary.get("shard")
        for status in shard_summary.get("statuses", []):
            summary = status.get("summary") or {}
            file_probe = summary.get("file_probe") or {}
            frame_summary = summary.get("frame_summary") or {}
            row = {
                "shard": shard,
                "video_id": status.get("video_id"),
                "state": status.get("state"),
                "title": status.get("title") or summary.get("title") or "",
                "kind": status.get("kind") or summary.get("inventory_kind") or "",
                "categories": "|".join(status.get("categories") or summary.get("inventory_categories") or []),
                "analysis_level": status.get("analysis_level") or summary.get("analysis_level"),
                "duration_seconds": file_probe.get("duration"),
                "resolution": f"{file_probe.get('width','')}x{file_probe.get('height','')}",
                "strategy_events": summary.get("strategy_events"),
                "sparse_frames": frame_summary.get("sparse_frames"),
                "orderbook_candidates": frame_summary.get("orderbook_candidate_frames"),
                "dense_windows": frame_summary.get("dense_windows"),
                "elapsed_seconds": status.get("elapsed_seconds"),
                "error": status.get("error"),
            }
            video_rows.append(row)
            if status.get("state") == "completed":
                completed_duration += float(file_probe.get("duration") or 0)
                category_counts.update(status.get("categories") or summary.get("inventory_categories") or [])
            else:
                blocked_rows.append(row)

        for video_dir in sorted(path for path in shard_root.iterdir() if path.is_dir()):
            status_path = video_dir / "runner_status.json"
            if not status_path.exists():
                continue
            status = json.loads(status_path.read_text(encoding="utf-8"))
            if status.get("state") != "completed":
                continue
            summary = status.get("summary") or {}
            categories = status.get("categories") or summary.get("inventory_categories") or []
            events = read_csv(video_dir / "strategy_events.csv")
            for event_index, event in enumerate(events, 1):
                groups = [group for group in (event.get("groups") or "").split("|") if group]
                group_counts.update(groups)
                frame_rel = event.get("frame_file") or ""
                frame_source = video_dir / frame_rel if frame_rel else Path("/__missing__")
                evidence_rows.append({
                    "shard": shard,
                    "video_id": status.get("video_id"),
                    "title": status.get("title") or summary.get("title") or "",
                    "kind": status.get("kind") or summary.get("inventory_kind") or "",
                    "inventory_categories": "|".join(categories),
                    "analysis_level": status.get("analysis_level"),
                    "start": event.get("start"),
                    "end": event.get("end"),
                    "time_label": event.get("time_label"),
                    "groups": event.get("groups"),
                    "keywords": event.get("keywords"),
                    "priority": event.get("priority"),
                    "text": event.get("text"),
                    "frame_source": frame_source.as_posix() if frame_source.exists() else "",
                    "video_directory": video_dir.as_posix(),
                    "event_index": event_index,
                })
            for segment in read_csv(video_dir / "transcript.csv"):
                transcript_rows.append({
                    "shard": shard,
                    "video_id": status.get("video_id"),
                    "title": status.get("title") or summary.get("title") or "",
                    "kind": status.get("kind") or summary.get("inventory_kind") or "",
                    "inventory_categories": "|".join(categories),
                    **segment,
                })

    evidence_rows.sort(key=lambda row: (str(row.get("video_id")), float(row.get("start") or 0)))
    transcript_rows.sort(key=lambda row: (str(row.get("video_id")), float(row.get("start") or 0)))

    representative_records: list[dict[str, Any]] = []
    by_group: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in evidence_rows:
        for group in (row.get("groups") or "").split("|"):
            if group:
                by_group[group].append(row)
    for group, slug in GROUP_TO_SLUG.items():
        selected: list[dict[str, Any]] = []
        seen_videos: set[str] = set()
        candidates = sorted(by_group.get(group, []), key=lambda row: (-int(float(row.get("priority") or 0)), str(row.get("video_id")), float(row.get("start") or 0)))
        for row in candidates:
            video_id = str(row.get("video_id") or "")
            frame = Path(str(row.get("frame_source") or ""))
            if video_id in seen_videos or not frame.exists():
                continue
            copied = copy_frame(frame, output_root, slug, video_id, len(selected) + 1)
            if not copied:
                continue
            selected.append({**row, "representative_frame": copied})
            seen_videos.add(video_id)
            if len(selected) >= 16:
                break
        representative_records.extend(selected)
        write_csv(output_root / f"evidence_{slug}.csv", by_group.get(group, []))

    write_csv(output_root / "fullscan_status.csv", video_rows)
    write_csv(output_root / "blocked_videos.csv", blocked_rows)
    write_csv(output_root / "evidence_corpus.csv", evidence_rows)
    write_csv(output_root / "transcript_corpus.csv", transcript_rows)
    write_csv(output_root / "representative_frames.csv", representative_records)

    completed = [row for row in video_rows if row.get("state") == "completed"]
    blocked = [row for row in video_rows if row.get("state") != "completed"]
    report = {
        "shards_found": len(shard_roots),
        "requested_new_videos": len(video_rows),
        "completed_new_videos": len(completed),
        "blocked_new_videos": len(blocked),
        "existing_verified_videos": 4,
        "channel_total_videos": 451,
        "total_verified_after_scan": len(completed) + 4,
        "completed_duration_seconds": round(completed_duration, 3),
        "category_completed_counts": dict(category_counts),
        "strategy_group_counts": dict(group_counts),
        "evidence_rows": len(evidence_rows),
        "transcript_segments": len(transcript_rows),
        "blocked_videos": blocked_rows,
    }
    dump(output_root / "fullscan_status.json", report)

    cards = "".join(
        f"<div class='card'><span>{html.escape(label)}</span><b>{value}</b></div>"
        for label, value in [
            ("채널 전체", 451),
            ("기존 원본 검증", 4),
            ("신규 완료", len(completed)),
            ("막힘·실패", len(blocked)),
            ("전략 발언", len(evidence_rows)),
            ("전사 구간", len(transcript_rows)),
        ]
    )
    category_html = "".join(
        f"<tr><td>{html.escape(category)}</td><td>{count}</td></tr>"
        for category, count in sorted(category_counts.items(), key=lambda item: (-item[1], item[0]))
    )
    group_html = "".join(
        f"<tr><td>{html.escape(group)}</td><td>{count}</td></tr>"
        for group, count in sorted(group_counts.items(), key=lambda item: (-item[1], item[0]))
    )
    blocked_html = "".join(
        f"<tr><td>{html.escape(str(row.get('video_id') or ''))}</td><td>{html.escape(str(row.get('title') or ''))}</td><td>{html.escape(str(row.get('error') or ''))}</td></tr>"
        for row in blocked_rows
    )
    frames_html = ""
    for row in representative_records[:96]:
        path = output_root / str(row["representative_frame"])
        if not path.exists():
            continue
        frames_html += (
            "<figure><img src='" + image_data_uri(path) + "'><figcaption>"
            + html.escape(f"{row.get('groups')} · {row.get('video_id')} · {row.get('time_label')} · {row.get('text')}")
            + "</figcaption></figure>"
        )
    css = """body{margin:0;background:#eef3f8;color:#142033;font-family:Arial,'Malgun Gothic',sans-serif}main{max-width:1500px;margin:22px auto;background:#fff;border:1px solid #d7e0ea;border-radius:16px;padding:28px}h1{margin:0}.sub{color:#64748b}.cards{display:grid;grid-template-columns:repeat(6,1fr);gap:10px;margin:20px 0}.card{padding:14px;border:1px solid #dbe4ed;border-radius:12px;background:#f8fafc}.card span{display:block;color:#64748b;font-size:12px}.card b{font-size:27px;color:#1463d6}section{margin-top:28px}table{border-collapse:collapse;width:100%;font-size:12px}th,td{border:1px solid #dfe6ee;padding:7px;text-align:left;vertical-align:top}th{background:#f1f5f9}.twocol{display:grid;grid-template-columns:1fr 1fr;gap:18px}.gallery{display:grid;grid-template-columns:repeat(3,1fr);gap:12px}.gallery figure{margin:0;border:1px solid #dbe4ed;border-radius:10px;overflow:hidden;background:#fff}.gallery img{width:100%;display:block}.gallery figcaption{padding:8px;font-size:10px;line-height:1.45}.scroll{max-height:520px;overflow:auto}@media(max-width:900px){main{margin:0;border-radius:0;padding:14px}.cards{grid-template-columns:1fr 1fr}.twocol,.gallery{grid-template-columns:1fr}}"""
    document = f"""<!doctype html><html lang='ko'><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'><title>라르고TV OpenBot 전수 분석 현황</title><style>{css}</style></head><body><main><h1>라르고TV OpenBot 전수 분석 현황</h1><p class='sub'>공개 원본이 열린 영상은 전체 다운로드·음성 전사·전 구간 프레임·발언 주변 고밀도 프레임 분석을 완료했습니다. 막힌 영상은 별도 목록으로 분리했습니다.</p><div class='cards'>{cards}</div><div class='twocol'><section><h2>제목 분류별 완료 영상</h2><table><tr><th>분류</th><th>완료</th></tr>{category_html}</table></section><section><h2>실제 전사에서 검출된 전략 발언</h2><table><tr><th>전략</th><th>발언 수</th></tr>{group_html}</table></section></div><section><h2>대표 원본 프레임</h2><div class='gallery'>{frames_html}</div></section><section><h2>막힌 영상</h2><div class='scroll'><table><tr><th>영상 ID</th><th>제목</th><th>사유</th></tr>{blocked_html}</table></div></section></main></body></html>"""
    (output_root / "fullscan_status.html").write_text(document, encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
