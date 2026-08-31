from fastapi.testclient import TestClient

from underwriter.main import app


def test_health_is_live() -> None:
    response = TestClient(app).get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"
