"""The §18 schema — DB-001 … DB-020, as SQLAlchemy models.

Three constraints in here are load-bearing rather than decorative, and each one
turns a rule the SRS states in prose into something the database refuses:

* `accounts.is_paper` carries `CHECK(is_paper = 1)`. A live account cannot be
  recorded at all, so the paper-only guarantee survives a bad config file.
* `orders.kernel_decision_id` is `NOT NULL`. There is no way to write an order
  row without the verdict that authorised it, which is what makes NFR-008's
  "100% of executed orders traceable to a stored verdict" a schema property
  instead of a promise.
* `kernel_decisions.nonce` is `UNIQUE`. Nonce reuse fails at the database even
  if every layer above it has been bypassed (§14.4, mechanism 5).

No SQLite-only syntax anywhere, so TD-02's Postgres path stays open.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    CheckConstraint,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from underwriter.db.base import Base, JsonText, Money, UtcTimestamp, new_id, utc_now

# Reusable column shapes.
ID = String(36)
SHORT = String(32)
SYMBOL = String(32)
HASH = String(64)


class SystemConfig(Base):
    """DB-001 — single-row operational state.

    The `id = 1` check is what makes "single-row" true rather than intended.
    """

    __tablename__ = "system_config"
    __table_args__ = (
        CheckConstraint("id = 1", name="ck_system_config_singleton"),
        CheckConstraint("mode IN ('HALT','MANAGE_ONLY','ACTIVE')", name="ck_system_config_mode"),
        CheckConstraint(
            "strategy_profile IN ('CONSERVATIVE','PERFORMANCE')",
            name="ck_system_config_profile",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    mode: Mapped[str] = mapped_column(SHORT, nullable=False, default="MANAGE_ONLY")
    kill_switch: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    strategy_profile: Mapped[str] = mapped_column(SHORT, default="PERFORMANCE")
    calibration_multiplier: Mapped[Decimal | None] = mapped_column(Money, default=Decimal("1.0"))
    peak_equity: Mapped[Decimal | None] = mapped_column(Money)
    daily_loss_baseline: Mapped[Decimal | None] = mapped_column(Money)
    daily_loss_baseline_date: Mapped[str | None] = mapped_column(SHORT)
    updated_at: Mapped[datetime] = mapped_column(UtcTimestamp, default=utc_now)
    updated_by: Mapped[str | None] = mapped_column(SHORT)
    created_at: Mapped[datetime] = mapped_column(UtcTimestamp, default=utc_now)


class Account(Base):
    """DB-002 — the Alpaca account, and the hard guard against a live one."""

    __tablename__ = "accounts"
    __table_args__ = (CheckConstraint("is_paper = 1", name="ck_accounts_paper_only"),)

    id: Mapped[str] = mapped_column(ID, primary_key=True, default=new_id)
    alpaca_account_id: Mapped[str] = mapped_column(SHORT, unique=True, nullable=False)
    is_paper: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    baseline_equity: Mapped[Decimal | None] = mapped_column(Money)  # ALP-002
    baseline_at: Mapped[datetime | None] = mapped_column(UtcTimestamp)
    created_at: Mapped[datetime] = mapped_column(UtcTimestamp, default=utc_now)


class Strategy(Base):
    """DB-003 — the exact parameter set a policy was written under."""

    __tablename__ = "strategies"

    id: Mapped[str] = mapped_column(ID, primary_key=True, default=new_id)
    name: Mapped[str] = mapped_column(SHORT, nullable=False)
    version: Mapped[str] = mapped_column(SHORT, nullable=False)
    params_json: Mapped[Any | None] = mapped_column(JsonText, nullable=True)
    # CFG-002: a policy can be traced to the exact limits in force when written.
    params_hash: Mapped[str] = mapped_column(HASH, index=True)
    enabled: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = mapped_column(UtcTimestamp, default=utc_now)


class MarketSnapshotRow(Base):
    """DB-004 — immutable cycle inputs, enough to replay the Actuary (FR-008)."""

    __tablename__ = "market_snapshots"

    id: Mapped[str] = mapped_column(ID, primary_key=True, default=new_id)
    correlation_id: Mapped[str] = mapped_column(ID, index=True)
    underlying: Mapped[str] = mapped_column(SYMBOL, index=True)
    underlying_price: Mapped[Decimal | None] = mapped_column(Money)
    iv_rank: Mapped[Decimal | None] = mapped_column(Money)
    realized_vol: Mapped[Decimal | None] = mapped_column(Money)
    vix: Mapped[Decimal | None] = mapped_column(Money)
    chain_json: Mapped[Any | None] = mapped_column(JsonText, nullable=True)
    snapshot_hash: Mapped[str] = mapped_column(HASH, unique=True)
    source: Mapped[str] = mapped_column(SHORT, default="rest")  # FR-005
    fetched_at: Mapped[datetime] = mapped_column(UtcTimestamp, index=True)
    created_at: Mapped[datetime] = mapped_column(UtcTimestamp, default=utc_now)


class Candidate(Base):
    """DB-005 — every priced candidate, accepted or not (FR-023)."""

    __tablename__ = "candidates"

    id: Mapped[str] = mapped_column(ID, primary_key=True, default=new_id)
    correlation_id: Mapped[str] = mapped_column(ID, index=True)
    snapshot_id: Mapped[str | None] = mapped_column(ForeignKey("market_snapshots.id"), index=True)

    underlying: Mapped[str] = mapped_column(SYMBOL, index=True)
    structure: Mapped[str] = mapped_column(SHORT)
    short_symbol: Mapped[str | None] = mapped_column(SYMBOL)
    long_symbol: Mapped[str | None] = mapped_column(SYMBOL)
    short_strike: Mapped[Decimal | None] = mapped_column(Money)
    long_strike: Mapped[Decimal | None] = mapped_column(Money)
    width: Mapped[Decimal | None] = mapped_column(Money)
    expiration: Mapped[date | None] = mapped_column(String(10))
    dte: Mapped[int | None] = mapped_column(Integer)

    net_credit: Mapped[Decimal | None] = mapped_column(Money)
    max_profit: Mapped[Decimal | None] = mapped_column(Money)
    max_loss: Mapped[Decimal | None] = mapped_column(Money)
    capital_reserve: Mapped[Decimal | None] = mapped_column(Money)
    breakeven: Mapped[Decimal | None] = mapped_column(Money)

    short_delta: Mapped[Decimal | None] = mapped_column(Money)
    p_profit_proxy: Mapped[Decimal | None] = mapped_column(Money)
    expected_loss: Mapped[Decimal | None] = mapped_column(Money)
    expected_value: Mapped[Decimal | None] = mapped_column(Money)
    edge_ratio: Mapped[Decimal | None] = mapped_column(Money)
    liquidity_score: Mapped[Decimal | None] = mapped_column(Money)
    bid_ask_pct: Mapped[Decimal | None] = mapped_column(Money)

    accepted: Mapped[int] = mapped_column(Integer, default=0)
    rejection_reason: Mapped[str | None] = mapped_column(SHORT)
    # Binds this candidate to the verdict that adjudicated it.
    proposal_hash: Mapped[str] = mapped_column(HASH, unique=True, index=True)
    created_at: Mapped[datetime] = mapped_column(UtcTimestamp, default=utc_now)


class UnderwritingDecision(Base):
    """DB-006 — the LLM's decision, with everything needed to reproduce it.

    FR-043 wants the exact prompt, model, version, temperature, token counts,
    latency and raw response on every call. A decision nobody can reproduce is
    a decision nobody can audit.
    """

    __tablename__ = "underwriting_decisions"
    __table_args__ = (CheckConstraint("action IN ('WRITE','DECLINE')", name="ck_decision_action"),)

    id: Mapped[str] = mapped_column(ID, primary_key=True, default=new_id)
    correlation_id: Mapped[str] = mapped_column(ID, index=True)
    candidate_id: Mapped[str | None] = mapped_column(ForeignKey("candidates.id"))

    action: Mapped[str] = mapped_column(SHORT, nullable=False)
    confidence: Mapped[Decimal | None] = mapped_column(Money)
    requested_contracts: Mapped[int | None] = mapped_column(Integer)
    rationale: Mapped[str | None] = mapped_column(Text)
    identified_risks_json: Mapped[Any | None] = mapped_column(JsonText, nullable=True)
    declined_reason: Mapped[str | None] = mapped_column(Text)

    model: Mapped[str | None] = mapped_column(SHORT)
    model_version: Mapped[str | None] = mapped_column(SHORT)
    temperature: Mapped[Decimal | None] = mapped_column(Money)
    prompt_sha256: Mapped[str | None] = mapped_column(HASH)
    raw_response: Mapped[str | None] = mapped_column(Text)
    prompt_tokens: Mapped[int | None] = mapped_column(Integer)
    completion_tokens: Mapped[int | None] = mapped_column(Integer)
    latency_ms: Mapped[int | None] = mapped_column(Integer)
    schema_valid: Mapped[int] = mapped_column(Integer, default=1)
    retry_count: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(UtcTimestamp, default=utc_now)


class KernelDecision(Base):
    """DB-007 — every verdict, approvals and rejections alike (FR-065)."""

    __tablename__ = "kernel_decisions"
    __table_args__ = (
        CheckConstraint("verdict IN ('APPROVE','REJECT')", name="ck_kernel_verdict"),
        CheckConstraint("action_type IN ('ENTRY','EXIT','HEDGE')", name="ck_kernel_action_type"),
    )

    id: Mapped[str] = mapped_column(ID, primary_key=True, default=new_id)
    correlation_id: Mapped[str] = mapped_column(ID, index=True)
    candidate_id: Mapped[str | None] = mapped_column(ForeignKey("candidates.id"), index=True)
    decision_id: Mapped[str | None] = mapped_column(
        ForeignKey("underwriting_decisions.id"), index=True
    )

    proposal_hash: Mapped[str] = mapped_column(HASH, index=True)
    verdict: Mapped[str] = mapped_column(SHORT, nullable=False)
    approved_contracts: Mapped[int] = mapped_column(Integer, default=0)
    reject_reasons_json: Mapped[Any | None] = mapped_column(JsonText, nullable=True)
    # §14.4 mechanism 5: single use, enforced by the database itself.
    nonce: Mapped[str] = mapped_column(HASH, unique=True, nullable=False)
    nonce_consumed_at: Mapped[datetime | None] = mapped_column(UtcTimestamp)
    issued_at: Mapped[datetime] = mapped_column(UtcTimestamp, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(UtcTimestamp, nullable=False)
    signature: Mapped[str | None] = mapped_column(HASH)  # present iff APPROVE
    action_type: Mapped[str] = mapped_column(SHORT, default="ENTRY")
    created_at: Mapped[datetime] = mapped_column(UtcTimestamp, default=utc_now)

    risk_checks: Mapped[list[RiskCheck]] = relationship(back_populates="kernel_decision")


class RiskCheck(Base):
    """DB-008 — one row per rule per verdict, passes included (FR-061)."""

    __tablename__ = "risk_checks"
    __table_args__ = (
        CheckConstraint("severity IN ('HARD','SOFT')", name="ck_risk_check_severity"),
        Index("ix_risk_checks_rule_passed", "rule_id", "passed"),
    )

    id: Mapped[str] = mapped_column(ID, primary_key=True, default=new_id)
    kernel_decision_id: Mapped[str] = mapped_column(
        ForeignKey("kernel_decisions.id"), index=True, nullable=False
    )
    rule_id: Mapped[str] = mapped_column(SHORT, nullable=False)
    rule_name: Mapped[str] = mapped_column(SHORT)
    passed: Mapped[int] = mapped_column(Integer, index=True)
    severity: Mapped[str] = mapped_column(SHORT)
    observed: Mapped[str | None] = mapped_column(Text)
    limit_value: Mapped[str | None] = mapped_column(Text)
    message: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(UtcTimestamp, default=utc_now)

    kernel_decision: Mapped[KernelDecision] = relationship(back_populates="risk_checks")


class Policy(Base):
    """DB-009 — a written policy through its whole life."""

    __tablename__ = "policies"
    __table_args__ = (
        CheckConstraint(
            "status IN ('PENDING','OPEN','CLOSING','SETTLED','FAILED','LEG_RISK')",
            name="ck_policy_status",
        ),
    )

    id: Mapped[str] = mapped_column(ID, primary_key=True, default=new_id)
    policy_number: Mapped[str] = mapped_column(SHORT, unique=True)  # UW-2026-0007
    correlation_id: Mapped[str] = mapped_column(ID, index=True)
    candidate_id: Mapped[str | None] = mapped_column(ForeignKey("candidates.id"))
    kernel_decision_id: Mapped[str | None] = mapped_column(ForeignKey("kernel_decisions.id"))
    strategy_id: Mapped[str | None] = mapped_column(ForeignKey("strategies.id"))

    underlying: Mapped[str] = mapped_column(SYMBOL, index=True)
    structure: Mapped[str] = mapped_column(SHORT, index=True)
    contracts: Mapped[int] = mapped_column(Integer, default=0)

    opening_credit: Mapped[Decimal | None] = mapped_column(Money)
    max_profit: Mapped[Decimal | None] = mapped_column(Money)
    max_loss: Mapped[Decimal | None] = mapped_column(Money)
    capital_reserve: Mapped[Decimal | None] = mapped_column(Money)

    expiration: Mapped[str | None] = mapped_column(String(10), index=True)
    status: Mapped[str] = mapped_column(SHORT, index=True, default="PENDING")
    opened_at: Mapped[datetime | None] = mapped_column(UtcTimestamp)
    closed_at: Mapped[datetime | None] = mapped_column(UtcTimestamp)
    closing_debit: Mapped[Decimal | None] = mapped_column(Money)
    realized_pnl: Mapped[Decimal | None] = mapped_column(Money)
    settlement_reason: Mapped[str | None] = mapped_column(SHORT)

    predicted_confidence: Mapped[Decimal | None] = mapped_column(Money)
    outcome_win: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(UtcTimestamp, default=utc_now)

    legs: Mapped[list[PolicyLeg]] = relationship(back_populates="policy")
    orders: Mapped[list[Order]] = relationship(back_populates="policy")


class PolicyLeg(Base):
    """DB-010 — one leg of a policy, priced at open and at close."""

    __tablename__ = "policy_legs"

    id: Mapped[str] = mapped_column(ID, primary_key=True, default=new_id)
    policy_id: Mapped[str] = mapped_column(ForeignKey("policies.id"), index=True)
    option_symbol: Mapped[str] = mapped_column(SYMBOL, nullable=False)
    side: Mapped[str] = mapped_column(SHORT)
    position_intent: Mapped[str | None] = mapped_column(SHORT)
    ratio_qty: Mapped[int] = mapped_column(Integer, default=1)
    strike: Mapped[Decimal | None] = mapped_column(Money)
    expiration: Mapped[str | None] = mapped_column(String(10))
    option_type: Mapped[str | None] = mapped_column(SHORT)
    open_price: Mapped[Decimal | None] = mapped_column(Money)
    close_price: Mapped[Decimal | None] = mapped_column(Money)
    open_delta: Mapped[Decimal | None] = mapped_column(Money)
    open_iv: Mapped[Decimal | None] = mapped_column(Money)
    created_at: Mapped[datetime] = mapped_column(UtcTimestamp, default=utc_now)

    policy: Mapped[Policy] = relationship(back_populates="legs")


class Order(Base):
    """DB-011 — an order, with the verdict that authorised it.

    `kernel_decision_id` is NOT NULL. That single constraint is what turns
    NFR-008 from a claim into a property of the database: an order row cannot
    exist without a stored verdict to trace it to.
    """

    __tablename__ = "orders"
    __table_args__ = (CheckConstraint("intent IN ('ENTRY','EXIT')", name="ck_order_intent"),)

    id: Mapped[str] = mapped_column(ID, primary_key=True, default=new_id)
    policy_id: Mapped[str | None] = mapped_column(ForeignKey("policies.id"), index=True)
    alpaca_order_id: Mapped[str | None] = mapped_column(SHORT, unique=True)
    # FR-083: derived from the proposal hash, so a retry after an ambiguous
    # network failure collides with the original instead of double-submitting.
    client_order_id: Mapped[str] = mapped_column(SHORT, unique=True, nullable=False)
    kernel_decision_id: Mapped[str] = mapped_column(
        ForeignKey("kernel_decisions.id"), nullable=False
    )

    intent: Mapped[str] = mapped_column(SHORT)
    order_class: Mapped[str] = mapped_column(SHORT, default="mleg")
    limit_price: Mapped[Decimal | None] = mapped_column(Money)
    submitted_at: Mapped[datetime | None] = mapped_column(UtcTimestamp)
    status: Mapped[str] = mapped_column(SHORT, index=True, default="new")
    filled_qty: Mapped[Decimal | None] = mapped_column(Money)
    filled_avg_price: Mapped[Decimal | None] = mapped_column(Money)
    request_json: Mapped[Any | None] = mapped_column(JsonText, nullable=True)
    response_json: Mapped[Any | None] = mapped_column(JsonText, nullable=True)
    attempt: Mapped[int] = mapped_column(Integer, default=1)
    terminal: Mapped[int] = mapped_column(Integer, default=0)
    error_code: Mapped[str | None] = mapped_column(SHORT)
    error_message: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(UtcTimestamp, default=utc_now)

    policy: Mapped[Policy | None] = relationship(back_populates="orders")
    fills: Mapped[list[Fill]] = relationship(back_populates="order")


class Fill(Base):
    """DB-012 — an execution against an order."""

    __tablename__ = "fills"

    id: Mapped[str] = mapped_column(ID, primary_key=True, default=new_id)
    order_id: Mapped[str] = mapped_column(ForeignKey("orders.id"), index=True)
    option_symbol: Mapped[str] = mapped_column(SYMBOL)
    side: Mapped[str] = mapped_column(SHORT)
    qty: Mapped[Decimal | None] = mapped_column(Money)
    price: Mapped[Decimal | None] = mapped_column(Money)
    filled_at: Mapped[datetime | None] = mapped_column(UtcTimestamp)
    created_at: Mapped[datetime] = mapped_column(UtcTimestamp, default=utc_now)

    order: Mapped[Order] = relationship(back_populates="fills")


class PositionSnapshot(Base):
    """DB-013 — reconciliation. A null `matched_policy_id` is an orphan."""

    __tablename__ = "positions_snapshot"

    id: Mapped[str] = mapped_column(ID, primary_key=True, default=new_id)
    taken_at: Mapped[datetime] = mapped_column(UtcTimestamp, index=True, default=utc_now)
    symbol: Mapped[str] = mapped_column(SYMBOL)
    qty: Mapped[Decimal | None] = mapped_column(Money)
    avg_entry_price: Mapped[Decimal | None] = mapped_column(Money)
    market_value: Mapped[Decimal | None] = mapped_column(Money)
    unrealized_pl: Mapped[Decimal | None] = mapped_column(Money)
    # Null means the broker holds a position the book does not know about,
    # which is F-19 and always a CRITICAL risk event.
    matched_policy_id: Mapped[str | None] = mapped_column(ForeignKey("policies.id"))
    created_at: Mapped[datetime] = mapped_column(UtcTimestamp, default=utc_now)


class PnlRecord(Base):
    """DB-014 — the equity curve, one row per reconciliation."""

    __tablename__ = "pnl_records"

    id: Mapped[str] = mapped_column(ID, primary_key=True, default=new_id)
    recorded_at: Mapped[datetime] = mapped_column(UtcTimestamp, index=True, default=utc_now)
    equity: Mapped[Decimal | None] = mapped_column(Money)
    cash: Mapped[Decimal | None] = mapped_column(Money)
    buying_power: Mapped[Decimal | None] = mapped_column(Money)
    realized_pnl_cum: Mapped[Decimal | None] = mapped_column(Money)
    unrealized_pnl: Mapped[Decimal | None] = mapped_column(Money)
    open_policies: Mapped[int] = mapped_column(Integer, default=0)
    closed_policies: Mapped[int] = mapped_column(Integer, default=0)
    drawdown_pct: Mapped[Decimal | None] = mapped_column(Money)
    loss_ratio: Mapped[Decimal | None] = mapped_column(Money)
    win_rate: Mapped[Decimal | None] = mapped_column(Money)
    created_at: Mapped[datetime] = mapped_column(UtcTimestamp, default=utc_now)


class Reserve(Base):
    """DB-015 — capital held against a policy (SK-002).

    DB-INV-1: the sum of HELD reserves must equal the sum of max_loss across
    OPEN and CLOSING policies, checked every reconcile cycle.
    """

    __tablename__ = "reserves"
    __table_args__ = (CheckConstraint("status IN ('HELD','RELEASED')", name="ck_reserve_status"),)

    id: Mapped[str] = mapped_column(ID, primary_key=True, default=new_id)
    policy_id: Mapped[str] = mapped_column(ForeignKey("policies.id"), index=True)
    amount: Mapped[Decimal] = mapped_column(Money, nullable=False)
    reserved_at: Mapped[datetime] = mapped_column(UtcTimestamp, default=utc_now)
    released_at: Mapped[datetime | None] = mapped_column(UtcTimestamp)
    status: Mapped[str] = mapped_column(SHORT, default="HELD", index=True)
    created_at: Mapped[datetime] = mapped_column(UtcTimestamp, default=utc_now)


class RiskEvent(Base):
    """DB-016 — anything that should wake the operator."""

    __tablename__ = "risk_events"
    __table_args__ = (
        CheckConstraint("severity IN ('INFO','WARN','CRITICAL')", name="ck_risk_event_severity"),
    )

    id: Mapped[str] = mapped_column(ID, primary_key=True, default=new_id)
    occurred_at: Mapped[datetime] = mapped_column(UtcTimestamp, index=True, default=utc_now)
    event_type: Mapped[str] = mapped_column(SHORT, index=True)
    severity: Mapped[str] = mapped_column(SHORT, index=True)
    policy_id: Mapped[str | None] = mapped_column(ForeignKey("policies.id"))
    detail_json: Mapped[Any | None] = mapped_column(JsonText, nullable=True)
    resolved_at: Mapped[datetime | None] = mapped_column(UtcTimestamp)
    created_at: Mapped[datetime] = mapped_column(UtcTimestamp, default=utc_now)


class AuditLog(Base):
    """DB-017 — the append-only hash chain.

    `seq` is an autoincrementing integer rather than a UUID because the chain
    needs a total order, and `prev_hash` links each record to the one before it
    so any edit to history breaks every hash after it (API-061).
    """

    __tablename__ = "audit_log"

    seq: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    occurred_at: Mapped[datetime] = mapped_column(UtcTimestamp, index=True, default=utc_now)
    correlation_id: Mapped[str | None] = mapped_column(ID, index=True)
    actor: Mapped[str] = mapped_column(SHORT, index=True)
    action: Mapped[str] = mapped_column(SHORT)
    entity_type: Mapped[str | None] = mapped_column(SHORT)
    entity_id: Mapped[str | None] = mapped_column(ID)
    before_json: Mapped[Any | None] = mapped_column(JsonText, nullable=True)
    after_json: Mapped[Any | None] = mapped_column(JsonText, nullable=True)
    prev_hash: Mapped[str | None] = mapped_column(HASH)
    record_hash: Mapped[str] = mapped_column(HASH, nullable=False)
    created_at: Mapped[datetime] = mapped_column(UtcTimestamp, default=utc_now)


class CalibrationRecord(Base):
    """DB-018 — was the model's stated confidence any good? (Brier score)"""

    __tablename__ = "calibration_records"

    id: Mapped[str] = mapped_column(ID, primary_key=True, default=new_id)
    policy_id: Mapped[str] = mapped_column(ForeignKey("policies.id"), unique=True)
    predicted_confidence: Mapped[Decimal | None] = mapped_column(Money)
    actual_outcome: Mapped[int | None] = mapped_column(Integer)
    brier_contribution: Mapped[Decimal | None] = mapped_column(Money)
    settled_at: Mapped[datetime | None] = mapped_column(UtcTimestamp)
    created_at: Mapped[datetime] = mapped_column(UtcTimestamp, default=utc_now)


