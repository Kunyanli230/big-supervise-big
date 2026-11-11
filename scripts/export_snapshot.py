#!/usr/bin/env python3
"""Export only trajectories and the 6 required metrics + SHA-256 manifest from the VM."""

import argparse
import datetime
import hashlib
import json
import statistics
import tarfile
from pathlib import Path


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        while chunk := f.read(1024 * 1024):
            h.update(chunk)
    return h.hexdigest()


def parse_arm_metrics(arm_dir: Path) -> dict:
    instance_id = arm_dir.parent.name
    arm = arm_dir.name
    result_path = arm_dir / "result.json"
    events_path = arm_dir / "events.jsonl"
    grade_path = arm_dir / "grading" / "grade.json"

    result = json.loads(result_path.read_text(encoding="utf-8")) if result_path.exists() else {}
    grade = json.loads(grade_path.read_text(encoding="utf-8")) if grade_path.exists() else {}

    policy_in = 0
    policy_out = 0
    selector_in = 0
    selector_out = 0
    candidate_branches = 0
    selector_latencies = []

    if events_path.exists():
        for line in events_path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            ev = json.loads(line)
            if ev.get("event") == "candidate":
                candidate_branches += 1
            elif ev.get("event") == "response":
                role = ev.get("role")
                body = ev.get("body") if isinstance(ev.get("body"), dict) else {}
                usage = body.get("usage") if isinstance(body.get("usage"), dict) else {}
                p_tok = int(usage.get("prompt_tokens") or usage.get("input_tokens") or 0)
                c_tok = int(usage.get("completion_tokens") or usage.get("output_tokens") or 0)
                if role == "policy":
                    policy_in += p_tok
                    policy_out += c_tok
                elif role == "selector":
                    selector_in += p_tok
                    selector_out += c_tok
                    lat = ev.get("latency_seconds")
                    if isinstance(lat, (int, float)) and ev.get("status") == 200:
                        selector_latencies.append(round(float(lat), 4))

    react_steps = int(result.get("steps") or 0)
    elapsed_sec = result.get("elapsed_seconds")
    resolved = grade.get("resolved") if grade_path.exists() else result.get("resolved")

    return {
        "instance_id": instance_id,
        "arm": arm,
        "status": result.get("status"),
        "resolved": resolved,
        "grade_status": grade.get("status", result.get("grade_status", "not_evaluated")),
        "react_steps": react_steps,
        "total_candidate_branches": candidate_branches,
        "token_usage": {
            "policy_input_tokens": policy_in,
            "policy_output_tokens": policy_out,
            "selector_input_tokens": selector_in,
            "selector_output_tokens": selector_out,
            "total_input_tokens": policy_in + selector_in,
            "total_output_tokens": policy_out + selector_out,
        },
        "step_d3_choice_latency_seconds": {
            "count": len(selector_latencies),
            "mean": round(statistics.mean(selector_latencies), 4) if selector_latencies else None,
            "median": round(statistics.median(selector_latencies), 4) if selector_latencies else None,
            "max": round(max(selector_latencies), 4) if selector_latencies else None,
            "per_step": selector_latencies,
        },
        "end_to_end_elapsed_seconds": round(float(elapsed_sec), 3) if isinstance(elapsed_sec, (int, float)) else None,
        "cost_usd": (result.get("usage") or {}).get("cost_usd"),
    }


