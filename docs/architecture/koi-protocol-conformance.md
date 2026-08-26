# KOI protocol conformance

**Status:** living document · first written 2026-08-26
**Scope:** `api/koi_net_router.py`, `api/koi_poller.py`, `api/event_queue.py`, `api/vault_sync.py`

## Why this document exists

This repository implements the KOI-net node protocol by hand — roughly 2,400
lines in `koi_net_router.py` plus the poller and event queue. Upstream publishes a
reference implementation, `koi-net`, and we do not use it. We use only `rid-lib`.

That is a fork. Forking is a legitimate choice; drifting into one is not. The
purpose of this file is to make every divergence a **decision with a reason**
rather than an accident nobody remembers making.

Each divergence below is classified:

- **CONFORM** — we match the reference.
- **INTENTIONAL FORK** — we deliberately differ, with the reason recorded.
- **DEFECT** — we differ by accident and it should be fixed.

## Upstream, as of 2026-08-26

| Package | Latest | Released | Releases | We use |
|---|---|---|---|---|
| `koi-net` (`DynamicalSystemsGroup/koi-net`) | 2.1.2 | 2026-08-06 | 80 | **no** |
| `rid-lib` (`DynamicalSystemsGroup/rid-lib`) | 3.3.0 | 2026-06-18 | 24 | yes, pinned **3.2.12** |
| `koi-net-obsidian-manager-node` | 0.1.0 | 2026-04-01 | 1 | no |
| `koi-net-coordinator-node` | 0.1.0 | 2026-03-25 | 1 | no |
| `koi-net-graph-extension` | 0.1.3 | 2026-06-18 | 1 | no |

`koi-net` 2.1.2 is ~5,694 LOC across `protocol/` (event, edge, node, envelope,
secure, knowledge_object), `components/` (sync_manager, event_buffer, cache,
effector, graph, secure_manager, server, response_handler), `config/` and
`infra/`. It is actively maintained. `koi-net` 2.1.2 requires `rid-lib>=3.3.0`.

**A note on the Obsidian node**, because its name invites a wrong conclusion:
`koi-net-obsidian-manager-node` 0.1.0 is a **114-line scaffold** — an
`ObsidianVault`/`ObsidianNote` RID type pair plus a contact handler that proposes
edges to nodes advertising `ObsidianNote`. It contains no file watching, no
syncing and no deletion detection. It is **not** a replacement for our 91 KB
`api/vault_sync.py`, and should not be cited as one.

---

## B2 — Decision: do NOT port to upstream `koi-net`

> **CORRECTED 2026-08-26.** The decision below was stated as "do not port to
> upstream `koi-net`" and justified entirely with `components/` reasons — the
> Postgres store, the durable queue. Those reasons are sound, and they say
> **nothing about `koi_net.protocol`**, which is a separate 676-line layer that
> imports only `rid_lib` and pydantic and never touches `components/` (verified
> by reading the imports of every module in `protocol/`). Treating one decision
> as covering both layers is how a storage constraint silently became a licence
> to diverge on the wire format — and we did diverge, in a way that made our
> vault-file events unparseable by any stock KOI-net node (divergence 11).
>
> **The decision is now split:**
>
> | layer | LOC | decision |
> |---|---|---|
> | `koi_net.protocol` (wire format, API paths, Event/Manifest/Edge/Node) | 676 | **CONFORM.** Match it. Validate against it. |
> | `koi_net.components` (cache, event buffers, effector, server) | 2,881 | **INTENTIONAL FORK**, for the reasons below. |

**Decision:** keep the local `components` implementation; conform to
`koi_net.protocol`. Bump `rid-lib` to 3.3.0 on its own merits. Revisit if the
constraints below change.

**Reasons, in order of weight:**

1. **Storage model mismatch.** `koi-net` is built around a `rid-lib` file `Cache`
   as the knowledge store. This deployment's entire value is knowledge in
   Postgres + pgvector — entity resolution, embeddings, SQL-queryable facts.
   Adoption means reimplementing `Cache` over Postgres, which is the majority of
   the port and the part most likely to be subtly wrong.
2. **Durability of the event queue.** Upstream `EventBuffer`s are in-process and
   die with the process. Our `koi_net_events` is a Postgres table with a 24 h TTL,
   and this mesh routinely has peers unreachable for **weeks** — Shawn's node was
   unreachable from March to August. Losing a durable queue is a regression, not
   an upgrade.
3. **No upstream equivalent** for commons intake, vault sync, share/retract, the
   E2EE `encryption_key` exchange, or `ontology_uri`. These are not incidental;
   they are the product.
4. **Cost is concrete and the timing is bad.** Five endpoints would need
   re-hosting while the vault was actively losing files.

