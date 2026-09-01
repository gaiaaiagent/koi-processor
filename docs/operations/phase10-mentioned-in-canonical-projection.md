# Phase 10 — DRY RUN: canonical `mentionedIn` projection

**Status: READ-ONLY. Nothing written, no DB row altered, no service restarted,
MacBook vault sync remains OFF.** Written 2026-08-26 with NUC sync ON.

Canonical form, per operator decision: **full vault-relative foldered paths**. Flat
paths are legacy output, **not** an alternative convention, and are treated as
defects to be remapped or reported — never preserved as a valid variant.

All artifacts under `~/backups/casefold-20260826-184458/phase10/`:
`mac/` `nuc/` `proposed/` `classification.json` `projection.json`
`proposed_summary.json`.

---

## 1. Independent classification of all 33 `hash_mismatch` files

Reproduced from scratch — both versions pulled from both nodes, frontmatter split
from body, compared byte-wise:

| class | count |
|---|---|
| byte-identical across nodes | **4** |
| `mentionedIn`/frontmatter only | **28** |
| **real body difference** | **1** |

The four byte-identical files are in `hash_mismatch` because the **MacBook's DB row
is stale against its own disk**, not because the nodes disagree:

* `People/Christopher - United Independence.md`
* `Projects/Pete Corke.md`
* `Projects/Kwaxala.md`
* `Organizations/United Independence.md`

### The one real body difference

`People/Will Ruddick.md`. The MacBook body is a strict superset — two hand-authored
bullets the NUC lacks:

```
- questions for WIll
	- can he share source code for cosmo-local credit visualizer ?  (https://willruddick.substack.com/...)
```

MacBook body 1,490 B · NUC body 1,286 B. **Adopting the NUC version would destroy
204 B of authored content.** The proposed file preserves the MacBook body
byte-for-byte; both bullets verified present in `proposed/`.

## 2. The two mentioned-in implementations are not the same

`api/personal_ingest_api.py`: MacBook **7,016** lines / `6be83cfd…`, NUC **6,409**
lines / `f35b89a2…`. The NUC is 607 lines behind.

**Worse, single and batch diverge on *both* nodes** — the batch endpoint's SQL
filter and converter both omit `orn:obsidian.document:`:

| | single `GET /entity/{uri}/mentioned-in` | batch `POST /entities/mentioned-in` |
|---|---|---|
| WHERE `orn:obsidian.entity:%` | yes | yes |
| WHERE `orn:obsidian.document:%` | **yes** | **NO** |
| WHERE `vault:%` | yes | yes |
| converter `orn:obsidian.document:` | **yes** | **NO** |

So the same entity returns different backlink sets depending on which endpoint asked.

### What the RIDs actually are

| form | rows (Mac / NUC) | handled by single | handled by batch |
|---|---|---|---|
| `vault:` | 7,055 / 8,497 | yes | yes |
| `orn:obsidian.note:` | 35 / 35 | **NO — excluded by WHERE** | **NO** |
| `orn:obsidian.document:` | 14 / 14 | yes | **NO** |
| `orn:obsidian.meeting:` | 13 / 13 | **NO** | **NO** |
| `orn:obsidian.entity:` | **0 / 0** | yes (dead code) | yes (dead code) |
| bare, no colon | **0 / 0** | yes (dead code) | yes (dead code) |

The branch both converters try **first** matches nothing, while three live forms are
dropped. And `vault:` RIDs are already **foldered** — 6,894 foldered vs 31 flat — so
the database already holds the canonical shape. The NUC's flat `mentionedIn` entries
are stale propagate output, not a live convention.

## 3. One shared RID → vault-path converter

Single implementation, used by both endpoints and by the propagation helper.
Returns `(path, reason)` so callers can distinguish *excluded* from *failed*.

