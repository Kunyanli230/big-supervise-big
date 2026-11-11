"""Fresh-image replay followed by the unchanged official V2 tests/test.sh."""

import json
from pathlib import Path

from .environment import Environment, docker, image_id
from .storage import atomic_json, verify_patch
from .tasks import Task


def grade(*, checkout: Path, output: Path):
    patch = verify_patch(output)
    manifest = json.loads((output / "manifest.json").read_text(encoding="utf-8"))
    task = Task.load(checkout, manifest["instance_id"])
    if any(task.manifest()[key] != manifest[key] for key in task.manifest()):
        raise ValueError("Task/config differs from sealed generation manifest")
    pinned = manifest["image_id"]
    if image_id(pinned) != pinned:
        raise ValueError("Original immutable image is not available")
    evaluation = output / "grading"
    # A second grading must have its own explicit directory, never overwrite a verdict.
    evaluation.mkdir(exist_ok=False)
    record = {"instance_id": task.instance_id, "resolved": None, "status": "eval_error",
              "patch_sha256": json.loads((output / "patch-seal.json").read_text())["sha256"],
              "official_tests_executed": False, "image_id": pinned}
    env = None
    try:
        # Public network is only available to the verifier, which has no model/API credentials.
        env = Environment(task, pinned, network="bridge")
        docker("cp", str(patch.resolve()), f"{env.container_id}:/tmp/model.patch")
        applied = env.execute({"command": "git apply --check /tmp/model.patch && git apply /tmp/model.patch"})
        if patch.stat().st_size == 0:
            applied = {"returncode": 0, "output": "Empty patch; apply nothing"}
        atomic_json(evaluation / "apply.json", applied)
        if applied["returncode"] != 0:
            record.update(status="invalid_patch", resolved=False)
            return record
        env.execute({"command": "mkdir -p /tests /logs/verifier"})
        docker("cp", str(task.path / "tests") + "/.", f"{env.container_id}:/tests/")
        record["official_tests_executed"] = True
        verifier_timeout = float(manifest.get("config", {}).get("verifier_seconds") or 1800)
        verified = env.execute({"command": "bash /tests/test.sh"}, timeout=verifier_timeout)
        atomic_json(evaluation / "verifier-process.json", verified)
        docker("cp", f"{env.container_id}:/logs/verifier/.", str(evaluation))
        reward = (evaluation / "reward.txt").read_text(encoding="utf-8").strip()
        # A verifier trap may write reward=0 even before tests/parsing could run.
        parsed = json.loads((evaluation / "output.json").read_text(encoding="utf-8"))
        valid_reward = (verified["returncode"], reward) in ((0, "1"), (1, "0"))
        if isinstance(parsed.get("tests"), list) and valid_reward:
            record.update(status="graded", resolved=reward == "1")
    except Exception as exc:
        record["error_type"] = type(exc).__name__
    finally:
        if env:
            record["cleanup_verified"] = env.cleanup()
            if not record["cleanup_verified"]:
                record["remaining_container_id"] = env.container_id
        atomic_json(evaluation / "grade.json", record)
    return record
