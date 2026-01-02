## Fuseki Provenance Writes (KOI Event Bridge v2)

This runbook covers authentication for provenance triple writes from `koi_event_bridge_v2.py` to Apache Jena Fuseki.

### What This Affects

- **Affects:** SPARQL Update writes to Fuseki (e.g., CAT receipt provenance / transformation tracking).
- **Does NOT affect:** document embeddings to Postgres (`koi_embeddings`), KOI search, or the weekly digest.

### Configuration

`koi-processor/src/core/provenance_to_rdf.py` supports optional **Basic Auth** via environment variables:

- `FUSEKI_URL` (default: `http://localhost:3030`)
- `FUSEKI_DATASET` (default: `koi`)
- `FUSEKI_USER` (optional)
- `FUSEKI_PASSWORD` (optional)

When `FUSEKI_USER` and `FUSEKI_PASSWORD` are set, the event bridge will authenticate to:

- Update: `${FUSEKI_URL}/${FUSEKI_DATASET}/update`
- Query: `${FUSEKI_URL}/${FUSEKI_DATASET}/sparql`

### Production Steps

1) SSH to prod:
```bash
ssh darren@202.61.196.119
```

2) Set credentials in `/opt/projects/koi-processor/.env`:
```bash
FUSEKI_URL=http://localhost:3030
FUSEKI_DATASET=koi
FUSEKI_USER=admin
FUSEKI_PASSWORD=admin
```

3) Restart the event bridge:
```bash
sudo systemctl restart koi-event-bridge.service
```

4) Verify Fuseki accepts authenticated update (expects HTTP 204):
```bash
curl -i -u "$FUSEKI_USER:$FUSEKI_PASSWORD" \
  -H "Content-Type: application/sparql-update" \
  --data "INSERT DATA { <urn:test> <urn:p> <urn:o> . }" \
  "$FUSEKI_URL/$FUSEKI_DATASET/update"
```

### Notes

- Do not log or paste credentials into chat transcripts.
- If Fuseki is configured without auth, leave `FUSEKI_USER` / `FUSEKI_PASSWORD` unset.
