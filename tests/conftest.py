import json

import httpx
import pytest

from big_supervise_big.config import Config
from big_supervise_big.router import Router
from big_supervise_big.storage import Journal


def policy(command="echo ok", *, call_id="call-1", content="visible text", **message_updates):
    message = {"role": "assistant", "content": content, "tool_calls": [{"id": call_id,
               "type": "function", "function": {"name": "bash", "arguments": json.dumps({"command": command})}}]}
    message.update(message_updates)
    return {"model": "openai/gpt-6-luna", "provider": "OpenAI", "id": call_id,
            "choices": [{"message": message}], "usage": {"cost": 0.001, "prompt_tokens": 10,
                                                                       "completion_tokens": 5}}


def decision(choice="option_2", probs=None):
    return {"model": "openai/gpt-6-luna-decisions", "provider": "OpenAI", "id": "decision-id",
            "answers": {"next_action": {"type": "choice", "choice": choice,
                                        "probabilities": probs or {"option_1": 0.1, "option_2": 0.8,
                                                                   "option_3": 0.1}}},
            "usage": {"cost": 0.001, "input_tokens": 25, "output_tokens": 0}}


@pytest.fixture
def router_factory(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-secret-not-for-publication")
    def factory(responses=(), **config):
        cfg = Config(**config)
        catalog = {model: {"context_length": 1050000, "pricing": {"prompt": "0.0000001",
                   "completion": "0.0000005"}} for model in (cfg.generator, cfg.supervisor)}
        queue, requests = list(responses), []
        def handler(request):
            requests.append((str(request.url), json.loads(request.content)))
            response = queue.pop(0)
            if isinstance(response, Exception):
                raise response
            if isinstance(response, tuple):
                return httpx.Response(response[0], json=response[1])
            return httpx.Response(200, json=response)
        client = httpx.Client(transport=httpx.MockTransport(handler))
        journal = Journal(tmp_path / f"events-{len(list(tmp_path.glob('events-*')))}.jsonl")
        router = Router(cfg, journal, catalog=catalog, client=client, sleep=lambda _: None)
        router.requests = requests
        return router
    return factory


class FakeEnvironment:
    def __init__(self):
        self.commands = []

    def execute(self, action):
        from minisweagent.environments.docker import DockerEnvironment
        command = action["command"]
        self.commands.append(command)
        output = {"returncode": 0, "output": "observation:" + command, "exception_info": ""}
        if command == "submit":
            output["output"] = "COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT\nverified-patch\n"
        DockerEnvironment._check_finished(self, output)
        return output

    def get_template_vars(self):
        return {"cwd": "/app"}

    def serialize(self):
        return {"info": {"config": {"environment": "offline-test"}}}
