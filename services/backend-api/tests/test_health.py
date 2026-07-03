from fastapi.testclient import TestClient

from app.main import app


def test_health_check_returns_ok() -> None:
    with TestClient(app) as client:
        response = client.get("/api/v1/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_unbuilt_endpoint_returns_problem_json() -> None:
    with TestClient(app) as client:
        response = client.get("/api/v1/market/state")

    assert response.status_code == 501
    assert response.headers["content-type"] == "application/problem+json"
    assert response.json()["status"] == 501
