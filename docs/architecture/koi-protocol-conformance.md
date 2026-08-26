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

**Decision:** keep the local implementation. Bump `rid-lib` to 3.3.0 on its own
merits. Revisit if the constraints below change.

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

There is **one such edge live on the NUC today** (a PROPOSED edge to
`legion-koi+cf8b5829…`). PROPOSED means it delivers nothing, so this is latent —
but approving it would silently grant full access.

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
are different axes, and the conflation has a concrete consequence:

- There are **236 `SpecDoc` entities** in the graph.
- They travel as `_koi_domain = "entity"`.
- An edge scoped `{SpecDoc}` — intended as a "SpecDoc review channel" — matches on
  the domain name `entity`, which is absent, so **it carries none of them**. It
  carries only RID-typed `orn:koi-net.specdoc:*` objects.

So a peer can negotiate a channel that appears to be about SpecDocs and receives
no SpecDocs. **Fix direction:** either a qualified form (`entity:SpecDoc`) or
separate `domains` and `rid_types` columns on the edge.

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
