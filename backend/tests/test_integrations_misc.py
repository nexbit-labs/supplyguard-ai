import pytest
from django.core.management import call_command
from rest_framework.test import APIClient

from apps.core.models import IdSequence

pytestmark = pytest.mark.django_db


def test_openapi_builds_with_the_three_routes(capfd):
    r = APIClient().get("/api/schema/?format=json")
    assert r.status_code == 200
    paths = r.json()["paths"]
    assert "post" in paths["/api/v1/events/disruptions"]
    assert "post" in paths["/api/v1/demo/simulate-disruption"]
    assert "get" in paths["/api/v1/integrations/status"]
    err = capfd.readouterr().err
    assert "Warning" not in err and "Error" not in err


def test_evt_sequence_is_seeded_by_the_migration():
    assert IdSequence.objects.get(prefix="EVT").last_value == 900000


def test_no_missing_migrations():
    call_command("makemigrations", "--check", "--dry-run", verbosity=0)
