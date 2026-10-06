"""Exercise both dashboard entry points without database or network access."""

import importlib.util
import socket
import sys
import uuid
from pathlib import Path

import dotenv
import psycopg2
import pytest
import yaml
from flask import Flask
from werkzeug.security import generate_password_hash


ROOT = Path(__file__).resolve().parents[1]
DASHBOARDS = ("content_dashboard", "content_dashboard_enhanced")
AUTH_ENV = (
    "DASHBOARD_AUTH_ENABLED", "DASHBOARD_ADMIN_USERNAME",
    "DASHBOARD_ADMIN_PASSWORD_HASH", "FLASK_SECRET_KEY", "DASHBOARD_CONFIG_PATH",
)
TEST_PASSWORD = "isolated-dashboard-password"
TEST_SECRET = "isolated-session-secret-" + "x" * 40


@pytest.fixture(params=DASHBOARDS)
def dashboard_loader(request, monkeypatch, tmp_path):
    def forbidden(*args, **kwargs):
        pytest.fail("Dashboard auth tests must not access a database or network")

    monkeypatch.setattr(psycopg2, "connect", forbidden)
    monkeypatch.setattr(socket.socket, "connect", forbidden)
    monkeypatch.setattr(socket.socket, "connect_ex", forbidden)
    monkeypatch.setattr(dotenv, "load_dotenv", lambda *args, **kwargs: False)
    monkeypatch.setenv("DB_PASSWORD", "isolated-test-only")
    for key in AUTH_ENV:
        monkeypatch.delenv(key, raising=False)

    def load(*, auth=True, overrides=None, file_auth=False, config_file=True):
        settings = {
            "dashboard": {"port": 8400, "refresh_interval": 30, "auth_enabled": file_auth}
        }
        path = tmp_path / "dashboard_config.yaml"
        path.write_text(yaml.safe_dump(settings))
        env = {
            "DASHBOARD_AUTH_ENABLED": "true" if auth else "false",
            "DASHBOARD_ADMIN_USERNAME": "operator",
            "DASHBOARD_ADMIN_PASSWORD_HASH": generate_password_hash(TEST_PASSWORD),
            "FLASK_SECRET_KEY": TEST_SECRET,
            "DASHBOARD_CONFIG_PATH": str(path) if config_file else str(tmp_path / "missing.yaml"),
        }
        env.update(overrides or {})
        for key, value in env.items():
            if value is None:
                monkeypatch.delenv(key, raising=False)
            else:
                monkeypatch.setenv(key, value)
        name = "dashboard_test_" + uuid.uuid4().hex
        spec = importlib.util.spec_from_file_location(
            name, ROOT / "src/content" / (request.param + ".py")
        )
        module = importlib.util.module_from_spec(spec)
        monkeypatch.setitem(sys.modules, name, module)
        spec.loader.exec_module(module)
        module.app.config["TESTING"] = True
        return module

    return load


def assert_denied(response):
    assert response.status_code in (302, 401)
    if response.status_code == 302:
        assert response.headers["Location"].endswith("/login")


def test_login_and_logout_protect_actual_dashboard(dashboard_loader):
    dashboard = dashboard_loader()
    client = dashboard.app.test_client()
    assert_denied(client.get("/"))
    assert client.get("/login").status_code == 200
    assert_denied(client.get("/api/dashboard/overview"))
    result = client.post("/login", data={"username": "operator", "password": TEST_PASSWORD})
    assert result.status_code == 302
    assert result.headers["Location"] == "/"
    assert client.get("/").status_code == 200
    with client.session_transaction() as session:
        assert session["user"] == "operator"
    assert client.get("/logout").status_code == 302
    assert_denied(client.get("/"))


@pytest.mark.parametrize("credentials", [
    {"username": "admin", "password": "regen2025"},
    {"username": "operator", "password": "wrong-password"},
    {"username": "somebody-else", "password": TEST_PASSWORD},
    {"username": "opérator", "password": TEST_PASSWORD},
    {},
])
def test_invalid_credentials_do_not_create_session(dashboard_loader, credentials):
    client = dashboard_loader().app.test_client()
    response = client.post("/login", data=credentials)
    assert response.status_code == 401
    assert b"Invalid credentials" in response.data
    with client.session_transaction() as session:
        assert "user" not in session
    assert_denied(client.get("/"))


@pytest.mark.parametrize("missing", [
    "FLASK_SECRET_KEY", "DASHBOARD_ADMIN_USERNAME", "DASHBOARD_ADMIN_PASSWORD_HASH",
])
def test_auth_enabled_refuses_missing_configuration(dashboard_loader, missing):
    with pytest.raises(RuntimeError, match=missing):
        dashboard_loader(overrides={missing: None})


