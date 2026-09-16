# Federated fact retractions (issue #67): what is proved, what is not, how to operate it

**Measured 2026-09-15** on branch `fix/federated-fact-retractions` (worktree
`koi-federated-retractions-20260915`), against the live laptop database `personal_koi`
read-only and the scratch database `personal_koi_test` in rolled-back transactions. Every
figure below names the command or test that produced it. Nothing in this branch is deployed:
the live API process and both runtime checkouts predate it, and migration 127 is **not
applied** on `personal_koi`. Re-measure before acting on any number here.

This document exists because the incident handoff said "delivery 4/4" and the operator
believed it. What follows is the positive statement of what the columns actually mean, the
design that records the obligation the columns cannot, and the audit that reads the history
back out.

---

## 1. The defect, and the boundary as proved

### 1.1 What `POST /knowledge/facts/{id}/retract` did before this branch

It set `knowledge_facts.valid_to = NOW()` and emitted nothing. Every fact ever retracted on
this node stayed live on every peer that had received it. The 2026-09-14 Michael Garfield
Substack incident (five wrongly bound facts, three documents) was repaired by hand: three
`knowledge_episode UPDATE` events were reconstructed and queued, carrying the five tombstones.

### 1.2 What the transport columns mean — pinned, not assumed

[`tests/test_fact_retraction_boundary.py`](../../tests/test_fact_retraction_boundary.py) reads
each of these out of [`api/event_queue.py`](../../api/event_queue.py) and then proves it
against a real `EventQueue` on the scratch database:

| pin | test | what it proves |
|---|---|---|
| `confirmed_by` is receipt only | `test_pin_confirm_is_receipt_only` | `confirm()` changes exactly one column, `confirmed_by`. There is no column that could hold an application result. |
| `delivered_to` marks scope-EXCLUDED events too | `test_pin_delivered_to_marks_scope_excluded_events` | `poll()` with a narrow edge scope appends the peer to `delivered_to` for an event it did **not** return (the starvation fix). `delivered_to ∋ peer` is therefore not evidence the peer ever saw the event. |
| unconfirmed events are never redelivered | `test_pin_unconfirmed_events_are_not_redelivered` | `poll()` excludes on `delivered_to`, which it set at hand-over. The `koi_poller` comment "will re-deliver on next poll" is false. An event whose apply failed is lost for that peer until expiry. |
| `emit_domain_event` swallows queue failures | `test_pin_emit_domain_event_swallows_queue_failure` | It returns `None` on a failing queue and never raises. A retraction cannot ride on it and claim durability. |

Run them:

```
/Users/darrenzal/venvs/koi-server/bin/python -m pytest tests/test_fact_retraction_boundary.py -p no:cacheprovider -p no:warnings -q
```

### 1.3 The incident's "delivery 4/4", re-read

The five incident facts, audited read-only on the live database
([`scripts/audit_fact_retractions.py`](../../scripts/audit_fact_retractions.py) `--fact-id …`, §4.6; output saved with the session):

| fact | committed `valid_to` | carrying events | `confirmed_by` (receipt) | `delivered_to` (includes scope-exclusions; not evidence) |
|---|---|---|---|---|
| `aaca4875-07ea-46f1-a6e9-e744e2963237` | `2026-09-14T15:13:07.924484+00:00` | NEW `4b9d4401…` (live copy) · UPDATE `b9c3345b…` (tombstone, exact) | nuc-personal | 4 |
| `b944d963-733d-48a2-83fb-c8a5f45f5600` | `2026-09-14T15:13:08.124536+00:00` | same two events | nuc-personal | 4 |
| `8e137ee3-10c2-4706-9d91-de95d2e265d1` | `2026-09-14T15:13:08.245582+00:00` | NEW `40ffbffa…` · UPDATE `c1af376a…` | nuc-personal | 4 |
| `8c484441-2352-4bd3-aa11-714b9072321c` | `2026-09-14T15:13:08.360058+00:00` | NEW `fb4b0fa8…` · UPDATE `801576bc…` | nuc-personal | 4 |
| `a269d7f3-3db3-4680-9f18-0a3356dcb30d` | `2026-09-14T15:13:08.514162+00:00` | same two events | nuc-personal | 4 |

