#!/usr/bin/env python3
"""Batch runner for the 28 formal (+2 dev = 30 total) stratified SWE-bench Pro V2 tasks.

Enforces:
1. 28 formal tasks + 2 dev tasks = 30 total tasks (from 11 repositories, 2~3 per repo).
2. Strict exception handling pipeline per task:
   [异常封存 (Seal anomaly)] -> [清理本题资源及模型缓存 (Clean task containers/images/caches)]
   -> [恢复核验 (Doctor & host recovery check)] -> [跳过本题、进入下一题 (Skip to next task)].
3. Harbor 0.22 locked protocol:
   - Agent solving timeout: 50 minutes (3000s)
   - Max ReAct steps: unlimited (max_steps = 0)
   - Single bash command timeout: 120s
   - Verifier grading timeout: 30 minutes (1800s)
4. Automatic verified snapshot export every 10 tasks (task 10, 20, and completion at 28).
"""

import argparse
import datetime
import hashlib
import json
import shutil
import subprocess
import sys
from pathlib import Path


def utc_now() -> str:
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        while chunk := f.read(1024 * 1024):
            h.update(chunk)
    return h.hexdigest()


def run_cli(*args):
    cmd = [sys.executable, "-m", "big_supervise_big", *map(str, args)]
    proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8")
    return proc.returncode, proc.stdout, proc.stderr


