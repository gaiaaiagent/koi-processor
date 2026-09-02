#!/usr/bin/env python3
"""Seed entity_non_match from the operator-adjudicated do-not-merge register.

Source: ~/.claude/plans/merge-adjudication-worklist-2026-08-31.md section C.

WHY THIS EXISTS AS A SCRIPT AND NOT A MIGRATION
------------------------------------------------
The register is prose with a table in it, hand-adjudicated by the operator.
Some rows are fully machine-shaped (two backticked URIs); others name entities
by id, by display name, or as a class ("any Clare/Claire row", "9 more 08-24
association pairs"). A migration with a hardcoded INSERT list would freeze a
partial transcription and give no signal about what it left out.

THE ONE RULE THIS SCRIPT ENFORCES ON ITSELF: every row in section C is either
SEEDED or REPORTED. Nothing is silently dropped. A veto register with quiet
gaps is worse than no register, because the gaps look like decisions -- and the
whole point of this table is to be the place where "these are NOT the same" is
remembered. Losing a row here means a resolver re-merges a pair a human already
separated, which is exactly the failure the table exists to prevent.

Usage:
    python scripts/seed_entity_non_match.py --dry-run      # default
    python scripts/seed_entity_non_match.py --apply
"""

from __future__ import annotations

import argparse
import os
import re
import sys

import psycopg2

WORKLIST = os.path.expanduser(
    "~/.claude/plans/merge-adjudication-worklist-2026-08-31.md"
)
URI_PREFIX = "orn:personal-koi.entity:"

# The register writes entity references in FOUR shapes, and an earlier version
# of this script matched only the first -- silently reclassifying the other
# three as unparseable prose. That inflated the manual list and would have left
# real vetoes unseeded while the register looked handled.
#
#   1. full shorthand   `person-clare-strawn-b5d818a9bd9c`
#   2. ellipsis + hash  `…9d1a36920cff`      (very common in the org rows)
#   3. alternate scheme `org:bioregional-economics-learning-field`
#   4. hash-less slug   `location-vashon`
#
# 2 and 4 need a database lookup to resolve, and 4 is only accepted when the
# lookup is UNAMBIGUOUS -- a slug matching several rows is reported, not guessed.
SHORTHAND = re.compile(r"`([a-z][a-z0-9]*-[^`]*?-[0-9a-f]{12})`")
ELLIPSIS_HASH = re.compile(r"`[…\.]{1,3}([0-9a-f]{12})`")
SCHEME_URI = re.compile(r"`((?:org|orn):[^`]+)`")
BARE_SLUG = re.compile(r"`([a-z][a-z0-9]*-[a-z0-9-]+)`")
ASSERTED_BY = "merge-adjudication-worklist-2026-08-31 section C (operator: Darren Zal)"
OVERRIDE_ASSERTED_BY = (
    "merge-adjudication-worklist-2026-08-31 ADJUDICATION RECORD, operator override "
    "(Darren Zal, 2026-08-31 18:05 PDT) -- testimony, not corpus inference"
)