Three distinct UPDATE events carry the five tombstones (2 + 1 + 2), each with the exact
committed `valid_to`. Each was marked `delivered_to` four peers and **confirmed by one**.
The four are `shawn`, `front-range`, `cowichan-valley`, `nuc-personal`; their APPROVED
outbound edge scopes, read live:

```
psql personal_koi -X -c "select target_node, rid_types from koi_net_edges where source_node like 'orn:koi-net.node:darren-personal+%' and status='APPROVED'"
```

| peer (APPROVED edge from darren-personal) | `rid_types` |
|---|---|
| nuc-personal | `{document,Project,claim,Person,knowledge_episode,document_entity_link,Vault-file,Concept,attestation,intent,commitment,knowledge_fact,entity,Location,task,commitment_pool,Organization}` |
| cowichan-valley (`…52ae5cd1514bfa02cdb2958574ecfe63…`) | `{Organization,Person,Project,Concept,Location,vault-file}` |
| friend-e2e | `{Organization,Person,Project,Concept,Location}` |
| front-range | `{Organization,Person,Project,Concept,Location}` |
| shawn | `{SpecDoc}` |

Only nuc-personal's edge admits `knowledge_episode` or `knowledge_fact`. The other three
`delivered_to` entries on every one of those events are exclusion marks: `poll()` looked at
the event on their behalf, refused it by scope, and marked it so it would not block the head
of their queue. **"Delivery 4/4" was one hand-over and three refusals.** Application, even for
the one receipt, is unproven from this node (§3).

The audit classifies exactly that: nuc-personal `tombstone_confirmed` on all five; shawn,
front-range and cowichan-valley `unauthorized` (evidence `unverifiable`: a `delivered_to` mark
on both the live copy and the tombstone, receipt of neither); friend-e2e, the REJECTED
octo-salish-sea edge and the REVOKED short-RID cowichan-valley edge `never_sent`.

### 1.4 The systemic number

The full-population run (§4.6, no `--limit`, exit 1, `elapsed_s` 19.4) reports, from its own
`summary` block:

| measure | value |
|---|---|
| locally retracted facts (`valid_to IS NOT NULL`) | **4,188** (3 of them with a *future* `valid_to` — validity intervals such as `TRAVELS_TO`, not retractions; flagged, not excluded) |
| … carried by at least one surviving `koi_net_events` row | **4,063** (4,058 by one event, 5 — the incident — by two) |
| … with no surviving history | 125 (`history=none`; **0** `history_unknown`: the oldest event row, `2026-02-25T02:30:47Z`, predates every retracted fact) |
| (fact, peer) `possibly_live` | **3,847** — all on nuc-personal |
| (fact, peer) `unverifiable` | 196 — all on nuc-personal |
| (fact, peer) `tombstone_confirmed` | 26 (20 nuc-personal; 2 each cowichan-valley, front-range, friend-e2e — facts *born* with a `valid_to`, so the carried copy already matched) |
| (fact, peer) `unauthorized` | 732 (cowichan-valley 244, front-range 244, shawn 243, friend-e2e 1) — 729 with `unverifiable` evidence, 3 with `possibly_live` evidence |
| dry-run plan | **4,043** `would_queue` lines, all to nuc-personal; 0 `wait`; 732 `blocked` |

So the honest sentence is: **nuc-personal probably holds 3,847 facts this node has retracted,
plus 196 it may hold; the narrow-scope peers hold an unknown number of facts from before
`2c497f0` (2026-08-25, when domain events started being scoped per edge — until then every
approved edge received the whole knowledge stream), and policy today forbids telling them.**
Read live, `confirmed_by` on `knowledge_episode` NEW events by month: cowichan-valley,
front-range and friend-e2e each confirmed 271 in 2026-06, 13 in 2026-07 and 142 in 2026-08,
shawn 271 in 2026-06 only, none of the four in 2026-09 — i.e. June through August, and shawn
in June (`SELECT to_char(queued_at,'YYYY-MM'), n, count(*) FROM koi_net_events, unnest(confirmed_by) n WHERE contents->>'_koi_domain'='knowledge_episode' AND event_type='NEW' GROUP BY 1,2`).
Before this branch there was no way to see any of that.