**What the decision does not excuse.** "We forked" is not a reason to keep the
defects below. Three of them cost real data or real reasoning time in August 2026.

---

## Divergence register

### 1. `orn:koi-net.vault-file:` occupies the protocol's reserved namespace — **DEFECT**

`rid_lib.types` reserves the `koi-net` namespace for exactly two protocol
objects:

```
KoiNetNode.namespace = koi-net.node
KoiNetEdge.namespace = koi-net.edge
```

We mint `orn:koi-net.vault-file:Shared/…`, which is application data wearing
protocol clothes. Upstream defines `obsidian.vault` / `obsidian.note` for exactly
this content — and **our own vault frontmatter already uses `orn:obsidian.note:`
RIDs**, so two conventions coexist inside one system.

This is not cosmetic. The Telegram engineering log records the symptom directly:
*"old `obsidian.note`-format events at head of queue silently unapplied"* and
*"RID format skew"*.

**Fix direction:** migrate vault files to the `obsidian.note` namespace, or, if we
keep a local namespace, use one we own (`personal-koi.vault-file`) rather than
squatting `koi-net.*`. Requires a migration and peer coordination — not a
drive-by change.

### 2. FORGET ships the full record — **INTENTIONAL FORK (newly deliberate), pending D1**

Upstream builds a delete as `Event.from_rid(EventType.FORGET, rid)` — a bare RID,
no manifest, no contents. We ship both: measured **1,581 of 1,583** FORGET rows
carry manifest *and* contents. A delete is thus the one event guaranteed to carry
the full payload of what is being deleted.

That is backwards as a privacy default. **But** on 2026-08-26 it was the
mechanism that recovered two files deleted from the NUC and absent from the Mac's
git: ECDH is symmetric, so the queued ciphertext could be decrypted locally and
the plaintext restored.

**Resolution:** git is the correct pre-delete snapshot, not the wire format. The
NUC vault became a git repo on 2026-08-26 with a 30-minute autocommit timer and an
off-machine mirror. **Once that has soaked, strip payload from FORGET** — the
recovery path it currently provides will no longer be the only one.

### 3. Read-time filtering over a broadcast table — **INTENTIONAL FORK**

Upstream targets events into per-peer `EventBuffer`s at write time. We write one
broadcast row (`target_node IS NULL`) and filter per peer at read time: **165,821
of 188,706** rows are broadcast.

Kept deliberately: one durable row per event is far cheaper than N per-peer copies
for a mesh with long-absent peers, and it is what makes a late-returning peer able
to drain months of history.

**The cost is real, though**, and it is why exclusion had to be retrofitted as a
per-peer read-time filter — which is precisely where defects 4, 5 and 9 live. Any
future scoping mechanism has to be built on the read path, so it must be
fail-closed by construction.

### 4. Empty `rid_types` is fail-open — **DEFECT**

`event_queue.poll()` gates on `if rid_types:` — a truthiness test. An edge with
`rid_types = {}` therefore disables filtering **entirely** and receives
everything. Upstream does a membership test against a declared set, which is
fail-closed.

There is **one such edge live on the NUC today**, and the first version of this
entry described it wrongly. Re-read from the table on 2026-08-26:

| direction | status | rid_types |
|---|---|---|
| `legion-koi+cf8b5829… -> nuc-personal` | **APPROVED** | `{}` |
| `nuc-personal -> legion-koi+cf8b5829…` | PROPOSED | `{Organization,Person,Project,Concept,Location,Vault-file}` |

The empty-`rid_types` edge is APPROVED, not PROPOSED — but it is the **inbound**
edge, and our poll gate reads the **outbound** one
(`source_node = self AND target_node = requester`). So the empty list governs what
*Legion* serves *us*, which Legion's own code enforces. Nothing is served from here
either way, because our outbound edge to that RID is PROPOSED. Latent, but for a
different reason than originally written.

**Fix:** `if rid_types is not None:`, and treat an empty list as "nothing".

### 5. `edge_rid` embeds node identities as a formatted string — **DEFECT (low)**

Upstream derives an opaque edge id. We format
`orn:koi-net.edge:<source>><target>:poll`. Because the row is upserted on
`edge_rid`, a peer that re-keys leaves its **old** identity baked into the string
while `target_node` moves on.

This cost real reasoning time on 2026-08-25: an edge whose `edge_rid` contained
`shawn+dcee9de3…` while `target_node` was `shawn+135d478e…` was briefly read as a
third, unaccounted-for identity. The filtering itself is correct — the poll gate
queries `target_node` — so this is a legibility defect, not a security one.