class SystemEvent(Base):
    """DB-019 — structured operational log, including every cycle abort."""

    __tablename__ = "system_events"
    __table_args__ = (Index("ix_system_events_at_level", "occurred_at", "level"),)

    id: Mapped[str] = mapped_column(ID, primary_key=True, default=new_id)
    occurred_at: Mapped[datetime] = mapped_column(UtcTimestamp, default=utc_now)
    level: Mapped[str] = mapped_column(SHORT)
    component: Mapped[str] = mapped_column(SHORT)
    event: Mapped[str] = mapped_column(SHORT)
    detail_json: Mapped[Any | None] = mapped_column(JsonText, nullable=True)
    correlation_id: Mapped[str | None] = mapped_column(ID, index=True)
    created_at: Mapped[datetime] = mapped_column(UtcTimestamp, default=utc_now)


class SchedulerRun(Base):
    """DB-020 — one row per job execution.

    FR-026 shows up here as data: `NO_ACTION` is a distinct status from
    `ERROR`, because a cycle that declined to trade succeeded.
    """

    __tablename__ = "scheduler_runs"
    __table_args__ = (
        CheckConstraint(
            "status IN ('SUCCESS','NO_ACTION','ABORTED','ERROR')", name="ck_scheduler_status"
        ),
    )

    id: Mapped[str] = mapped_column(ID, primary_key=True, default=new_id)
    job_name: Mapped[str] = mapped_column(SHORT, index=True)
    correlation_id: Mapped[str | None] = mapped_column(ID, index=True)
    started_at: Mapped[datetime] = mapped_column(UtcTimestamp, default=utc_now)
    finished_at: Mapped[datetime | None] = mapped_column(UtcTimestamp)
    status: Mapped[str] = mapped_column(SHORT, index=True)
    outcome: Mapped[str | None] = mapped_column(SHORT)
    error_message: Mapped[str | None] = mapped_column(Text)
    duration_ms: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(UtcTimestamp, default=utc_now)


ALL_TABLES = (
    SystemConfig,
    Account,
    Strategy,
    MarketSnapshotRow,
    Candidate,
    UnderwritingDecision,
    KernelDecision,
    RiskCheck,
    Policy,
    PolicyLeg,
    Order,
    Fill,
    PositionSnapshot,
    PnlRecord,
    Reserve,
    RiskEvent,
    AuditLog,
    CalibrationRecord,
    SystemEvent,
    SchedulerRun,
)
