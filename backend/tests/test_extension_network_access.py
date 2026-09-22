from fastapi.testclient import TestClient

from backend.app.main import app


def test_extension_private_network_preflight() -> None:
    origin = "chrome-extension://abcdefghijklmnopabcdefghijklmnop"

    with TestClient(app) as client:
        response = client.options(
            "/api/v1/asr/stream",
            headers={
                "Origin": origin,
                "Access-Control-Request-Method": "GET",
                "Access-Control-Request-Private-Network": "true",
            },
        )

    assert response.status_code == 204
    assert response.headers["access-control-allow-origin"] == origin
    assert response.headers["access-control-allow-private-network"] == "true"


def test_extension_health_response_has_network_access_headers() -> None:
    origin = "chrome-extension://abcdefghijklmnopabcdefghijklmnop"

    with TestClient(app) as client:
        response = client.get(
            "/api/v1/health",
            headers={"Origin": origin},
        )

    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == origin
    assert response.headers["access-control-allow-private-network"] == "true"
