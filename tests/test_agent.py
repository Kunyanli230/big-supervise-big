import copy

import pytest
from conftest import FakeEnvironment, decision, policy

from big_supervise_big.agent import Decisions, SupervisedAgent, policy_messages
from big_supervise_big.router import RunStop


def test_only_selected_action_executes_and_only_selected_history_is_inherited(router_factory, tmp_path):
    first = [policy("unselected-first"), policy("chosen", call_id="chosen-id"), policy("unselected-third")]
    second = [policy("unselected-submit-first", call_id="submit-1"), policy("submit", call_id="submit-2"),
              policy("unselected-submit-third", call_id="submit-3")]
    router = router_factory([*first, decision(), *second, decision()])
    env = FakeEnvironment()
    agent = SupervisedAgent(router, env, output_path=tmp_path / "trajectory.json")
    result = agent.run("FULL REQUIREMENTS AND INTERFACES")
    assert result["exit_status"] == "Submitted"
    assert result["submission"] == "verified-patch\n"
    assert env.commands == ["chosen", "submit"]
    policy_requests = [payload for _, payload in router.requests if "messages" in payload]
    assert policy_requests[0]["messages"] == policy_requests[1]["messages"] == policy_requests[2]["messages"]
    for payload in policy_requests[3:]:
        history = str(payload["messages"])
        assert "unselected-first" not in history and "unselected-third" not in history
        assert "chosen-id" in history and "observation:chosen" in history
        tool = next(m for m in payload["messages"] if m["role"] == "tool")
        assistant = next(m for m in payload["messages"] if m["role"] == "assistant")
        assert tool["tool_call_id"] == assistant["tool_calls"][0]["id"]
    selectors = [p for _, p in router.requests if "questions" in p]
    assert selectors[0]["state"]["task"] == "FULL REQUIREMENTS AND INTERFACES"
    assert selectors[0]["state"]["shared_prefix"] == []
    assert selectors[1]["state"]["shared_prefix"][0]["action"] == "chosen"
    assert agent.n_calls == 2 and router.calls == 8


def test_invalid_selected_branch_falls_back_to_highest_probability_valid(router_factory):
    invalid = policy(tool_calls=[])
    router = router_factory([policy("wrong"), invalid, policy("submit"),
                             decision(probs={"option_1": 0.1, "option_2": 0.6, "option_3": 0.3})])
    env = FakeEnvironment()
    agent = SupervisedAgent(router, env)
    agent.run("task")
    assert env.commands == ["submit"]
    assert agent.decisions[0]["selected_branch_id"] == 2
    assert agent.decisions[0]["fallback_reason"]


def test_all_invalid_uses_native_format_error_feedback_without_paid_replacements(router_factory):
    responses = [policy(tool_calls=[]) for _ in range(3)]
    responses += [policy("wrong"), policy("submit"), policy("other"), decision()]
    router = router_factory(responses)
    env = FakeEnvironment()
    agent = SupervisedAgent(router, env)
    agent.run("task")
    assert router.calls == 7 and agent.n_calls == 2 and env.commands == ["submit"]
    next_history = router.requests[3][1]["messages"]
    assert next_history[-1]["role"] == "user" and "Invalid format" in next_history[-1]["content"]
    assert not any(m["role"] == "assistant" for m in next_history)


def test_identical_valid_actions_skip_selector_and_keep_matching_branch_history(router_factory):
    responses = [policy("submit", call_id=f"branch-{i}", content=f"thought-{i}") for i in range(3)]
    router = router_factory(responses)
    env = FakeEnvironment()
    agent = SupervisedAgent(router, env)
    result = agent.run("task")
    assert result["exit_status"] == "Submitted" and env.commands == ["submit"]
    assert router.calls == 3 and not any("questions" in payload for _, payload in router.requests)
    selection = agent.decisions[0]
    assert selection["answer"] is None
    assert selection["fallback_reason"] == "unanimous_valid_action_random_choice"
    selected = selection["selected_branch_id"]
    assistant = next(m for m in policy_messages(agent.messages) if m["role"] == "assistant")
    assert assistant["content"] == f"thought-{selected}"
    assert assistant["tool_calls"][0]["id"] == f"branch-{selected}"


@pytest.mark.parametrize("bad", [None, {"option_1": 1.0},
                                {"option_1": float("nan"), "option_2": 0, "option_3": 0},
                                {"option_1": 0.2, "option_2": 0.2, "option_3": 0.2}])
def test_bad_selector_never_executes_or_silently_uses_first_branch(router_factory, bad):
    response = decision()
    response["answers"]["next_action"]["probabilities"] = bad
    # NaN cannot be encoded by httpx: exercise validation directly with a mocked transport result.
    router = router_factory()
    router.request = lambda *a: copy.deepcopy(response)
    criteria = {f"option_{i}": {} for i in (1, 2, 3)}
    with pytest.raises(RunStop, match="selector_error"):
        Decisions(router).choose({}, criteria)


def test_selector_error_terminates_before_action(router_factory):
    response = decision()
    response["answers"]["next_action"]["choice"] = "unknown"
    router = router_factory([policy("one"), policy("two"), policy("three"), response])
    env = FakeEnvironment()
    result = SupervisedAgent(router, env).run("task")
    assert result["exit_status"] == "selector_error" and not env.commands


def test_single_mode_has_no_supervisor_and_honors_step_limit(router_factory):
    router = router_factory([policy("one")], mode="single", max_steps=1)
    env = FakeEnvironment()
    result = SupervisedAgent(router, env).run("task")
    assert result["exit_status"] == "step_limit" and router.calls == 1 and env.commands == ["one"]


def test_reasoning_fields_and_call_ids_survive_without_inventing_hidden_text(router_factory):
    response = policy("submit", content=None, reasoning="actual visible text",
                      reasoning_details=[{"type": "reasoning.text", "text": "actual visible text"}])
    router = router_factory([response], mode="single")
    agent = SupervisedAgent(router, FakeEnvironment())
    agent.run("task")
    message = next(m for m in policy_messages(agent.messages) if m["role"] == "assistant")
    assert message["reasoning_details"] == response["choices"][0]["message"]["reasoning_details"]
    assert message["content"] == "actual visible text" and "extra" not in message


@pytest.mark.parametrize("command", ["", "  ", 1, ["echo"]])
def test_nonstring_or_empty_command_is_invalid(router_factory, command):
    router = router_factory([policy(command)], mode="single", max_steps=1)
    env = FakeEnvironment()
    result = SupervisedAgent(router, env).run("task")
    assert result["exit_status"] == "step_limit" and not env.commands
