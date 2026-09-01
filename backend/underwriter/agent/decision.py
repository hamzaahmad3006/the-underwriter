"""The `UnderwriterDecision` schema — §13.3, normative.

Two layers, deliberately:

* the **wire schema** below is what Groq enforces with `strict: true`. Strict
  mode demands that every property appear in `required` and that
  `additionalProperties` be false, so the five conditionally-present fields are
  declared nullable rather than optional.
* the **semantic checks** are everything strict mode cannot express — that a
  WRITE actually carries a size and a confidence, and that the candidate is one
  we offered. Conditional presence lives here, never in the JSON schema.

Both run on every response. The wire format is never trusted (SEC-010), so a
model that returns valid-looking JSON still has to survive the second layer.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

MAX_RATIONALE = 600
MAX_RISK_ITEM = 200
MAX_DECLINE_REASON = 300
MAX_RISKS = 5

# SEC-011: an instruction that reaches us inside a *model's own output* is
# either an injection that worked upstream or a model going off-script. Either
# way the rationale is display text, and display text has no imperatives,
# no URLs and no tool-call syntax in it.
FORBIDDEN_IN_RATIONALE = (
    re.compile(r"https?://", re.I),
    re.compile(r"\bignore (all |previous|prior)\b", re.I),
    re.compile(r"\bsystem\s*:", re.I),
    re.compile(r"<\s*/?\s*(tool|function|script)", re.I),
    re.compile(r"\{\{.*?\}\}", re.S),
    re.compile(
        r"\b(execute|submit|place|liquidate|override|disregard)\s+(the\s+)?(order|trade|book|rule)",
        re.I,
    ),
)


class UnderwriterDecision(BaseModel):
    """One decision, after schema validation but before semantic validation."""

    model_config = ConfigDict(extra="forbid")

    action: Literal["WRITE", "DECLINE"]
    candidate_id: str | None = None
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    contracts: int | None = Field(default=None, ge=1, le=20)
    rationale: str = Field(max_length=MAX_RATIONALE)
    identified_risks: list[str] | None = None
    declined_reason: str | None = None

    @property
    def is_write(self) -> bool:
        return self.action == "WRITE"

    @property
    def confidence_decimal(self) -> Decimal | None:
        """Confidence as Decimal for persistence (NFR-013)."""
        return None if self.confidence is None else Decimal(str(self.confidence))


def wire_schema() -> dict[str, Any]:
    """The JSON schema handed to Groq with `strict: true`.

    Every property is in `required` and the optional ones are nullable, which
    is what strict mode demands. Length limits are absent on purpose: strict
    mode's keyword support is narrow, and Pydantic enforces them a moment later
    anyway.
    """
    return {
        "type": "object",
        "additionalProperties": False,
        "required": [
            "action",
            "candidate_id",
            "confidence",
            "contracts",
            "rationale",
            "identified_risks",
            "declined_reason",
        ],
        "properties": {
            "action": {"type": "string", "enum": ["WRITE", "DECLINE"]},
            "candidate_id": {"type": ["string", "null"]},
            "confidence": {"type": ["number", "null"]},
            "contracts": {"type": ["integer", "null"]},
            "rationale": {"type": "string"},
            "identified_risks": {"type": ["array", "null"], "items": {"type": "string"}},
            "declined_reason": {"type": ["string", "null"]},
        },
    }


@dataclass(frozen=True, slots=True)
class SemanticFailure:
    """Why a schema-valid decision is still unusable."""

    code: str
    detail: str


def validate_semantics(
    decision: UnderwriterDecision, *, supplied_candidate_ids: frozenset[str]
) -> SemanticFailure | None:
    """§13.3's post-schema checks. Returns the first failure, or None.

    The membership check is the one that matters most: it is what catches a
    hallucinated or injected instrument (F-09), and SK-024 checks it again
    independently because one guard on the system's central claim is not enough.
    """
    if decision.is_write:
        if not decision.candidate_id:
            return SemanticFailure("WRITE_WITHOUT_CANDIDATE", "action=WRITE with no candidate_id")
        if decision.candidate_id not in supplied_candidate_ids:
            return SemanticFailure(
                "CANDIDATE_NOT_SUPPLIED",
                f"candidate_id={decision.candidate_id!r} was never offered to the model",
            )
        if decision.confidence is None:
            return SemanticFailure("WRITE_WITHOUT_CONFIDENCE", "action=WRITE with no confidence")
        if decision.contracts is None:
            return SemanticFailure("WRITE_WITHOUT_CONTRACTS", "action=WRITE with no contracts")
        if not decision.identified_risks:
            return SemanticFailure(
                "WRITE_WITHOUT_RISKS", "action=WRITE must identify at least one risk"
            )
        if len(decision.identified_risks) > MAX_RISKS:
            return SemanticFailure(
                "TOO_MANY_RISKS", f"{len(decision.identified_risks)} risks, maximum {MAX_RISKS}"
            )
        for risk in decision.identified_risks:
            if len(risk) > MAX_RISK_ITEM:
                return SemanticFailure("RISK_TOO_LONG", f"risk item exceeds {MAX_RISK_ITEM} chars")
    else:
        if not decision.declined_reason:
            return SemanticFailure(
                "DECLINE_WITHOUT_REASON", "action=DECLINE must state declined_reason"
            )
        if len(decision.declined_reason) > MAX_DECLINE_REASON:
            return SemanticFailure(
                "DECLINE_REASON_TOO_LONG", f"declined_reason exceeds {MAX_DECLINE_REASON} chars"
            )
        # A decline that names a candidate must still name a real one.
        if decision.candidate_id and decision.candidate_id not in supplied_candidate_ids:
            return SemanticFailure(
                "CANDIDATE_NOT_SUPPLIED",
                f"candidate_id={decision.candidate_id!r} was never offered to the model",
            )

    for pattern in FORBIDDEN_IN_RATIONALE:
        if pattern.search(decision.rationale):
            return SemanticFailure(
                "RATIONALE_CONTAINS_INSTRUCTIONS",
                f"rationale matched {pattern.pattern!r} (SEC-011)",
            )

    return None
