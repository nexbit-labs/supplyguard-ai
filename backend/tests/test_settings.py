import importlib

import environ
import pytest
from django.contrib.auth import get_user_model
from django.core.exceptions import ImproperlyConfigured
from django.core.management import call_command

from config import settings as settings_module


def test_custom_user_model_is_active():
    assert get_user_model()._meta.label == "accounts.User"


@pytest.mark.django_db
def test_no_missing_migrations():
    call_command("makemigrations", "--check", "--dry-run")


@pytest.fixture
def reload_settings(monkeypatch):
    # settings.py re-reads <repo>/.env on every reload (read_env uses setdefault), which would
    # re-add SECRET_KEY after we delete it. Make these tests independent of a developer's .env.
    monkeypatch.setattr(environ.Env, "read_env", classmethod(lambda cls, *a, **k: None))
    yield lambda: importlib.reload(settings_module)
    monkeypatch.undo()
    importlib.reload(settings_module)  # restore the test configuration


@pytest.mark.parametrize("secret", [None, ""])
def test_secret_key_required_when_debug_off(monkeypatch, reload_settings, secret):
    monkeypatch.setenv("DEBUG", "False")
    if secret is None:
        monkeypatch.delenv("SECRET_KEY", raising=False)
    else:
        monkeypatch.setenv("SECRET_KEY", secret)
    with pytest.raises(ImproperlyConfigured):
        reload_settings()


def test_empty_secret_key_falls_back_only_in_debug(monkeypatch, reload_settings):
    monkeypatch.setenv("DEBUG", "True")
    monkeypatch.setenv("SECRET_KEY", "")
    reload_settings()
    assert settings_module.SECRET_KEY == "dev-insecure-secret-key"
