"""Alpaca CLI integration — ALP-005, ALP-007, §17.2.

The third surface, and the one with the narrowest job. The CLI is documented as
built for agents and pipelines: no confirmation prompts, JSON output, and a
`--dry-run` that prints the request body without submitting it.

Two uses, both deliberately small:

* **`doctor`** answers "is this machine configured to trade at all?" for
  `/health/deep` — profile, credentials, connectivity, in one call.
* **`--dry-run`** is a deterministic pre-flight. It hands our constructed
  payload to a *second, independent implementation* and asks what it would
  send. A leg combination the CLI refuses is one the broker would refuse, and
  finding that locally costs milliseconds instead of a rejected order.

What `--dry-run` is not, tested rather than assumed: it does **not** validate
values. Handed `position_intent: "not_a_real_intent"` it renders it back
unchanged. So the CLI round-trip proves the two implementations agree on what
would be sent, and nothing about whether it is legal.

The value check is therefore ours, against the sets ALP-012 and ALP-015
document. Together the two halves cover different failures: our check catches
an illegal value, the round-trip catches a payload the CLI would reshape.
Neither reaches Alpaca, so neither is a broker-side validation — ALP-007
permits this or the REST path, and claiming server validation we do not perform
would be the wrong kind of confidence.

ALP-007 makes the *response* mandatory even though the check is optional: if
the pre-flight fails, the order is not transmitted.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

CLI_TIMEOUT_SEC = 20
TOOLS_DIR = Path(__file__).resolve().parents[2] / ".tools"

# Fields whose disagreement means we would transmit something other than what
# we adjudicated. Anything the CLI adds of its own (advanced_instructions) is
# not our concern.
COMPARED_FIELDS = (
    "order_class",
    "qty",
    "type",
    "time_in_force",
    "limit_price",
    "client_order_id",
)

# The documented legal values. ALP-012 for intents, ALP-015 for the rest.
LEGAL_INTENTS = frozenset({"buy_to_open", "sell_to_open", "buy_to_close", "sell_to_close"})
LEGAL_SIDES = frozenset({"buy", "sell"})
LEGAL_TIF = frozenset({"day", "gtc"})
LEGAL_TYPES = frozenset({"limit"})  # FR-082: entries are never market orders


@dataclass(frozen=True, slots=True)
class PreflightResult:
    """ALP-007's answer. `ok` false means the order is not transmitted."""

    ok: bool
    detail: str
    differences: tuple[str, ...] = field(default_factory=tuple)
    rendered: dict[str, Any] | None = None
    skipped: bool = False

    @property
    def blocks_execution(self) -> bool:
        """A skipped check never blocks; a failed one always does."""
        return not self.ok and not self.skipped


@dataclass(frozen=True, slots=True)
class DoctorResult:
    ok: bool
    detail: str
    paper: bool = False


def cli_command() -> str | None:
    """Where the CLI binary is, if it is here at all.

    Checked in `.tools/` first because that is where the Makefile puts it: the
    CLI ships as a GitHub release archive rather than a package, so there is no
    dependency manager to ask.
    """
    explicit = os.environ.get("ALPACA_CLI_COMMAND", "").strip()
    if explicit:
        return explicit

    for name in ("alpaca.exe", "alpaca"):
        candidate = TOOLS_DIR / name
        if candidate.exists():
            return str(candidate)

    return shutil.which("alpaca")


def is_available() -> bool:
    return cli_command() is not None


def _run(args: list[str]) -> subprocess.CompletedProcess[str]:
    command = cli_command()
    if command is None:
        raise FileNotFoundError("the Alpaca CLI is not installed")

    return subprocess.run(  # noqa: S603 - fixed binary, arguments built from our own payload
        [command, *args],
        capture_output=True,
        text=True,
        timeout=CLI_TIMEOUT_SEC,
        check=False,
        env={**os.environ, "ALPACA_PAPER_TRADE": "true"},
    )


def doctor() -> DoctorResult:
    """ALP-005 — configuration and connectivity in one call."""
    try:
        completed = _run(["doctor"])
    except FileNotFoundError as exc:
        return DoctorResult(False, str(exc))
    except subprocess.TimeoutExpired:
        return DoctorResult(False, f"alpaca doctor timed out after {CLI_TIMEOUT_SEC}s")

    output = f"{completed.stdout}\n{completed.stderr}"
    connected = "trading API: connected" in output
    paper = "active profile: paper" in output or "paper-api" in output

    if not paper:
        # Louder than a failed connection: a live profile is a configuration
        # error this system must never run against (SEC-004).
        return DoctorResult(False, "the CLI is not on a paper profile", paper=False)

    return DoctorResult(
        ok=connected,
        detail="paper profile, trading API reachable"
        if connected
        else "paper profile, but the trading API is unreachable",
        paper=True,
    )