### 6. `koi_net_nodes.last_seen` is never updated on poll — **DEFECT**

Written only by handshake and key-bootstrap paths. Every value on this node is
from **March**, despite peers polling continuously.

This is not cosmetic either: it caused a peer to be reported as "offline since
mid-March" when it had in fact been delivering until June, and that wrong reading
was sent to the peer. Any freshness check must currently use
`vault_sync_applied_events.applied_at`, not this column.

**Fix:** touch `last_seen` in the `/events/poll` handler.

### 7. `/handshake` with auto-APPROVE — **INTENTIONAL FORK, with a caveat**

Upstream has no such endpoint and approves an edge only after checking that the
requested `rid_types` are a subset of what the peer declares it provides. Our
local handshake auto-approves.

Kept for operator ergonomics on a small trusted mesh. **The caveat is that
auto-approval without the subset check is how an edge ends up broader than either
side intended** — which is the shape of defect 9. If the mesh grows beyond
hand-managed peers, adopt the subset check.

### 8. Two node identities sharing one `base_url` — **DEFECT (operational)**

`koi_net_nodes` is unique on `node_rid`, not `node_name`, and nothing prevents two
identities pointing at the same address. `10.100.0.4:8351` currently hosts both
`shawn+135d478e…` (active) and `legion-koi+3b551708…` (rejected here, active on
the NUC). A `rejected` status is therefore not enforceable by identity, and the
two nodes disagree about it.

Upstream has no status field at all; trust is the edge plus a pinned key.

### 9. `rid_types` conflates two orthogonal axes — **DEFECT (design)**

A single `rid_types` list is matched against *both* a RID type (`Vault-file`,
`Person`) and a domain-event name (`entity`, `task`, `knowledge_episode`). These
are different axes. Measured on 2026-08-26, joining the queue to the registry on
the canonical URI rather than trusting the payload label:

| measurement | value |
|---|---|
| `SpecDoc` entities in `entity_registry` | **236** |
| federation events whose payload `fuseki_uri` is one of them | **21** |
| their `_koi_domain` | **`entity`** (21 of 21) |
| their payload `entity_type` | **`Project`** (21 of 21) |
| RID-typed `orn:koi-net.specdoc:*` events, all time | **0** |
| positive control: events for `Person` URIs | 27,187 |

So an edge scoped `{SpecDoc}` matches against the domain name `entity`, which is
not in the list, and **carries none of them**. It carries only RID-typed
`orn:koi-net.specdoc:*` objects, of which this node has never emitted one. The
channel is empty except for whatever the peer puts into it — which is exactly
what Shawn's canary is.

**Two further traps found while measuring this**, both of which cost a wrong
intermediate answer before the join above was written:

1. Querying `contents->'payload'->>'entity_type' = 'SpecDoc'` returns **zero**
   and looks like proof that SpecDocs never federate. It is a query artifact: the
   payload carries the *extraction-time* label, not the canonical type. All 21
   say `Project`. Any scoping built on the payload label would therefore also
   miss them.
2. `SpecDoc` entities *do* reach the resolve path (41 `document_entity_links`),
   so "they are never resolved, therefore never emitted" is also wrong.

**Fix direction:** either a qualified form (`entity:SpecDoc`) or separate
`domains` and `rid_types` columns on the edge. Whichever is chosen, it must key
off the canonical `entity_registry.entity_type`, not the payload label.

### 10. An unrecognized RID namespace bypasses the edge scope — **DEFECT (fixed 2026-08-26)**

Found while verifying divergence 9, and it is the same fail-open shape as defect 4
one branch further down. `extract_rid_type` returns `None` for any namespace outside
`koi-net.*` and `entity:`. The RID branch of the filter read:

```python
excluded = bool(rid_type) and rid_type.lower() not in lowered
```

`bool(None)` is `False`, so an unrecognized RID short-circuited to *not excluded* and
was delivered to every peer **regardless of what its edge declared**. Where defect 4
was "the edge declares nothing", this is "the event resolves to nothing".

Measured against the live queue:

| namespace | `extract_rid_type` | non-domain events (Mac / NUC) | effect |
|---|---|---|---|
| `koi-net.vault-file` | `Vault-file` | 22,843 / 100,313 | filtered correctly |
| `obsidian.note` | **`None`** | **40 / 23** | **bypassed the scope** |
| `personal-koi.testdoc`, `test` | `None` | 2 / – | bypassed the scope |
| `personal-koi.doclink`, `.knowledge-episode` | `None` | 0 / 0 | *not* affected — they carry `_koi_domain` and take the domain branch |

