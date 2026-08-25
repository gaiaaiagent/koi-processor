# Migration 112 — entity-type canonicalization

**Status:** **COMPLETE (2026-08-25).** Population fell 421 → 31 (Document/Event admitted,
2026-08-24) → 3 (the tail retyped, commit `331b105`, same day) → 0 (the last row, "BKC COP
Emails", retyped 2026-08-25 — see below). Live-verified: `SELECT entity_type, count(*) FROM
entity_registry WHERE entity_type NOT IN (<30 canonical types>) AND merged_into IS NULL
GROUP BY 1` returns zero rows.

The 2 `Session` rows named in "The 3 rows deliberately left" below were also resolved before
this pass (confirmed gone via live query); no commit in this repo's history documents how —
attributed to a prior session, not fabricated here.

## Why this document exists

Migration 112 had no specification anywhere — no SQL, no ADR, no task. It existed as a
two-line parking-lot entry and one gate script. The 08-29 date was repeatedly cited as the
blocker; it was not. The absence of a spec was.

Two numbers in that parking-lot entry are wrong, measured 2026-08-23:

| parking-lot entry | measured |
|---|---|
| "~546 non-canonical rows" | **421** rows across **16** types |
| "44 collision groups the retype exposes" | **4** |

## The population

421 live rows carry a type outside the 28 canonical types the API reports at `/health`.
Two types account for 390 of them, and they are **not the same kind of problem**:

| type | rows | source | last written | doc links | edges |
|---|---|---|---|---|---|
| `Document` | 240 | `johar-corpus-intake-v1` (227) | 2026-04-04 | 3 | **598** |
| `Event` | 150 | `extract-session-entities` | 2026-04-16 | **498** | 0 |
| 14 others | 31 | mixed | — | — | — |

Both sources are dormant (nothing written in 4+ months), so the population is stable and
this is a one-off, not a recurring intake problem.

## COMPLETE 2026-08-24 — 421 rows → 3

The retype pass ran after the Document/Event admission below. **28 of the remaining 31
rows were retyped**, all reversible (tombstones tagged `merged_by='type-tail-20260824'`;
snapshot `entity_registry_backup_type_tail_20260824`).

| from | → | rows | rationale |
|---|---|---|---|
| `article`, `gist`, `paper`, `repo-doc`, `Presentation`, `Transcript`, `Research`, `CreativeWork` | `Document` | 15 | written works; casing/vocabulary drift now that `Document` exists |
| `DefinedTerm`, `Idea`, `Tool` | `Concept` | 12 | terms and named ideas; `SoftwareApplication` already canonicalizes to `Concept`, so `Tool` follows |
| `Collection` | `Project` | 1 | "Indy Johar Substack" is the **object of 227 `belongs_to_project` edges** — it was already functioning as a Project |

Every count survived: `Indian Act` kept its 35 document links, `Indy Johar Substack` its 227
edges, and the two colliding rows (`Threshold-Based Flow Funding`, `Reciprocal compute
infrastructure`) folded into their existing 0-link `Concept` twins, carrying 7 and 1 links in.

### The 3 rows deliberately left

- **`Resource` — "BKC COP Emails"** (0 links, 0 edges). Its vault note
  `Projects/BKC COP Emails.md` declares `"@type": Resource`, so **retyping the database row
  alone would be undone by the next vault sync**. This needs a vault edit first, or nothing.
  Filed under `Projects/`, so `Project` is the likely target once the note is changed.

  **RESOLVED 2026-08-25 — retyped to `Document`, not `Project`** (operator decision: the
  note's actual content — an email correspondence log/archive — matches the 15 other
  article/gist/paper-shaped rows retyped to `Document` in the tail pass above, better than
  its `Projects/` folder placement alone suggested). Order followed the house rule: vault
  frontmatter `@type: Resource` → `Document` first (via `vault_write_note`), then
  `POST /entities/retype` (`dry_run:true` confirmed no existing `Document`-typed twin at
  that name — would have minted a new row and correctly rewired `entity_rid_mappings`, not
  merged into a twin; `dry_run:false` applied, `merge_log_id: 258`). New live URI
  `orn:personal-koi.entity:document-bkc-cop-emails-b7ecc417ce30`; old
  `resource-bkc-cop-emails-c06037298f4a` tombstoned (not deleted) via `merged_into`. Vault
  note's `koi:` block refreshed via a follow-up `vault_register_entity` call, which
  confirmed "Linked to existing entity" (no duplicate minted). Verified: 0 non-canonical
  rows remain.
- **2 × `Session`** — `claude-code session <uuid>`, from the `koi_sustained_write` pass on
  2026-04-29. No vault mapping, no links, no edges, no facts. Inert artifacts with no
  sensible target type; deletion is the honest option but is a data decision, not a retype.

  Also resolved by the time this update was written (2026-08-25) — confirmed 0 rows matching
  `entity_text LIKE 'claude-code session%'` remain live. No commit in this repo documents the
  mechanism; not claimed here beyond "gone, verified."

Note the other 74 rows from `koi_sustained_write` are **real `SpecDoc` content**
(`bkc.foundations.*`), not load-test junk — verified no twins exist, so they are the only
copy of those spec names. Do not sweep that source.

## RESOLVED 2026-08-24 — option (a)

`Document` and `Event` were admitted as canonical types: added to `DEFAULT_SCHEMAS` in
`api/entity_schema.py` (folders `Documents` / `Events`, thresholds 0.90/0.95 — matching the
UNKNOWN fallback they were already resolving under, deliberately not loosened) and to
`allowed_entity_types` with `extractable = true`, since both were produced by extractors.
The vocabulary is now 30 types and the non-canonical population is **31 rows across 14
types**. `tests/test_canonical_entity_types.py` pins it, including a whole-vocabulary check
that the code list and the database allowlist cannot drift apart.

What decided it: retrieval never cared (a `Document` entity ranks *first* in
`/knowledge/unified-search`, same as a canonical control; all 390 rows carry embeddings),
resolution never failed (unknown types fall back to a schema *stricter* than most real
ones), and no mapping into the existing 28 was defensible.

## ENFORCEMENT — measured, and deliberately NOT a hard constraint

`allowed_entity_types` is read by nothing: no foreign key on `entity_registry`, no
create-path guard. The instinct is to add one. The data says don't:

- Since the canonicalizing validator landed on **2026-07-13** (`f1d69e6`,
  `ExtractedEntity.type` → `canonicalize_entity_type`), **exactly one** non-canonical row
  has been created — `Idea`, on 2026-08-17.
- `canonicalize_entity_type` already maps every other recent case correctly:
  `schema:Person` → `Person`, `schema:Place` → `Location`,
  `schema:SoftwareApplication` → `Concept`. Those 22 rows predate the validator.
- A hard FK would therefore reject at the database what the application already fixes,
  and would fail a whole ingest over one unrecognised label instead of coercing it.

The leak is closed at the right layer. If enforcement is still wanted, the useful shape is
a create-path guard that *logs* an unrecognised type rather than a constraint that drops
the write — the failure mode this repo keeps hitting is silent coercion, not permissive
writes.

## The decision this needed first (historical)

**`Document` and `Event` are not typos — they are schema.org types used deliberately by
two intake pipelines, and 240+150 rows carry 598 edges and 498 document links between
them.** "Retype the non-canonical rows" is therefore not a cleanup; for these two it is a
choice between:

- **(a) Admit them as canonical types.** Add `Document` and `Event` to `api/entity_schema.py`.
  Nothing is retyped, nothing is merged, 390 of the 421 rows stop being anomalies, and the
  migration shrinks to the 31-row tail. Cheapest, and it matches how they are used.
- **(b) Retype them into existing canonical types** (`Document` → `SpecDoc`/`Evidence`,
  `Event` → `Meeting`?). Requires a defensible mapping for each, rewires 598 edges and 498
  links, and asserts these are the same kind of thing as their targets. They may not be:
  an `Event` from session extraction is not a `Meeting` with attendees.

**Do not start the migration until (a) or (b) is chosen.** The 14-type tail is the same
question at small scale — `paper`, `article`, `repo-doc`, `Transcript`, `Presentation` are
casing/vocabulary drift and are safe to retype; `DefinedTerm`, `Research`, `Idea` are
arguably real distinctions.

## Collisions

Only 4 rows would collide with an existing canonical row of the same name:

| name | non-canonical type | existing canonical |
|---|---|---|
| `karpathy llm wiki` | `gist` | Concept |
| `threshold based flow funding` | `DefinedTerm` | Concept |
| `commoning` | `Event` | Concept |
| `reciprocal compute infrastructure` | `Idea` | Concept |

All four resolve to `Concept`, so each is a single `/entities/retype` call — which folds
into the existing row automatically when the target URI is occupied.

## Constraints

- **Keep `Organization` a distinct core type.** From B0's refutation; the evidence gate
  asserts `organization_must_remain_core`.
- **No Person deduplication.** 1,034 of 4,770 live Person rows are bare single tokens, and
  `dave` carries an alias that would misroute 20 of 22 "Dave" attendees to Dave Bronner.
- **Write the migration against the STRICT resolver.** As of `c350640`/`ce400f1` both the
  fuzzy and semantic tiers use the strict token-overlap policy. The parking-lot entry
  predates that, so any assumption it makes about what a retype will merge into is stale.
- **Use `/entities/retype`, not raw SQL.** It mints the new-typed URI, rewires every
  reference via `_do_merge`, updates `entity_rid_mappings.entity_type`, and tombstones
  rather than deleting — a hard delete would CASCADE away the relationships just rewired.
  It also supports `dry_run`.
  ⚠ Verified 2026-08-23: retyping into a name that has no live row at the target type
  **mints a new row** rather than folding into a near-match. That recreated a duplicate
  during the Location/Organization dedup and had to be merged afterwards. Check for an
  existing target row first.

## Gate

`scripts/check_migration_112_evidence.py` — exit 0 to proceed, 2 = `incomplete_soak`.

Open gap: the gate does **not** implement the burst/organic separation the handoff asks
for. 279 of 317 observed rows (88%) are one `personal-vault` burst. Either add that split
to the gate or state explicitly that the soak is being read as a whole.

One signal worth watching before running anything: `tier3_created_ambiguous` — a row minted
despite an existing same-name row — currently has only 5 observations, all `personal-vault`.
That is the tier that says most about whether retyping will collide.