# --- The five operator overrides ---------------------------------------------
# The adjudication record says, verbatim: "C: approved; add rows 1-5 below to the
# register." Those five were never transcribed into section C's table, so parsing
# section C alone -- however faithfully -- misses them. They are listed here
# explicitly because they are the HIGHEST-VALUE rows in the register and the only
# ones that cannot be re-derived: each is operator testimony that contradicts what
# the corpus implies. A resolver will never learn these from the data, and a
# future re-read of the documents will keep proposing the merges they forbid.
E = "orn:personal-koi.entity:"
OPERATOR_OVERRIDES = [
    (E + "organization-cascadia-north-9229f3b70ff4", E + "organization-cnss-27e28c2235a8",
     "Override 1: a separate group named 'Cascadia North' existed (several meetings + a Signal "
     "chat; Daniel Lindenberger, Darren Zal, Clare Attwell, Patricia Parkinson). The org row is "
     "that group/initiative; CNSS is the legal vehicle. Distinct."),
    (E + "organization-cascadia-north-9229f3b70ff4",
     E + "organization-cascadia-north-services-society-05b362e76d84",
     "Override 1 (same ruling, other CNSS spelling): the 'Cascadia North' group is not the "
     "Services Society legal vehicle. Vetoed against both CNSS rows so the veto survives the "
     "section-A6 CNSS<->CNSS-Society merge whichever row wins."),
    (E + "organization-salt-spring-digital-ecologies-7ef74fe2678e",
     E + "organization-salt-spring-ai-0d9fe1ba1105",
     "Override 2: Salt Spring Digital Ecologies is an ART SHOW "
     "(saltspringarts.com/sas/digital-ecologies/) -- also a retype candidate (Event/Project, not "
     "Organization). Salt Spring AI is a different community group. Distinct confirmed."),
    (E + "organization-lightcone-foundation-491050263902", E + "organization-lightcone-ddcc7176cbb3",
     "Override 3: multiple distinct 'Lightcone' groups exist (>=2, likely 3 referents). Session "
     "d3f5090f's referent is Lightcone Commons / Lightcone Infrastructure (LessWrong; Habryka "
     "correspondence), NOT the Lightcone Foundation row. Do not merge."),
    (E + "organization-raven-8875a6e498ff", E + "organization-raven-trust-53b3b2a06bcc",
     "Override 4: bare 'Raven' sits in Indigenomics Book-2 work where Raven Indigenous Capital "
     "Partners is equally plausible. Needs per-document resolution, not a merge. Do not merge."),
    (E + "concept-lekwungen-3abc2d34e741", E + "organization-lekwungen-0421e1b246af",
     "Override 5: 'Lekwungen' is primarily the name of an Indigenous LANGUAGE; it may also denote "
     "a people/nation or a territory. Do not collapse language-sense and people-sense into one "
     "row. The canonical type choice is culturally sensitive and stays with the operator."),
]


def load_section_c(path: str) -> str:
    text = open(path).read()
    try:
        body = text.split("## C. Do-NOT-merge register")[1]
        return body.split("## D. Resolver-review prefills")[0]
    except IndexError:
        sys.exit("FATAL: could not locate section C boundaries in the worklist")


def resolve_cell(cell: str, cur) -> tuple[list[str], list[str]]:
    """Return (resolved_uris, ambiguity_notes) for one table cell."""
    found, notes = [], []

    for slug in SHORTHAND.findall(cell):
        found.append(URI_PREFIX + slug)

    for h in ELLIPSIS_HASH.findall(cell):
        cur.execute(
            "SELECT fuseki_uri FROM entity_registry WHERE fuseki_uri LIKE %s", ("%" + h,)
        )
        rows = [r[0] for r in cur.fetchall()]
        if len(rows) == 1:
            found.append(rows[0])
        else:
            notes.append(f"hash …{h} matched {len(rows)} rows")

    for u in SCHEME_URI.findall(cell):
        cur.execute("SELECT 1 FROM entity_registry WHERE fuseki_uri=%s", (u,))
        if cur.fetchone():
            found.append(u)
        else:
            notes.append(f"scheme URI not in registry: {u}")

    # Hash-less slugs last, and only if nothing better matched this cell --
    # they are the weakest signal and must resolve to exactly one live row.
    if not found:
        for slug in BARE_SLUG.findall(cell):
            cur.execute(
                "SELECT fuseki_uri FROM entity_registry "
                "WHERE fuseki_uri LIKE %s AND merged_into IS NULL",
                (URI_PREFIX + slug + "%",),
            )
            rows = [r[0] for r in cur.fetchall()]
            if len(rows) == 1:
                found.append(rows[0])
            elif len(rows) > 1:
                notes.append(f"slug '{slug}' is ambiguous ({len(rows)} live rows)")

    return list(dict.fromkeys(found)), notes