def seal_anomaly(arm_dir: Path, instance_id: str, arm: str, stage: str, reason: str, details: str = "") -> dict:
    """Step 1 of exception chain: 异常封存 (Seal all existing artifacts and record anomaly receipt)."""
    arm_dir.mkdir(parents=True, exist_ok=True)
    sealed_files = {}
    for p in sorted(arm_dir.rglob("*")):
        if p.is_file() and p.name != "anomaly-seal.json":
            sealed_files[p.relative_to(arm_dir).as_posix()] = {
                "bytes": p.stat().st_size,
                "sha256": sha256_file(p),
            }
    receipt = {
        "event": "anomaly_sealed",
        "timestamp_utc": utc_now(),
        "instance_id": instance_id,
        "arm": arm,
        "stage": stage,
        "reason": reason,
        "details": details[-2000:] if details else "",
        "sealed_files": sealed_files,
    }
    (arm_dir / "anomaly-seal.json").write_text(
        json.dumps(receipt, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return receipt


def cleanup_task_resources_and_cache(instance_id: str) -> dict:
    """Step 2 of exception chain: 清理本题资源及模型缓存."""
    image = f"ghcr.io/scaleapi/swe-bench_pro-v2:{instance_id}"
    # Kill and remove any container using this task's image or exited containers
    ps_proc = subprocess.run(
        ["docker", "ps", "-a", "--filter", f"ancestor={image}", "-q"],
        capture_output=True, text=True, check=False
    )
    container_ids = [cid.strip() for cid in ps_proc.stdout.splitlines() if cid.strip()]
    for cid in container_ids:
        subprocess.run(["docker", "rm", "-f", cid], capture_output=True, check=False)

    subprocess.run(["docker", "container", "prune", "-f"], capture_output=True, check=False)
    rmi_proc = subprocess.run(["docker", "rmi", "-f", image], capture_output=True, text=True, check=False)
    subprocess.run(["docker", "image", "prune", "-f"], capture_output=True, check=False)

    # Clean temporary model/http/litellm caches in /tmp
    removed_tmp = []
    for tmp_item in Path("/tmp").glob("*"):
        if any(prefix in tmp_item.name for prefix in ("litellm", "httpx", "miniswe", "model.patch", "pip-")):
            try:
                if tmp_item.is_dir():
                    shutil.rmtree(tmp_item, ignore_errors=True)
                else:
                    tmp_item.unlink(missing_ok=True)
                removed_tmp.append(tmp_item.name)
            except Exception:
                pass

    return {
        "event": "resources_and_cache_cleaned",
        "timestamp_utc": utc_now(),
        "instance_id": instance_id,
        "removed_containers": container_ids,
        "image_removed": image,
        "rmi_returncode": rmi_proc.returncode,
        "cleaned_tmp_entries": removed_tmp,
    }


def verify_recovery(cfg_path: Path, arm_dir: Path) -> dict:
    """Step 3 of exception chain: 恢复核验 (Verify Docker, disk space, no stray containers, and API catalog)."""
    rc, out, err = run_cli("doctor", "--config", cfg_path)
    ps_running = subprocess.run(
        ["docker", "ps", "-q"], capture_output=True, text=True, check=False
    ).stdout.strip().splitlines()
    disk = shutil.disk_usage("/")
    free_gb = round(disk.free / (1024 ** 3), 2)

    doctor_data = {}
    try:
        idx = out.find("{")
        doctor_data = json.loads(out[idx:]) if idx != -1 else {}
    except Exception:
        doctor_data = {"raw_stdout": out[-1000:], "raw_stderr": err[-1000:]}

    healthy = (rc == 0) and (len(ps_running) == 0) and (free_gb >= 10.0)
    receipt = {
        "event": "recovery_verified",
        "timestamp_utc": utc_now(),
        "healthy": healthy,
        "doctor_returncode": rc,
        "doctor": doctor_data,
        "stray_running_containers": len(ps_running),
        "disk_free_gb": free_gb,
    }
    arm_dir.mkdir(parents=True, exist_ok=True)
    (arm_dir / "recovery-check.json").write_text(
        json.dumps(receipt, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    if not healthy:
        raise RuntimeError(f"Recovery verification failed: {json.dumps(receipt, ensure_ascii=False)}")
    return receipt


def handle_task_exception_and_skip(
    cfg_path: Path, arm_dir: Path, instance_id: str, arm: str, stage: str, reason: str, details: str = ""
):
    """Execute the full 4-step chain: 异常封存 -> 清理本题资源及模型缓存 -> 恢复核验 -> 跳过本题、进入下一题."""
    seal_receipt = seal_anomaly(arm_dir, instance_id, arm, stage, reason, details)
    clean_receipt = cleanup_task_resources_and_cache(instance_id)
    recovery_receipt = verify_recovery(cfg_path, arm_dir)
    skip_record = {
        "event": "task_skipped_after_anomaly_chain",
        "timestamp_utc": utc_now(),
        "instance_id": instance_id,
        "arm": arm,
        "seal": seal_receipt,
        "cleanup": clean_receipt,
        "recovery": recovery_receipt,
        "action": "skip_to_next_task",
    }
    print(json.dumps(skip_record, ensure_ascii=False), flush=True)


def image_exists_locally(image: str) -> bool:
    proc = subprocess.run(["docker", "image", "inspect", image], capture_output=True, text=True, check=False)
    return proc.returncode == 0


def run_and_grade(cfg_path: Path, checkout: Path, instance_id: str, arm_dir: Path, arm: str, *, clean_image: bool = True):
    if (arm_dir / "anomaly-seal.json").exists():
        print(json.dumps({"event": "already_sealed_anomaly_skip", "instance_id": instance_id, "arm": arm}), flush=True)
        return

    image = f"ghcr.io/scaleapi/swe-bench_pro-v2:{instance_id}"
    try:
        if not (arm_dir / "result.json").exists():
            if arm_dir.exists():
                shutil.rmtree(arm_dir)
            if not image_exists_locally(image):
                pull_proc = subprocess.run(["docker", "pull", image], capture_output=True, text=True, timeout=900)
                if pull_proc.returncode != 0:
                    handle_task_exception_and_skip(
                        cfg_path, arm_dir, instance_id, arm, "image_pull", "docker_pull_failed",
                        (pull_proc.stdout or "") + "\n" + (pull_proc.stderr or "")
                    )
                    return

            rc, out, err = run_cli(
                "run", "--config", cfg_path, "--checkout", checkout,
                "--instance-id", instance_id, "--arm", arm, "--output", arm_dir
            )
            res_path = arm_dir / "result.json"
            status = json.loads(res_path.read_text(encoding="utf-8")).get("status") if res_path.exists() else "missing_result"
            if rc != 0 or not (arm_dir / "patch-seal.json").exists():
                handle_task_exception_and_skip(
                    cfg_path, arm_dir, instance_id, arm, "run", f"run_anomaly:{status}",
                    (out or "") + "\n" + (err or "")
                )
                return

        if (arm_dir / "patch-seal.json").exists() and not (arm_dir / "grading" / "grade.json").exists():
            if not image_exists_locally(image):
                pull_proc = subprocess.run(["docker", "pull", image], capture_output=True, text=True, timeout=900)
                if pull_proc.returncode != 0:
                    handle_task_exception_and_skip(
                        cfg_path, arm_dir, instance_id, arm, "grade_image_pull", "docker_pull_failed",
                        (pull_proc.stdout or "") + "\n" + (pull_proc.stderr or "")
                    )
                    return

            rc, out, err = run_cli("grade", "--checkout", checkout, "--output", arm_dir)
            grade_path = arm_dir / "grading" / "grade.json"
            g_status = json.loads(grade_path.read_text(encoding="utf-8")).get("status") if grade_path.exists() else "missing_grade"
            if rc != 0 or g_status == "eval_error":
                handle_task_exception_and_skip(
                    cfg_path, arm_dir, instance_id, arm, "grade", f"grade_anomaly:{g_status}",
                    (out or "") + "\n" + (err or "")
                )
                return
    except Exception as exc:
        handle_task_exception_and_skip(
            cfg_path, arm_dir, instance_id, arm, "unhandled_exception", type(exc).__name__, str(exc)
        )
        return

    # Normal completion: clean up task image/containers when requested so 240GB SSD stays clean
    if clean_image:
        cleanup_task_resources_and_cache(instance_id)
    else:
        subprocess.run(["docker", "container", "prune", "-f"], capture_output=True, check=False)


def main():
    parser = argparse.ArgumentParser(description="Run 28 formal tasks (+2 dev) in batches of 10 with snapshot export")
    parser.add_argument("--tasks-json", type=Path, default=Path("configs/tasks_30_stratified.json"))
    parser.add_argument("--single-config", type=Path, default=Path("configs/luna.single.json"))
    parser.add_argument("--step-choice-config", type=Path, default=Path("configs/luna.step_choice.json"))
    parser.add_argument("--checkout", type=Path, required=True)
    parser.add_argument("--exp-root", type=Path, default=Path("/home/swebench/big-supervise-big-exp"))
    parser.add_argument("--snap-dir", type=Path, default=Path("/home/swebench/snapshots"))
    parser.add_argument("--phase", choices=["b", "c", "bc", "a_rest", "pass_at_3", "pass_at_1", "all", "dev_b"], default="b",
                        help="b = experimental group B on 28 formal tasks; pass_at_3 = A_r1..A_r3; pass_at_1 = independent C on 28 formal tasks")
    parser.add_argument("--batch-size", type=int, default=10)
    args = parser.parse_args()

    spec = json.loads(args.tasks_json.read_text(encoding="utf-8"))
    tasks = spec["dev_tasks"] if args.phase == "dev_b" else spec["tasks"]
    runs_root = args.exp_root / ("dev_runs" if args.phase == "dev_b" else "runs")
    runs_root.mkdir(parents=True, exist_ok=True)
    sync_triggers_dir = args.exp_root / "sync_checkpoints"
    sync_triggers_dir.mkdir(parents=True, exist_ok=True)

    for idx, instance_id in enumerate(tasks, 1):
        task_dir = runs_root / instance_id

        if args.phase in ("b", "dev_b"):
            run_and_grade(args.step_choice_config, args.checkout, instance_id, task_dir / "B", "B")
        elif args.phase == "c":
            run_and_grade(args.single_config, args.checkout, instance_id, task_dir / "A_r1", "A_r1")
            if (task_dir / "A_r1" / "patch-seal.json").exists() and not (task_dir / "C" / "alias.json").exists():
                run_cli("alias-c", "--source", task_dir / "A_r1", "--output", task_dir / "C")
        elif args.phase == "pass_at_3":
            for arm, seed_offset, is_last in (("A_r1", 1, False), ("A_r2", 2, False), ("A_r3", 3, True)):
                base_cfg = json.loads(args.single_config.read_text(encoding="utf-8"))
                base_cfg["seed"] = 2026110500 + seed_offset
                arm_cfg_path = args.exp_root / "configs" / f"luna.single.{arm}.json"
                arm_cfg_path.parent.mkdir(parents=True, exist_ok=True)
                arm_cfg_path.write_text(json.dumps(base_cfg, indent=2), encoding="utf-8")
                run_and_grade(arm_cfg_path, args.checkout, instance_id, task_dir / arm, arm, clean_image=is_last)
            cleanup_task_resources_and_cache(instance_id)
        elif args.phase == "pass_at_1":
            c_dir = task_dir / "C"
            if c_dir.exists() and not (c_dir / "result.json").exists() and not (c_dir / "anomaly-seal.json").exists():
                shutil.rmtree(c_dir)
            base_cfg = json.loads(args.single_config.read_text(encoding="utf-8"))
            base_cfg["seed"] = 2026110504
            arm_cfg_path = args.exp_root / "configs" / "luna.single.C.json"
            arm_cfg_path.parent.mkdir(parents=True, exist_ok=True)
            arm_cfg_path.write_text(json.dumps(base_cfg, indent=2), encoding="utf-8")
            run_and_grade(arm_cfg_path, args.checkout, instance_id, c_dir, "C", clean_image=True)
        elif args.phase in ("bc", "all"):
            order = ["B", "A_r1"] if idx % 2 == 1 else ["A_r1", "B"]
            for arm in order:
                cfg = args.step_choice_config if arm == "B" else args.single_config
                run_and_grade(cfg, args.checkout, instance_id, task_dir / arm, arm)
            if (task_dir / "A_r1" / "patch-seal.json").exists() and not (task_dir / "C" / "alias.json").exists():
                run_cli("alias-c", "--source", task_dir / "A_r1", "--output", task_dir / "C")

        if args.phase in ("a_rest", "all"):
            for arm, seed_offset in (("A_r2", 2), ("A_r3", 3)):
                base_cfg = json.loads(args.single_config.read_text(encoding="utf-8"))
                base_cfg["seed"] = 2026110500 + seed_offset
                arm_cfg_path = args.exp_root / "configs" / f"luna.single.{arm}.json"
                arm_cfg_path.parent.mkdir(parents=True, exist_ok=True)
                arm_cfg_path.write_text(json.dumps(base_cfg, indent=2), encoding="utf-8")
                run_and_grade(arm_cfg_path, args.checkout, instance_id, task_dir / arm, arm)

        _, summary_out, _ = run_cli("summarize", "--runs", runs_root)
        if summary_out.strip():
            idx_json = summary_out.find("{")
            clean_summary = summary_out[idx_json:] if idx_json != -1 else summary_out
            (args.exp_root / "summary.json").write_text(clean_summary, encoding="utf-8")

        if idx % args.batch_size == 0 or idx == len(tasks):
            milestone = f"{args.phase}_task_{idx:02d}"
            marker = sync_triggers_dir / f"{milestone}.json"
            if not marker.exists():
                from export_snapshot import build_snapshot
                meta = build_snapshot(args.exp_root, args.snap_dir, milestone=milestone)
                marker.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
                print(json.dumps({
                    "event": "checkpoint_10_ready",
                    "task_index": idx,
                    "milestone": milestone,
                    "snapshot": meta,
                }, ensure_ascii=False), flush=True)

    if args.phase == "b":
        rerun_context_limit_tasks(args, tasks, runs_root)
    return 0


def rerun_context_limit_tasks(args, tasks, runs_root):
    """After finishing the initial pass over all tasks, archive and rerun any unresolved task that hit the old 262144 context_limit or selector_error."""
    archive_root = args.exp_root.parent / "archived_before_rerun"
    rerun_list = []
    for instance_id in tasks:
        arm_dir = runs_root / instance_id / "B"
        res_path = arm_dir / "result.json"
        grade_path = arm_dir / "grading" / "grade.json"
        manifest_path = arm_dir / "manifest.json"
        if not res_path.exists():
            continue
        res = json.loads(res_path.read_text(encoding="utf-8"))
        grade = json.loads(grade_path.read_text(encoding="utf-8")) if grade_path.exists() else {}
        manifest = json.loads(manifest_path.read_text(encoding="utf-8")) if manifest_path.exists() else {}
        old_ctx = (manifest.get("config") or {}).get("context_limit", 262144)
        if ((res.get("status") == "context_limit" and old_ctx < 1050000) or res.get("status") == "selector_error") and grade.get("resolved") is not True:
            rerun_list.append(instance_id)

    if not rerun_list:
        return

    archive_root.mkdir(parents=True, exist_ok=True)
    for instance_id in rerun_list:
        arm_dir = runs_root / instance_id / "B"
        dest_dir = archive_root / instance_id / "B"
        dest_dir.parent.mkdir(parents=True, exist_ok=True)
        if dest_dir.exists():
            shutil.rmtree(dest_dir)
        shutil.move(str(arm_dir), str(dest_dir))
        print(json.dumps({
            "event": "rerunning_old_context_limit_task",
            "timestamp_utc": utc_now(),
            "instance_id": instance_id,
            "archived_to": str(dest_dir),
            "new_context_limit": 1050000,
        }, ensure_ascii=False), flush=True)
        run_and_grade(args.step_choice_config, args.checkout, instance_id, arm_dir, "B")

    _, summary_out, _ = run_cli("summarize", "--runs", runs_root)
    if summary_out.strip():
        idx_json = summary_out.find("{")
        clean_summary = summary_out[idx_json:] if idx_json != -1 else summary_out
        (args.exp_root / "summary.json").write_text(clean_summary, encoding="utf-8")

    from export_snapshot import build_snapshot
    milestone = f"{args.phase}_after_context_limit_rerun"
    meta = build_snapshot(args.exp_root, args.snap_dir, milestone=milestone)
    marker = args.exp_root / "sync_checkpoints" / f"{milestone}.json"
    marker.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({
        "event": "checkpoint_10_ready",
        "task_index": len(tasks),
        "milestone": milestone,
        "snapshot": meta,
    }, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    raise SystemExit(main())
