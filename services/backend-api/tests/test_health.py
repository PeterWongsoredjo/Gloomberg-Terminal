from fastapi.testclient import TestClient

from app.main import app


def test_health_check_returns_ok() -> None:
    with TestClient(app) as client:
        response = client.get("/api/v1/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_error_contract_is_problem_json() -> None:
    """A missing security returns the RFC 9457 problem+json shape, not a bare error."""
    with TestClient(app) as client:
        response = client.get("/api/v1/securities/__NOPE__")

    assert response.status_code == 404
    assert response.headers["content-type"] == "application/problem+json"
    assert response.json()["status"] == 404
