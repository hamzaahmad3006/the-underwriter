"""Candidate enumeration and pricing.

FR-021 enumerates structures from the chain; FR-022 prices them; FR-023
discards on threshold failure with the failing reason recorded; FR-026 treats
an empty result as a successful cycle.

FR-027 is the property that makes the rest testable: no clock, no randomness,
no network. Every time-dependent value is derived from `snapshot.as_of`, so
100 runs over one snapshot produce byte-identical output (TEST-024).
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from underwriter.actuary import formulas
from underwriter.actuary.thresholds import DEFAULT_THRESHOLDS, ActuaryThresholds
from underwriter.actuary.validation import (
    Discard,
    DiscardReason,
    short_delta_in_band,
    validate_quote,
)
from underwriter.domain.hashing import sha256_of
from underwriter.domain.market import ContractQuote, MarketSnapshot, OptionRight, Side
from underwriter.domain.money import ONE, MoneyError, q_ratio
from underwriter.domain.proposal import (
    Action,
    SpreadLeg,
    Structure,
    UnderwritingProposal,
)


@dataclass(frozen=True, slots=True)
class ActuaryResult:
    """Everything one pricing pass produced, including what it threw away.

    The discards matter as much as the proposals: UI-006 requires an empty book
    to explain why it is empty, and the ledger has to show every reason a trade
    never happened.
    """

    proposals: tuple[UnderwritingProposal, ...]
    discards: tuple[Discard, ...]

    @property
    def is_empty(self) -> bool:
        return len(self.proposals) == 0


def candidate_id_for(
    underlying: str,
    expiry_iso: str,
    short_strike: Decimal,
    long_strike: Decimal,
    snapshot_hash: str,
) -> str:
    """Deterministic id, stable across runs of the same snapshot (TEST-024).

    Includes the snapshot hash so the same strikes priced off a later snapshot
    get a different id. SK-024 checks membership in the set actually supplied
    to the LLM, and a recycled id would defeat that check.
    """
    digest = sha256_of(
        {
            "underlying": underlying,
            "structure": Structure.PUT_CREDIT_SPREAD,
            "expiry": expiry_iso,
            "short_strike": short_strike,
            "long_strike": long_strike,
            "snapshot_hash": snapshot_hash,
        }
    )
    return f"cand_{digest[:16]}"


def price_put_credit_spreads(
    snapshot: MarketSnapshot,
    *,
    thresholds: ActuaryThresholds = DEFAULT_THRESHOLDS,
    universe: tuple[str, ...] | None = None,
) -> ActuaryResult:
    """Enumerate and price every eligible put credit spread in the snapshot.

    Never raises into the cycle (§11.2 failure behaviour): a math error on one
    candidate discards that candidate with ACTUARY_MATH_ERROR and the pass
    continues with the rest.
    """
    as_of_date = snapshot.as_of.date()
    snapshot_hash = snapshot.snapshot_hash

    usable: list[ContractQuote] = []
    discards: list[Discard] = []

    for quote in snapshot.quotes:
        if universe is not None and quote.underlying not in universe:
            continue
        if quote.right is not OptionRight.PUT:
            continue

        dte = quote.dte(as_of_date)
        if not (thresholds.dte_min <= dte <= thresholds.dte_max):
            discards.append(Discard(quote.symbol, DiscardReason.DTE_OUT_OF_WINDOW, f"dte={dte}"))
            continue

        try:
            failure = validate_quote(quote, thresholds)
        except Exception as exc:  # the Actuary never raises into the cycle
            discards.append(
                Discard(
                    quote.symbol,
                    DiscardReason.ACTUARY_MATH_ERROR,
                    f"{type(exc).__name__}: {exc}",
                )
            )
            continue

        if failure is not None:
            discards.append(failure)
            continue

        usable.append(quote)

    by_symbol = {q.symbol: q for q in usable}
    proposals: list[UnderwritingProposal] = []

    # Sorted enumeration keeps output order independent of dict iteration and
    # of whatever order Alpaca happened to return the chain in.
    ordered = sorted(usable, key=lambda q: (q.underlying, q.expiry, q.strike, q.symbol))

    for short_leg in ordered:
        short_delta = short_leg.delta
        if short_delta is None or not short_delta_in_band(short_delta, thresholds):
            continue

        for long_leg in ordered:
            if long_leg.underlying != short_leg.underlying:
                continue
            if long_leg.expiry != short_leg.expiry:
                continue
            if long_leg.strike >= short_leg.strike:
                continue

            width = short_leg.strike - long_leg.strike
            if not (thresholds.width_min <= width <= thresholds.width_max):
                continue

            candidate_id = candidate_id_for(
                short_leg.underlying,
                short_leg.expiry.isoformat(),
                short_leg.strike,
                long_leg.strike,
                snapshot_hash,
            )
            priced = _price_one(
                candidate_id=candidate_id,
                short_leg=short_leg,
                long_leg=long_leg,
                by_symbol=by_symbol,
                snapshot=snapshot,
                snapshot_hash=snapshot_hash,
                thresholds=thresholds,
            )
            if isinstance(priced, Discard):
                discards.append(priced)
            else:
                proposals.append(priced)

    # Best edge first, candidate_id as the tiebreak so the ordering is total.
    proposals.sort(key=lambda p: (-p.edge_ratio, p.candidate_id))
    return ActuaryResult(tuple(proposals), tuple(discards))


def _price_one(
    *,
    candidate_id: str,
    short_leg: ContractQuote,
    long_leg: ContractQuote,
    by_symbol: dict[str, ContractQuote],
    snapshot: MarketSnapshot,
    snapshot_hash: str,
    thresholds: ActuaryThresholds,
) -> UnderwritingProposal | Discard:
    """Price one short/long pair, or record why it cannot be priced."""
    legs = (
        SpreadLeg(
            symbol=short_leg.symbol,
            right=OptionRight.PUT,
            side=Side.SELL,
            strike=short_leg.strike,
            expiry=short_leg.expiry,
        ),
        SpreadLeg(
            symbol=long_leg.symbol,
            right=OptionRight.PUT,
            side=Side.BUY,
            strike=long_leg.strike,
            expiry=long_leg.expiry,
        ),
    )

    short_delta = short_leg.delta
    if short_delta is None:
        return Discard(candidate_id, DiscardReason.MISSING_GREEKS, "short delta unavailable")

    try:
        width = formulas.spread_width(short_leg.strike, long_leg.strike)
        credit = formulas.net_credit(short_leg.bid, long_leg.ask)
        gross_profit = formulas.max_profit(credit)
        gross_loss = formulas.max_loss(width, credit)
        break_even = formulas.breakeven(short_leg.strike, credit)
        ctw = formulas.credit_to_width(credit, width)

        p_loss = formulas.loss_probability_proxy(short_delta)
        ev = formulas.expected_value(p_loss, gross_profit, gross_loss)
        edge = formulas.edge_ratio(ev, gross_loss)

        leg_scores = [
            formulas.liquidity_score(
                quote,
                max_bid_ask_pct=thresholds.max_bid_ask_pct,
                min_depth=thresholds.min_depth_target,
                min_oi=thresholds.min_oi_target,
            )
            for quote in (short_leg, long_leg)
        ]
        # A spread is only as liquid as its worse leg.
        liquidity = min(leg_scores)
        worst_leg_spread = max(short_leg.spread_pct, long_leg.spread_pct)
        net_delta, net_vega = formulas.position_greeks(legs, by_symbol)
    except MoneyError as exc:
        return Discard(candidate_id, DiscardReason.ACTUARY_MATH_ERROR, str(exc))
    except Exception as exc:  # the Actuary never raises into the cycle (§11.2)
        return Discard(
            candidate_id,
            DiscardReason.ACTUARY_MATH_ERROR,
            f"{type(exc).__name__}: {exc}",
        )

    if ctw < thresholds.min_credit_to_width:
        return Discard(
            candidate_id,
            DiscardReason.CREDIT_TO_WIDTH_LOW,
            f"credit_to_width={ctw} < {thresholds.min_credit_to_width}",
        )
    if ctw > thresholds.max_credit_to_width:
        return Discard(
            candidate_id,
            DiscardReason.CREDIT_TO_WIDTH_HIGH,
            f"credit_to_width={ctw} > {thresholds.max_credit_to_width}",
        )
    if edge < thresholds.min_edge_ratio:
        return Discard(
            candidate_id,
            DiscardReason.INSUFFICIENT_EDGE,
            f"edge_ratio={edge} < {thresholds.min_edge_ratio}",
        )
    if liquidity < thresholds.min_liquidity_score:
        return Discard(
            candidate_id,
            DiscardReason.ILLIQUID,
            f"liquidity_score={liquidity} < {thresholds.min_liquidity_score}",
        )

    return UnderwritingProposal(
        candidate_id=candidate_id,
        underlying=short_leg.underlying,
        structure=Structure.PUT_CREDIT_SPREAD,
        action=Action.OPEN,
        legs=legs,
        short_strike=short_leg.strike,
        long_strike=long_leg.strike,
        expiry=short_leg.expiry,
        dte=short_leg.dte(snapshot.as_of.date()),
        net_credit=credit,
        max_profit=gross_profit,
        max_loss=gross_loss,
        capital_reserve=gross_loss,  # SK-002: fully reserved, always
        breakeven=break_even,
        credit_to_width=ctw,
        p_loss_proxy=p_loss,
        p_profit_proxy=q_ratio(ONE - p_loss),
        expected_value=ev,
        edge_ratio=edge,
        liquidity_score=liquidity,
        max_leg_spread_pct=worst_leg_spread,
        short_delta=short_delta,
        net_delta=net_delta,
        net_vega=net_vega,
        greeks_complete=True,
        snapshot_hash=snapshot_hash,
        snapshot_as_of=snapshot.as_of,
    )
