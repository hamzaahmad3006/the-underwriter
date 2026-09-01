# The Underwriter

**An autonomous AI options underwriting desk.** Built for the Alpaca AI Trading
Agents Hackathon, 2026. Paper trading only.

> **The LLM proposes. The deterministic Solvency Kernel disposes.
> The LLM never holds execution authority.**

This is not a policy statement. It is enforced structurally: the AI Underwriter
process has no Alpaca trading credentials, and execution requires a
cryptographically signed `KernelVerdict` that only the Solvency Kernel can mint.
A model that hallucinates, is prompt-injected, or is instructed by a hostile
operator to liquidate the book cannot produce a valid verdict, and therefore
cannot trade.

---

## The honesty statement

**This is not a guaranteed-profit system.** Credit spreads have a high hit rate
and a negatively skewed payoff: the desk expects to win often and lose
occasionally by a larger amount. Over four trading sessions the sample is far
too small to demonstrate edge, and no edge is claimed.

What the system *does* guarantee is a **bounded, pre-computed, per-policy and
portfolio-level maximum loss**, enforced before any order is transmitted.

Two further limitations, stated up front rather than buried:

- The free Alpaca options feed is **indicative** — quotes are derived and trades
  are delayed roughly 15 minutes. No claim is made about fill quality or
  slippage. Pricing is deliberately conservative to partly compensate.
- Probability of profit is **delta-implied and approximate**. Delta is a
  risk-neutral proxy for finishing in the money, not a real-world probability,
  and it ignores the partial-loss region between breakeven and the short strike.

---

## Why an insurance desk

Underwriting solved the problem of pricing and bounding uncertain risk two
centuries before software existed, and it did so with a specific structure: an
underwriter who judges, an actuary who computes, a reserve that guarantees
solvency, and a claims desk that manages outcomes. Crucially, **the
underwriter's judgment is bounded by the actuary's math and the firm's solvency
limits** — an underwriter cannot write a policy that would bankrupt the firm,
however persuasive the case.

That is exactly the structure agentic trading needs and does not have. Today's
LLM trading agents give the model both the judgment and the chequebook. The
blocker is not signal quality; it is that no regulated institution can allow a
stochastic text generator unbounded authority over capital.

| Component | Role |
|---|---|
| **Actuary** | Deterministic Python. It computes; it never persuades. |
| **AI Underwriter** | Judgment over pre-priced candidates. It persuades; it never computes and never executes. |
| **Solvency Kernel** | 25 fixed rules, fail-closed, with veto authority. It cannot be argued with, because it does not read arguments — only numbers. |
| **Claims Desk** | Owns the position after entry, which is where retail options traders actually lose money. |

---

## The five mechanisms

Any one of these alone blocks a rogue model. Together they are the system's
central claim, and each is asserted by a test.

1. **Credential isolation.** Nothing under `underwriter/agent/` can import a
   broker or read a trading key. Asserted statically by walking the AST.
2. **Signature requirement.** `execute()` raises `UnauthorizedExecution` unless
   an HMAC verifies against the exact proposal being executed.
3. **Proposal binding.** The signature covers the proposal hash, so mutating a
   strike, size, symbol or leg side after approval invalidates it.
4. **Time to live.** Verdicts expire in 45 seconds; a captured one cannot be
   replayed later.
5. **Single-use nonce.** Enforced in code *and* by a `UNIQUE` constraint, so
   reuse fails at the database even if every layer above were bypassed.

**Operator instructions are not privileged.** A human command to "liquidate
everything and buy calls" enters the same pipeline as a model proposal and is
adjudicated by the same rules. There is no override path, and its absence is
the point.

---

## What it does, in order

```
Market Data → Actuary → AI Underwriter → Solvency Kernel → Execution → Claims Desk
```

1. **Discovers** candidate policies from liquid option chains, discarding any
   contract without complete Greeks — it never estimates one.
2. **Prices and reserves** deterministically: max loss, expected value, edge
   ratio, capital reserve. Zero model involvement in any number.
3. **Underwrites** — one Groq call, structured output, schema-validated. The
   model selects among pre-priced candidates and explains itself.
4. **Adjudicates** — 25 deterministic rules, all evaluated every time, never
   short-circuiting, so the ledger shows every reason a trade died.