def parse_rows(section: str, cur):
    """Yield (row_text, uris_a, uris_b, reason, notes)."""
    for line in section.splitlines():
        line = line.strip()
        if not line.startswith("|") or line.startswith("|---"):
            continue
        cells = [c.strip() for c in line.strip("|").split("|")]
        if len(cells) < 3:
            continue
        if cells[0].lower() in ("uri_a", "uri a"):
            continue  # header
        a, na = resolve_cell(cells[0], cur)
        b, nb = resolve_cell(cells[1], cur)
        yield line, a, b, cells[2], na + nb


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="write; default is dry-run")
    args = ap.parse_args()

    dsn = os.environ.get("POSTGRES_URL")
    if not dsn:
        sys.exit("FATAL: POSTGRES_URL not set (source config/personal.env)")

    section = load_section_c(WORKLIST)
    conn = psycopg2.connect(dsn)
    cur = conn.cursor()

    seedable, unparseable, missing = [], [], []

    notes_all = []
    for row_text, a_uris, b_uris, reason, notes in parse_rows(section, cur):
        notes_all.extend(notes)
        # Cross-product: a cell may legitimately name more than one URI
        # (e.g. two Octo rows that are both distinct from the real place).
        if not a_uris or not b_uris:
            unparseable.append((row_text[:150], reason[:110]))
            continue
        for a in a_uris:
            for b in b_uris:
                if a == b:
                    continue
                # resolve_cell() already returns FULL URIs -- do not re-prefix.
                seedable.append((a, b, reason))

    # Verify both endpoints exist -- the table's FK requires it, and a pair that
    # cannot be stored must be surfaced rather than swallowed by an FK error.
    checked = []
    for a, b, reason in seedable:
        cur.execute(
            "SELECT (SELECT 1 FROM entity_registry WHERE fuseki_uri=%s),"
            "       (SELECT 1 FROM entity_registry WHERE fuseki_uri=%s)",
            (a, b),
        )
        ok_a, ok_b = cur.fetchone()
        if ok_a and ok_b:
            checked.append((a, b, reason))
        else:
            missing.append((a if not ok_a else b, reason[:110]))

    if notes_all:
        print("  resolution notes (ambiguous or unfound references):")
        for n in dict.fromkeys(notes_all):
            print(f"    - {n}")
    print(f"  parsed section C")
    print(f"    seedable pairs (both endpoints resolve): {len(checked)}")
    print(f"    pairs with a missing endpoint:           {len(missing)}")
    print(f"    rows not machine-shaped:                 {len(unparseable)}")

    if missing:
        print("\n  PAIRS SKIPPED -- endpoint not in entity_registry:")
        for uri, reason in missing:
            print(f"    {uri}\n        reason: {reason}")

    if unparseable:
        print("\n  ROWS REQUIRING MANUAL ENTRY (not silently dropped):")
        print("    These name entities by id, display name, or as a class, so they")
        print("    cannot be resolved to a URI pair mechanically. Each still")
        print("    represents a real operator judgement and belongs in the table.")
        for row_text, reason in unparseable:
            print(f"    - {row_text}")

    if not args.apply:
        print(f"\n  plus {len(OPERATOR_OVERRIDES)} operator overrides from the ADJUDICATION RECORD")
        print(f"  (section C's table omits them; the record says 'add rows 1-5 to the register')")
        print(f"\n  DRY RUN -- nothing written. Re-run with --apply to seed "
              f"{len(checked) + len(OPERATOR_OVERRIDES)} pairs.")
        return 0

    written = 0
    # Operator overrides first -- they are the rows the register itself says to add.
    ov_written = 0
    for a, b, reason in OPERATOR_OVERRIDES:
        cur.execute(
            "SELECT (SELECT 1 FROM entity_registry WHERE fuseki_uri=%s),"
            "       (SELECT 1 FROM entity_registry WHERE fuseki_uri=%s)", (a, b))
        ok_a, ok_b = cur.fetchone()
        if not (ok_a and ok_b):
            print(f"  OVERRIDE NOT SEEDED (endpoint missing): {a if not ok_a else b}")
            continue
        cur.execute(
            """INSERT INTO entity_non_match (uri_lo, uri_hi, asserted_by, reason)
               VALUES (%s, %s, %s, %s) ON CONFLICT (uri_lo, uri_hi) DO NOTHING""",
            (a, b, OVERRIDE_ASSERTED_BY, reason))
        ov_written += cur.rowcount
    print(f"  operator overrides: {ov_written} new of {len(OPERATOR_OVERRIDES)}")

    for a, b, reason in checked:
        cur.execute(
            """
            INSERT INTO entity_non_match (uri_lo, uri_hi, asserted_by, reason)
            VALUES (%s, %s, %s, %s)
            ON CONFLICT (uri_lo, uri_hi) DO NOTHING
            """,
            (a, b, ASSERTED_BY, reason),
        )
        written += cur.rowcount
    conn.commit()
    print(f"\n  seeded {written} new vetoes ({len(checked) - written} already present)")
    cur.execute("SELECT count(*) FROM entity_non_match")
    print(f"  entity_non_match now holds {cur.fetchone()[0]} pairs")
    return 0


if __name__ == "__main__":
    sys.exit(main())