def _args_from(payload: dict[str, Any]) -> list[str]:
    """Turn our order payload into CLI flags, unchanged."""
    return [
        "order",
        "submit",
        "--order-class",
        str(payload["order_class"]),
        "--qty",
        str(payload["qty"]),
        "--type",
        str(payload["type"]),
        "--time-in-force",
        str(payload["time_in_force"]),
        "--limit-price",
        str(payload["limit_price"]),
        "--client-order-id",
        str(payload["client_order_id"]),
        "--legs",
        json.dumps(payload["legs"]),
        "--dry-run",
    ]


def _compare(payload: dict[str, Any], rendered: dict[str, Any]) -> tuple[str, ...]:
    """Anything that would make us transmit other than what we adjudicated."""
    differences: list[str] = []

    for name in COMPARED_FIELDS:
        ours, theirs = str(payload.get(name)), str(rendered.get(name))
        if ours != theirs:
            differences.append(f"{name}: ours={ours!r} cli={theirs!r}")

    our_legs = payload.get("legs") or []
    their_legs = rendered.get("legs") or []

    if len(our_legs) != len(their_legs):
        differences.append(f"legs: ours={len(our_legs)} cli={len(their_legs)}")
        return tuple(differences)

    for index, (ours_leg, theirs_leg) in enumerate(zip(our_legs, their_legs, strict=True)):
        for key in ("symbol", "side", "ratio_qty", "position_intent"):
            if str(ours_leg.get(key)) != str(theirs_leg.get(key)):
                differences.append(
                    f"leg[{index}].{key}: ours={ours_leg.get(key)!r} cli={theirs_leg.get(key)!r}"
                )

    return tuple(differences)


def check_values(payload: dict[str, Any]) -> tuple[str, ...]:
    """Our half of the pre-flight: are the values legal at all?

    The CLI will not do this — it renders an invalid `position_intent` back
    unchanged — so an illegal enum would otherwise travel all the way to the
    broker before anything objected.
    """
    problems: list[str] = []

    if payload.get("order_class") != "mleg":
        problems.append(f"order_class={payload.get('order_class')!r}, expected 'mleg' (ALP-010)")
    if payload.get("type") not in LEGAL_TYPES:
        problems.append(f"type={payload.get('type')!r} is not a limit order (FR-082)")
    if payload.get("time_in_force") not in LEGAL_TIF:
        problems.append(f"time_in_force={payload.get('time_in_force')!r} (ALP-015)")

    try:
        if int(payload.get("qty", 0)) < 1:
            problems.append(f"qty={payload.get('qty')!r} is not a whole positive number")
    except (TypeError, ValueError):
        problems.append(f"qty={payload.get('qty')!r} is not an integer")

    legs = payload.get("legs") or []
    if len(legs) < 2:
        problems.append(f"{len(legs)} leg(s); an mleg needs every short leg covered (ALP-014)")

    for index, leg in enumerate(legs):
        if leg.get("position_intent") not in LEGAL_INTENTS:
            problems.append(
                f"leg[{index}].position_intent={leg.get('position_intent')!r} (ALP-012)"
            )
        if leg.get("side") not in LEGAL_SIDES:
            problems.append(f"leg[{index}].side={leg.get('side')!r}")
        if not str(leg.get("symbol") or "").strip():
            problems.append(f"leg[{index}] has no symbol")

    return tuple(problems)


def validate_order(payload: dict[str, Any]) -> PreflightResult:
    """ALP-007 — hand the payload to a second implementation and compare.

    A missing CLI skips the check rather than failing it: the pre-flight is a
    SHOULD, and refusing to trade because an optional tool is absent would be
    the wrong reading. A CLI that *is* present and disagrees blocks the order.
    """
    # Our own value check runs first and runs always. It needs no binary, and
    # an illegal enum is worth catching whether or not the CLI is installed.
    illegal = check_values(payload)
    if illegal:
        return PreflightResult(
            False,
            "the order contains values Alpaca does not accept",
            differences=illegal,
        )

    if not is_available():
        return PreflightResult(
            ok=True,
            skipped=True,
            detail=(
                "values are legal; the Alpaca CLI is not installed, so the "
                "round-trip was skipped (ALP-007 is a SHOULD)"
            ),
        )

    try:
        completed = _run(_args_from(payload))
    except subprocess.TimeoutExpired:
        return PreflightResult(False, f"pre-flight timed out after {CLI_TIMEOUT_SEC}s")
    except Exception as exc:
        return PreflightResult(False, f"pre-flight could not run: {type(exc).__name__}: {exc}")

    if completed.returncode != 0:
        # The CLI refused the flags. A combination it will not accept is one
        # the broker would reject, found for the cost of a subprocess.
        return PreflightResult(
            False, f"the CLI rejected the order: {completed.stderr.strip()[:300]}"
        )

    try:
        rendered = json.loads(completed.stdout)
    except json.JSONDecodeError:
        return PreflightResult(
            False, f"the CLI produced no parseable request body: {completed.stdout[:200]}"
        )

    differences = _compare(payload, rendered)
    if differences:
        return PreflightResult(
            False,
            "the CLI would transmit something other than what was adjudicated",
            differences=differences,
            rendered=rendered,
        )

    return PreflightResult(
        True,
        "values are legal and the CLI renders the same order that was adjudicated",
        rendered=rendered,
    )
