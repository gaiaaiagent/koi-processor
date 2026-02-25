# KOI Configuration

## OAuth Setup (Required for Google Drive Access)

### For Production Server Operators

1. **Get OAuth Credentials from GCP:**
   - Go to [GCP Console Credentials](https://console.cloud.google.com/apis/credentials)
   - Create OAuth 2.0 Client ID (Web Application)
   - Set redirect URI: `https://your-domain.com/api/koi/auth/callback`
   - Download the JSON file

2. **Install Credentials:**
   ```bash
   cp /path/to/downloaded/client_secret_*.json config/client_secret.json
   ```

3. **Verify:**
   ```bash
   ls -la config/client_secret.json  # Should exist
   ```

### For Local Development

If you're just developing and don't need Google Drive integration:
- OAuth endpoints will be available but won't work without credentials
- Public data queries will work fine
- Skip the OAuth setup

### Other Configuration Files

- `dashboard_config.yaml` - Created automatically by setup.sh
- `curator_config.yaml` - Curator settings
- `services.json` - Service definitions
- `personal.env.example` - Local/personal KOI config template (includes KOI-net + optional TerminusDB vars)

## Local Env Loading

Use this pattern when running processes that spawn child processes:

```bash
set -a; source config/personal.env; set +a
```

This safely exports all variables (including quoted values) to child processes.