```python
VAULT_PATH_PREFIXES = ("vault:", "orn:obsidian.note:", "orn:obsidian.document:")
NON_PATH_PREFIXES   = ("orn:obsidian.entity:", "orn:obsidian.meeting:")

def rid_to_vault_path(rid):
    if not isinstance(rid, str) or not rid:
        return None, "not-a-string"
    for p in NON_PATH_PREFIXES:          # entity/meeting RIDs are IDENTIFIERS,
        if rid.startswith(p):            # not document paths — never coerce them
            return None, f"non-path-rid ({p})"
    hit = next((p for p in VAULT_PATH_PREFIXES if rid.startswith(p)), None)
    if hit is None:
        if ":" not in rid: path = rid    # bare
        else:              return None, "non-vault-scheme"
    else:
        path = rid[len(hit):]
    for root in ("Notes/", "notes/"):    # optional vault-root segment, either case
        if path.startswith(root):
            path = path[len(root):]; break
    if path.endswith(".md"):             # `orn:obsidian.note:` carries BOTH variants
        path = path[:-3]
    if not path or path.startswith("/") or ".." in path.split("/"):
        return None, "unsafe-path"
    return path, "ok"
```

Three properties the current code lacks: `orn:obsidian.entity:`/`.meeting:` are
**identifiers, not paths** and are refused rather than falling through to
`vault_path = doc_rid` (which today emits `[[orn:obsidian.meeting:…]]` as a
wikilink); the `Notes/` prefix and `.md` suffix are handled in **both** presences,
because `orn:obsidian.note:` carries both variants; and traversal is rejected.

## 4. Propagation helper — required behaviour

1. **Exclude `vault_note_exists=false`.** A backlink to a note that does not exist
   is the defect being repaired, not data to carry forward.
2. **Fail closed when existence metadata is unavailable.** If the endpoint response
   lacks `vault_note_exists`, the helper must **refuse the whole entity**, not treat
   the field as absent-means-true. This is the divergence-10 lesson: a missing
   attribute must never widen access or content.
3. **Refuse full replacement on `truncated=true`.** `mentionedIn` is written as a
   *full replacement*, so writing a truncated set silently deletes real backlinks.
   Both endpoints already compute `truncated`; neither caller currently honours it.

## 5. Proposed canonical projection

Built from **both** databases, union of `document_entity_links` per entity, scoped
to vault-backed RID forms whose target **exists in the MacBook vault** — the
MacBook filesystem is authoritative for these MacBook-owned folders.

Entity resolution: `koi.canonical_uri` where present (**two** frontmatter formats
exist — an inline Python-dict string and a multi-line YAML block; a parser handling
only the first finds 0 of 33), else exact `entity_text` match.

| outcome | count |
|---|---|
| files projected | **30** |
| unresolved entity | **3** |
| legacy flat RIDs uniquely remapped | **58** |
| **ambiguous, left unresolved** | **27** |
| excluded — non-vault scheme | 6,100 |
| excluded — target not in MacBook vault | 82 |

A legacy flat RID is remapped **only** when its basename resolves to exactly one
current full path. Multiple candidates are reported, never guessed:

| file | unresolved flat RID | candidates |
|---|---|---|
| `People/Sam Bennetts.md` | `Meetings/2026-03-03 Regen AI Meeting` | 2 |
| `People/Sam Bennetts.md` | `Meetings/2026-03-04 Regen AI Meeting` | 2 |
| `People/Sam Bennetts.md` | `Meetings/2026-03-10 Regen AI Meeting` | 2 |
| `People/David Fortson.md` | `Meetings/2025-11-18 Regen <> GAIA Meeting` | 2 |
| `People/David Fortson.md` | `Meetings/2026-03-12 Regen AI Meeting` | 2 |
| `People/David Fortson.md` | `Meetings/2026-02-17 Regen AI Meeting` | 2 |
| `People/David Fortson.md` | `Meetings/2026-03-03 Regen AI Meeting` | 2 |
| `People/David Fortson.md` | `Meetings/2026-03-05 Regen AI Meeting` | 2 |
| `People/Samu Barnes.md` | `Meetings/2026-03-03 Regen AI Meeting` | 2 |
| `People/Samu Barnes.md` | `Meetings/2026-03-05 Regen AI Meeting` | 2 |
| `People/Gregory Landua.md` | `Meetings/2026-02-10 Regen <> GAIA Meeting` | 2 |
| `People/Gregory Landua.md` | `Meetings/2025-11-18 Regen <> GAIA Meeting` | 2 |

