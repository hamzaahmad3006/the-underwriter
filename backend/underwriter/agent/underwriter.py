"""The underwriting call, start to finish — §11.3.

One LLM call per decision (TD-04). No debate, no committee, no chained agents:
every extra agent is another schema, another retry path and another latency
budget for no judged credit, and risk logic inside a model is unauditable by
construction.

The outcome of this module is never a trade. It is a *proposal*, which the
Kernel then adjudicates. Four things can happen and all four are valid:

* `WRITE`      — a candidate was selected, and the Kernel gets to decide
* `DECLINE`    — the model declined, which FR-026 calls a successful cycle
* `NO_CANDIDATES` — the Actuary found nothing; no LLM call is made at all
* `ABORTED`    — schema violation or provider failure; **no trade** (FR-041, FR-047)
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from pydantic import ValidationError

from underwriter.agent.client import LLMClient, LLMResponse, LLMUnavailableError
from underwriter.agent.decision import (
    SemanticFailure,
    UnderwriterDecision,
    validate_semantics,
)
from underwriter.agent.prompt import (
    PortfolioContext,
    build_user_message,
    load_system_prompt,
)
from underwriter.domain.proposal import UnderwritingProposal

MAX_SCHEMA_ATTEMPTS = 2  # FR-041: one retry, then abort

RETRY_NUDGE = (
    "\n\n# Your previous response was rejected\n\n"
    "{reason}\n\n"
    "Return only an object matching the schema. If in doubt, DECLINE with a "
    "declined_reason — that is always an acceptable answer."
)


class Outcome(StrEnum):
    WRITE = "WRITE"
    DECLINE = "DECLINE"
    NO_CANDIDATES = "NO_CANDIDATES"
    ABORTED = "ABORTED"


class AbortReason(StrEnum):
    SCHEMA_VIOLATION = "LLM_SCHEMA_VIOLATION"
    PROVIDER_UNAVAILABLE = "LLM_UNAVAILABLE"


@dataclass(frozen=True, slots=True)
class UnderwritingOutcome:
    """One cycle's underwriting result, with its whole audit trail (FR-043)."""

    outcome: Outcome
    decision: UnderwriterDecision | None = None
    selected: UnderwritingProposal | None = None
    abort_reason: AbortReason | None = None
    detail: str = ""

    prompt_sha256: str = ""
    prompt_version: str = ""
    model: str | None = None
    model_version: str | None = None
    temperature: float | None = None
    prompt_tokens: int = 0
    completion_tokens: int = 0
    latency_ms: int = 0
    retry_count: int = 0
    raw_response: str = ""
    used_fallback: bool = False
    as_of: datetime = datetime(1970, 1, 1, tzinfo=UTC)

    @property
    def is_write(self) -> bool:
        return self.outcome is Outcome.WRITE

    @property
    def traded_nothing(self) -> bool:
        """True for every outcome except WRITE. All of them are valid."""
        return self.outcome is not Outcome.WRITE


class AIUnderwriter:
    """Selects among pre-priced candidates. Computes nothing, executes nothing."""

    def __init__(self, client: LLMClient, *, prompt_file: str | None = None) -> None:
        self._client = client
        self._prompt = load_system_prompt(prompt_file) if prompt_file else load_system_prompt()

    def decide(
        self,
        proposals: tuple[UnderwritingProposal, ...],
        context: PortfolioContext,
    ) -> UnderwritingOutcome:
        """Run one underwriting decision. Never raises into the cycle."""
        now = datetime.now(UTC)

        # FR-026: no candidates is a successful cycle, and there is nothing to
        # ask a model about. Spending a call to be told so would be theatre.
        if not proposals:
            return UnderwritingOutcome(
                outcome=Outcome.NO_CANDIDATES,
                detail="the Actuary produced no qualifying candidates",
                prompt_sha256=self._prompt.sha256,
                prompt_version=self._prompt.version,
                as_of=now,
            )

        supplied_ids = frozenset(p.candidate_id for p in proposals)
        by_id = {p.candidate_id: p for p in proposals}
        user_message = build_user_message(proposals, context)

        last_failure = ""
        response: LLMResponse | None = None

        for attempt in range(MAX_SCHEMA_ATTEMPTS):
            message = (
                user_message
                if attempt == 0
                else user_message + RETRY_NUDGE.format(reason=last_failure)
            )

            try:
                response = self._client.complete(system=self._prompt.text, user=message)
            except LLMUnavailableError as exc:
                # FR-047: no rule-based fallback writes a policy. The cycle ends.
                return self._aborted(
                    AbortReason.PROVIDER_UNAVAILABLE, str(exc), attempt, now, response
                )

            parsed, failure = self._parse(response.content, supplied_ids)
            if parsed is not None:
                selected = by_id.get(parsed.candidate_id or "") if parsed.is_write else None
                return UnderwritingOutcome(
                    outcome=Outcome.WRITE if parsed.is_write else Outcome.DECLINE,
                    decision=parsed,
                    selected=selected,
                    detail=parsed.rationale,
                    retry_count=attempt,
                    as_of=now,
                    **self._telemetry(response, self._prompt.sha256, self._prompt.version),
                )

            last_failure = failure

        return self._aborted(AbortReason.SCHEMA_VIOLATION, last_failure, 1, now, response)

    def _parse(
        self, content: str, supplied_ids: frozenset[str]
    ) -> tuple[UnderwriterDecision | None, str]:
        """Schema, then semantics. The wire format is never trusted (SEC-010)."""
        try:
            payload: Any = json.loads(content)
        except json.JSONDecodeError as exc:
            return None, f"response was not JSON: {exc}"

        try:
            decision = UnderwriterDecision.model_validate(payload)
        except ValidationError as exc:
            return None, f"schema validation failed: {exc.errors(include_url=False)}"

        failure: SemanticFailure | None = validate_semantics(
            decision, supplied_candidate_ids=supplied_ids
        )
        if failure is not None:
            return None, f"{failure.code}: {failure.detail}"

        return decision, ""

    @staticmethod
    def _telemetry(response: LLMResponse, sha: str, version: str) -> dict[str, Any]:
        return {
            "prompt_sha256": sha,
            "prompt_version": version,
            "model": response.model,
            "model_version": response.model_version,
            "temperature": response.temperature,
            "prompt_tokens": response.prompt_tokens,
            "completion_tokens": response.completion_tokens,
            "latency_ms": response.latency_ms,
            "raw_response": response.content,
            "used_fallback": response.used_fallback,
        }

    def _aborted(
        self,
        reason: AbortReason,
        detail: str,
        retries: int,
        now: datetime,
        response: LLMResponse | None,
    ) -> UnderwritingOutcome:
        """An abort still records everything it learned. No trade either way."""
        telemetry: dict[str, Any] = (
            self._telemetry(response, self._prompt.sha256, self._prompt.version)
            if response is not None
            else {"prompt_sha256": self._prompt.sha256, "prompt_version": self._prompt.version}
        )
        return UnderwritingOutcome(
            outcome=Outcome.ABORTED,
            abort_reason=reason,
            detail=detail,
            retry_count=retries,
            as_of=now,
            **telemetry,
        )