---

## 2. The design

### 2.1 One transaction

[`api/fact_retraction.py`](../../api/fact_retraction.py) `retract_fact_transactional` runs on
the caller's connection inside the caller's transaction and never acquires another: lock the
fact (`FOR UPDATE`), write `valid_to = NOW()`, snapshot the committed row, write the
tombstone-ledger row, queue **one unicast `knowledge_fact` UPDATE event per authorized
recipient** through `EventQueue.add(conn=conn)`, write one deliveries row per peer. If any
step fails the retraction rolls back and the endpoint returns 500. This is real rather than
claimed only because `EventQueue.add` grew a `conn=` parameter
(`test_req_event_queue_add_accepts_caller_connection`;
[`tests/test_fact_retraction_outbox.py`](../../tests/test_fact_retraction_outbox.py)`::test_add_with_conn_writes_on_the_caller_connection_not_the_pool`
proves it with two real connections).

If migration 127 is absent the endpoint refuses with **503 before any write**. A retraction
whose obligation cannot be recorded is the silent-loss class this issue names.

`emit_domain_event` is deliberately not used: it is post-commit and best-effort, which is
right for a create and wrong for an obligation.

### 2.2 Wire shape

Domain `knowledge_fact`, event type `UPDATE`, unicast (`target_node` set), TTL 72 h. Copied
from the module docstring of `api/fact_retraction.py`:

> payload = the knowledge_facts row snapshot in the exact key set `_insert_fact` reads (id,
> episode_id, subject_uri, predicate, object_uri, object_literal, fact_text, valid_from,
> valid_to, created_at, group_id, source_node_rid, turn_range_start, turn_range_end,
> embedding_column, embedding_value) PLUS a `retraction` block:
>
> ```
> {"retraction_id": int, "origin_node": str, "valid_to": iso,
>  "reason": str|None, "retracted_by": str|None, "attempt": int,
>  "episode_rid": str, "source_document": str|None,
>  "original_event_ids": [str, ...]}
> ```
>
> A recipient on OLD code ignores `retraction` and applies the row through `_insert_fact`'s
> upsert — which sets `valid_to` — so the tombstone still lands where the episode exists. A
> recipient on NEW code routes on the block (`domain_event_handlers._apply_knowledge_fact`).
>
> `valid_to` in the payload is `row["valid_to"].isoformat()` of the value the
> UPDATE ... RETURNING committed. It is never constructed from the clock.

`build_retraction_payload` refuses a snapshot whose `valid_to` disagrees with the committed
value (`test_build_payload_refuses_a_snapshot_that_disagrees_with_the_committed_value`).

### 2.3 Two ledger tables (migration 127)

[`migrations/127_fact_retraction_ledger.sql`](../../migrations/127_fact_retraction_ledger.sql),
dry-run + negative controls in [`tests/test_migration_127.py`](../../tests/test_migration_127.py).

`knowledge_fact_retractions` — the tombstone ledger, one row per
`(fact_id, valid_to, origin_node)`. Written by **both** roles: the publisher in the retraction
transaction (`origin_node` = this node), the recipient on receipt (`origin_node` = the
authenticated sender). `applied_at IS NULL` means the tombstone is **pending** — the fact is
not present locally yet.

`knowledge_fact_retraction_deliveries` — publisher-only, one row per `(retraction, peer)`.
The `state` column is the operator's evidence, and it is deliberately finer than
`koi_net_events` can express:

