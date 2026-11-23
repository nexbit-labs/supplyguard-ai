import uuid

import pytest
from django.test import Client, override_settings


@pytest.fixture(autouse=True)
def _urlconf():
    with override_settings(ROOT_URLCONF="tests.urls_correlation"):
        yield


@pytest.fixture
def client():
    return Client(raise_request_exception=False)


def test_api_404_is_contract_json(client):
    cid = str(uuid.uuid4())
    response = client.get("/api/nope", HTTP_X_CORRELATION_ID=cid)
    assert response.status_code == 404
    error = response.json()["error"]
    assert error["code"] == "NOT_FOUND"
    assert set(error) == {"code", "message", "details", "correlation_id"}
    assert error["correlation_id"] == response["X-Correlation-ID"] == cid


def test_api_500_is_generic_and_logged(client, caplog):
    cid = str(uuid.uuid4())
    with caplog.at_level("ERROR"):
        response = client.get("/api/boom", HTTP_X_CORRELATION_ID=cid)
    assert response.status_code == 500
    body = response.json()
    assert body["error"]["code"] == "INTERNAL_ERROR"
    assert body["error"]["correlation_id"] == response["X-Correlation-ID"] == cid
    assert "secret-exception-text" not in response.content.decode()
    assert "Traceback" not in response.content.decode()
    # The exception itself is logged server-side, exactly once (by django.request).
    logged = [
        r for r in caplog.records if r.exc_info and "secret-exception-text" in str(r.exc_info[1])
    ]
    assert len(logged) == 1


def test_non_api_404_is_not_json_error_body(client):
    response = client.get("/not-an-api-path")
    assert response.status_code == 404
    assert b'"error"' not in response.content
    assert response["X-Correlation-ID"]


def test_non_api_500_keeps_django_default(client):
    response = client.get("/boom-html")
    assert response.status_code == 500
    assert b'"error"' not in response.content


@pytest.mark.parametrize(
    ("path", "status", "code"),
    [("/api/forbidden", 403, "FORBIDDEN"), ("/api/bad", 400, "VALIDATION_ERROR")],
)
def test_api_400_403_are_contract_json(client, path, status, code):
    cid = str(uuid.uuid4())
    response = client.get(path, HTTP_X_CORRELATION_ID=cid)
    assert response.status_code == status
    error = response.json()["error"]
    assert error["code"] == code
    assert error["correlation_id"] == response["X-Correlation-ID"] == cid
    assert "secret-reason" not in response.content.decode()


@pytest.mark.parametrize("path", ["/forbidden-html", "/bad-html"])
def test_non_api_400_403_keep_django_default(client, path):
    response = client.get(path)
    assert response.status_code in (400, 403)
    assert b'"error"' not in response.content


def test_drf_errors_use_header_id(client):
    cid = str(uuid.uuid4())
    response = client.get("/api/protected", HTTP_X_CORRELATION_ID=cid)
    assert response.status_code == 401
    assert response.json()["error"]["correlation_id"] == cid