The exposure is narrow but it is precisely the wrong one. `orn:obsidian.note:` is the
namespace **our own vault frontmatter already uses** (divergence 1), so a vault file
emitted under it would have been served to an edge scoped `{SpecDoc}` — the scope both
of Shawn's old Legion identities are currently pinned to, and the basis on which he was
told on 2026-08-26 06:01 that "there is no vault-file path to either one now". That
statement was true for `orn:koi-net.vault-file:` RIDs and **not** for `obsidian.note`
ones. It has been corrected to him.

No such event was actually delivered — all 40 are expired and both Legion nodes have
been unreachable — so this is a latent hole that was closed, not an incident.

**Fix:** `excluded = rid_type is None or rid_type.lower() not in lowered`, in both
`poll()` and `peek_undelivered()`. Unknown type matches no declared type. This makes
`obsidian.note` events undeliverable to *any* scoped edge, which is the safe default
and is already the status quo in effect — the Telegram engineering log records these
events as "silently unapplied". The real repair is divergence 1, the namespace
migration.

### 11. Vault-file manifests were unparseable by a stock KOI-net node — **DEFECT (fixed 2026-08-26)**

The most consequential divergence found so far, and it was invisible because
both ends of this mesh run our code.

`koi_net.protocol.Event.manifest` is typed as `rid_lib.ext.Manifest`, which
requires exactly three fields: `rid`, `timestamp`, `sha256_hash`. We emitted:

```json
{"bytes": 333, "deleted": false, "base_hash": null,
 "timestamp": "...", "origin_seq": 251, "origin_node": "orn:koi-net.node:...",
 "content_hash": "a40683fd...", "relative_path": "Meetings/..."}
```

`timestamp` matched. `rid` was absent. `sha256_hash` was present under the name
`content_hash`. Validating a **real payload pulled from `koi_net_events`**
against koi-net 2.1.2 + rid-lib 3.3.0:

```
2 validation errors for Event
  manifest.rid          Field required
  manifest.sha256_hash  Field required
```

So **every vault-file event this node has ever sent would be rejected by a stock
KOI-net node.** Domain events were unaffected — they carry `manifest=None`, so
there is nothing to validate, which is why the defect never surfaced.

We already held all three values, so this was a naming mismatch, not a missing
capability. `rid` and `sha256_hash` are now emitted alongside the existing
fields; pydantic ignores unknown keys, so upstream reads the three it needs and
all seven of our extensions survive on the wire. Verified: `Event`,
`EventsPayload` (the `/events/poll` response body) and the bundle round-trip all
parse.

The **inbound** path was fixed symmetrically: it read `manifest["content_hash"]`
and would have rejected a stock node's events. It now accepts either spelling and
derives `relative_path` from the RID when the peer sends only the upstream
three-field manifest.

**Not a divergence, worth recording:** our five KOI-net endpoint paths already
match `koi_net.protocol.api.paths` exactly (`/events/broadcast`, `/events/poll`,
`/rids/fetch`, `/manifests/fetch`, `/bundles/fetch`, under a `/koi-net` prefix).
The transport surface conformed all along; only the payload did not.

---

## Conformance summary

| # | Divergence | Class |
|---|---|---|
| 1 | `koi-net.vault-file` in reserved namespace | DEFECT |
| 2 | FORGET ships full payload | INTENTIONAL FORK (pending D1 soak) |
| 3 | Broadcast table + read-time filtering | INTENTIONAL FORK |
| 4 | Empty `rid_types` fail-open | DEFECT |
| 5 | `edge_rid` embeds identities | DEFECT (low) |
| 6 | `last_seen` not updated on poll | DEFECT |
| 7 | `/handshake` auto-APPROVE | INTENTIONAL FORK (caveated) |
| 8 | Two identities per `base_url` | DEFECT (operational) |
| 9 | `rid_types` conflates domain and entity type | DEFECT (design) |
| 10 | Unrecognized RID namespace bypasses edge scope | DEFECT (fixed 2026-08-26) |
| 11 | Vault-file manifest unparseable by stock KOI-net | DEFECT (fixed 2026-08-26) |

**Not a divergence, and worth stating plainly:** none of the above caused the
2026-08-25 FORGET storm. That defect is entirely inside `api/vault_sync.py`, before
any event reaches the KOI layer. The transport did exactly what it was told and
faithfully delivered ~21,000 correctly-formed deletions of files that were never
deleted. Any protocol, upstream or local, would have delivered them.

## Related

- `docs/operations/peer-onboarding.md` — the peer flow these divergences shape
- Commit `2c497f0` — domain-event scope fix (defect class of 3/4/9)
- Commit `77ce423` — same fix in `peek_undelivered`
- Commits `3bd94d0`, `68dcf90` — the vault-sync emitter fix