| state | what it proves | written by |
|---|---|---|
| `queued` | outbox row + unicast event committed together with `valid_to` | the retraction transaction |
| `delivered` | this node's `poll()` **handed the event over** (not merely marked it) | `delivery_observer` hook, same connection as the `delivered_to` write |
| `received` | the peer confirmed **receipt** — nothing about application | `record_receipts` on `/koi-net/events/confirm` |
| `applied` | the peer **reported** application (`status: applied`, `already_tombstoned`, or `pending`-recorded) in the confirm payload | `record_applications`; report stored verbatim in `application` |
| `rejected` | the peer reported it could not apply; reason retained verbatim | `record_applications` |
| `retrying` | the prior event expired with no report; a fresh event id was queued, `attempt` incremented, old id kept in `attempt_history` | the sweep |
| `failed` | terminal: attempts exhausted (default 5), or the edge stopped admitting `knowledge_fact` while the obligation was open (`edge_scope_excluded_at_poll` / `edge_no_longer_admits_knowledge_fact`) | observer or sweep |
| `unverifiable` | receipt confirmed, event expired, **no application report ever arrived** — an older peer or a lost report; indistinguishable from applied without the lookup slice | the sweep |
| `unauthorized` | a peer with positive evidence (`confirmed_by`) of having received the original fact whose edge no longer admits `knowledge_fact`; nothing was sent | the retraction transaction |

Terminal states keep their first verdict; a later differing report is counted but does not
overwrite (`test_terminal_states_keep_their_first_verdict`). A report from a node other than
the row's `target_node` is ignored and logged.

### 2.4 Recipient selection

`select_recipients`: **authorized** = every peer with an APPROVED outbound edge whose scope
admits `knowledge_fact` **now**, decided by `scope_admits(rid_types)` — the one mirror of the
poll filter (`None` = everything flows; a list must contain the lowercased domain name),
pinned against a real `poll()` in
`test_scope_admits_agrees_with_a_real_poll`. A peer whose edge was narrowed or revoked since
the original delivery is not authorized, whatever it holds; if it appears in `confirmed_by` of
an event that carried the fact it is recorded `unauthorized` so the operator can see it.
`delivered_to` is not used as evidence anywhere in this path.

### 2.5 Recipient behaviour

[`api/domain_event_handlers.py`](../../api/domain_event_handlers.py):

* **Monotone tombstones.** `_insert_fact`'s upsert is now
  `valid_to = COALESCE(knowledge_facts.valid_to, EXCLUDED.valid_to)`: a stale or replayed
  NEW/UPDATE carrying `valid_to = NULL` can no longer resurrect a tombstoned fact
  (`test_req_new_after_tombstone_does_not_resurrect`).