def aggregate_metrics(records: list[dict]) -> dict:
    by_arm = {}
    for r in records:
        by_arm.setdefault(r["arm"], []).append(r)

    summary = {}
    for arm, items in sorted(by_arm.items()):
        evaluated = [x for x in items if x["resolved"] is not None]
        resolved_cnt = sum(1 for x in evaluated if x["resolved"] is True)
        all_lat = [lat for x in items for lat in x["step_d3_choice_latency_seconds"]["per_step"]]
        e2e_times = [x["end_to_end_elapsed_seconds"] for x in items if x["end_to_end_elapsed_seconds"] is not None]
        steps_list = [x["react_steps"] for x in items]
        branches_list = [x["total_candidate_branches"] for x in items]

        p_in = sum(x["token_usage"]["policy_input_tokens"] for x in items)
        p_out = sum(x["token_usage"]["policy_output_tokens"] for x in items)
        s_in = sum(x["token_usage"]["selector_input_tokens"] for x in items)
        s_out = sum(x["token_usage"]["selector_output_tokens"] for x in items)

        summary[arm] = {
            "total_trajectories": len(items),
            "evaluated_count": len(evaluated),
            "resolved_count": resolved_cnt,
            "unresolved_count": len(evaluated) - resolved_cnt,
            "resolve_rate": round(resolved_cnt / len(evaluated), 4) if evaluated else None,
            "token_totals": {
                "policy_input_tokens": p_in,
                "policy_output_tokens": p_out,
                "selector_input_tokens": s_in,
                "selector_output_tokens": s_out,
                "total_input_tokens": p_in + s_in,
                "total_output_tokens": p_out + s_out,
            },
            "react_steps": {
                "total": sum(steps_list),
                "mean_per_trajectory": round(statistics.mean(steps_list), 2) if steps_list else 0,
            },
            "total_candidate_branches": {
                "total": sum(branches_list),
                "mean_per_trajectory": round(statistics.mean(branches_list), 2) if branches_list else 0,
            },
            "single_step_d3_choice_latency_seconds": {
                "total_decisions": len(all_lat),
                "mean": round(statistics.mean(all_lat), 4) if all_lat else None,
                "median": round(statistics.median(all_lat), 4) if all_lat else None,
            },
            "end_to_end_trajectory_elapsed_seconds": {
                "total": round(sum(e2e_times), 2) if e2e_times else 0,
                "mean": round(statistics.mean(e2e_times), 2) if e2e_times else None,
                "median": round(statistics.median(e2e_times), 2) if e2e_times else None,
            },
        }

    a_records = [r for r in records if r["arm"] in ("A_r1", "A_r2", "A_r3")]
    if a_records:
        by_task = {}
        for r in a_records:
            by_task.setdefault(r["instance_id"], {})[r["arm"]] = r
        complete_tasks = [
            iid for iid, arms in sorted(by_task.items())
            if all(k in arms and arms[k]["resolved"] is not None for k in ("A_r1", "A_r2", "A_r3"))
        ]
        resolved_tasks = [
            iid for iid in complete_tasks
            if any(by_task[iid][k]["resolved"] is True for k in ("A_r1", "A_r2", "A_r3"))
        ]
        p_in = sum(r["token_usage"]["policy_input_tokens"] for r in a_records)
        p_out = sum(r["token_usage"]["policy_output_tokens"] for r in a_records)
        steps_list = [r["react_steps"] for r in a_records]
        e2e_times = [r["end_to_end_elapsed_seconds"] for r in a_records if r["end_to_end_elapsed_seconds"] is not None]
        task_e2e = [
            sum(by_task[iid][k]["end_to_end_elapsed_seconds"] or 0 for k in ("A_r1", "A_r2", "A_r3"))
            for iid in complete_tasks
        ]
        summary["A_pass_at_3"] = {
            "total_tasks_started": len(by_task),
            "total_trajectories": len(a_records),
            "evaluated_count": len(complete_tasks),
            "resolved_count": len(resolved_tasks),
            "unresolved_count": len(complete_tasks) - len(resolved_tasks),
            "resolve_rate": round(len(resolved_tasks) / len(complete_tasks), 4) if complete_tasks else None,
            "token_totals": {
                "policy_input_tokens": p_in,
                "policy_output_tokens": p_out,
                "selector_input_tokens": 0,
                "selector_output_tokens": 0,
                "total_input_tokens": p_in,
                "total_output_tokens": p_out,
            },
            "react_steps": {
                "total": sum(steps_list),
                "mean_per_trajectory": round(statistics.mean(steps_list), 2) if steps_list else 0,
                "mean_per_task_3_runs": round(sum(steps_list) / len(by_task), 2) if by_task else 0,
            },
            "total_candidate_branches": {
                "total": len(a_records),
                "mean_per_task": round(len(a_records) / len(by_task), 2) if by_task else 0,
            },
            "single_step_d3_choice_latency_seconds": {
                "total_decisions": 0,
                "mean": None,
                "median": None,
            },
            "end_to_end_trajectory_elapsed_seconds": {
                "total": round(sum(e2e_times), 2) if e2e_times else 0,
                "mean_per_trajectory": round(statistics.mean(e2e_times), 2) if e2e_times else None,
                "mean_per_task_3_runs": round(statistics.mean(task_e2e), 2) if task_e2e else None,
                "median_per_task_3_runs": round(statistics.median(task_e2e), 2) if task_e2e else None,
            },
        }
    return {"by_arm": summary, "trajectories": records}


