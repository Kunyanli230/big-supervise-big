import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from conftest import FakeEnvironment

from big_supervise_big.analysis import summarize
from big_supervise_big.config import Config
from big_supervise_big.grade import grade
from big_supervise_big.runner import alias_c, run
from big_supervise_big.storage import atomic_json, seal_patch


@pytest.fixture
def task(tmp_path):
    task_path = tmp_path / "instance_demo"
    (task_path / "tests").mkdir(parents=True)
    class DemoTask:
        path = task_path
        instruction = "Full issue requirements and interfaces"
        image = "ghcr.io/scaleapi/swe-bench_pro-v2:instance_demo"
        instance_id = "instance_demo"
        timeout = 3000
        verifier_timeout = 3000
        def manifest(self):
            return {"instance_id": self.instance_id, "image": self.image}
    return DemoTask()


def test_runner_captures_partial_patch_on_policy_failure_and_never_overwrites(
        task, router_factory, tmp_path, monkeypatch):
    from big_supervise_big.router import Router
    router = router_factory([(400, {"error": "bad request"})], mode="single")
    monkeypatch.setattr("big_supervise_big.runner.Task.load", lambda *a: task)
    monkeypatch.setattr("big_supervise_big.runner.fetch_catalog", lambda *a: router.catalog)
    monkeypatch.setattr("big_supervise_big.runner.image_id", lambda *a: "sha256:pinned")
    env = FakeEnvironment()
    env.capture_patch = lambda: "partial-patch\n"
    env.cleanup = lambda: True
    monkeypatch.setattr("big_supervise_big.runner.Environment", lambda *a, **kw: env)
    # Use real Router with the mock HTTP client, not a fake generator or fake agent.
    def factory(cfg, journal, **kwargs):
        return Router(cfg, journal, catalog=router.catalog, client=router.client)
    monkeypatch.setattr("big_supervise_big.runner.Router", factory)
    output = tmp_path / "run"
    result = run(Config(mode="single"), checkout=tmp_path, instance_id="instance_demo",
                 output=output, arm="A_r1")
    assert result["status"] == "policy_error" and result["patch_sealed"]
    assert (output / "model.patch").read_text() == "partial-patch\n"
    assert json.loads((output / "result.json").read_text())["resolved"] is None
    assert result["cleanup_verified"]
    with pytest.raises(FileExistsError):
        run(Config(mode="single"), checkout=tmp_path, instance_id="instance_demo", output=output, arm="A_r1")


@pytest.mark.parametrize("reward,rc,has_output,expected", [("1", 0, True, True),
                                                         ("0", 1, True, False),
                                                         ("0", 124, True, None),
                                                         ("0", 1, False, None)])
def test_grading_uses_new_container_official_verifier_and_keeps_errors_unknown(
        task, tmp_path, monkeypatch, reward, rc, has_output, expected):
    output = tmp_path / "generation"
    output.mkdir()
    seal_patch(output, "a sealed diff")
    atomic_json(output / "manifest.json", {**task.manifest(), "image_id": "sha256:pinned"})
    monkeypatch.setattr("big_supervise_big.grade.Task.load", lambda *a: task)
    monkeypatch.setattr("big_supervise_big.grade.image_id", lambda *a: "sha256:pinned")
    calls, environments = [], []
    class EvalEnv:
        container_id = "fresh-verifier-not-agent"
        def execute(self, action, **kwargs):
            calls.append(action["command"])
            return {"returncode": rc if action["command"] == "bash /tests/test.sh" else 0,
                    "output": "", "exception_info": ""}
        def cleanup(self):
            return True
    def environment(*args, **kwargs):
        environments.append(kwargs)
        return EvalEnv()
    def docker_copy(*args, **kwargs):
        assert args[0] == "cp"
        calls.append(list(map(str, args)))
        if str(args[1]).endswith(":/logs/verifier/."):
            directory = Path(args[2])
            (directory / "reward.txt").write_text(reward)
            if has_output:
                atomic_json(directory / "output.json", {"tests": []})
        return SimpleNamespace(returncode=0, stdout="", stderr="")
    monkeypatch.setattr("big_supervise_big.grade.Environment", environment)
    monkeypatch.setattr("big_supervise_big.grade.docker", docker_copy)
    result = grade(checkout=tmp_path, output=output)
    assert environments == [{"network": "bridge"}]
    assert result["resolved"] is expected
    assert "bash /tests/test.sh" in calls
    assert any(isinstance(call, list) and ":/tests/" in call[-1] for call in calls)
    assert not any(isinstance(call, list) and "solution" in str(call) for call in calls)


def add_run(root, arm, score, cost, *, task="instance_demo"):
    output = root / task / arm
    output.mkdir(parents=True)
    seal = seal_patch(output, arm + "-patch")
    atomic_json(output / "result.json", {"instance_id": task, "arm": arm, "usage": {"cost_usd": cost}})
    if score is not None:
        atomic_json(output / "grading" / "grade.json", {"resolved": score, "patch_sha256": seal["sha256"]})
    return output


def test_three_groups_share_predeclared_first_attempt_without_double_counting(tmp_path):
    source = add_run(tmp_path, "A_r1", False, 1)
    add_run(tmp_path, "A_r2", True, 1)
    add_run(tmp_path, "A_r3", False, 1)
    add_run(tmp_path, "B", True, 2)
    alias_c(source, tmp_path / "instance_demo" / "C")
    result = summarize(tmp_path)
    assert result["actual_total_cost_usd"] == 5
    assert result["groups"]["A"]["successes"] == result["groups"]["B"]["successes"] == 1
    assert result["groups"]["C"]["successes"] == 0
    assert result["paired"]["B_minus_C"]["wins"] == 1


def test_unknown_candidate_scores_are_not_reported_as_confirmed_pass3_failure(tmp_path):
    add_run(tmp_path, "A_r1", False, None)
    add_run(tmp_path, "A_r2", None, 1)
    result = summarize(tmp_path)
    assert result["groups"]["A"]["unknown"] == 1
    assert result["actual_total_cost_usd"] is None
    assert result["known_actual_cost_usd"] == 1


def test_c_alias_cannot_choose_best_attempt(tmp_path):
    source = add_run(tmp_path, "A_r2", True, 1)
    with pytest.raises(ValueError, match="predeclared A_r1"):
        alias_c(source, tmp_path / "C")


def test_duplicate_cells_rejected_instead_of_picking_best_rerun(tmp_path):
    add_run(tmp_path / "original", "B", False, 1)
    add_run(tmp_path / "rerun", "B", True, 1)
    with pytest.raises(ValueError, match="Duplicate experiment cell"):
        summarize(tmp_path)