### Unresolved entities

| file | reason |
|---|---|
| `People/Mark DeRugeriis.md` | no-entity-row-for-name |
| `People/Christopher - United Independence.md` | no-entity-row-for-name |
| `Organizations/United Independence.md` | no-entity-row-for-name |

All three are left untouched. Two of them are byte-identical across nodes anyway.

## 6. Proposed arrays, counts, hashes — and the body proof

**Bodies byte-identical to the MacBook original in 30 of 30 proposed files. Zero bodies changed.**
`People/Will Ruddick.md` is included in that count: its body is preserved *because*
the MacBook version is the superset, which is the deliberate exception.

| file | cur | new | Δ | current sha | proposed sha |
|---|---|---|---|---|---|
| `Projects/Kwaxala.md` | 0 | 16 | +16 | `37bf1c520d4e0098` | `1c0a27a06cb3adf7` |
| `People/Darren Zal.md` | 213 | 222 | +9 | `093b690d5a786ac4` | `350f81253945fbb1` |
| `People/Shawn Anderson.md` | 144 | 152 | +8 | `af3d776c7fc7a3ca` | `c6cd9a92f01dd666` |
| `People/Gregory Landua.md` | 44 | 48 | +4 | `cfd96fc048f9b7ff` | `bf22de712b0a4ba5` |
| `People/David Fortson.md` | 34 | 37 | +3 | `0c017ac75f7fa672` | `348b7bbef846b68e` |
| `People/Samu Barnes.md` | 29 | 31 | +2 | `585b7a1aa0335a64` | `69fb34c3b4f8a2af` |
| `People/Will Ruddick.md` | 15 | 13 | -2 | `8e89a1ec437ec52f` | `10f3eea1322726c1` |
| `Organizations/Atlas Research Group.md` | 8 | 9 | +1 | `adda642b040be014` | `0d751befa47961a9` |
| `People/Ken Bruskiewicz.md` | 13 | 14 | +1 | `3b7ef82fbbe791e1` | `9b89ee95f84854bf` |
| `People/Sam Bennetts.md` | 6 | 5 | -1 | `97ff6b73b5241f9e` | `99aa1204c0b64df8` |
| `Projects/Koi Mcp.md` | 2 | 3 | +1 | `d199f1fdf2f44e98` | `b043ee559c8e199d` |
| `Projects/Pete Corke.md` | 0 | 1 | +1 | `5f4a13c5dfa75745` | `b5d5552d2b965990` |
| `Organizations/Foresight Institute.md` | 13 | 13 | +0 | `dacd722697f15836` | `dacd722697f15836` |
| `Organizations/Longview Philanthropy.md` | 7 | 7 | +0 | `7aa69ff7828aca96` | `7aa69ff7828aca96` |
| `Organizations/Schmidt Sciences.md` | 4 | 4 | +0 | `7690e3a464873433` | `7690e3a464873433` |
| `Organizations/Topos Institute.md` | 4 | 4 | +0 | `888065654b3bcc00` | `888065654b3bcc00` |
| `People/Alok Srivastava.md` | 20 | 20 | +0 | `ff172384db62577e` | `ff172384db62577e` |
| `People/Austin Wade Smith.md` | 8 | 8 | +0 | `a767414e289e891e` | `a18c74dee0266ce8` |
| `People/Christian Shearer.md` | 11 | 11 | +0 | `acc2ecedafe7ded2` | `acc2ecedafe7ded2` |
| `People/David I. Spivak.md` | 3 | 3 | +0 | `ab08358f4ba42597` | `ab08358f4ba42597` |
| `People/Giancarlo.md` | 4 | 4 | +0 | `981b50fc38a48d48` | `981b50fc38a48d48` |
| `People/JC.md` | 1 | 1 | +0 | `5a0fea8e29cbbdab` | `5a0fea8e29cbbdab` |
| `People/Julian Fleck.md` | 25 | 25 | +0 | `42062d3b5861353a` | `42062d3b5861353a` |
| `People/Marie Gauthier.md` | 37 | 37 | +0 | `0c48a784692e1984` | `4910b10266fe51bb` |
| `People/Maxine Levesque.md` | 14 | 14 | +0 | `4467247a1b5d7b0d` | `4467247a1b5d7b0d` |
| `People/Megan Shabram.md` | 23 | 23 | +0 | `0a71a450a6e06ec1` | `0a71a450a6e06ec1` |
| `People/Michelle Thuo.md` | 10 | 10 | +0 | `b6e1fd57081e0447` | `b6e1fd57081e0447` |
| `People/Rebecca Harman.md` | 24 | 24 | +0 | `bd9adcd0636d3987` | `577e3838acc34301` |
| `Projects/Regen OS.md` | 10 | 10 | +0 | `d22245ba5511f876` | `d22245ba5511f876` |
| `Projects/Substrate Dynamics.md` | 20 | 20 | +0 | `d2e4501713cdeab9` | `d2e4501713cdeab9` |