5. **Executes** — idempotent multi-leg orders, polled to a terminal state,
   because an HTTP 200 is not a fill.
6. **Manages to settlement** — profit target, stop loss, and a mandatory flat
   before any position reaches 0DTE.
7. **Records everything** — inputs, decisions, verdicts and outcomes, in a
   hash-chained ledger that can be verified in one request.

### Why it goes flat at 2 DTE

Alpaca publishes no Greeks for 0DTE contracts — time to expiry sits in the
Black-Scholes denominator, so the value is mathematically undefined at expiry.
A position held that far becomes **unmeasurable by this system's own risk
model**. Entering no earlier than 7 DTE and closing at 2 guarantees the book
never holds risk it cannot measure. A profitable unmeasurable position is still
unmeasurable, so force-flat is unconditional.

---

## Running it

```bash
# Backend
cd backend
python -m venv .venv && .venv/Scripts/python -m pip install -e ".[dev]"
cp .env.example .env          # fill in ALPACA_API_KEY, ALPACA_SECRET_KEY, GROQ_API_KEY
make serve                    # http://127.0.0.1:8000

# Frontend
cd frontend
npm install && npm run dev    # http://localhost:5173
```

Or both together:

```bash
docker compose up
```

### Commands

| Command | What it does |
|---|---|
| `make check` | Lint, types, tests and the coverage gates |
| `make test` | The suite, excluding anything that needs credentials |
| `make test-live` | Day 0 provisioning checks against the real paper account |
| `make serve` | The API with the scheduler running |

`make test-live` places no orders unless you also set
`ALPACA_ALLOW_TEST_ORDER=true`. A test that places orders should never be
something you run by accident.

---

## Quality gates

The Solvency Kernel is the product, so it gets disproportionate coverage. The
build fails below these numbers.

| Component | Gate | Actual |
|---|---|---|
| Solvency Kernel | 100% line **and branch** | 100% |
| Actuary | ≥ 95% | 99% |
| Whole backend | — | 86% |

539 tests. `ruff`, `ruff format`, and `mypy` (strict on the Kernel and Actuary)
all clean, plus TypeScript and oxlint on the front end.

---

## Notable engineering decisions

- **Money is `Decimal`, never `float`** — enforced by a column type that
  refuses a float outright. A `REAL` column holding a reserve is invisible until
  a rounding error turns up in a settled P&L.
- **SQLite with WAL**, single writer, on a persistent volume. One less service
  to be down at 3am, and SQLAlchemy keeps the Postgres path open.
- **Polling, not WebSockets.** MCP exposes no streaming, nobody notices a 15
  second refresh, and everybody notices a dead socket on demo day.
- **One model call per decision.** No debate, no committee. Every extra agent is
  another schema, another retry path and another latency budget, and risk logic
  inside a model is unauditable by construction.
- **The desk boots in `MANAGE_ONLY`, always.** After a restart the book and the
  broker may disagree, and the first cycle must not open a position on top of a
  divergence nobody has looked at.

---

## Repository layout

```
backend/underwriter/
├── domain/       Shared typed values — money, quotes, proposals
├── data/         Market Data Layer; only `alpaca_source.py` imports alpaca-py
├── actuary/      Deterministic pricing. No clock, no randomness, no network
├── agent/        The AI Underwriter. Holds no broker credentials, ever
├── kernel/       The Solvency Kernel — 25 rules and the signing key
├── execution/    The only package that can transmit an order
├── claims/       Exit precedence and settlement arithmetic
├── cycle/        Orchestration, the scheduler, and the recorder
├── db/           The 20-table schema and its read models
├── audit/        The hash-chained ledger
├── routes/       Path, method, auth declaration only
├── controllers/  One module per resource, callable straight from a test
└── middleware/   Correlation ids, error envelope, operator auth

frontend/src/
├── api/          The only place that writes a URL or attaches a token
├── components/   Reusable pieces; `ui/` holds the primitives
├── Pages/        Frontend/ (public) and Dashboard/, one folder per page
└── Routes.tsx    Every route in one file
```

The full specification is in [SRS.md](SRS.md), including the requirement ids
referenced throughout the code. Where the implementation had to depart from it,
the deviation is recorded in §14.5 rather than left as silent drift.

---

Paper trading only. No live-money trading in any scope.
