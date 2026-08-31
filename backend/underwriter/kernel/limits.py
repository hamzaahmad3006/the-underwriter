"""Kernel limits — the numbers the firm's survival is defined by (§14.3).

Every percentage is of current account equity (NAV), which is what makes
ASM-005 non-binding: the paper account's starting balance can be anything.

CFG-001: changing any value here is an audited operator action. These defaults
mirror `config/underwriter.yaml`; the config file is authoritative at runtime.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal


@dataclass(frozen=True, slots=True)
class KernelLimits:
    # capital
    max_deployed_pct: Decimal = Decimal("0.60")  # SK-001
    max_position_loss_pct: Decimal = Decimal("0.03")  # SK-003
    max_open_policies: int = 8  # SK-005
    max_underlying_concentration: Decimal = Decimal("0.25")  # SK-006
    max_portfolio_risk_pct: Decimal = Decimal("0.15")  # SK-007

    # greeks (soft)
    max_net_delta_per_10k: Decimal = Decimal("15")  # SK-008
    max_vega_per_100k: Decimal = Decimal("50")  # SK-009
    soft_factor: Decimal = Decimal("0.5")  # §14.2 multiplicative reduction

    # loss control
    max_daily_loss_pct: Decimal = Decimal("0.03")  # SK-010
    max_drawdown_pct: Decimal = Decimal("0.10")  # SK-022

    # structure and quality
    min_dte_at_entry: int = 7  # SK-011
    min_liquidity_score: Decimal = Decimal("0.55")  # SK-014
    max_bid_ask_pct: Decimal = Decimal("0.15")  # SK-015
    min_edge_ratio: Decimal = Decimal("0.05")  # SK-016

    # timing
    blackout_open_min: int = 15  # SK-017
    blackout_close_min: int = 30  # SK-017
    max_data_age_sec: int = 120  # SK-018
    verdict_ttl_sec: int = 45  # FR-064

    # sizing
    calibration_multiplier: Decimal = Decimal("1.0")  # §15.6 profile knob


DEFAULT_LIMITS = KernelLimits()
