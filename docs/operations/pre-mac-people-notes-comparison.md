# READ-ONLY — the two People notes that diverge, compared before the MacBook enable

**Status: READ-ONLY. Nothing chosen, merged, overwritten, repaired, or enabled.**
Written 2026-08-26 with NUC vault sync ON and MacBook vault sync OFF.

Both versions of each file are preserved at
`~/backups/casefold-20260826-184458/pre-mac-people/{mac,nuc}/`, with unified diffs
alongside. Nothing in this document modifies either node.

## Why these two are worth stopping for

The step-6 reconcile reported **33 `hash_mismatch`** entries. These two are in it,
and they are the two notes about the people this federation is *for*. If the
MacBook is enabled with `People/` in `KOI_VAULT_READONLY_PATHS` (MacBook owns) and
`KOI_VAULT_MIRROR_PATHS` (NUC mirrors, **overwrites unconditionally, no conflict
copies**), the MacBook's version wins and the NUC's is gone with no artifact left
behind.

## Measured state

| | `People/Darren Zal.md` | `People/Shawn Anderson.md` |
|---|---|---|
| MacBook disk | 24,126 B · `093b690d…` | 19,172 B · `af3d776c…` |
| NUC disk | 27,761 B · `5b82d964…` | 20,713 B · `54d7087d…` |
| MacBook DB row | `f2d4a496…` seq 370/370 size **24,046** | `30145cba…` seq 335/335 size **19,109** |
| NUC DB row | `5b82d964…` seq 15/15 size 27,761 | `54d7087d…` seq 20/20 size 20,713 |

Two things fall out of that table:

1. **The MacBook's DB row is stale against its own disk** — the recorded hash and
   size match neither the file on disk nor the NUC. The NUC's rows match their own
   disks exactly. So the MacBook's first scan will emit an UPDATE for both files
   regardless of what is decided here.
2. **Sequence numbers are wildly asymmetric** — 370/335 on the MacBook against
   15/20 on the NUC. Any resolution that leans on sequence ordering hands it to the
   MacBook automatically, and that would be an artifact of scan history, not of
   which content is better.

## What actually differs — the bodies are identical

```
Darren Zal.md    frontmatter mac=23,723 B  nuc=27,358 B   identical=False
                 body        mac=   321 B  nuc=   321 B   identical=TRUE
Shawn Anderson.md frontmatter mac=18,157 B nuc=19,698 B   identical=False
                 body        mac=   980 B  nuc=   980 B   identical=TRUE
```

**Every byte of difference is in the `mentionedIn` backlink array.** No prose, no
metadata, no hand-written content is in dispute on either node.

## Most of the difference is a path-convention artifact, not content

The MacBook writes **foldered** backlinks; the NUC writes **flat** ones:

```
mac : - '[[Meetings/IndigenomicsAI/2026-02-26 IndigenomicsAI Meeting]]'
nuc : - '[[Meetings/2026-02-26 IndigenomicsAI Meeting]]'
```

Same meeting, two path schemes. Normalising to basenames collapses most of it:

| | raw full-path diff | after path normalisation |
|---|---|---|
| Darren Zal — mac-only | 28 | **7** |
| Darren Zal — nuc-only | 81 | **46** |
| Shawn Anderson — mac-only | 22 | **9** |
| Shawn Anderson — nuc-only | 47 | **25** |

## And much of what remains on the NUC is stale

Of the genuinely NUC-only entries:

| | conflict-copy references | other | of the "other", target missing on the MacBook |
|---|---|---|---|
| Darren Zal | **27 of 46 (58%)** | 19 | 5 |
| Shawn Anderson | **12 of 25 (48%)** | 13 | 3 |

The conflict references look like:

```
- '[[Meetings/2025-08-21 Mehul<>Darren Meeting (conflict 2026-04-23 02-31-13)]]'
```

Those point at vault **conflict copies from the April 2026 storm**, which were
swept and deleted. They are dangling backlinks to files that no longer exist.

**So "the NUC version is bigger" does not mean it is richer.** Roughly half its
surplus is references to deleted conflict artifacts. Neither node holds a clean
superset.

## The consequence for how this gets resolved

`mentionedIn` is **not authored content**. Per the vault convention it is a
human-readable *cache* of the backend's `document_entity_links` table:

> **Backend is source of truth**: `document_entity_links` tracks all mentions.
> **Frontmatter is a human-readable cache**, synced via `/process-note --propagate`.
> **Full replacement**: each sync completely replaces the array.

That makes "choose the MacBook version" and "choose the NUC version" both wrong in
the same way: each would enshrine one node's stale cache as though it were content.
The category of the right answer is **regenerate both arrays from the backend and
let the sync propagate the result** — but that is a decision, and this document does
not take it.

## Open questions for the operator — none answered here

1. Should `mentionedIn` be regenerated from `document_entity_links` rather than
   either on-disk version chosen? If so, on which node, and before or after the
   MacBook is enabled?
2. Which path convention is canonical — foldered (MacBook) or flat (NUC)? The
   answer decides what a regenerated array should emit and is likely to matter for
   **all 33** `hash_mismatch` files, not just these two.
3. The 7 / 9 genuinely MacBook-only backlinks are recent (May–July 2026 meetings).
   Are they simply newer than the NUC's last propagate, or does the NUC have a
   propagate path that has stopped running?
4. Should the dangling conflict-copy references be treated as a separate cleanup
   with its own sweep, given they will recur in every other `People/` note?

## What was done, and what was not

**Done (read-only):** copied both versions of both files off both nodes; generated
unified diffs; compared frontmatter against body; normalised backlink paths and
counted true differences; classified NUC-only entries; checked whether their
targets exist on the MacBook.

**Not done:** no file written on either node, no DB row altered, no merge, no
choice recorded, no `mentionedIn` regenerated, MacBook vault sync still **OFF**.

## Artifacts

```
~/backups/casefold-20260826-184458/pre-mac-people/
  mac/Darren Zal.md          24,126 B
  mac/Shawn Anderson.md      19,172 B
  nuc/Darren Zal.md          27,761 B
  nuc/Shawn Anderson.md      20,713 B
  Darren Zal.diff
  Shawn Anderson.diff
```
