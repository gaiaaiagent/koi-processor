# Code ↔ Docs Bridge (AGE + Postgres/RDF)

## Purpose

RegenAI maintains two complementary representations:

- **Semantic KG (PostgreSQL → Fuseki/RDF):** people, orgs, claims, domain concepts, governance artifacts, and *semantic* relationships.
- **Code graph (Apache AGE + tree-sitter):** symbols, files, AST-derived relationships and structural connections.

The bridge makes these systems **joinable**, so documentation can resolve to concrete code artifacts and the query layer can traverse both.

## Bridge Contract

### Canonical identifiers

All joins flow through **stable string identifiers** shared across systems:

- `repo_key`: `<host>/<org>/<repo>` (e.g. `github.com/regen-network/regen-ledger`)
- `file_path`: repo-relative path (e.g. `x/ecocredit/keeper/keeper.go`)
- `symbol`: best-effort symbol name (e.g. `MsgCreateBatch`, `BasketKeeper`)
- `code_uri`: canonical artifact ID (stored as `TEXT`)

Recommended `code_uri`:
- Initial (available now): `rid://code/...` from `code_entity_provenance.entity_rid`
- Future (optional): `https://regen.network/koi/code/...` if you want web-safe URIs

### Tables

Two Postgres tables form the bridge:

1) `koi_code_artifacts`
- One row per canonical code artifact.
- Can optionally include `(age_graph, age_id)` for direct AGE mapping.

2) `koi_doc_code_links`
- Many-to-many links from documents (`koi_memories.rid`) to `code_uri`.
- Populated by a “doc→code linker” job (regex/symbol resolution, or LLM assisted).

## Migrations

- `migrations/024_code_docs_bridge.sql`

## Initial Population (Code → Bridge)

Use the provenance view created by `migrations/add_code_graph_provenance.sql`:

- `code_entity_provenance` is built from CAT receipts (`koi_transformation_receipts`).

Script:

```bash
cd /opt/projects/koi-processor
PYTHONPATH=src ./.venv/bin/python scripts/code_bridge/export_code_artifacts.py
```

This upserts `code_entity_provenance` into `koi_code_artifacts`.

## Linking (Docs → Code)

Recommended minimal linker behavior (future job):

- For each doc (`koi_memories`), find mentions of:
  - Cosmos modules (`x/<name>`)
  - protobuf message types (`Msg[A-Za-z0-9]+`)
  - keeper names (`<Something>Keeper`)
  - full import paths (`github.com/<org>/<repo>/...`)
- Resolve to `koi_code_artifacts.code_uri` by matching `(repo_key, file_path)` or `(repo_key, symbol)`.
- Insert into `koi_doc_code_links`.
- Optionally write `entity_registry.metadata.code_uri` for FIX-005 types (MODULE/API_MESSAGE/KEEPER) when a confident match is found.

### Initial linker implementation

Script (regex-based, high-precision):

```bash
cd /opt/projects/koi-processor
PYTHONPATH=src ./.venv/bin/python scripts/code_bridge/link_docs_to_code.py --dry-run
PYTHONPATH=src ./.venv/bin/python scripts/code_bridge/link_docs_to_code.py
```

Behavior:
- Links **symbol mentions** when the symbol is globally unique (or uniquely resolvable by repo context).
- Links **file paths** only when a full org/repo path is present *and* maps to a single artifact.
- Uses the same docs-only corpus filter as Stage 6.

## Query Federation Pattern

1) Semantic KG answers the “what/why” and yields `code_uri` values (directly on entities or via `koi_doc_code_links`).
2) Query layer fetches code details:
   - via Postgres `koi_code_artifacts` (fast; no AGE call)
   - optionally via AGE by `(age_graph, age_id)` when available

## Notes

- Stage 6 semantic re-extraction can remain **docs-only** while still linking into the code graph through this bridge.
- Keep the bridge table schema stable; enrich it incrementally (AGE ids, commit-aware versioning) when needed.
