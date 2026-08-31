"""Actuary-level pre-filter thresholds (§11.2).

These are the Actuary's own acceptance gates, applied before the LLM ever sees
a candidate. Several of them are checked *again* by the Kernel (SK-014, SK-015,
SK-016) — that duplication is deliberate: the Actuary filters to keep the
prompt small, the Kernel adjudicates because it is the only authority.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal


@dataclass(frozen=True, slots=True)
class ActuaryThresholds:
    min_credit_to_width: Decimal = Decimal("0.20")
    max_credit_to_width: Decimal = Decimal("0.45")
    min_edge_ratio: Decimal = Decimal("0.05")
    min_liquidity_score: Decimal = Decimal("0.55")
    max_bid_ask_pct: Decimal = Decimal("0.15")
    short_delta_min: Decimal = Decimal("0.12")
    short_delta_max: Decimal = Decimal("0.28")

    dte_min: int = 7
    dte_max: int = 21
    width_min: Decimal = Decimal("1.0")
    width_max: Decimal = Decimal("5.0")

    # The §11.2 liquidity formula names these but states no default. Chosen
    # here for SPY/QQQ/IWM weeklies, where both are comfortably exceeded by
    # any strike worth trading; raising them tightens the filter.
    min_depth_target: Decimal = Decimal("10")
    min_oi_target: Decimal = Decimal("500")

    # IV sanity band from the §11.1 validation pipeline, step 5.
    iv_min: Decimal = Decimal("0")
    iv_max: Decimal = Decimal("5.0")


DEFAULT_THRESHOLDS = ActuaryThresholds()
