from __future__ import annotations

import argparse
import base64
import csv
import gzip
import json
import os
import shutil
import subprocess
import time
from collections import Counter
from pathlib import Path
from typing import Any

from PIL import Image


def dump(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, default=str), encoding="utf-8")


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
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


def read_inventory(path: Path) -> dict[str, Any]:
    encoded = path.read_text(encoding="utf-8").strip()
    return json.loads(gzip.decompress(base64.b64decode(encoded)).decode("utf-8"))


def prune_ranked(directory: Path, metrics_csv: Path, score_key: str, limit: int) -> None:
    if not directory.exists() or not metrics_csv.exists():
        return
    with metrics_csv.open(encoding="utf-8-sig", newline="") as file:
        rows = list(csv.DictReader(file))
    selected = {
        row.get("frame", "")
        for row in sorted(rows, key=lambda row: float(row.get(score_key) or 0), reverse=True)[:limit]
    }
    for path in directory.glob("*.jpg"):
        if path.name not in selected:
            path.unlink(missing_ok=True)


def compress_images(root: Path) -> None:
    for path in root.rglob("*.jpg"):
        try:
            image = Image.open(path).convert("RGB")
            image.save(path, "JPEG", quality=72, optimize=True)
        except Exception:
            pass


def trim_output(root: Path) -> None:
    (root / "transcript.json").unlink(missing_ok=True)
    (root / "strategy_events.json").unlink(missing_ok=True)
    metrics = root / "sparse_frame_metrics.csv"
    prune_ranked(root / "selected_scene_frames", metrics, "scene_score", 10)
    prune_ranked(root / "selected_orderbook_frames", metrics, "orderbook_score", 12)

    event_frames = sorted((root / "event_frames").glob("*.jpg")) if (root / "event_frames").exists() else []
    if len(event_frames) > 24:
        keep_indices = {round(i * (len(event_frames) - 1) / 23) for i in range(24)}
        for index, path in enumerate(event_frames):
            if index not in keep_indices:
                path.unlink(missing_ok=True)

    dense = root / "dense_windows"
    if dense.exists():
        for window in dense.iterdir():
            if not window.is_dir():
                continue
            for image in window.glob("*.jpg"):
                if image.name != "contact_sheet.jpg":
                    image.unlink(missing_ok=True)
    compress_images(root)


def patch_summary(video_dir: Path, item: dict[str, Any]) -> dict[str, Any] | None:
    path = video_dir / "summary.json"
    if not path.exists():
        return None
    data = json.loads(path.read_text(encoding="utf-8"))
    data["inventory_title"] = item.get("title", "")
    data["inventory_kind"] = item.get("kind", "")
    data["inventory_categories"] = item.get("categories", [])
    data["analysis_level"] = item.get("analysis_level", "standard")
    dump(path, data)
    return data


def run_video(analyzer: Path, item: dict[str, Any], output_root: Path, model_root: Path) -> dict[str, Any]:
    video_id = item["video_id"]
    video_dir = output_root / video_id
    video_dir.mkdir(parents=True, exist_ok=True)
    started = time.time()
    env = os.environ.copy()
    env.update({
        "VIDEO_ID": video_id,
        "OUT": str(video_dir),
        "WHISPER_MODEL": "small",
        "WHISPER_MODEL_ROOT": str(model_root),
        "SPARSE_INTERVAL": "10" if item.get("analysis_level") == "detailed" else "20",
        "DENSE_WINDOWS": "3" if item.get("analysis_level") == "detailed" else "1",
        "EVENT_FRAME_LIMIT": "28" if item.get("analysis_level") == "detailed" else "12",
    })
    log_file = video_dir / "runner.log"
    with log_file.open("w", encoding="utf-8") as file:
        proc = subprocess.run(["python3", str(analyzer)], env=env, text=True, stdout=file, stderr=subprocess.STDOUT)
    summary = patch_summary(video_dir, item)
    trim_output(video_dir)
    if proc.returncode == 0 and summary:
        state = "completed"
        error = None
    else:
        state = "blocked"
        failure_path = video_dir / "failure.json"
        if failure_path.exists():
            try:
                error = json.loads(failure_path.read_text(encoding="utf-8")).get("error")
            except Exception:
                error = "failure.json 파싱 실패"
        else:
            error = f"analyzer exit {proc.returncode}"
    status = {
        "video_id": video_id,
        "title": item.get("title", ""),
        "kind": item.get("kind", ""),
        "categories": item.get("categories", []),
        "analysis_level": item.get("analysis_level", "standard"),
        "state": state,
        "error": error,
        "elapsed_seconds": round(time.time() - started, 2),
        "summary": summary,
    }
    dump(video_dir / "runner_status.json", status)
    return status


def update_shard_summary(output_root: Path, shard: int, statuses: list[dict[str, Any]], started: float) -> None:
    completed = [row for row in statuses if row["state"] == "completed"]
    blocked = [row for row in statuses if row["state"] != "completed"]
    categories: Counter[str] = Counter()
    duration = 0.0
    for row in completed:
        categories.update(row.get("categories") or [])
        duration += float((row.get("summary") or {}).get("file_probe", {}).get("duration") or (row.get("summary") or {}).get("duration_seconds") or 0)
    report = {
        "shard": shard,
        "requested": len(statuses),
        "completed": len(completed),
        "blocked": len(blocked),
        "completed_ids": [row["video_id"] for row in completed],
        "blocked_videos": [{"video_id": row["video_id"], "title": row["title"], "error": row.get("error")} for row in blocked],
        "category_completed_counts": dict(categories),
        "completed_duration_seconds": round(duration, 3),
        "elapsed_seconds": round(time.time() - started, 2),
        "statuses": statuses,
    }
    dump(output_root / "shard_summary.json", report)
    write_csv(output_root / "shard_summary.csv", [{
        "video_id": row["video_id"], "state": row["state"], "title": row["title"],
        "categories": "|".join(row.get("categories") or []),
        "duration": ((row.get("summary") or {}).get("file_probe") or {}).get("duration"),
        "strategy_events": (row.get("summary") or {}).get("strategy_events"),
        "sparse_frames": ((row.get("summary") or {}).get("frame_summary") or {}).get("sparse_frames"),
        "orderbook_candidates": ((row.get("summary") or {}).get("frame_summary") or {}).get("orderbook_candidate_frames"),
        "error": row.get("error"),
    } for row in statuses])


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--inventory", required=True)
    parser.add_argument("--shard", required=True, type=int)
    parser.add_argument("--shards", default=16, type=int)
    parser.add_argument("--analyzer", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--model-root", default="/opt/whisper-models")
    args = parser.parse_args()

    started = time.time()
    inventory = read_inventory(Path(args.inventory))
    items = [item for item in inventory["items"] if int(item.get("shard", -1)) == args.shard and not item.get("already_completed")]
    output_root = Path(args.output)
    output_root.mkdir(parents=True, exist_ok=True)
    dump(output_root / "shard_input.json", {"shard": args.shard, "items": items})

    statuses: list[dict[str, Any]] = []
    for index, item in enumerate(items, 1):
        print(f"[{args.shard:02d}] {index}/{len(items)} {item['video_id']} {item.get('title','')}", flush=True)
        statuses.append(run_video(Path(args.analyzer), item, output_root, Path(args.model_root)))
        update_shard_summary(output_root, args.shard, statuses, started)
    update_shard_summary(output_root, args.shard, statuses, started)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
