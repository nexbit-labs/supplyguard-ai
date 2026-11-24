"""The two Boomi callbacks appear in the generated OpenAPI schema."""
import pytest
from rest_framework.test import APIClient

pytestmark = pytest.mark.django_db


def test_openapi_schema_lists_both_callbacks():
    response = APIClient().get("/api/schema/")
    assert response.status_code == 200
    text = response.content.decode()
    assert "/api/v1/internal/risk-cases" in text
    assert "/api/v1/internal/executions/{execution_id}/result" in text
