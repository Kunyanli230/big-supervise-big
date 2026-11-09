import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from big_supervise_big.config import V2_SHA, Config
from big_supervise_big.storage import atomic_json, seal_patch, verify_patch
from big_supervise_big.tasks import Task, verify_checkout


def test_configuration_rejects_other_family_and_legacy_fields():
    with pytest.raises(ValidationError):
        Config(supervisor="vllm-sr/d3-mini")
    with pytest.raises(ValidationError):
        Config.model_validate({"qwen": {"base_url": "http://localhost"}})


def test_task_reader_retains_complete_instructions_and_does_not_read_hidden_files(tmp_path, monkeypatch):
    monkeypatch.setattr("big_supervise_big.tasks.git", lambda root, *args: V2_SHA if args[0] == "rev-parse" else "")
    path = tmp_path / "v2" / "tasks" / "instance_demo"
    path.mkdir(parents=True)
    (path / "instruction.md").write_text("Problem\nRequirements\nInterfaces", encoding="utf-8")
    (path / "task.toml").write_text('''
[agent]
network_mode = "no-network"
timeout_sec = 3000
[environment]
docker_image = "ghcr.io/scaleapi/swe-bench_pro-v2:instance_demo"
[verifier]
timeout_sec = 3000
''')
    original = Path.read_text
    def guarded_read(self, *args, **kwargs):
        assert "solution" not in self.parts and "tests" not in self.parts
        return original(self, *args, **kwargs)
    monkeypatch.setattr(Path, "read_text", guarded_read)
    task = Task.load(tmp_path, "instance_demo")
    assert task.instruction == "Problem\nRequirements\nInterfaces"
    assert task.manifest()["harness_sha"] == V2_SHA
    with pytest.raises(ValueError):
        Task.load(tmp_path, "../instance_demo")


def test_mismatched_benchmark_revision_is_rejected(tmp_path, monkeypatch):
    monkeypatch.setattr("big_supervise_big.tasks.git", lambda *a: "wrong-sha")
    with pytest.raises(ValueError, match="checkout must"):
        verify_checkout(tmp_path)


def test_seal_detects_patch_tampering(tmp_path):
    seal_patch(tmp_path, "diff --git a/file b/file\n")
    assert verify_patch(tmp_path).name == "model.patch"
    (tmp_path / "model.patch").write_text("changed")
    with pytest.raises(ValueError, match="seal mismatch"):
        verify_patch(tmp_path)


def test_atomic_json_is_utf8_and_valid(tmp_path):
    atomic_json(tmp_path / "中文.json", {"中文": "内容"})
    assert json.loads((tmp_path / "中文.json").read_text(encoding="utf-8")) == {"中文": "内容"}
