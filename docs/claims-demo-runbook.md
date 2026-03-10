# Claims Engine Demo — Production Runbook

**Server:** `darren@202.61.196.119`
**Last updated:** 2026-03-10

---

## Architecture

```
Browser → Docker nginx (ports 80/443)
           ├─ GET /claims           → static file (claims-demo.html)
           ├─ /claims/*             → proxy to 172.17.0.1:8352
           ├─ /entities             → proxy to 172.17.0.1:8352
           ├─ /entity-search        → proxy to 172.17.0.1:8352
           └─ /* (catch-all)        → auth + ElizaOS agents (port 3000)

Host: personal_ingest_api on port 8352 (claims + entities + extraction)
      adaptive_extraction_api on port 8351 (unchanged, separate service)
```

## Endpoints

| Endpoint | URL |
|----------|-----|
| Portal | https://regen.gaiaai.xyz/claims |
| Claims list | https://regen.gaiaai.xyz/claims/?limit=200 |
| AI extraction | POST https://regen.gaiaai.xyz/claims/extract |
| Entity search | https://regen.gaiaai.xyz/entity-search?query=Regen&limit=5 |
| Entity list | https://regen.gaiaai.xyz/entities?entity_type=ORGANIZATION&limit=10 |
| Health (internal) | `curl http://127.0.0.1:8352/health` |

## Files on Production

| File | Purpose |
|------|---------|
| `/opt/projects/GAIA/config/nginx-ssl.conf` | Docker nginx config (lines ~963-1003: claims blocks) |
| `/opt/projects/GAIA/graph/claims-demo.html` | Static portal HTML served by nginx |
| `/opt/projects/koi-processor/start-claims-api.sh` | Startup script (sources .env, sets POSTGRES_URL, runs uvicorn on 8352) |
| `/opt/projects/koi-processor/.env` | Env vars (ANTHROPIC_API_KEY, OPENAI_API_KEY, DB creds) |
| `/tmp/claims-api.log` | API runtime logs |

## Start / Stop / Restart

```bash
# Start
ssh darren@202.61.196.119 \
  "nohup /opt/projects/koi-processor/start-claims-api.sh > /tmp/claims-api.log 2>&1 &"

# Stop
ssh darren@202.61.196.119 "pkill -f 'uvicorn api.personal_ingest.*8352'"

# Restart
ssh darren@202.61.196.119 \
  "pkill -f 'uvicorn api.personal_ingest.*8352'; sleep 2; \
   nohup /opt/projects/koi-processor/start-claims-api.sh > /tmp/claims-api.log 2>&1 &"

# Verify (wait ~10s for startup)
ssh darren@202.61.196.119 "curl -s http://127.0.0.1:8352/health | python3 -m json.tool"
```

**Note:** The process does NOT survive server reboots. For persistence, create a systemd unit.

## Nginx

The nginx serving HTTPS is a **Docker container**, not the host nginx service.

```bash
# Test config
ssh darren@202.61.196.119 "docker exec nginx nginx -t"

# Reload after config changes
ssh darren@202.61.196.119 "docker restart nginx"
```

**Important:** Always use `docker restart nginx`, not `nginx -s reload`. The config file is a bind mount — if you edit it with `sed -i` or similar tools that replace the file (new inode), the container won't see changes until restart.

## Database

**Connection:** `PGPASSWORD=postgres psql -h localhost -p 5433 -U postgres -d eliza`

### Schema changes applied (2026-03-10)

| Change | Purpose |
|--------|---------|
| Created `claims` table (with `tx_hash`) | Core claims storage |
| Created `claim_state_log` table | Verification audit trail |
| Added `created_at TIMESTAMPTZ` to `entity_registry` | Required by list/search endpoints |
| Added `node_private BOOLEAN DEFAULT FALSE` to `entity_registry` | Required by list/search endpoints |
| Made `embedding` nullable on `entity_registry` | Allows claim entity creation without embeddings |
| Inserted 3 predicates into `allowed_predicates` | `makes_claim`, `evidences_claim`, `supersedes_claim` |

### Verify seed data

```sql
SELECT claim_type, verification, LEFT(statement, 80) AS statement
FROM claims
WHERE source_document LIKE 'claims-demo-portal:%'
ORDER BY created_at;
-- Expect: 3 rows (governance, ecological, financial)
```

### Entity types

Production uses **UPPERCASE** entity types (`ORGANIZATION`, `PERSON`, `CONCEPT`). The demo portal's entity typeahead works with both cases via the search endpoint, but the entity list filter requires the exact case.

## Cleanup

```sql
-- 1. Remove state log entries for demo claims
DELETE FROM claim_state_log
WHERE claim_rid IN (
  SELECT claim_rid FROM claims
  WHERE source_document LIKE 'claims-demo-portal:%'
);

-- 2. Remove demo claims
DELETE FROM claims
WHERE source_document LIKE 'claims-demo-portal:%';

-- 3. (Optional) Remove claim entity entries
DELETE FROM entity_registry
WHERE fuseki_uri LIKE 'orn:personal-koi.entity:claim-%';

-- 4. (Optional) Remove demo-created org entities
DELETE FROM entity_registry
WHERE fuseki_uri IN (
  'https://regen.network/org/cec_demo_001',
  'https://regen.network/org/zfp_demo_001'
);
```

## Update demo.html

When the portal HTML changes locally:

```bash
# Upload to production
scp ~/projects/regenai/koi-processor/static/demo.html \
  darren@202.61.196.119:/opt/projects/koi-processor/static/demo.html

# Copy to nginx-mounted location
ssh darren@202.61.196.119 \
  "cp /opt/projects/koi-processor/static/demo.html \
      /opt/projects/GAIA/graph/claims-demo.html"
```

No nginx reload needed — the static file is served directly from disk.

## Full Rollback

```bash
# 1. Stop claims API
ssh darren@202.61.196.119 "pkill -f 'uvicorn api.personal_ingest.*8352'"

# 2. Remove nginx blocks
# Edit /opt/projects/GAIA/config/nginx-ssl.conf
# Delete the "Claims Engine Demo Portal" section (~lines 963-1003)
ssh darren@202.61.196.119 "docker restart nginx"

# 3. Run cleanup SQL (see above)

# 4. Remove static file
ssh darren@202.61.196.119 "rm /opt/projects/GAIA/graph/claims-demo.html"
```
