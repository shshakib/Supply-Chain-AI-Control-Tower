from __future__ import annotations

import math
import os
from dataclasses import dataclass, field
from time import monotonic


class RunLimitExceeded(RuntimeError):
    """A run must stop; this error must not be converted into a successful answer."""


@dataclass(frozen=True)
class RunLimits:
    timeout_seconds: float = 180
    request_timeout_seconds: float = 30
    max_model_calls: int = 24
    max_total_tokens: int = 60000
    max_output_tokens: int = 3000
    max_cost_usd: float = 0
    input_usd_per_million: float = 0
    output_usd_per_million: float = 0

    def __post_init__(self) -> None:
        for name in (
            "timeout_seconds",
            "request_timeout_seconds",
            "max_model_calls",
            "max_total_tokens",
            "max_output_tokens",
        ):
            value = getattr(self, name)
            if not math.isfinite(value) or value <= 0:
                raise ValueError(f"{name} must be finite and positive")
        for name in ("max_cost_usd", "input_usd_per_million", "output_usd_per_million"):
            value = getattr(self, name)
            if not math.isfinite(value) or value < 0:
                raise ValueError(f"{name} must be finite and nonnegative")
        if self.max_cost_usd and not (self.input_usd_per_million and self.output_usd_per_million):
            raise ValueError("Dollar budgets require positive input and output USD rates.")

    @classmethod
    def from_env(cls) -> RunLimits:
        defaults = cls()
        values = {}
        for name in cls.__dataclass_fields__:
            raw = os.getenv(f"CONTROL_TOWER_{name.upper()}", "").strip()
            parser = (
                int
                if name in {"max_model_calls", "max_total_tokens", "max_output_tokens"}
                else float
            )
            values[name] = parser(raw) if raw else getattr(defaults, name)
        return cls(**values)


@dataclass
class RunBudget:
    limits: RunLimits
    started_at: float = field(default_factory=monotonic)
    model_calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    stopped_reason: str | None = None

    @property
    def estimated_cost_usd(self) -> float | None:
        if not (self.limits.input_usd_per_million and self.limits.output_usd_per_million):
            return None
        return (
            self.input_tokens * self.limits.input_usd_per_million
            + self.output_tokens * self.limits.output_usd_per_million
        ) / 1_000_000

    def stop(self, reason: str) -> None:
        self.stopped_reason = self.stopped_reason or reason
        raise RunLimitExceeded(self.stopped_reason)

    def check(self) -> None:
        if self.stopped_reason:
            raise RunLimitExceeded(self.stopped_reason)
        if monotonic() - self.started_at >= self.limits.timeout_seconds:
            self.stop("Run time limit reached.")
        if self.input_tokens + self.output_tokens >= self.limits.max_total_tokens:
            self.stop("Run token budget reached.")
        cost = self.estimated_cost_usd
        if self.limits.max_cost_usd and cost is not None and cost >= self.limits.max_cost_usd:
            self.stop("Estimated run cost budget reached.")

    def before_model(self) -> None:
        self.check()
        if self.model_calls >= self.limits.max_model_calls:
            self.stop("Run model-call limit reached.")
        # No await between check and increment: parallel specialists share this counter.
        self.model_calls += 1

    def after_model(self, usage: object) -> None:
        incoming = getattr(usage, "input_tokens", None)
        outgoing = getattr(usage, "output_tokens", None)
        if (
            not isinstance(incoming, int)
            or not isinstance(outgoing, int)
            or min(incoming, outgoing) < 0
        ):
            self.stop("Model usage is unavailable; cannot enforce the run budget.")
        self.input_tokens += incoming
        self.output_tokens += outgoing
        self.check()

    def summary(self) -> dict[str, object]:
        return {
            "model_calls": self.model_calls,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "estimated_cost_usd": self.estimated_cost_usd,
            "elapsed_seconds": round(monotonic() - self.started_at, 2),
            "stopped_reason": self.stopped_reason,
        }
