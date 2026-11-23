import pytest

from tests.factories import make_tenant


@pytest.fixture
def tenant_a(db):
    return make_tenant("TEN-A")


@pytest.fixture
def tenant_b(db):
    return make_tenant("TEN-B")
