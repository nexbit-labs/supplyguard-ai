import json

import pytest
from rest_framework.test import APIClient

pytestmark = pytest.mark.django_db


def test_openapi_has_settings_users_me_audit(capfd):
    r = APIClient().get("/api/schema/?format=json")
    assert r.status_code == 200
    paths = json.loads(r.content)["paths"]
    assert set(paths["/api/v1/settings"]) >= {"get", "patch"}
    assert set(paths["/api/v1/users"]) >= {"get", "post"}
    assert "/api/v1/me" in paths and "/api/v1/audit" in paths
    assert "Warning" not in capfd.readouterr().err
