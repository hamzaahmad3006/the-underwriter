"""The Groq call — TD-13, F-29, FR-043, FR-047.

One call per cycle, structured output enforced, and everything FR-043 asks for
recorded on the way back: model, version, temperature, token counts, latency
and the raw response.

`LLMClient` is a protocol so the whole underwriting path can be tested without
a network or a key. `GroqClient` is the only implementation that imports groq.
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass, field
from typing import Any, Protocol

from underwriter.agent.decision import wire_schema

DEFAULT_MODEL = "openai/gpt-oss-120b"
DEFAULT_FALLBACK = "openai/gpt-oss-20b"
DEFAULT_TEMPERATURE = 0.2  # FR-045 requires <= 0.3
DEFAULT_TIMEOUT_SEC = 45.0
SCHEMA_NAME = "UnderwriterDecision"

# The SDK reads GROQ_BASE_URL itself and only falls back to its own default
# when the variable is *absent*. An empty variable is therefore used verbatim
# as the base URL, which fails every call — so the resolution happens here
# where "empty" and "unset" can mean the same thing. The path segment is the
# SDK's to add: see ASM-006 for what happens when it is added twice.
GROQ_DEFAULT_BASE_URL = "https://api.groq.com"


class LLMUnavailableError(RuntimeError):
    """The provider could not be reached, or refused every model tried."""


@dataclass(frozen=True, slots=True)
class LLMResponse:
    """One completion, with everything FR-043 requires recorded."""

    content: str
    model: str
    model_version: str
    temperature: float
    prompt_tokens: int
    completion_tokens: int
    latency_ms: int
    used_fallback: bool = False
    notes: tuple[str, ...] = field(default_factory=tuple)


class LLMClient(Protocol):
    """What the underwriting path needs from a language model."""

    def complete(self, *, system: str, user: str) -> LLMResponse:
        """One structured-output completion, or raise `LLMUnavailableError`."""
        ...


class GroqClient:
    """Groq over its OpenAI-compatible API.

    F-29 lives here: if the configured model has been retired, the call falls
    back to the secondary id, records that it did, and continues. What it never
    does is invent a decision — FR-047 forbids any rule-based fallback that
    writes a policy without an underwriting decision, so exhausting both models
    aborts the cycle rather than trading on a default.
    """

    def __init__(
        self,
        *,
        api_key: str | None = None,
        model: str | None = None,
        fallback_model: str | None = None,
        temperature: float = DEFAULT_TEMPERATURE,
        timeout_sec: float = DEFAULT_TIMEOUT_SEC,
        max_retries: int = 2,
    ) -> None:
        key = api_key or os.environ.get("GROQ_API_KEY", "").strip()
        if not key:
            raise LLMUnavailableError("GROQ_API_KEY is not configured")

        if temperature > 0.3:
            raise ValueError(f"temperature {temperature} exceeds the FR-045 ceiling of 0.3")

        from groq import Groq

        # Resolved here rather than left to the SDK — see GROQ_DEFAULT_BASE_URL.
        base_url = os.environ.get("GROQ_BASE_URL", "").strip() or GROQ_DEFAULT_BASE_URL
        self._client = Groq(
            api_key=key, base_url=base_url, timeout=timeout_sec, max_retries=max_retries
        )
        self._model = model or os.environ.get("GROQ_MODEL", "").strip() or DEFAULT_MODEL
        self._fallback = (
            fallback_model or os.environ.get("GROQ_MODEL_FALLBACK", "").strip() or DEFAULT_FALLBACK
        )
        self._temperature = temperature

    def complete(self, *, system: str, user: str) -> LLMResponse:
        notes: list[str] = []
        last_error: Exception | None = None

        for attempt, model in enumerate((self._model, self._fallback)):
            if attempt == 1:
                notes.append(f"primary model {self._model!r} unusable: {last_error}")
            try:
                return self._call(model, system, user, used_fallback=attempt == 1, notes=notes)
            except Exception as exc:
                if self._is_model_gone(exc):
                    last_error = exc
                    continue
                raise LLMUnavailableError(f"{type(exc).__name__}: {exc}") from exc

        raise LLMUnavailableError(
            f"neither {self._model!r} nor {self._fallback!r} is available: {last_error}"
        )

    @staticmethod
    def _is_model_gone(exc: Exception) -> bool:
        """F-29's detection: a retired or inaccessible model id."""
        message = str(exc).lower()
        return "model_not_found" in message or "does not exist" in message

    def _call(
        self, model: str, system: str, user: str, *, used_fallback: bool, notes: list[str]
    ) -> LLMResponse:
        started = time.monotonic()
        response: Any = self._client.chat.completions.create(
            model=model,
            temperature=self._temperature,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            response_format={
                "type": "json_schema",
                "json_schema": {
                    "name": SCHEMA_NAME,
                    "schema": wire_schema(),
                    "strict": True,
                },
            },
        )
        latency_ms = int((time.monotonic() - started) * 1000)
        usage = getattr(response, "usage", None)

        return LLMResponse(
            content=response.choices[0].message.content or "",
            model=model,
            model_version=getattr(response, "model", model),
            temperature=self._temperature,
            prompt_tokens=int(getattr(usage, "prompt_tokens", 0) or 0),
            completion_tokens=int(getattr(usage, "completion_tokens", 0) or 0),
            latency_ms=latency_ms,
            used_fallback=used_fallback,
            notes=tuple(notes),
        )
