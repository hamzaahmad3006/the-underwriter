"""`mleg` payload construction — §17.3, FR-081 … FR-083, ALP-010 … ALP-016.

The payload is built from the proposal and the verdict, never from anything
else. In particular `qty` comes from the verdict's `approved_contracts`, not
from what the model asked for: FR-046 makes the model's number advisory, and
this is where that stops being a policy statement.

ALP-014 is a happy redundancy — Alpaca rejects an `mleg` containing an
uncovered short, which is SK-004 enforced a second time by the broker.
"""

from __future__ import annotations

from decimal import ROUND_DOWN, ROUND_UP, Decimal
from math import gcd
from typing import Any

from underwriter.domain.market import Side
from underwriter.domain.proposal import Action, UnderwritingProposal
from underwriter.kernel.rules import client_order_id_for

# ALP-012: the four legal intents.
INTENTS = {
    (Action.OPEN, Side.SELL): "sell_to_open",
    (Action.OPEN, Side.BUY): "buy_to_open",
    (Action.CLOSE, Side.SELL): "sell_to_close",
    (Action.CLOSE, Side.BUY): "buy_to_close",
}

PRICE_STEP = Decimal("0.01")
MAX_PRICE_STEPS = 3  # FR-082


class OrderConstructionError(ValueError):
    """The payload could not be built, so nothing is transmitted."""


def simplify_ratios(ratios: list[int]) -> list[int]:
    """ALP-011: ratio_qty across legs in simplest form (GCD = 1)."""
    if not ratios or any(r < 1 for r in ratios):
        raise OrderConstructionError(f"ratio quantities must be positive: {ratios}")

    divisor = 0
    for ratio in ratios:
        divisor = gcd(divisor, ratio)
    return [r // divisor for r in ratios]


def entry_limit_price(net_credit: Decimal, step: int = 0) -> Decimal:
    """FR-082: start at the modelled credit, then walk toward a worse fill.

    Walking *down* on an entry means accepting less credit, which is the
    conservative direction: it can only reduce what the position pays, never
    increase what it risks. Max loss is fixed by the strikes either way.
    """
    if step < 0 or step > MAX_PRICE_STEPS:
        raise OrderConstructionError(f"price step {step} outside 0..{MAX_PRICE_STEPS}")

    price = net_credit - (PRICE_STEP * Decimal(step))
    if price <= 0:
        raise OrderConstructionError(
            f"credit walked to {price} at step {step}; refusing a non-positive entry credit"
        )
    return price.quantize(PRICE_STEP, rounding=ROUND_DOWN)


def exit_limit_price(target_debit: Decimal, step: int = 0) -> Decimal:
    """The mirror: an exit walks *up*, paying more to get out.

    Asymmetric on purpose. Getting out is worth overpaying for; getting in is
    not worth underpricing.
    """
    if step < 0 or step > MAX_PRICE_STEPS:
        raise OrderConstructionError(f"price step {step} outside 0..{MAX_PRICE_STEPS}")

    price = target_debit + (PRICE_STEP * Decimal(step))
    return price.quantize(PRICE_STEP, rounding=ROUND_UP)


def closing_side(side: Side) -> Side:
    """Closing a position means doing the opposite of what opened it.

    A put credit spread is opened by selling the near strike and buying the far
    one. Closing it buys the short back and sells the long — so the sides flip.
    Reusing the opening sides would send an order that doubles the position
    instead of removing it, and Alpaca would happily accept it.
    """
    return Side.BUY if side is Side.SELL else Side.SELL


def _legs_payload(proposal: UnderwritingProposal, action: Action) -> list[dict[str, str]]:
    ratios = simplify_ratios([leg.ratio_qty for leg in proposal.legs])
    legs: list[dict[str, str]] = []

    for leg, ratio in zip(proposal.legs, ratios, strict=True):
        side = closing_side(leg.side) if action is Action.CLOSE else leg.side
        intent = INTENTS.get((action, side))
        if intent is None:
            raise OrderConstructionError(f"no position_intent for {action}/{side}")
        legs.append(
            {
                "symbol": leg.symbol,
                "side": side.value.lower(),
                "ratio_qty": str(ratio),
                "position_intent": intent,
            }
        )

    return legs


def _build(
    proposal: UnderwritingProposal,
    *,
    action: Action,
    contracts: int,
    limit_price: Decimal,
) -> dict[str, Any]:
    if contracts < 1:
        raise OrderConstructionError(f"contracts must be at least 1, got {contracts}")

    legs = _legs_payload(proposal, action)
    if len(legs) < 2:
        # ALP-014: Alpaca rejects an mleg with an uncovered short anyway. Better
        # to fail here, where the reason is legible, than at the broker.
        raise OrderConstructionError(
            f"an mleg order needs every short leg covered; got {len(legs)} leg(s)"
        )

    return {
        "order_class": "mleg",  # ALP-010
        "qty": str(contracts),
        "type": "limit",  # ALP-015, FR-082: never a market order
        "time_in_force": "day",
        "limit_price": str(limit_price),
        "legs": legs,
        # FR-083: deterministic, so a retry after an ambiguous network failure
        # collides with the original instead of opening a second position.
        "client_order_id": client_order_id_for(proposal.proposal_hash),
    }


def build_entry_order(
    proposal: UnderwritingProposal, *, contracts: int, step: int = 0
) -> dict[str, Any]:
    """An opening `mleg`, sized by the Kernel rather than by the model."""
    if proposal.action is not Action.OPEN:
        raise OrderConstructionError(f"cannot build an entry from action={proposal.action}")

    return _build(
        proposal,
        action=Action.OPEN,
        contracts=contracts,
        limit_price=entry_limit_price(proposal.net_credit, step),
    )


def build_exit_order(
    proposal: UnderwritingProposal,
    *,
    contracts: int,
    target_debit: Decimal,
    step: int = 0,
) -> dict[str, Any]:
    """ALP-016: the mirror order, with `*_to_close` intents."""
    return _build(
        proposal,
        action=Action.CLOSE,
        contracts=contracts,
        limit_price=exit_limit_price(target_debit, step),
    )
