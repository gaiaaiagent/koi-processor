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
- `personal.env.example` - Local/personal KOI config template (includes KOI-net + optional TerminusDB vars; also documents the optional `DOC_EXTRACTOR_*` deep-extraction transport knobs)

### Personal config: `*.example` template pattern

Some config holds personal choices (which publications/feeds/authors to ingest, which
extraction transport to use for a batch). These follow a convention: the **`*.example.*`
file is a committed, generic template**; copy it to the un-suffixed name (which is
**gitignored** — never committed) and fill in your values.

- `substack_publications.example.yaml` → `substack_publications.yaml` — Substacks to ingest (see the "Substack corpus ingestion" section in the repo `CLAUDE.md`)
- `rss_feeds.example.yaml` → `rss_feeds.yaml` — RSS/Atom feeds to poll
- `research_author_sensors.example.yaml` → `research_author_sensors.yaml` — research authors + local corpus path to watch (lives in the runtime checkout that runs the sensor)
- `extract-batch.env.example` → `extract-batch.env` — transport override for one-off batch deep-extraction via `scripts/run_batch_extract.sh`

## Local Env Loading

Use this pattern when running processes that spawn child processes:

```bash
set -a; source config/personal.env; set +a
```

This safely exports all variables (including quoted values) to child processes.
