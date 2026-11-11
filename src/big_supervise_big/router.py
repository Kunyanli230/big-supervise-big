"""OpenRouter transport with bounded retries and an append-only billable-call ledger."""

import copy
import math
import os
import time

import httpx

from .storage import canonical, digest

CHAT_URL = "https://openrouter.ai/api/v1/chat/completions"
DECISIONS_URL = "https://openrouter.ai/api/alpha/decisions"
MODELS_URL = "https://openrouter.ai/api/v1/models"


class RunStop(Exception):
    def __init__(self, reason):
        self.reason = reason
        super().__init__(reason)


def input_bound(payload):
    """Conservative application bound, NOT a claim of exact Luna tokenization.

    Text-only requests: count serialized UTF-8 bytes plus 8192 framing tokens.
    This deliberately stops earlier than the model's advertised context window.
    """
    return len(canonical(payload).encode("utf-8")) + 8192


def price_bounds(model):
    """Reserve at the highest advertised input/cache-write and output tariff."""
    pricing = model.get("pricing", {})
    tiers = [pricing, *pricing.get("overrides", [])]
    try:
        inputs = [float(t[k]) for t in tiers for k in ("prompt", "input_cache_write") if k in t]
        outputs = [float(t["completion"]) for t in tiers if "completion" in t]
        if not inputs or not outputs or any(not math.isfinite(x) or x < 0 for x in inputs + outputs):
            raise ValueError
        return max(inputs), max(outputs)
    except (TypeError, ValueError):
        raise ValueError("Model catalog is missing usable pricing") from None


def fetch_catalog(client, cfg):
    result = {}
    for suffix in ("", "?output_modalities=decisions"):
        response = client.get(MODELS_URL + suffix, timeout=30)
        response.raise_for_status()
        for model in response.json()["data"]:
            if model.get("id") in (cfg.generator, cfg.supervisor):
                result[model["id"]] = model
    required = [cfg.generator] + ([cfg.supervisor] if cfg.mode == "step_choice" else [])
    for model in required:
        if model not in result:
            raise ValueError(f"Requested model not in current catalog: {model}")
        price_bounds(result[model])
    capabilities = result[cfg.generator].get("supported_parameters", [])
    for needed in ("tools", "seed", "reasoning"):
        if needed not in capabilities:
            raise ValueError(f"Generator does not advertise {needed}")
    return result


class Router:
    def __init__(self, cfg, journal, *, catalog, client=None, clock=time.monotonic, sleep=time.sleep):
        self.cfg, self.journal, self.catalog = cfg, journal, catalog
        self.client = client or httpx.Client()
        self.clock, self.sleep = clock, sleep
        self.deadline = clock() + cfg.wall_seconds
        self.known_cost = 0.0
        self.unknown_cost_calls = 0
        self.calls = 0
        self.key = os.environ.get(cfg.api_key_env, "")
        if not self.key:
            raise ValueError(f"Set {cfg.api_key_env} in the host environment")
        self.journal.secret = self.key

    def remaining(self):
        remaining = self.deadline - self.clock()
        if remaining <= 0:
            raise RunStop("time_limit")
        return remaining

    def request(self, role, payload):
        cfg = self.cfg
        model = payload["model"]
        bound = input_bound(payload)
        output_cap = cfg.max_output_tokens if role == "policy" else 0
        context = min(cfg.context_limit, int(self.catalog[model]["context_length"]))
        if bound + output_cap > context:
            raise RunStop("context_limit")
        input_price, output_price = price_bounds(self.catalog[model])
        reserve = bound * input_price + output_cap * output_price
        url = CHAT_URL if role == "policy" else DECISIONS_URL
        timeout = cfg.policy_timeout if role == "policy" else cfg.decision_timeout
        request_id = f"{self.calls + 1}-{digest(payload)}"
        for attempt in range(cfg.max_retries + 1):
            if self.known_cost + reserve > cfg.budget_usd:
                raise RunStop("budget_limit")
            remaining = self.remaining()
            self.calls += 1
            self.journal.append({"event": "request", "role": role, "request_id": request_id,
                                 "attempt": attempt, "url": url, "payload": payload,
                                 "input_token_bound": bound, "reserved_usd": reserve})
            started = self.clock()
            retry = False
            try:
                response = self.client.post(
                    url, json=copy.deepcopy(payload),
                    headers={"Authorization": f"Bearer {self.key}"},
                    timeout=min(timeout, remaining),
                )
            except httpx.TransportError as exc:
                # Upstream may already have billed an unknown response: reserve it permanently.
                self.known_cost += reserve
                self.unknown_cost_calls += 1
                self.journal.append({"event": "transport_error", "role": role, "request_id": request_id,
                                     "attempt": attempt, "error": type(exc).__name__,
                                     "cost_usd": None, "reserved_usd": reserve})
                retry = True
            else:
                try:
                    body = response.json()
                except ValueError:
                    body = {"invalid_json": response.text}
                usage = body.get("usage", {}) if isinstance(body, dict) else {}
                raw_cost = usage.get("cost")
                known = (isinstance(raw_cost, (int, float)) and not isinstance(raw_cost, bool)
                         and math.isfinite(raw_cost) and raw_cost >= 0)
                self.known_cost += raw_cost if known else reserve
                if not known:
                    self.unknown_cost_calls += 1
                self.journal.append({"event": "response", "role": role, "request_id": request_id,
                                     "attempt": attempt, "status": response.status_code, "body": body,
                                     "latency_seconds": self.clock() - started,
                                     "cost_usd": raw_cost if known else None,
                                     "reserved_usd": reserve})
                if response.status_code == 429 or 500 <= response.status_code <= 599:
                    retry = True
                elif response.status_code != 200 or not isinstance(body, dict) or body.get("error"):
                    raise RunStop("policy_error" if role == "policy" else "selector_error")
                else:
                    if not known:
                        raise RunStop("unknown_cost")
                    allowed_models = {model, self.catalog[model].get("canonical_slug")} - {None}
                    if body.get("model") not in allowed_models:
                        raise RunStop("model_mismatch")
                    if not body.get("provider"):
                        raise RunStop("provider_missing")
                    if str(body["provider"]).casefold() != cfg.provider.casefold():
                        raise RunStop("provider_mismatch")
                    self.remaining()
                    if self.known_cost > cfg.budget_usd:
                        raise RunStop("budget_limit")
                    return body
            if retry and attempt < cfg.max_retries:
                self.sleep(min(2 ** attempt, self.remaining()))
            else:
                raise RunStop("policy_error" if role == "policy" else "selector_error")

    def routing(self):
        return {"only": [self.cfg.provider], "allow_fallbacks": False, "require_parameters": True}

    def summary(self):
        known = sum(event["cost_usd"] for event in self.journal.events
                    if event["event"] == "response" and event.get("cost_usd") is not None)
        return {"attempts": self.calls, "cost_usd": self.known_cost if not self.unknown_cost_calls else None,
                "known_actual_cost_usd": known, "known_and_reserved_usd": self.known_cost,
                "unknown_cost_calls": self.unknown_cost_calls}