def build_snapshot(exp_root: Path, out_dir: Path, milestone: str) -> dict:
    exp_root = exp_root.resolve()
    out_dir = out_dir.resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    snap_id = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%SZ")

    runs_root = exp_root / "runs"
    records = []
    if runs_root.exists():
        for res_file in sorted(runs_root.rglob("result.json")):
            records.append(parse_arm_metrics(res_file.parent))

    metrics_data = aggregate_metrics(records)
    metrics_path = exp_root / "metrics_summary.json"
    metrics_path.write_text(json.dumps(metrics_data, ensure_ascii=False, indent=2), encoding="utf-8")

    # Keep only trajectories + sealed patches + grading verdicts + anomaly/recovery receipts + metrics_summary.json
    allowed_names = {
        "trajectory.json", "events.jsonl", "result.json", "manifest.json",
        "model.patch", "patch-seal.json", "alias.json", "grade.json",
        "reward.txt", "output.json", "metrics_summary.json", "summary.json",
        "anomaly-seal.json", "recovery-check.json"
    }

    files_manifest = []
    integrity_errors = []

    for p in sorted(exp_root.rglob("*")):
        if not p.is_file():
            continue
        if p.name not in allowed_names:
            continue
        rel_parts = p.relative_to(exp_root).parts
        if len(rel_parts) >= 3 and rel_parts[0] == "runs":
            arm_dir = exp_root / rel_parts[0] / rel_parts[1] / rel_parts[2]
            if not (arm_dir / "result.json").exists() and not (arm_dir / "anomaly-seal.json").exists():
                continue
        rel = p.relative_to(exp_root).as_posix()
        files_manifest.append({
            "path": rel,
            "bytes": p.stat().st_size,
            "sha256": sha256_file(p),
        })

    for seal_path in sorted(exp_root.rglob("patch-seal.json")):
        try:
            seal = json.loads(seal_path.read_text(encoding="utf-8"))
            diff_path = seal_path.parent / "model.patch"
            if not diff_path.exists():
                integrity_errors.append(f"Missing model.patch for {seal_path.relative_to(exp_root).as_posix()}")
            elif sha256_file(diff_path) != seal.get("sha256"):
                integrity_errors.append(f"SHA256 mismatch for {diff_path.relative_to(exp_root).as_posix()}")
        except Exception as exc:
            integrity_errors.append(f"Invalid seal {seal_path.relative_to(exp_root).as_posix()}: {exc}")

    manifest_data = {
        "snapshot_id": snap_id,
        "milestone": milestone,
        "created_at_utc": snap_id,
        "file_count": len(files_manifest),
        "metrics_summary": metrics_data["by_arm"],
        "files": files_manifest,
        "integrity_errors": integrity_errors,
    }

    manifest_file = out_dir / f"file-manifest-{snap_id}.json"
    manifest_file.write_text(json.dumps(manifest_data, ensure_ascii=False, indent=2), encoding="utf-8")

    archive_path = out_dir / f"bsb-pro-v2-{snap_id}.tar.gz"
    with tarfile.open(archive_path, "w:gz") as tar:
        tar.add(manifest_file, arcname="snapshot/file-manifest.json")
        for entry in files_manifest:
            src = exp_root / entry["path"]
            tar.add(src, arcname=f"snapshot/{entry['path']}")

    manifest_file.unlink(missing_ok=True)
    archive_sha = sha256_file(archive_path)

    return {
        "id": snap_id,
        "milestone": milestone,
        "archive": str(archive_path),
        "sha256": archive_sha,
        "file_count": len(files_manifest),
        "metrics_summary": metrics_data["by_arm"],
        "integrity_errors": integrity_errors,
    }


def main():
    parser = argparse.ArgumentParser(description="Export verified trajectories and 6 key metrics")
    parser.add_argument("--exp-root", type=Path, default=Path("/home/swebench/big-supervise-big-exp"))
    parser.add_argument("--out-dir", type=Path, default=Path("/home/swebench/snapshots"))
    parser.add_argument("--milestone", type=str, default="manual")
    args = parser.parse_args()
    meta = build_snapshot(args.exp_root, args.out_dir, args.milestone)
    print(json.dumps(meta, ensure_ascii=False))


if __name__ == "__main__":
    main()
