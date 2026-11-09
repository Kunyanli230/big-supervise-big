"""Mini's Docker execution/submission, with explicit phase isolation and bounded commands."""

import json
import math
import shlex
import subprocess

from minisweagent.environments.docker import DockerEnvironment


def docker(*args, timeout=120, check=True):
    return subprocess.run(["docker", *map(str, args)], capture_output=True, text=True,
                          encoding="utf-8", errors="replace", timeout=timeout, check=check)


def image_id(image):
    result = docker("image", "inspect", image, check=False)
    if result.returncode:
        raise ValueError(f"Image is not prepared. Run: docker pull {image}")
    return json.loads(result.stdout)[0]["Id"]


class Environment(DockerEnvironment):
    def __init__(self, task, pinned_image, *, network="none", deadline=None, command_seconds=120):
        self.deadline = deadline
        self.base_commit = None
        super().__init__(image=pinned_image, cwd="/", env=task.env if network == "none" else task.verifier_env,
                         forward_env=[], timeout=command_seconds,
                         container_timeout=f"{math.ceil(task.timeout + task.verifier_timeout + 600)}s",
                         run_args=["--rm", "--network", network, "--cpus", str(task.cpus),
                                   "--memory", f"{task.memory_mb}m"])
        try:
            probe = self.execute({"command": "git -C /app rev-parse --show-toplevel 2>/dev/null "
                                  "|| git -C /testbed rev-parse --show-toplevel"})
            cwd = probe["output"].strip()
            if probe["returncode"] or cwd not in ("/app", "/testbed"):
                raise ValueError("V2 repository not found at /app or /testbed")
            self.config.cwd = cwd
            base = self.execute({"command": "git rev-parse HEAD"})
            if base["returncode"]:
                raise ValueError("Could not read V2 base commit")
            self.base_commit = base["output"].strip()
        except BaseException:
            self.cleanup()
            raise

    def execute(self, action, cwd="", *, timeout=None):
        seconds = timeout or self.config.timeout
        if self.deadline is not None:
            seconds = min(seconds, self.deadline())
        # Killing just the local docker CLI leaves shell processes alive in the container.
        # Bound the command inside the container too, while keeping Mini's submission detection.
        wrapped = dict(action, command=(f"timeout -k 5s {max(seconds, 0.01):.3f}s "
                                        f"bash -lc {shlex.quote(action['command'])}"))
        return super().execute(wrapped, cwd, timeout=math.ceil(seconds) + 10)

    def capture_patch(self):
        # This is a host checkpoint operation, allowed after the agent deadline.
        previous = self.deadline
        self.deadline = None
        try:
            result = self.execute({"command": "git add -A && git diff --cached --binary "
                                   + shlex.quote(self.base_commit) + " --"}, timeout=30)
            if result["returncode"]:
                raise RuntimeError("Failed to capture working-tree diff")
            return result["output"]
        finally:
            self.deadline = previous

    def cleanup(self):
        container = getattr(self, "container_id", None)
        if container:
            try:
                result = docker("rm", "-f", container, timeout=60, check=False)
                if result.returncode:
                    self.logger.error("Container cleanup failed: %s", result.stderr)
                    return False
                self.container_id = None
            except (OSError, subprocess.SubprocessError) as exc:
                self.logger.error("Container cleanup failed: %s", type(exc).__name__)
                return False
        return True
