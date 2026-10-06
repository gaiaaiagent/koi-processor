"""Configuration and optional local login shared by the content dashboards."""

import os
import re
import secrets
from pathlib import Path

import yaml
from flask import abort, redirect, render_template, request, session, url_for
from werkzeug.security import check_password_hash


def load_dashboard_config(defaults):
    """Read the repository config, or an explicitly selected deployment config."""
    selected = os.environ.get("DASHBOARD_CONFIG_PATH")
    path = (
        Path(selected) if selected
        else Path(__file__).resolve().parents[2] / "config/dashboard_config.yaml"
    )
    if not path.exists():
        if selected:
            raise RuntimeError("DASHBOARD_CONFIG_PATH does not point to an existing file")
        return defaults
    with path.open() as handle:
        loaded = yaml.safe_load(handle) or {}
    if not isinstance(loaded, dict) or not isinstance(loaded.get("dashboard", {}), dict):
        raise RuntimeError("Dashboard configuration must contain a dashboard mapping")
    config = {**defaults, **loaded}
    config["dashboard"] = {**defaults.get("dashboard", {}), **loaded.get("dashboard", {})}
    return config


def configure_dashboard_auth(app, config):
    """Require explicit credentials and a persistent signing key for local login.

    When local login is disabled, callers may use a trusted reverse proxy or a
    loopback deployment. No built-in credentials are accepted in that mode.
    """
    enabled = config["dashboard"].get("auth_enabled", False)
    override = os.environ.get("DASHBOARD_AUTH_ENABLED")
    if override is not None:
        value = override.strip().lower()
        if value not in ("true", "false", "1", "0"):
            raise RuntimeError("DASHBOARD_AUTH_ENABLED must be true, false, 1 or 0")
        enabled = value in ("true", "1")
    if not isinstance(enabled, bool):
        raise RuntimeError("dashboard.auth_enabled must be a boolean")

    secret = os.environ.get("FLASK_SECRET_KEY")
    if enabled and not secret:
        raise RuntimeError("FLASK_SECRET_KEY is required when dashboard authentication is enabled")
    if secret and (len(secret) < 32 or secret == "dev-secret-key-change-in-production"):
        raise RuntimeError("FLASK_SECRET_KEY must be a private random value of at least 32 characters")

    username = os.environ.get("DASHBOARD_ADMIN_USERNAME", "").strip()
    password_hash = os.environ.get("DASHBOARD_ADMIN_PASSWORD_HASH", "").strip()
    if enabled:
        if not username:
            raise RuntimeError("DASHBOARD_ADMIN_USERNAME is required when dashboard authentication is enabled")
        try:
            method, salt, digest = password_hash.split("$")
            if (
                method.split(":")[0] not in ("scrypt", "pbkdf2")
                or not salt
                or not re.fullmatch(r"[0-9a-f]{64,}", digest)
            ):
                raise ValueError("Invalid password hash")
            # Validate the selected Werkzeug method before accepting requests.
            check_password_hash(password_hash, "dashboard-configuration-check")
        except (ValueError, TypeError, OverflowError):
            raise RuntimeError("DASHBOARD_ADMIN_PASSWORD_HASH must be a valid Werkzeug scrypt or pbkdf2 hash") from None

    config["dashboard"]["auth_enabled"] = enabled
    app.config.update(
        SECRET_KEY=secret or secrets.token_hex(32),
        DASHBOARD_AUTH_ENABLED=enabled,
        DASHBOARD_ADMIN_USERNAME=username,
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
    )

    @app.route("/login", methods=["GET", "POST"])
    def login():
        if not enabled:
            abort(404)
        if request.method == "POST":
            candidate = request.form.get("username", "")
            password = request.form.get("password", "")
            valid_password = check_password_hash(password_hash, password)
            valid_username = secrets.compare_digest(candidate.encode("utf-8"), username.encode("utf-8"))
            if valid_username and valid_password:
                session.clear()
                session["user"] = username
                return redirect(url_for("index"))
            return render_template("login.html", error="Invalid credentials"), 401
        return render_template("login.html")

    @app.route("/logout")
    def logout():
        session.clear()
        return redirect(url_for("login" if enabled else "index"))


def dashboard_authenticated(app):
    """Accept only the configured admin's signed session when local login is on."""
    return (
        not app.config["DASHBOARD_AUTH_ENABLED"]
        or session.get("user") == app.config["DASHBOARD_ADMIN_USERNAME"]
    )
