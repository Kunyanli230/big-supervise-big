"""Read only public V2 instructions/configuration in the agent-facing task adapter."""

import subprocess
import tomllib
from dataclasses import dataclass
from pathlib import Path

from .config import V2_SHA
from .storage import digest


def git(checkout, *args):
    return subprocess.run(["git", "-C", str(checkout), *args], check=True,
                          capture_output=True, text=True, encoding="utf-8").stdout.strip()


def verify_checkout(checkout: Path):
    if git(checkout, "rev-parse", "HEAD") != V2_SHA:
        raise ValueError(f"SWE-bench Pro V2 checkout must be at {V2_SHA}")
    if git(checkout, "status", "--porcelain", "--untracked-files=all", "--", "v2"):
        raise ValueError("Official v2/ tree has local modifications; use an unmodified checkout")


@dataclass(frozen=True)
class Task:
    path: Path
    instruction: str
    image: str
    timeout: float
    verifier_timeout: float
    cpus: float
    memory_mb: int
    env: dict
    verifier_env: dict

    @property
    def instance_id(self):
        return self.path.name

    @classmethod
    def load(cls, checkout: Path, instance_id: str):
        verify_checkout(checkout)
        if not instance_id.startswith("instance_") or Path(instance_id).name != instance_id:
            raise ValueError("Expected an instance_id, not a path")
        root = (checkout / "v2" / "tasks").resolve()
        path = (root / instance_id).resolve()
        if path.parent != root:
            raise ValueError("Task must be inside official v2/tasks")
        config = tomllib.loads((path / "task.toml").read_text(encoding="utf-8"))
        expected_image = f"ghcr.io/scaleapi/swe-bench_pro-v2:{instance_id}"
        if config["agent"]["network_mode"] != "no-network":
            raise ValueError("Official agent phase must be network-locked")
        if config["environment"]["docker_image"] != expected_image:
            raise ValueError("Expected the V2 GHCR image")
        return cls(path, (path / "instruction.md").read_text(encoding="utf-8"), expected_image,
                   float(config["agent"]["timeout_sec"]), float(config["verifier"]["timeout_sec"]),
                   float(config["environment"].get("cpus", 1)),
                   int(config["environment"].get("memory_mb", 4096)),
                   config["environment"].get("env", {}) | config["agent"].get("env", {}),
                   config["environment"].get("env", {}) | config["verifier"].get("env", {}))

    def manifest(self):
        return {"instance_id": self.instance_id, "harness_sha": V2_SHA, "image": self.image,
                "instruction_sha256": digest(self.instruction), "agent_network": "none",
                "agent_timeout": self.timeout, "verifier_timeout": self.verifier_timeout}
