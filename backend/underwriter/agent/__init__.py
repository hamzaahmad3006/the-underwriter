"""The AI Underwriter — `underwriter/agent/` (§11.3).

It exercises judgment over pre-priced candidates. It persuades; it never
computes and never executes.

SEC-010 is the governing assumption: this component is treated as untrusted and
potentially adversarial. Its only permitted influence is selecting one entry
from a set the Actuary already priced and validated. No output of it becomes a
code path, a query, a symbol, a URL, or a number that reaches risk math.

TEST-031 asserts by introspection that nothing in this package can obtain a
trading-credentialed Alpaca client. That is why the boundary holds even if the
model is compromised: there is nothing here to trade with.
"""

from underwriter.agent.decision import UnderwriterDecision
from underwriter.agent.underwriter import AIUnderwriter, UnderwritingOutcome

__all__ = ["AIUnderwriter", "UnderwriterDecision", "UnderwritingOutcome"]
