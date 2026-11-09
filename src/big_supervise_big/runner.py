"""One fresh run directory and one isolated container per coding trajectory."""

import json
import os
import time
from pathlib import Path

import httpx

from . import __version__, prompts
from .agent import SupervisedAgent
from .config import SOURCE_SHA
from .environment import Environment, image_id
from .router import Router, RunStop, fetch_catalog
from .storage import Journal, atomic_json, digest, seal_patch
from .tasks import Task


def run(cfg, *, checkout: Path, instance_id: str, output: Path, arm: str):
    task = Task.load(checkout, instance_id)
    if arm not in ("A_r1", "A_r2", "A_r3", "B", "C"):
        raise ValueError("arm must be A_r1, A_r2, A_r3, B, or C")
    if (arm == "B") != (cfg.mode == "step_choice"):
        raise ValueError("B uses step_choice; other arms use single")
    if not os.environ.get(cfg.api_key_env):
        raise ValueError(f"Set {cfg.api_key_env} in the host environment")
    # Never restart/overwrite an unsealed trajectory: the last action may have had side effects.
    output.mkdir(parents=True, exist_ok=False)
    journal = Journal(output / "events.jsonl", secret=os.environ[cfg.api_key_env])
    env, agent, router = None, None, None
    result = {"instance_id": instance_id, "arm": arm, "mode": cfg.mode,
              "status": "starting", "resolved": None, "grade_status": "not_evaluated"}
    started = time.monotonic()
    try:
        with httpx.Client() as client:
            catalog = fetch_catalog(client, cfg)
            atomic_json(output / "catalog.json", catalog)
            pinned = image_id(task.image)
            manifest = {**task.manifest(), "image_id": pinned, "source_sha": SOURCE_SHA,
                        "config": cfg.snapshot(), "config_sha256": digest(cfg.snapshot()), "arm": arm,
                        "package_version": __version__, "code_sha256": digest({
                            path.name: path.read_text(encoding="utf-8")
                            for path in sorted(Path(__file__).parent.glob("*.py"))})}
            atomic_json(output / "manifest.json", manifest)
            atomic_json(output / "prompts.json", {"system": prompts.SYSTEM, "instance": prompts.INSTANCE,
                                                 "supervisor": prompts.INSTRUCTIONS})
            # Container setup is not charged against the agent's 50-minute deadline.
            env = Environment(task, pinned, command_seconds=cfg.command_seconds)
            effective_cfg = cfg.model_copy(update={"wall_seconds": min(cfg.wall_seconds, int(task.timeout))})
            router = Router(effective_cfg, journal, catalog=catalog, client=client)
            env.deadline = router.remaining
            agent = SupervisedAgent(router, env, output_path=output / "trajectory.json")
            terminal = agent.run(task.instruction)
            result["status"] = terminal.get("exit_status", "unknown")
            # Preserve the native submission verbatim. On a limit/error use the current working tree.
            patch = terminal.get("submission", "") if result["status"] == "Submitted" else env.capture_patch()
            seal_patch(output, patch)
    except Exception as exc:
        result["status"] = exc.reason if isinstance(exc, RunStop) else type(exc).__name__
        journal.append({"event": "run_error", "type": type(exc).__name__, "message": str(exc)})
        if env is not None and not (output / "patch-seal.json").exists():
            try:
                seal_patch(output, env.capture_patch())
            except Exception as capture_error:
                journal.append({"event": "patch_capture_error", "type": type(capture_error).__name__})
                result["patch_capture_failed"] = True
    finally:
        try:
            if agent is not None:
                agent.save(output / "trajectory.json")
        finally:
            if env is not None:
                result["cleanup_verified"] = env.cleanup()
                if not result["cleanup_verified"]:
                    result["remaining_container_id"] = env.container_id
            result.update(elapsed_seconds=time.monotonic() - started,
                          usage=router.summary() if router else None,
                          steps=agent.n_calls if agent else 0,
                          patch_sealed=(output / "patch-seal.json").exists())
            atomic_json(output / "result.json", result)
    return result


def alias_c(source: Path, output: Path):
    """Pass@1 is a reference to PREDECLARED A_r1, never a second paid generation."""
    from .storage import verify_patch

    verify_patch(source)
    result = json.loads((source / "result.json").read_text(encoding="utf-8"))
    if result["arm"] != "A_r1":
        raise ValueError("C may only reference the predeclared A_r1")
    output.mkdir(parents=True, exist_ok=False)
    alias = {"instance_id": result["instance_id"], "arm": "C", "source": str(source.resolve()),
             "source_patch_sha256": json.loads((source / "patch-seal.json").read_text())["sha256"],
             "extra_actual_cost_usd": 0}
    atomic_json(output / "alias.json", alias)
    return alias
