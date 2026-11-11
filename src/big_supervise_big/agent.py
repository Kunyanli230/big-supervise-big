"""Source StepD3VerifierAgent's query logic on Mini's native run/execute protocol."""

import copy
import math
import random
from types import SimpleNamespace

from minisweagent.agents.default import DefaultAgent
from minisweagent.exceptions import FormatError, InterruptAgentFlow
from minisweagent.models.utils.actions_toolcall import (
    BASH_TOOL,
    format_toolcall_observation_messages,
    parse_toolcall_actions,
)

from .prompts import FORMAT_ERROR, INSTANCE, INSTRUCTIONS, OBSERVATION, SYSTEM
from .router import RunStop


def visible_thought(message):
    parts = [message.get("reasoning_content") or message.get("reasoning"), message.get("content")]
    return "\n\n".join(dict.fromkeys(p.strip() for p in parts if isinstance(p, str) and p.strip()))


def policy_messages(messages):
    permitted = {"role", "content", "tool_calls", "tool_call_id", "reasoning", "reasoning_details"}
    result = []
    for message in messages:
        if message["role"] == "exit":
            continue
        prepared = {key: copy.deepcopy(value) for key, value in message.items() if key in permitted}
        if message.get("reasoning_content") and not prepared.get("reasoning"):
            prepared["reasoning"] = message["reasoning_content"]
        result.append(prepared)
    return result


def shared_prefix(messages):
    prefix = []
    for message in messages[2:]:
        role = message.get("role")
        if role == "assistant":
            actions = message.get("extra", {}).get("actions", [])
            prefix.append({"role": role, "thought": visible_thought(message),
                           "action": actions[0]["command"] if len(actions) == 1 else ""})
        elif role == "tool":
            prefix.append({"role": role, "observation": message.get("content", "")})
        elif role == "user":
            prefix.append({"role": role, "content": message.get("content", "")})
    return prefix