@pytest.mark.parametrize("secret", [
    "", "dev-secret-key-change-in-production", "dev-secret-key-milestone-b", "too-short",
])
def test_auth_enabled_refuses_public_or_short_secret(dashboard_loader, secret):
    with pytest.raises(RuntimeError, match="FLASK_SECRET_KEY"):
        dashboard_loader(overrides={"FLASK_SECRET_KEY": secret})


@pytest.mark.parametrize("password_hash", ["plaintext-password", "scrypt:invalid$salt$abcd", "sha256$salt$abcd"])
def test_auth_enabled_refuses_invalid_hash(dashboard_loader, password_hash):
    with pytest.raises(RuntimeError, match="DASHBOARD_ADMIN_PASSWORD_HASH"):
        dashboard_loader(overrides={"DASHBOARD_ADMIN_PASSWORD_HASH": password_hash})


@pytest.mark.parametrize("former_secret", [
    "dev-secret-key-change-in-production", "dev-secret-key-milestone-b",
])
def test_cookie_signed_with_former_public_secret_cannot_access_dashboard(dashboard_loader, former_secret):
    dashboard = dashboard_loader()
    attacker_app = Flask("attacker")
    attacker_app.secret_key = former_secret
    serializer = attacker_app.session_interface.get_signing_serializer(attacker_app)
    forged = serializer.dumps({"user": "operator"})
    client = dashboard.app.test_client()
    client.set_cookie("session", forged)
    assert_denied(client.get("/"))
    assert_denied(client.get("/api/dashboard/overview"))


def test_proxy_mode_needs_no_login_and_uses_fresh_secret(dashboard_loader):
    overrides = {"FLASK_SECRET_KEY": None, "DASHBOARD_ADMIN_USERNAME": None, "DASHBOARD_ADMIN_PASSWORD_HASH": None}
    first = dashboard_loader(auth=False, overrides=overrides)
    second = dashboard_loader(auth=False, overrides=overrides)
    assert first.app.secret_key != second.app.secret_key
    assert len(first.app.secret_key) >= 32
    client = first.app.test_client()
    assert client.get("/").status_code == 200
    response = client.post("/login", data={"username": "admin", "password": "regen2025"})
    assert response.status_code == 404
    with client.session_transaction() as session:
        assert "user" not in session


def test_yaml_auth_is_honored_when_env_override_absent(dashboard_loader):
    dashboard = dashboard_loader(file_auth=True, overrides={"DASHBOARD_AUTH_ENABLED": None})
    assert_denied(dashboard.app.test_client().get("/"))


def test_false_env_override_preserves_proxy_mode(dashboard_loader):
    dashboard = dashboard_loader(auth=False, file_auth=True)
    assert dashboard.config["dashboard"]["auth_enabled"] is False
    assert dashboard.app.test_client().get("/").status_code == 200


def test_missing_explicit_config_fails_startup(dashboard_loader):
    with pytest.raises(RuntimeError, match="DASHBOARD_CONFIG_PATH"):
        dashboard_loader(config_file=False)


def test_misspelled_auth_env_fails_startup(dashboard_loader):
    with pytest.raises(RuntimeError, match="DASHBOARD_AUTH_ENABLED"):
        dashboard_loader(overrides={"DASHBOARD_AUTH_ENABLED": "treu"})


def test_both_dashboards_require_explicit_database_password(dashboard_loader):
    with pytest.raises(RuntimeError, match="DB_PASSWORD"):
        dashboard_loader(overrides={"DB_PASSWORD": None})


def test_repository_config_is_used_by_default(dashboard_loader):
    dashboard = dashboard_loader(auth=False, overrides={"DASHBOARD_CONFIG_PATH": None})
    assert dashboard.config["thresholds"]["podcast"]["target_duration_min"] == 18
    assert dashboard.app.test_client().get("/").status_code == 200


def test_pbkdf2_hash_can_authenticate(dashboard_loader):
    dashboard = dashboard_loader(overrides={
        "DASHBOARD_ADMIN_PASSWORD_HASH": generate_password_hash(TEST_PASSWORD, method="pbkdf2:sha256:600000")
    })
    client = dashboard.app.test_client()
    assert client.post("/login", data={"username": "operator", "password": TEST_PASSWORD}).status_code == 302
    assert client.get("/").status_code == 200


def test_session_for_other_user_is_not_an_admin_session(dashboard_loader):
    client = dashboard_loader().app.test_client()
    with client.session_transaction() as session:
        session["user"] = "another-user"
    assert_denied(client.get("/"))
