"""Alpaca CLI integration — `underwriter/cli/` (§17.2, ALP-005, ALP-007).

The third surface. Narrow on purpose: `doctor` for the readiness check, and
`--dry-run` as a deterministic pre-flight that asks a second implementation
what it would send before we send anything.
"""

from underwriter.cli.preflight import (
    DoctorResult,
    PreflightResult,
    check_values,
    doctor,
    is_available,
    validate_order,
)

__all__ = [
    "DoctorResult",
    "PreflightResult",
    "check_values",
    "doctor",
    "is_available",
    "validate_order",
]