class Policy:
    """Mini Model protocol with the native tool parser and observation formatter."""

    def __init__(self, router):
        self.router = router

    def query(self, messages, *, seed):
        cfg = self.router.cfg
        response = self.router.request("policy", {
            "model": cfg.generator, "messages": policy_messages(messages),
            "tools": [BASH_TOOL], "tool_choice": "required",
            "reasoning": {"effort": cfg.reasoning_effort}, "seed": seed,
            "max_tokens": cfg.max_output_tokens, "provider": self.router.routing(),
        })
        try:
            choices = response["choices"]
            if len(choices) != 1 or not isinstance(choices[0]["message"], dict):
                raise ValueError
            message = copy.deepcopy(choices[0]["message"])
        except (KeyError, TypeError, ValueError):
            raise RunStop("policy_error") from None
        message["extra"] = {"response_id": response.get("id"), "actions": []}
        message.setdefault("role", "assistant")
        calls = message.get("tool_calls") or []
        try:
            if len(calls) != 1 or message["role"] != "assistant":
                raise ValueError("Expected exactly one assistant tool call")
            call = calls[0]
            if not isinstance(call.get("id"), str) or not call["id"]:
                raise ValueError("Missing tool-call id")
            function = call.get("function", {})
            native_call = SimpleNamespace(id=call["id"], function=SimpleNamespace(
                name=function.get("name"), arguments=function.get("arguments")))
            actions = parse_toolcall_actions([native_call], format_error_template=FORMAT_ERROR)
            command = actions[0]["command"]
            if not isinstance(command, str) or not command.strip():
                raise ValueError("bash command must be a nonempty string")
            message["extra"]["actions"] = actions
            error = None
        except (FormatError, ValueError, AttributeError, TypeError, KeyError) as exc:
            error = str(exc) or "Invalid bash tool call"
        return {"message": message, "thought": visible_thought(message),
                "action": message["extra"]["actions"][0]["command"] if not error else "",
                "valid_action": error is None, "format_error": error}

    def format_message(self, **kwargs):
        return kwargs

    def format_observation_messages(self, message, outputs, template_vars=None):
        clipped = copy.deepcopy(outputs)
        for output in clipped:
            text = str(output.get("output", ""))
            cap = self.router.cfg.observation_chars
            if len(text) > cap:
                output["output"] = text[:cap // 2] + "\n[tool output truncated]\n" + text[-cap // 2:]
        return format_toolcall_observation_messages(
            actions=message["extra"]["actions"], outputs=clipped,
            observation_template=OBSERVATION, template_vars=template_vars,
        )

    def get_template_vars(self):
        return {"model_name": self.router.cfg.generator}

    def serialize(self):
        return {"info": {"config": {"model": self.router.cfg.generator}}}


class Decisions:
    def __init__(self, router):
        self.router = router

    def choose(self, state, criteria):
        response = self.router.request("selector", {
            "model": self.router.cfg.supervisor, "state": state,
            "questions": {"next_action": {"type": "choice", "instructions": INSTRUCTIONS,
                                          "criteria": criteria}},
            "provider": self.router.routing(),
        })
        try:
            answer = response["answers"]["next_action"]
            probs = answer["probabilities"]
            if answer["type"] != "choice" or answer["choice"] not in criteria or set(probs) != set(criteria):
                raise ValueError
            if any(isinstance(p, bool) or not isinstance(p, (int, float)) or not math.isfinite(p)
                   or not 0 <= p <= 1 for p in probs.values()):
                raise ValueError
            if not math.isclose(sum(probs.values()), 1.0, abs_tol=1e-3):
                raise ValueError
            if probs[answer["choice"]] + 1e-6 < max(probs.values()):
                raise ValueError
            return copy.deepcopy(answer)
        except (KeyError, TypeError, ValueError):
            raise RunStop("selector_error") from None


class SupervisedAgent(DefaultAgent):
    """Three frozen-prefix samples -> one Choice -> Mini executes just that message.

    Single mode follows the same run/execute/save path with one policy candidate.
    No dependency on SWE-Verifier's training, scorers, GPU or deployment code.
    """

    def __init__(self, router, env, *, output_path=None):
        self.router = router
        self.selector = Decisions(router)
        self.decisions = []
        self.pending_action = False
        super().__init__(Policy(router), env, system_template=SYSTEM, instance_template=INSTANCE,
                         step_limit=router.cfg.max_steps, cost_limit=0, output_path=output_path)

    def query(self):
        try:
            self.router.remaining()
            if self.config.step_limit > 0 and self.n_calls >= self.config.step_limit:
                raise RunStop("step_limit")
            self.n_calls += 1
            count = 3 if self.router.cfg.mode == "step_choice" else 1
            # Each query receives its own deep copy of the SAME prefix, never other candidates.
            frozen = copy.deepcopy(self.messages)
            candidates = []
            for index in range(count):
                seed = (self.router.cfg.seed + 1000 * self.n_calls + index + 1
                        if count == 3 else self.router.cfg.seed + self.n_calls - 1)
                candidate = self.model.query(copy.deepcopy(frozen), seed=seed)
                candidates.append(candidate)
                self.router.journal.append({"event": "candidate", "step": self.n_calls,
                                            "index": index, "seed": seed, "candidate": candidate})
            valid = [i for i, candidate in enumerate(candidates) if candidate["valid_action"]]
            if not valid:
                self.router.journal.append({"event": "all_invalid", "step": self.n_calls})
                # The raw invalid candidates stay in sidecar logs. No unmatched tool ids enter history.
                raise FormatError({"role": "user", "content": "Invalid format: return exactly one bash "
                                   "tool call with a nonempty string command.",
                                   "extra": {"cost": 0.0, "interrupt_type": "FormatError"}})
            answer, fallback, chosen = None, None, 0
            if count == 3:
                if len(valid) == 3 and len({candidates[i]["action"].strip() for i in valid}) == 1:
                    chosen = random.Random(self.router.cfg.seed + self.n_calls).choice(valid)
                    fallback = "unanimous_valid_action_random_choice"
                else:
                    criteria = {f"option_{i + 1}": {key: candidate[key] for key in
                                ("thought", "action", "valid_action")} for i, candidate in enumerate(candidates)}
                    state = {"task": self.extra_template_vars["task"], "step": self.n_calls,
                             "shared_prefix": shared_prefix(frozen)}
                    answer = self.selector.choose(state, criteria)
                    chosen = int(answer["choice"].removeprefix("option_")) - 1
                    if chosen not in valid:
                        chosen = max(valid, key=lambda i: answer["probabilities"][f"option_{i + 1}"])
                        fallback = "selector_chose_invalid_branch_fallback_to_highest_prob_valid"
            self.router.remaining()
            decision = {"step": self.n_calls, "selected_branch_id": chosen,
                        "answer": answer, "fallback_reason": fallback}
            self.decisions.append(decision)
            self.router.journal.append({"event": "selection", **decision})
            selected = copy.deepcopy(candidates[chosen]["message"])
            # Preserve actual visible reasoning across providers which do not replay reasoning fields.
            if not selected.get("content") and candidates[chosen]["thought"]:
                selected["content"] = candidates[chosen]["thought"]
            self.add_messages(selected)
            return selected
        except RunStop as exc:
            raise InterruptAgentFlow({"role": "exit", "content": exc.reason,
                                      "extra": {"exit_status": exc.reason, "submission": ""}}) from exc
        finally:
            self.cost = self.router.known_cost

    def execute_actions(self, message):
        self.router.remaining()
        self.pending_action = True
        self.router.journal.append({"event": "action_start", "step": self.n_calls,
                                    "actions": message["extra"]["actions"]})
        self.save(self.config.output_path)
        try:
            result = super().execute_actions(message)
        except InterruptAgentFlow:
            self.pending_action = False
            self.router.journal.append({"event": "action_submitted", "step": self.n_calls})
            raise
        self.pending_action = False
        self.router.journal.append({"event": "action_end", "step": self.n_calls, "messages": result})
        return result

    def serialize(self, *extra_dicts):
        return super().serialize({"supervision": {"mode": self.router.cfg.mode,
                                                  "decisions": self.decisions,
                                                  "usage": self.router.summary(),
                                                  "pending_action": self.pending_action}}, *extra_dicts)
