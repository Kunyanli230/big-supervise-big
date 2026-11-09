import httpx
import pytest
from conftest import policy

from big_supervise_big.router import RunStop, input_bound, price_bounds


def test_retries_are_bounded_and_all_attempts_are_logged(router_factory):
    router = router_factory([(429, {"error": "rate limit"}), (503, {"error": "unavailable"}), policy()])
    response = router.request("policy", {"model": router.cfg.generator})
    assert response["choices"] and router.calls == 3
    assert len([e for e in router.journal.events if e["event"] == "response"]) == 3
    assert router.summary()["unknown_cost_calls"] == 2
    assert router.summary()["cost_usd"] is None


def test_400_not_retried(router_factory):
    router = router_factory([(400, {"error": "bad request"})])
    with pytest.raises(RunStop, match="policy_error"):
        router.request("policy", {"model": router.cfg.generator})
    assert router.calls == 1


def test_transport_timeout_charged_as_unknown_and_retried_at_most_twice(router_factory):
    router = router_factory([httpx.ReadTimeout("timeout")] * 3)
    with pytest.raises(RunStop):
        router.request("policy", {"model": router.cfg.generator})
    assert router.calls == 3 and router.unknown_cost_calls == 3


def test_context_and_budget_stop_before_any_http_request(router_factory):
    router = router_factory(budget_usd=0.00000001)
    with pytest.raises(RunStop, match="budget_limit"):
        router.request("policy", {"model": router.cfg.generator})
    assert not router.requests
    router = router_factory(context_limit=25000, max_output_tokens=1000)
    with pytest.raises(RunStop, match="context_limit"):
        router.request("policy", {"model": router.cfg.generator, "messages": ["x" * 25000]})
    assert not router.requests


def test_missing_cost_is_unknown_and_stops_after_recording_raw_response(router_factory):
    response = policy()
    response["usage"] = {}
    router = router_factory([response])
    with pytest.raises(RunStop, match="unknown_cost"):
        router.request("policy", {"model": router.cfg.generator})
    assert router.summary()["cost_usd"] is None
    assert router.journal.events[-1]["body"] == response


def test_no_silent_model_replacement_and_no_secret_in_saved_errors(router_factory):
    response = policy()
    response["model"] = "different-model"
    response["echo"] = "test-secret-not-for-publication"
    router = router_factory([response])
    with pytest.raises(RunStop, match="model_mismatch"):
        router.request("policy", {"model": router.cfg.generator})
    assert "test-secret-not-for-publication" not in router.journal.path.read_text()


def test_mismatched_actual_provider_is_rejected(router_factory):
    response = policy()
    response["provider"] = "unexpected-route"
    router = router_factory([response])
    with pytest.raises(RunStop, match="provider_mismatch"):
        router.request("policy", {"model": router.cfg.generator})


def test_expired_deadline_stops_without_generation(router_factory):
    router = router_factory()
    router.deadline = router.clock() - 1
    with pytest.raises(RunStop, match="time_limit"):
        router.request("policy", {"model": router.cfg.generator})
    assert not router.requests


def test_price_reservation_accounts_for_cache_writes_and_long_context_overrides():
    assert price_bounds({"pricing": {"prompt": "0.1", "completion": "0.5",
                                     "overrides": [{"prompt": "0.2", "input_cache_write": "0.25",
                                                    "completion": "0.75"}]}}) == (0.25, 0.75)
    assert input_bound({"text": "中文"}) > input_bound({"text": "aa"})