* **Retraction path.** A payload with a `retraction` block is routed to
  `_apply_fact_retraction` before anything can write. Validation *rejects* rather than raises
  (non-UUID id; missing, unparseable or naive `valid_to`; `retraction.valid_to` disagreeing
  with the row's; ledger tables absent → `rejected: ledger_unavailable`). It never raises
  `FederationDeferred`, because an unconfirmed event is never redelivered.
* **Pending tombstones (UPDATE-before-NEW).** If the fact is absent locally, no fact row is
  minted; a ledger row with `applied_at NULL` is written. Both fact-insert paths (bundled
  episode, standalone fact) call `apply_pending_tombstones` after inserting, so a late NEW
  lands already tombstoned. On a recipient without migration 127 that call degrades to a
  logged no-op — deliberate; crashing every episode apply would be the worse failure.
* **The application report.** The handler returns
  `{application: true, domain, event_id, fact_id, status, valid_to, valid_to_matches, reason, applied_at}`
  where `valid_to` is the **local** value after the call, so a mismatch is visible to the
  publisher verbatim. `status` ∈ `applied` (the local fact was live; `valid_to` now equals the
  payload's, to the microsecond) · `already_tombstoned` (kept; `valid_to_matches` says whether
  it agrees — also the idempotent answer to a duplicate) · `pending` · `rejected`.
* **The confirm extension.** [`api/koi_poller.py`](../../api/koi_poller.py) `_poll_peer` collects those reports and sends them in
  the confirm request as `applications`; `/koi-net/events/confirm` passes them to
  `record_applications`. Confirm alone still means receipt.

### 2.6 Retry / expiry sweep

`plan_requeue` is a pure read; `apply_requeue` executes it; `sweep_once` does both in one
transaction. Rules in order: edge no longer admits → `failed`; event gone or expired with
attempts left → `retrying` with a fresh event id; attempts exhausted → `failed`; `received`
and expired with no report → `unverifiable`; otherwise wait. Wired into the poller loop
behind `KOI_FACT_RETRACTION_SWEEP=true` (default **off**, re-read every cycle).

### 2.7 The authorized lookup surfaces

* `GET /knowledge/facts/{id}/tombstone` — service token (`KOI_CLAIMS_SERVICE_TOKEN` via
  `require_service_auth`). Returns `exists`, `valid_to` whether or not set, `tombstoned`,
  `pending_tombstone`, `ledger_available`, the fact's `retractions` rows and — on the
  publisher — every peer's `deliveries` row. Every other read surface in the router keeps its
  `valid_to IS NULL` filter (`test_endpoint_retract_then_tombstone_lookup_and_ordinary_read`).
* `POST /koi-net/facts/lookup` — signed envelope required; caller must hold an APPROVED
  edge **from the answering node** whose scope admits `knowledge_fact`. Payload
  `{"fact_ids": [uuid, …]}` (max 100). Returns the same status per fact **minus** the
  deliveries ledger (never another peer's states). This is the cross-node slice a publisher
  uses to *prove* application instead of trusting the report. It is in
  [`api/koi_net_router.py`](../../api/koi_net_router.py) on this branch; it has not been called against a live peer.

---

## 3. What is proven, and what is not

**Proven in this branch (scratch DB, rolled back — 8 boundary, 33 outbox, 21 apply, 3 integration, 14 migration and 26 audit tests, counted by `grep -c "def test_"`):**

* The retraction endpoint queues exactly one unicast event per authorized recipient, in the
  same transaction as `valid_to`, carrying the committed `valid_to` byte-for-byte; none to a
  narrow-scope or revoked peer; nothing at all when the ledger is absent.
* A real `poll()` moves the delivery to `delivered`; a real `confirm()` moves it to
  `received` and no further; an `applications` report moves it to `applied`/`rejected`.
* The recipient handler's four outcomes, the pending-tombstone landing, and that a
  `valid_to = NULL` replay cannot resurrect.
* End to end through the real transport on one database with two roles
  ([`tests/integration/test_federation_retraction.py`](../../tests/integration/test_federation_retraction.py)) — read its "ONE DATABASE, TWO ROLES"
  note before trusting an assertion there; the footprints of the two roles are not disjoint.

**Not proven, and not provable from this node:**

* `applied` is the **recipient's report**, carried back over the confirm channel. A peer on
  older code sends no report: its deliveries stop at `received` and the sweep ages them to
  `unverifiable`. That is the honest state, not a bug.
* Cross-node proof of a tombstone needs `POST /koi-net/facts/lookup` on a peer **running this
  code**. The NUC does not receive code automatically — nothing automated has put code on its
  koi-processor tree since 2026-07-20, and `deploy.sh`'s koi leg exits before syncing
  ([two-node-topology.md](../operations/two-node-topology.md) §2a). Until the NUC is updated
  by hand, every tombstone it holds is at best `tombstone_confirmed` (receipt).
* Nothing in this branch has run against a live peer. The incident's five tombstones were
  applied on the NUC by the **old** upsert path (`valid_to = EXCLUDED.valid_to`), if they were
  applied at all; the only evidence is one receipt per event.
* The 3 `unauthorized` rows with `possibly_live` evidence (cowichan-valley, front-range,
  friend-e2e; one fact each) and the 729 with `unverifiable` evidence date from before
  `2c497f0` (2026-08-25), when every approved edge still received every domain event — the
  narrow-scope peers confirmed `knowledge_episode` NEWs June through August 2026, shawn in
  June only (§1.4). Those peers may hold live copies that policy now forbids
  tombstoning. That is an operator decision, not something the code can resolve.

---

## 4. Runbook

### 4.1 Deploy order

1. **Migration 127 on the publisher, before the code that calls `retract`.** Apply:
   `psql -d personal_koi -v ON_ERROR_STOP=1 -f migrations/127_fact_retraction_ledger.sql`
   (ledger id `personal:127_fact_retraction_ledger`; down file exports before dropping).
   With the code deployed and 127 absent, every retract returns 503 and writes nothing —
   loud, by design.
2. **Migration 127 on each recipient.** A recipient without it answers every retraction
   `rejected: ledger_unavailable` (the publisher's row goes `rejected`, terminal) and cannot
   record a pending tombstone, so an UPDATE-before-NEW there lands the fact live. Its
   `apply_pending_tombstones` degrades to a logged no-op.
3. Code to **both** local checkouts (`koi-processor-service` serves :8351;
   `koi-processor-runtime` runs the launchd jobs) and restart with
   `~/.config/personal-koi/restart.sh`. Verify with `ps -o lstart=` on the serving PID, not
   with `git status` ([CLAUDE.md](../../CLAUDE.md), "ask the running process").
4. `KOI_FEDERATE_KNOWLEDGE=true` on the publisher, or retractions are local-only (the ledger
   row is still written so the audit can find them; no deliveries rows).
5. Optionally `KOI_FACT_RETRACTION_SWEEP=true` on the publisher to enable retry / expiry
   ageing in the poller loop. Without it, an obligation whose event expires unconfirmed stays
   `delivered` or `queued` forever, and a `received` row never ages to `unverifiable`.

### 4.2 Retract a fact

```
curl -s -X POST -H "Authorization: Bearer $KOI_CLAIMS_SERVICE_TOKEN" \
  -H 'Content-Type: application/json' -d '{"reason": "wrong binding"}' \
  http://localhost:8351/knowledge/facts/<uuid>/retract
```

Response: `retracted` / `already_retracted`, the committed `valid_to`, `retraction_id`, and
`federation.deliveries` — one `{target_node, state: queued|unauthorized, event_id, attempt}`
per peer. `enabled: false` means no federation was configured or the flag is off.

### 4.3 Read `tombstone_status`

```
curl -s -H "Authorization: Bearer $KOI_CLAIMS_SERVICE_TOKEN" \
  http://localhost:8351/knowledge/facts/<uuid>/tombstone
```

Read it in this order: `exists` → `tombstoned` / `valid_to` → `pending_tombstone` (recipient:
the fact has not arrived yet) → `ledger_available` (false = migration 127 absent here, and the
two lists below are empty for that reason, not because nothing happened) → `retractions`
(one per `(valid_to, origin)`) → `deliveries` (publisher only; `state`, `state_reason`,
`application` verbatim, timestamps per state). `received` is not `applied`; `unverifiable` is
"we cannot tell", not "it failed".

### 4.4 The audit CLI

[`scripts/audit_fact_retractions.py`](../../scripts/audit_fact_retractions.py):

```
scripts/audit_fact_retractions.py [--dsn DSN] [--node-rid RID] [--fact-id UUID ...] [--since ISO] [--limit N] [--json]
```

| flag | meaning |
|---|---|
| `--dsn` | default `$POSTGRES_URL`, else `postgresql://darrenzal:@localhost:5432/personal_koi` |
| `--node-rid` | this node's koi-net RID. When absent it is inferred as the majority `source_node` of `koi_net_events` rows with `_koi_domain` set (there is no self row to read), and the output says so with the counts. Live: `orn:koi-net.node:darren-personal+80e26aab…`. |
| `--fact-id` | repeatable; restrict to these facts |
| `--since` | only facts with `valid_to >=` this timestamp |
| `--limit` | at most N facts, newest `valid_to` first |
| `--json` | machine output: `{node_rid, node_rid_source, read_only, ledger_available, filters, facts[], plan[], blocked[], summary}` |
| `--apply` | **refused**, exit 2. Repair application is a #67 follow-up and does not exist in this branch. |

Exit codes: **0** no outstanding obligations · **1** outstanding (plan non-empty, or any
`possibly_live` / `unverifiable` / `unauthorized` / `tombstone_valid_to_mismatch` /
`history_unknown`) · **2** `--apply` refused · **3** misconfigured (cannot connect; node RID
undeterminable; read-only mode not in effect).

**Read-only by mechanism.** The connection runs `SET default_transaction_read_only = on`
before opening the transaction the audit reads in, and asserts
`SHOW transaction_read_only = on` before its first query (exit 3 otherwise). Any write
raises `ReadOnlySQLTransactionError`; [`tests/test_audit_fact_retractions.py`](../../tests/test_audit_fact_retractions.py)`::test_read_only_connection_refuses_a_write`
attempts one and checks nothing landed. Running it against the live `personal_koi` is safe
and is how §1.3–1.4 were produced.

**Cost.** One pass over every knowledge-carrying `koi_net_events` row — their `contents`
hold the fact ids and, for episodes, the embeddings (1,388 MB across 2,703 rows on the laptop
on 2026-09-15). ~20 s regardless of selection; the pass serves both the population counts and
the selected facts.

**Reading the output.** Per fact: `history` (`known` / `none` / `unknown` — the last means no
carrying row survives *and* the fact predates the oldest event row, so cleanup may have eaten
it); the carrying `events` with `carried` = `live_copy` (valid_to null) or `tombstone`
(valid_to set — including facts *born* with a validity interval; `valid_to_in_future` flags
those), `valid_to_matches_local`, and both transport columns labelled for what they are:

```
delivered_to (includes scope-exclusions; not evidence): 4 [...]
confirmed_by (receipt only): 1 [...]
```

Per (fact, peer): `edge_status`, `scope_admits_now` (via `fact_retraction.scope_admits`, not
a copy), the evidence booleans, the ledger row when 127 is present, and one classification:

| classification | rule | in the plan? |
|---|---|---|
| `applied` | ledger `applied` — the only source of this word | no |
| `rejected` / `failed` | ledger, verbatim | no |
| `tombstone_confirmed` | peer ∈ `confirmed_by` of an event carrying the **exact** `valid_to` (or ledger `received`); `application_proven` stays false | no |
| `tombstone_valid_to_mismatch` | the only confirmed tombstone carries a different `valid_to` | yes — with the committed value |
| `tombstone_unconfirmed` | an exact tombstone was unicast to the peer, is still pollable, is ledger `queued`/`delivered`/`retrying`, or carries a `delivered_to` mark on a peer that **confirmed** the live copy | `wait` if still pollable, else `would_queue` |
| `possibly_live` | live copy confirmed, no tombstone evidence | yes |
| `unverifiable` | live copy `delivered_to`-only (a `delivered_to` mark on the tombstone adds nothing — two non-evidence marks are not evidence), or ledger `unverifiable` | yes |
| `never_sent` | no evidence the peer was ever handed the live copy | no |
| `unauthorized` | any of the four plan-eligible classes on a peer whose edge does not admit `knowledge_fact` now; `evidence_class` keeps the underlying class | no — listed under `blocked` |
| `history_unknown` | see `history` | no |

The plan line is literally
`would queue knowledge_fact UPDATE retraction event to <peer> for fact <id> carrying valid_to=<isoformat of the DB value>`.
The `valid_to` is `.isoformat()` of the committed column, microseconds included
(`test_possibly_live_plan_carries_the_exact_valid_to`); a mutation that truncated it turned
five tests red.

### 4.5 What to do with `unauthorized`

Nothing automatic. It means: evidence says the peer may hold the fact, and the access policy
*as it stands now* does not admit `knowledge_fact` to it. The choices are (a) widen the edge
scope, re-run the audit, and let a future repair send the tombstone; (b) accept that the peer
keeps a copy this node has retracted; (c) ask the peer's operator out of band. The audit will
keep reporting the row (and exit 1) until one of those happens. On the live database that is
732 rows today, 729 of them with `unverifiable` evidence from before `2c497f0` (2026-08-25).

### 4.6 Reproduce the live numbers

```
NODE='orn:koi-net.node:darren-personal+80e26aab6b59178cd605c93b1aa0b903e61a283ee2a4ace07da3d1fabdd779f6'
POSTGRES_URL=postgresql://darrenzal:@localhost:5432/personal_koi \
  /Users/darrenzal/venvs/koi-server/bin/python scripts/audit_fact_retractions.py --json --node-rid "$NODE" > full.json
POSTGRES_URL=postgresql://darrenzal:@localhost:5432/personal_koi \
  /Users/darrenzal/venvs/koi-server/bin/python scripts/audit_fact_retractions.py --node-rid "$NODE" \
  --fact-id aaca4875-07ea-46f1-a6e9-e744e2963237 --fact-id b944d963-733d-48a2-83fb-c8a5f45f5600 \
  --fact-id 8e137ee3-10c2-4706-9d91-de95d2e265d1 --fact-id 8c484441-2352-4bd3-aa11-714b9072321c \
  --fact-id a269d7f3-3db3-4680-9f18-0a3356dcb30d
```

Both exit 1 today. Every number in §1.3–1.4 is read from the `summary` / `facts` / `plan`
blocks of those runs, not from a separate query.

---

## 5. Acceptance criteria for #67, as of this branch

`met-in-branch` = proved by tests on the scratch database; nothing deployed, nothing run
against a live peer. `unmet` = the branch does not do it.

| # | criterion | status | evidence / gap |
|---|---|---|---|
| 1 | Retracting one previously federated fact produces one durable update per authorized original recipient without manual episode reconstruction | **met-in-branch** | `test_req_retract_queues_one_unicast_event_per_authorized_recipient`; `test_only_admitting_approved_edges_get_an_event`; `test_queue_failure_mid_fanout_rolls_back_the_retraction`. Not deployed. |
| 2 | Each recipient stores the same fact UUID with the same non-null `valid_to` | **met-in-branch, unproven live** | `test_payload_carries_committed_valid_to_byte_for_byte`; recipient `applied` reports the local value and `valid_to_matches`. Cross-node equality needs `facts/lookup` on a peer running this code — none exists. |
| 3 | Repeated delivery is idempotent and cannot resurrect or duplicate the fact | **met-in-branch** | `already_tombstoned` report; COALESCE upsert (`test_req_new_after_tombstone_does_not_resurrect`); `record_inbound_tombstone` upsert key `(fact_id, valid_to, origin_node)`. |
| 4 | An offline peer retries after reconnect and eventually reports application or a visible terminal failure | **met-in-branch, opt-in** | sweep: `test_expired_unconfirmed_event_is_requeued_with_a_fresh_id`, `test_attempts_exhausted_is_terminal_failure`. Only runs with `KOI_FACT_RETRACTION_SWEEP=true` (default off) — with the default, no retry happens. |
| 5 | UPDATE-before-NEW has a defined, tested outcome that cannot expose the retracted fact as active | **met-in-branch, with a stated hole** | pending tombstone + `apply_pending_tombstones` on both insert paths ([`tests/test_fact_retraction_apply.py`](../../tests/test_fact_retraction_apply.py): `test_retraction_for_unknown_fact_is_pending_and_mints_no_row`, `test_pending_tombstone_lands_on_late_episode_new`, `test_pending_tombstone_lands_on_late_standalone_fact`). On a recipient **without migration 127** the tombstone is rejected, not recorded, and a later NEW lands live. |
| 6 | Operator evidence distinguishes queued, delivered, received, applied, rejected, and unverifiable states | **met-in-branch** | the nine ledger states (§2.3); `GET …/tombstone`; the audit. On the live database the ledger is absent, so today only transport evidence exists. |
| 7 | Ordinary peer search omits retracted facts while an authorized UUID lookup proves tombstone application | **partially met** | omission: pre-existing `valid_to IS NULL` filters, pinned by `test_endpoint_retract_then_tombstone_lookup_and_ordinary_read`. Lookup surfaces exist (§2.7). "Proves application" on a peer requires calling that peer's `facts/lookup`; not exercised live. |
| 8 | A historical audit reports locally retracted facts that may remain active on peers and supports a dry-run repair plan | **met** | [`scripts/audit_fact_retractions.py`](../../scripts/audit_fact_retractions.py), 26 tests, run read-only against the live database (§1.4: 4,188 / 4,063 / 3,847 `possibly_live` / plan 4,043). Repair *application* (`--apply`) is deliberately not implemented. |
| 9 | Tests cover access-policy changes and ensure tombstones are not sent to unauthorized peers | **met-in-branch** | `test_scope_narrowed_after_queueing_is_failed_not_delivered`, `test_confirmed_original_recipient_without_scope_is_recorded_unauthorized`, `test_revoked_edge_fails_the_open_obligation`; audit: `test_unauthorized_when_scope_no_longer_admits`, `test_revoked_edge_peer_with_confirmed_live_copy_is_unauthorized`. |

The lead reconciles this table with the final branch state before the PR is opened.