15 files have `cur_sha == new_sha` — the projection is a no-op for them.
Several show Δ=0 with a **different** hash: same number of backlinks, flat paths
rewritten to canonical foldered form.

## 7. Two separate plans — projection repair vs historical cleanup

They are different in kind and must not be combined.

### 7a. Projection repair (vault files only)

Rewrites the `mentionedIn` array in the 30 projected notes. **No DB write. No event
emission.** Per-file, transactional at the filesystem level:

```bash
# for each file: back up, write proposed, verify body unchanged, else restore
cp "$V/$F" "$BK/$F.orig"
cp "$P10/proposed/${F//\//~}" "$V/$F"
python3 - "$BK/$F.orig" "$V/$F" <<'EOF'   # assert body identical
import sys
def body(p):
    t=open(p,encoding='utf-8',errors='replace').read()
    e=t.find('\n---',3); return t[e+4:] if t.startswith('---') and e!=-1 else t
sys.exit(0 if body(sys.argv[1])==body(sys.argv[2]) else 1)
EOF
[ $? -eq 0 ] || { cp "$BK/$F.orig" "$V/$F"; echo "ROLLED BACK $F"; exit 1; }
```

Rollback: restore every `.orig`. The originals are already captured in
`phase10/mac/`, so rollback does not depend on the run creating them.

**Precondition: MacBook vault sync must be OFF**, or each write emits an UPDATE
mid-repair.

### 7b. Historical database cleanup — NOT part of 7a

Distinct problems, none of which the projection touches:

* 35 `orn:obsidian.note:` and 13 `orn:obsidian.meeting:` rows unreachable by either endpoint
* 31 flat `vault:Meetings/<file>` rows
* rows whose target no longer exists (82 encountered here)

**No `document_entity_links` row may be deleted.** These are provenance. The repair
shape is additive — a `canonical_document_rid` column, or a view — resolved in its
own transaction with its own rollback, and **only after** the endpoints share the
converter from §3. Repairing data against two disagreeing readers would bake in
whichever one ran.

## 8. Expected first MacBook scan, after normalization

| event | count | why |
|---|---|---|
| **UPDATE** | **33** | every `hash_mismatch` file — the MacBook DB row is stale against its own disk, so an UPDATE fires whether or not the projection alters bytes |
| **NEW** | **4** | `missing_in_db` — on disk, no DB row |
| **FORGET** | **1** | the retired 241-byte CIE stub, the known-expected deletion |

Of the 33 UPDATEs: 15 have content changed by the projection,
15 are byte-unchanged, 3 are unresolved and left as-is.
**All 33 emit UPDATE regardless** — the projection changes their content, not
whether they are stale.

### No unexpected deletion candidates

* `missing_on_disk` = **1**, and it is the CIE stub.
* All 33 affected files verified **present** on the MacBook (0 absent).
* The projection edits only the `mentionedIn` frontmatter array. It creates no path,
  renames nothing, deletes nothing — so it **cannot** introduce a deletion
  candidate. FORGET stays at 1 by construction, not by observation.

## What was NOT done

No file written on either node. No DB row inserted, updated or deleted. No event
emitted. No service restarted. No merge or choice applied. **MacBook vault sync
remains OFF.**
