"""Server wiring — the routes exist, the auth gate holds, the demo weapon works."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from underwriter.server import UnsafeConfigurationError, app, assert_paper_trading, cors_origins

client = TestClient(app, raise_server_exceptions=False)


def test_health_is_live_at_the_root() -> None:
    """OPS-021: the container probe must not depend on the API prefix."""
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_every_response_carries_a_correlation_id() -> None:
    response = client.get("/api/system/status")
    assert response.headers["X-Correlation-ID"].startswith("req_")


def test_an_inbound_correlation_id_is_honoured() -> None:
    response = client.get("/api/system/status", headers={"X-Correlation-ID": "req_from_caller"})
    assert response.headers["X-Correlation-ID"] == "req_from_caller"


def test_readiness_reports_degraded_while_dependencies_are_unbuilt() -> None:
    response = client.get("/api/health/deep")
    assert response.status_code == 503
    body = response.json()
    assert body["status"] == "degraded"
    assert body["checks"]["kernel"]["status"] == "ok"


def test_the_limit_table_comes_from_the_kernel_itself() -> None:
    """API-041 must not be a hand-maintained copy of the rules."""
    body = client.get("/api/risk/limits").json()
    rule_ids = {rule["rule_id"] for rule in body["rules"]}

    assert "SK-004" in rule_ids
    assert "SK-011" in rule_ids
    assert body["limits"]["SK-011_min_dte_at_entry"] == 7


def test_redacted_config_exposes_no_secrets() -> None:
    body = client.get("/api/config").json()
    serialised = str(body).lower()
    for secret in ("groq_api_key", "operator_token", "kernel_signing_secret", "alpaca_secret"):
        assert secret not in serialised


def test_the_honesty_statement_is_served_with_the_config() -> None:
    """§15.7 is normative and must appear wherever the system describes itself."""
    assert "no edge is claimed" in client.get("/api/config").json()["honesty_statement"]


def test_unbuilt_endpoints_explain_themselves_rather_than_inventing_data() -> None:
    """UI-006: an empty panel must say why it is empty."""
    response = client.get("/api/dashboard/overview")
    assert response.status_code == 503

    error = response.json()["error"]
    assert error["code"] == "NOT_YET_IMPLEMENTED"
    assert "persistence" in error["blocked_on"]


def test_write_endpoints_refuse_without_an_operator_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OPERATOR_TOKEN", "a-token-of-at-least-32-characters-long")
    response = client.post("/api/kernel/simulate", json={})
    assert response.status_code == 401


def test_write_endpoints_refuse_a_wrong_operator_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OPERATOR_TOKEN", "a-token-of-at-least-32-characters-long")
    response = client.post(
        "/api/kernel/simulate", json={}, headers={"Authorization": "Bearer wrong"}
    )
    assert response.status_code == 403


def test_write_endpoints_are_disabled_when_no_token_is_configured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Fail closed: an unset token disables writes rather than opening them."""
    monkeypatch.delenv("OPERATOR_TOKEN", raising=False)
    response = client.post("/api/kernel/simulate", json={})
    assert response.status_code == 503


def test_read_endpoints_need_no_token() -> None:
    """UI-003: judges see everything; only the controls are gated."""
    for path in ("/health", "/api/system/status", "/api/config", "/api/risk/limits"):
        assert client.get(path).status_code == 200


def test_validation_failure_returns_field_level_detail(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """API-004: 422 with the offending fields named."""
    monkeypatch.setenv("OPERATOR_TOKEN", "a-token-of-at-least-32-characters-long")
    response = client.post(
        "/api/kernel/simulate",
        json={"underlying": "not a ticker"},
        headers={"Authorization": "Bearer a-token-of-at-least-32-characters-long"},
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "VALIDATION_FAILED"
    assert response.json()["error"]["fields"]


# ---------------------------------------------------------------------------
# API-045 — the demo weapon, running the real kernel
# ---------------------------------------------------------------------------


def simulate(monkeypatch: pytest.MonkeyPatch, **body: object) -> dict:
    token = "a-token-of-at-least-32-characters-long"
    monkeypatch.setenv("OPERATOR_TOKEN", token)
    response = client.post(
        "/api/kernel/simulate", json=body, headers={"Authorization": f"Bearer {token}"}
    )
    assert response.status_code == 200, response.text
    return response.json()


def test_045_a_sound_proposal_is_approved_and_still_not_executed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    body = simulate(monkeypatch)
    assert body["verdict"]["verdict"] == "APPROVE"
    assert body["executed"] is False


def test_045_a_catastrophic_proposal_is_rejected_citing_the_rules(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """TEST-035: 90% of NAV, naked, 0DTE, hallucinated candidate."""
    body = simulate(
        monkeypatch,
        max_loss="90000.00",
        dte=0,
        naked=True,
        candidate_is_known=False,
        requested_contracts=50,
    )
    verdict = body["verdict"]

    assert verdict["verdict"] == "REJECT"
    assert verdict["approved_contracts"] == 0
    assert body["executed"] is False
    assert {
        "POSITION_LOSS_LIMIT",
        "UNDEFINED_RISK",
        "DTE_TOO_SHORT",
        "LLM_OUTPUT_INVALID",
    } <= set(verdict["reject_reasons"])


def test_045_never_returns_the_signature(monkeypatch: pytest.MonkeyPatch) -> None:
    """An endpoint that returns a signature is an endpoint that can leak one."""
    body = simulate(monkeypatch)
    assert body["verdict"]["signed"] is True
    assert "signature" not in str(body)


def test_045_reports_every_rule_not_only_the_failures(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    body = simulate(monkeypatch, dte=0)
    verdict = body["verdict"]
    assert verdict["rules_evaluated"] == 26  # 25 rules plus SK-020 after sizing
    assert verdict["rules_failed"] >= 1
    assert any(rule["rule_id"] == "SK-011" and not rule["passed"] for rule in verdict["rules"])


# ---------------------------------------------------------------------------
# Boot refusal
# ---------------------------------------------------------------------------


def test_the_app_refuses_to_boot_without_paper_trading(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """SEC-004. The check is positive: a missing value is not permission."""
    monkeypatch.setenv("ALPACA_PAPER_TRADE", "false")
    with pytest.raises(UnsafeConfigurationError, match="paper-only"):
        assert_paper_trading()


def test_production_refuses_a_wildcard_cors_origin(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.setenv("CORS_ORIGINS", "https://the-underwriter.fly.dev,*")
    with pytest.raises(UnsafeConfigurationError, match="SEC-007"):
        cors_origins()
