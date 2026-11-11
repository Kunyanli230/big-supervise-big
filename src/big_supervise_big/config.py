"""Explicit hosted-model configuration; no legacy Ollama/D3 deployment settings."""

import json
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

SOURCE_SHA = "7e8a2e3884e6c5d1417efd49adc8638c5f00dcd3"
V2_SHA = "66f92766bba642462d4bbe5479e83f91f9211862"


class Config(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    mode: Literal["single", "step_choice"] = "step_choice"
    generator: str = "openai/gpt-6-luna"
    supervisor: str = "openai/gpt-6-luna-decisions"
    api_key_env: str = "OPENROUTER_API_KEY"
    provider: str = "OpenAI"
    reasoning_effort: Literal["low", "medium", "high", "xhigh"] = "high"
    seed: int = 2026110500
    max_output_tokens: int = Field(default=16384, ge=1)
    context_limit: int = Field(default=1050000, ge=8192)
    max_steps: int = Field(default=0, ge=0)
    wall_seconds: int = Field(default=3000, ge=1)
    command_seconds: int = Field(default=120, ge=1)
    verifier_seconds: int = Field(default=1800, ge=1)
    policy_timeout: int = Field(default=180, ge=1)
    decision_timeout: int = Field(default=60, ge=1)
    max_retries: int = Field(default=2, ge=0, le=2)
    budget_usd: float = Field(default=3.0, gt=0, allow_inf_nan=False)
    # Uniform, deterministic tool-output clipping, never an LLM-generated summary.
    observation_chars: int = Field(default=30000, ge=1000)

    @model_validator(mode="after")
    def same_family(self):
        if self.supervisor != self.generator + "-decisions":
            raise ValueError("supervisor must be the generator's exact '-decisions' counterpart")
        if self.max_output_tokens + 8192 >= self.context_limit:
            raise ValueError("context_limit must leave room for input and output")
        if not self.provider.strip() or not self.api_key_env.isidentifier():
            raise ValueError("provider and an environment-variable name are required")
        return self

    @classmethod
    def load(cls, path: Path):
        return cls.model_validate_json(path.read_text(encoding="utf-8"))

    def snapshot(self):
        return json.loads(self.model_dump_json())
