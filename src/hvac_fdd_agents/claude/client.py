"""One structured-output call to Claude, with refusal fallback, caching, and usage tracking.

Agents call `LLM.structured(...)` and get back a validated Pydantic object. Tests pass a
fake with the same method, so nothing in the graph depends on the network.
"""

from dataclasses import dataclass, field
from typing import Protocol, TypeVar

import anthropic
from pydantic import BaseModel

DEFAULT_MODEL = "claude-opus-5-5"
FALLBACK_BETA = "server-side-fallback-2026-07-01"

# USD per million tokens (Claude Opus 5.5, Anthropic API, as of 2026-09).
PRICES = {"input": 4.00, "output": 20.00, "cache_read": 0.20, "cache_write": 5.00}

T = TypeVar("T", bound=BaseModel)


@dataclass
class Usage:
    calls: int = 0
    input: int = 0
    output: int = 0
    cache_read: int = 0
    cache_write: int = 0

    def add(self, u) -> None:
        self.calls += 1
        self.input += u.input_tokens or 0
        self.output += u.output_tokens or 0
        self.cache_read += getattr(u, "cache_read_input_tokens", 0) or 0
        self.cache_write += getattr(u, "cache_creation_input_tokens", 0) or 0

    def merge(self, other: "Usage") -> None:
        for k in ("calls", "input", "output", "cache_read", "cache_write"):
            setattr(self, k, getattr(self, k) + getattr(other, k))

    @property
    def cost_usd(self) -> float:
        return sum(getattr(self, k) * PRICES[k] for k in PRICES) / 1e6

    def as_dict(self) -> dict:
        return {"calls": self.calls, "input_tokens": self.input, "output_tokens": self.output,
                "cache_read_tokens": self.cache_read, "cache_write_tokens": self.cache_write,
                "est_cost_usd": round(self.cost_usd, 4)}


class Refused(RuntimeError):
    """The model (and its fallback) declined the request."""


class StructuredLLM(Protocol):
    def structured(self, system: str, user: str, schema: type[T], usage: Usage) -> T: ...


@dataclass
class LLM:
    model: str = DEFAULT_MODEL
    effort: str = "medium"
    max_tokens: int = 64000
    client: anthropic.Anthropic = field(default_factory=anthropic.Anthropic)

    def structured(self, system: str, user: str, schema: type[T], usage: Usage) -> T:
        # Stable system prompt first, cached: parallel taggers share it.
        with self.client.beta.messages.stream(
            model=self.model,
            max_tokens=self.max_tokens,
            system=[{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
            messages=[{"role": "user", "content": user}],
            output_config={"effort": self.effort},
            output_format=schema,
            betas=[FALLBACK_BETA],
            fallbacks="default",
        ) as stream:
            message = stream.get_final_message()
        usage.add(message.usage)
        if message.stop_reason == "refusal":
            details = getattr(message, "stop_details", None)
            raise Refused(f"request declined ({getattr(details, 'category', None)})")
        if message.stop_reason == "max_tokens":
            raise RuntimeError(f"response hit max_tokens={self.max_tokens}; raise it or split the work")
        return message.parsed_output
