import pytest
from django.core.cache import cache

from tests.factories import make_tenant
from tests.helpers import set_throttle_rates


@pytest.fixture(autouse=True)
def _auth_throttle_off(monkeypatch):
    """Login throttling must not leak between tests: high rate + empty cache by default
    (the throttle test sets a low rate itself)."""
    set_throttle_rates(monkeypatch)
    cache.clear()
    yield
    cache.clear()


@pytest.fixture(autouse=True)
def _fast_password_hasher(settings):
    """Test-only: PBKDF2 makes every make_user() slow. Hashing is still real (MD5 here)."""
    settings.PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]


@pytest.fixture
def tenant_a(db):
    return make_tenant("TEN-A")


@pytest.fixture
def tenant_b(db):
    return make_tenant("TEN-B")
