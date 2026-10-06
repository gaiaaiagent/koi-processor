# Content dashboard authentication

Both `src/content/content_dashboard.py` and
`src/content/content_dashboard_enhanced.py` read the repository's
`config/dashboard_config.yaml`. Set `DASHBOARD_CONFIG_PATH` to select a separate
deployment configuration; an explicitly selected missing file stops startup.
Both entry points require `DB_PASSWORD` from the environment.

Local login remains optional. A dashboard behind a trusted authenticating
reverse proxy can set `DASHBOARD_AUTH_ENABLED=false`. Loopback development can
use the same setting. Without a configured session secret, this mode creates a
new random secret for each process and disables the `/login` endpoint; it has
no built-in admin password. This mode relies on the proxy or loopback boundary
for access control.

To enable local login, set `DASHBOARD_AUTH_ENABLED=true`,
`DASHBOARD_ADMIN_USERNAME`, `DASHBOARD_ADMIN_PASSWORD_HASH`, and
`FLASK_SECRET_KEY` in the deployment environment. The environment override takes
precedence over `dashboard.auth_enabled` in YAML. Missing credentials, invalid
hashes, misspelled boolean overrides, and short or former public signing keys
stop startup. Credentials and signing secrets do not belong in YAML or git.

Generate a Werkzeug password hash using a password prompt:

```sh
python -c 'from getpass import getpass; from werkzeug.security import generate_password_hash; print(generate_password_hash(getpass()))'
```

Generate a private persistent session secret:

```sh
python -c 'import secrets; print(secrets.token_hex(32))'
```

Store the outputs in the deployment's secret environment. Preserve the literal
`$` characters in the password hash. Werkzeug scrypt and PBKDF2 hashes are
accepted. Keep the signing secret stable across process restarts/workers;
changing it invalidates existing sessions. Serve local login over HTTPS in
remote deployments.

The shared `/login` and `/logout` routes now work in both dashboards. Existing
routes using `require_auth` accept only the configured admin's signed session.
The separate bearer-token digest gates remain in place. This change does not
add local login to routes or Socket.IO events that were previously ungated.

Run the isolated route regression tests:

```sh
python -m pytest tests/test_dashboard_auth.py -q
```

The tests prohibit DB and network connections. They exercise rendered login
and dashboard pages, valid and invalid login, logout, configuration precedence,
startup failure, proxy mode, and cookies signed with the former public keys.
